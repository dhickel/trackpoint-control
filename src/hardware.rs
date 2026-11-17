//! Exclusive, reversible ownership. Lease JSON is compatible with Python 0.1.
use crate::{sys, wire::Wire};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    os::{
        fd::AsFd,
        unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    },
    path::{Path, PathBuf},
    process::Command,
    thread,
    time::{Duration, Instant},
};

pub const RUNTIME: &str = "/run/trackpoint-control";
const SYSFS: &str = "/sys/bus/serio/devices";
const ATTRS: &[&str] = &[
    "sensitivity",
    "press_to_select",
    "rate",
    "resolution",
    "bind_mode",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    pub port: String,
    pub physical_path: PathBuf,
    pub settings: BTreeMap<String, String>,
}

impl State {
    pub fn validate(&self) -> Result<()> {
        let number = self.port.strip_prefix("serio").unwrap_or("");
        ensure!(
            !number.is_empty() && number.bytes().all(|b| b.is_ascii_digit()),
            "Invalid saved port"
        );
        ensure!(
            self.physical_path.is_absolute(),
            "Invalid saved physical path"
        );
        for (name, value) in &self.settings {
            ensure!(
                ATTRS.contains(&name.as_str()),
                "Invalid saved attribute: {name}"
            );
            let valid = if name == "bind_mode" {
                matches!(value.as_str(), "auto" | "manual")
            } else {
                !value.is_empty() && value.len() <= 10 && value.bytes().all(|b| b.is_ascii_digit())
            };
            ensure!(valid, "Invalid saved attribute value: {name}");
        }
        Ok(())
    }
}

fn entries(path: &Path) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    Ok(fs::read_dir(path)?
        .map(|x| x.map(|x| x.path()))
        .collect::<std::io::Result<_>>()?)
}

pub fn ports() -> Result<Vec<PathBuf>> {
    let mut found = Vec::new();
    for port in entries(Path::new(SYSFS))? {
        for input in entries(&port.join("input"))? {
            if fs::read_to_string(input.join("name"))
                .is_ok_and(|s| s.trim() == "TPPS/2 Elan TrackPoint")
            {
                found.push(port.clone());
                break;
            }
        }
    }
    Ok(found)
}

fn runtime(create: bool) -> Result<bool> {
    let path = Path::new(RUNTIME);
    if create {
        match fs::create_dir(path) {
            Ok(()) => fs::set_permissions(path, fs::Permissions::from_mode(0o755))?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(e) => return Err(e.into()),
        }
    }
    if !path.try_exists()? {
        return Ok(false);
    }
    let meta = fs::symlink_metadata(path)?;
    ensure!(
        meta.is_dir() && meta.uid() == 0 && meta.mode() & 0o022 == 0,
        "Unsafe runtime directory"
    );
    Ok(true)
}

fn lock(wait: Duration) -> Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(Path::new(RUNTIME).join("lock"))?;
    let meta = file.metadata()?;
    ensure!(
        meta.is_file() && meta.uid() == 0 && meta.mode() & 0o022 == 0,
        "Unsafe lock file"
    );
    sys::lock_wait(file.as_fd(), wait)
        .context("TrackPoint is already owned by a live driver or recovery process")?;
    Ok(file)
}

fn read_state() -> Result<Option<State>> {
    let path = Path::new(RUNTIME).join("lease.json");
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&path)
    {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    let meta = file.metadata()?;
    ensure!(
        meta.is_file() && meta.uid() == 0 && meta.len() <= 16384,
        "Unsafe recovery state"
    );
    let state: State = serde_json::from_reader(file)?;
    state.validate()?;
    Ok(Some(state))
}

fn remove(name: &str) -> Result<()> {
    match fs::remove_file(Path::new(RUNTIME).join(name)) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.into()),
    }
}

pub fn modprobe(module: &str) -> Result<()> {
    let mut child = Command::new("/usr/sbin/modprobe").arg(module).spawn()?;
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(status.success(), "modprobe {module} failed: {status}");
            return Ok(());
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            bail!("modprobe {module} timed out");
        }
        thread::sleep(Duration::from_millis(20));
    }
}

fn raw_node(port: &Path) -> Result<PathBuf> {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let nodes: Vec<_> = entries(&port.join("misc"))?
            .into_iter()
            .filter(|p| {
                p.file_name()
                    .is_some_and(|s| s.to_string_lossy().starts_with("serio_raw"))
            })
            .collect();
        if nodes.len() == 1 {
            let node = Path::new("/dev").join(nodes[0].file_name().unwrap());
            if fs::metadata(&node).is_ok_and(|m| m.file_type().is_char_device()) {
                return Ok(node);
            }
        }
        ensure!(
            Instant::now() < deadline,
            "Raw TrackPoint node did not appear"
        );
        thread::sleep(Duration::from_millis(20));
    }
}

pub fn restore(state: &State) -> Result<()> {
    state.validate()?;
    let port = Path::new(SYSFS).join(&state.port);
    if !port.try_exists()? {
        return Ok(());
    }
    ensure!(
        port.canonicalize()? == state.physical_path,
        "TrackPoint port no longer has the saved physical path"
    );
    // Even after a protocol/reset error, attempt to rebind stock input. Preserve
    // the lease on error so the independent recovery unit can retry the reset.
    let reset = (|| -> Result<()> {
        fs::write(port.join("drvctl"), "serio_raw")?;
        let mut wire = Wire::open(&raw_node(&port)?)?;
        wire.force_reset()?;
        Ok(())
    })();
    fs::write(port.join("bind_mode"), "auto")?;
    fs::write(port.join("drvctl"), "rescan")?;
    ensure!(
        port.join("driver")
            .canonicalize()?
            .file_name()
            .is_some_and(|n| n == "psmouse"),
        "Stock psmouse driver did not reattach"
    );
    for (name, value) in &state.settings {
        let path = port.join(name);
        if path.try_exists()? {
            fs::write(path, value)?;
        }
    }
    reset.context("Stock driver rebound, but explicit reset failed")
}

fn recover_locked() -> Result<()> {
    if let Some(state) = read_state()? {
        restore(&state)?;
        remove("lease.json")?;
    }
    remove("worker.pid")?;
    remove("worker.start")?;
    Ok(())
}

pub fn recover(wait: Duration) -> Result<()> {
    ensure!(sys::root(), "Hardware recovery requires root");
    if runtime(false)? {
        let _lock = lock(wait)?;
        recover_locked()?;
    }
    Ok(())
}

pub struct Lease {
    pub node: PathBuf,
    state: State,
    _lock: File,
    armed: bool,
}

impl Lease {
    pub fn acquire() -> Result<Self> {
        ensure!(sys::root(), "Hardware access requires root");
        runtime(true)?;
        let lock = lock(Duration::ZERO)?;
        recover_locked()?;
        let ports = ports()?;
        ensure!(
            ports.len() == 1,
            "Expected one Elan PS/2 TrackPoint, found {}",
            ports.len()
        );
        let port = &ports[0];
        ensure!(
            port.join("driver")
                .canonicalize()?
                .file_name()
                .is_some_and(|n| n == "psmouse"),
            "TrackPoint must initially use psmouse"
        );
        let mut settings = BTreeMap::new();
        for &attr in ATTRS {
            let path = port.join(attr);
            if path.try_exists()? {
                settings.insert(
                    attr.to_string(),
                    fs::read_to_string(path)?.trim().to_string(),
                );
            }
        }
        let state = State {
            port: port.file_name().unwrap().to_string_lossy().into_owned(),
            physical_path: port.canonicalize()?,
            settings,
        };
        state.validate()?;
        let path = Path::new(RUNTIME).join("lease.json");
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)?;
        serde_json::to_writer(&mut file, &state)?;
        file.flush()?;
        file.sync_all()?;
        // Arm before the first mutation so any later error triggers Drop recovery.
        let mut lease = Self {
            node: PathBuf::new(),
            state,
            _lock: lock,
            armed: true,
        };
        modprobe("serio_raw")?;
        fs::write(port.join("drvctl"), "serio_raw")?;
        lease.node = raw_node(port)?;
        Ok(lease)
    }

    pub fn finish(&mut self) -> Result<()> {
        self.armed = false;
        restore(&self.state)?;
        remove("lease.json")?;
        remove("worker.pid")?;
        remove("worker.start")?;
        Ok(())
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        if let Err(e) = self.finish() {
            eprintln!("Recovery failed; run trackpoint-control restore: {e:#}");
        }
    }
}
