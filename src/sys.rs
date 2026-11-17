//! Small, documented Linux FFI boundary. File ownership stays in Rust.
use std::{
    io,
    os::fd::{AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

pub static STOP: AtomicBool = AtomicBool::new(false);
pub static CALIBRATE: AtomicBool = AtomicBool::new(false);

extern "C" fn handle_signal(signal: libc::c_int) {
    if signal == libc::SIGUSR1 {
        CALIBRATE.store(true, Ordering::Relaxed);
    } else {
        STOP.store(true, Ordering::Relaxed);
    }
}

pub fn signals() -> io::Result<()> {
    // SAFETY: zero is a valid initial sigaction; all fields used by the kernel
    // are initialized below. The handler only stores lock-free atomic booleans.
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = handle_signal as *const () as usize;
        libc::sigemptyset(&mut action.sa_mask);
        for sig in [libc::SIGINT, libc::SIGTERM, libc::SIGUSR1] {
            if libc::sigaction(sig, &action, std::ptr::null_mut()) < 0 {
                return Err(io::Error::last_os_error());
            }
        }
        action.sa_sigaction = libc::SIG_IGN;
        if libc::sigaction(libc::SIGPIPE, &action, std::ptr::null_mut()) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

pub fn root() -> bool {
    // SAFETY: geteuid takes no pointers and has no memory safety preconditions.
    unsafe { libc::geteuid() == 0 }
}

pub fn poll_read(fd: BorrowedFd<'_>, timeout: Duration) -> io::Result<bool> {
    let deadline = Instant::now() + timeout;
    loop {
        let left = deadline.saturating_duration_since(Instant::now());
        let mut pfd = libc::pollfd {
            fd: fd.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let ms = left
            .as_millis()
            .saturating_add(u128::from(!left.is_zero()))
            .min(i32::MAX as u128) as i32;
        // SAFETY: pfd points to one initialized pollfd, alive throughout poll.
        let result = unsafe { libc::poll(&mut pfd, 1, ms) };
        if result >= 0 {
            return Ok(result > 0);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
        if Instant::now() >= deadline {
            return Ok(false);
        }
    }
}

pub fn pipe() -> io::Result<(OwnedFd, OwnedFd)> {
    let mut fds = [-1; 2];
    // SAFETY: fds has room for both descriptors; successful pipe2 transfers
    // exactly one owned reference to each returned OwnedFd.
    unsafe {
        if libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok((OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])))
    }
}

pub fn lock(fd: BorrowedFd<'_>) -> io::Result<()> {
    // SAFETY: fd is borrowed from a live file and flock does not close it.
    if unsafe { libc::flock(fd.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub fn lock_wait(fd: BorrowedFd<'_>, timeout: Duration) -> io::Result<()> {
    let deadline = Instant::now() + timeout;
    loop {
        match lock(fd) {
            Ok(()) => return Ok(()),
            Err(error)
                if error.kind() == io::ErrorKind::WouldBlock && Instant::now() < deadline =>
            {
                std::thread::sleep(
                    Duration::from_millis(20)
                        .min(deadline.saturating_duration_since(Instant::now())),
                );
            }
            Err(error) => return Err(error),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs::OpenOptions, os::fd::AsFd, thread};

    #[test]
    fn recovery_waits_for_lock_release_without_stealing_a_live_lock() {
        let path =
            std::env::temp_dir().join(format!("trackpoint-lock-test-{}", std::process::id()));
        let first = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        let second = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        lock(first.as_fd()).unwrap();
        let start = Instant::now();
        assert_eq!(
            lock_wait(second.as_fd(), Duration::from_millis(50))
                .unwrap_err()
                .kind(),
            io::ErrorKind::WouldBlock
        );
        assert!(start.elapsed() < Duration::from_secs(1));
        let release = thread::spawn(move || {
            thread::sleep(Duration::from_millis(40));
            drop(first);
        });
        lock_wait(second.as_fd(), Duration::from_secs(1)).unwrap();
        release.join().unwrap();
        std::fs::remove_file(path).unwrap();
    }
}
