use crate::{
    hardware::{Lease, RUNTIME},
    motion::{Motion, Settings, median},
    sys,
    uinput::Pointer,
    wire::Wire,
};
use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::{
    fs::{self, File, OpenOptions},
    io::{self, LineWriter, Read, Write},
    os::{
        fd::{AsFd, AsRawFd, FromRawFd, OwnedFd},
        unix::fs::{MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
    sync::atomic::Ordering,
    thread,
    time::{Duration, Instant},
};

pub fn log(value: serde_json::Value) -> Result<()> {
    let mut out = io::stdout().lock();
    serde_json::to_writer(&mut out, &value)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn identify(wire: &mut Wire) -> Result<()> {
    let identity = json!({
        "secondary": hex(&wire.command(&[0xe1], 2)?),
        "firmware": hex(&wire.query(1)?), "module": hex(&wire.query(3)?),
        "iap": hex(&wire.query(6)?),
        "boot": [wire.word(7)?, wire.word(8)?, wire.word(9)?],
        "parameters": [wire.word(0x92)?, wire.word(0x93)?, wire.word(0x94)?, wire.word(0x95)?, wire.word(0x96)?],
    });
    let expected = json!({"secondary":"0300", "firmware":"000003", "module":"001000", "iap":"230102",
        "boot":[0,1,2], "parameters":[0x13,0x26,0x18,0x40,0xd2]});
    log(json!({"identity":identity, "profile_match": identity == expected}))?;
    ensure!(
        identity == expected,
        "No verified memory layout for this controller; refusing raw-sensor mode"
    );
    ensure!(
        wire.word(0x72)? == u16::from(wire.compat_read(0x4a)?),
        "Sensitivity sanity check failed"
    );
    Ok(())
}

pub struct Options {
    pub settings: Settings,
    pub duration: Duration,
    pub observe: bool,
    pub record: Option<PathBuf>,
}

fn worker(node: &Path, options: &Options, mut heartbeat: File) -> Result<()> {
    let mut wire = Wire::open(node)?;
    wire.force_reset()?;
    wire.command(&[0xf5], 0)?;
    identify(&mut wire)?;
    let origin = [
        f64::from(wire.word(0x17)? as i16),
        f64::from(wire.word(0x18)? as i16),
    ];
    wire.command(&[0xf0], 0)?;
    wire.command(&[0xf4], 0)?;
    let mut motion = Motion::new(origin, options.settings.clone())?;
    let mut recording = if let Some(path) = &options.record {
        let file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)?;
        ensure!(
            file.metadata()?.is_file(),
            "Recording must be a regular file"
        );
        file.set_len(0)?;
        Some(LineWriter::new(file))
    } else {
        None
    };
    let started = Instant::now();
    let mut previous = started;
    let mut next_log = started;
    let mut startup = Vec::new();
    let mut zero_samples = Vec::new();
    let mut initialized = false;
    let mut pointer = None;
    log(
        json!({"state":"running", "origin":origin, "observe":options.observe,
        "duration":options.duration.as_secs_f64(), "settings":options.settings,
        "profile":"elan-t16-0300-000003-001000-230102", "implementation":"rust"}),
    )?;
    while !sys::STOP.load(Ordering::Relaxed)
        && (options.duration.is_zero() || started.elapsed() < options.duration)
    {
        let raw = [wire.word(0x22)? as i16, wire.word(0x23)? as i16];
        let sample = raw.map(f64::from);
        let buttons = wire.word(0x12e)?;
        ensure!(buttons & !7 == 0, "Unexpected button state: {buttons:04x}");
        let now = Instant::now();
        let mut delta = [0; 2];
        if !initialized {
            startup.push(sample);
            if now.duration_since(started) >= Duration::from_secs(2) {
                let center = median(&startup);
                motion.calibrate(center);
                initialized = true;
                if !options.observe {
                    pointer = Some(Pointer::new()?);
                }
                log(
                    json!({"state":"ready", "calibrated_origin":center, "startup_samples":startup.len()}),
                )?;
            }
        } else if sys::CALIBRATE.load(Ordering::Relaxed) {
            zero_samples.push(sample);
            if zero_samples.len() >= 32 {
                motion.calibrate(median(&zero_samples));
                sys::CALIBRATE.store(false, Ordering::Relaxed);
                zero_samples.clear();
                log(json!({"calibrated_origin":motion.origin}))?;
            }
        } else {
            delta = motion.step(
                sample,
                now.duration_since(previous).as_secs_f64(),
                buttons as u8,
            );
        }
        if let Some(pointer) = &mut pointer {
            pointer.emit(delta, buttons as u8)?;
        }
        heartbeat.write_all(b".")?;
        if recording.is_some() || (options.observe && now >= next_log) {
            let row = json!({"t":(now.duration_since(started).as_secs_f64()*10000.0).round()/10000.0,
                "raw":raw, "origin":motion.origin, "force":motion.filtered, "delta":delta,
                "buttons":buttons, "packet":wire.last_packet, "packets":wire.packets});
            if let Some(out) = &mut recording {
                serde_json::to_writer(&mut *out, &row)?;
                writeln!(out)?;
            }
            if now >= next_log {
                log(row)?;
                next_log = now + Duration::from_secs(1);
            }
        }
        previous = now;
    }
    // Pointer and Wire drop here. Only the supervisor performs the hardware
    // reset, including when this process exits with a protocol error or panic.
    Ok(())
}

struct Child {
    pid: libc::pid_t,
    reaped: bool,
}

impl Child {
    fn poll(&mut self) -> Result<Option<i32>> {
        if self.reaped {
            return Ok(None);
        }
        let mut status = 0;
        // SAFETY: status is a live int; pid is this process's unreaped child.
        let result = unsafe { libc::waitpid(self.pid, &mut status, libc::WNOHANG) };
        if result == self.pid {
            self.reaped = true;
            return Ok(Some(status));
        }
        if result < 0 {
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::Interrupted {
                return Err(error.into());
            }
        }
        Ok(None)
    }

    fn stop(&mut self) -> Result<i32> {
        if let Some(status) = self.poll()? {
            return Ok(status);
        }
        ensure!(!self.reaped, "Child already reaped");
        // SAFETY: until waitpid reaps the child, its PID cannot be reused.
        unsafe {
            libc::kill(self.pid, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(1);
        while Instant::now() < deadline {
            if let Some(status) = self.poll()? {
                return Ok(status);
            }
            thread::sleep(Duration::from_millis(20));
        }
        // SAFETY: this is still our unreaped child. Wait for termination before
        // allowing the parent to touch the same physical port during recovery.
        unsafe {
            libc::kill(self.pid, libc::SIGKILL);
        }
        loop {
            let mut status = 0;
            // SAFETY: same owned child and a valid status pointer as above.
            let result = unsafe { libc::waitpid(self.pid, &mut status, 0) };
            if result == self.pid {
                self.reaped = true;
                return Ok(status);
            }
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::Interrupted {
                return Err(error.into());
            }
        }
    }
}

impl Drop for Child {
    fn drop(&mut self) {
        if !self.reaped {
            let _ = self.stop();
        }
    }
}

fn runtime_write(name: &str, contents: &str) -> Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(Path::new(RUNTIME).join(name))?;
    file.write_all(contents.as_bytes())?;
    Ok(())
}

fn process_start(pid: i32) -> Result<u64> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat"))?;
    let (_, fields) = stat.rsplit_once(") ").context("Invalid process stat")?;
    Ok(fields
        .split_whitespace()
        .nth(19)
        .context("Missing process start time")?
        .parse()?)
}

pub fn run(options: Options) -> Result<i32> {
    options.settings.validate()?;
    sys::signals()?;
    let mut lease = Lease::acquire()?;
    let (read, write) = sys::pipe()?;
    let parent = std::process::id() as libc::pid_t;
    // SAFETY: the CLI is single-threaded before fork. No Rust thread or async
    // runtime is started. The child exits through _exit and never drops the
    // inherited parent Lease, while retaining its duplicate flock descriptor.
    let pid = unsafe { libc::fork() };
    if pid < 0 {
        return Err(io::Error::last_os_error().into());
    }
    if pid == 0 {
        drop(read);
        sys::STOP.store(false, Ordering::Relaxed);
        sys::CALIBRATE.store(false, Ordering::Relaxed);
        // SAFETY: PR_SET_PDEATHSIG only configures this child. Rechecking PPID
        // covers the race in which the parent exits before prctl.
        unsafe {
            libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
            if libc::getppid() != parent {
                libc::_exit(1);
            }
        }
        let outcome = std::panic::catch_unwind(|| worker(&lease.node, &options, File::from(write)));
        let code = match outcome {
            Ok(Ok(())) => 0,
            Ok(Err(error)) => {
                let _ = log(json!({"error":format!("{error:#}")}));
                1
            }
            Err(_) => {
                let _ = log(json!({"error":"Worker panicked"}));
                1
            }
        };
        // SAFETY: child-local resources have dropped in worker. _exit avoids
        // executing the parent's Lease destructor in this forked process.
        unsafe {
            libc::_exit(code);
        }
    }
    drop(write);
    let mut child = Child { pid, reaped: false };
    runtime_write("worker.start", &process_start(pid)?.to_string())?;
    runtime_write("worker.pid", &pid.to_string())?;
    let mut read = File::from(read);
    let mut last = Instant::now();
    let mut timed_out = false;
    let status = loop {
        if let Some(status) = child.poll()? {
            break status;
        }
        timed_out = last.elapsed() > Duration::from_secs(5);
        if sys::STOP.load(Ordering::Relaxed) || timed_out {
            break child.stop()?;
        }
        if sys::poll_read(read.as_fd(), Duration::from_millis(500))? {
            let mut data = [0; 1024];
            match read.read(&mut data) {
                Ok(0) => thread::sleep(Duration::from_millis(20)),
                Ok(_) => last = Instant::now(),
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                    ) => {}
                Err(e) => return Err(e.into()),
            }
        }
    };
    lease.finish()?;
    let exit = if libc::WIFEXITED(status) {
        libc::WEXITSTATUS(status)
    } else {
        -libc::WTERMSIG(status)
    };
    log(
        json!({"state":"restored", "driver":"psmouse", "worker_status":exit, "watchdog_timeout":timed_out}),
    )?;
    Ok(i32::from(exit != 0 || timed_out))
}

fn runtime_read(name: &str) -> Result<String> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(Path::new(RUNTIME).join(name))
        .context("Driver is not running")?;
    let meta = file.metadata()?;
    ensure!(
        meta.is_file() && meta.uid() == 0 && meta.len() <= 64,
        "Unsafe worker identity file"
    );
    let mut text = String::new();
    file.take(65).read_to_string(&mut text)?;
    Ok(text)
}

pub fn calibrate() -> Result<()> {
    ensure!(sys::root(), "Calibration requires root");
    let pid: i32 = runtime_read("worker.pid")?.trim().parse()?;
    let expected: u64 = runtime_read("worker.start")?.trim().parse()?;
    ensure!(pid > 1, "Invalid worker PID");
    // SAFETY: pidfd_open creates a new owned descriptor without pointer arguments.
    let raw = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) };
    ensure!(
        raw >= 0,
        "Worker is not running: {}",
        io::Error::last_os_error()
    );
    // SAFETY: successful pidfd_open returned one descriptor owned by this scope.
    let fd = unsafe { OwnedFd::from_raw_fd(raw as i32) };
    ensure!(
        process_start(pid)? == expected,
        "Stale worker PID; refusing to signal a different process"
    );
    // SAFETY: fd refers to the verified worker; null siginfo requests standard
    // signal delivery. pidfd prevents PID reuse between validation and delivery.
    let result = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            fd.as_raw_fd(),
            libc::SIGUSR1,
            std::ptr::null::<libc::siginfo_t>(),
            0,
        )
    };
    ensure!(
        result == 0,
        "Calibration signal failed: {}",
        io::Error::last_os_error()
    );
    Ok(())
}
