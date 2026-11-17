//! Only SRAM/identification reads and standard volatile PS/2 mode/reset commands.
use crate::sys;
use std::{
    fs::{File, OpenOptions},
    io::{self, Read, Write},
    os::{fd::AsFd, unix::fs::OpenOptionsExt},
    path::Path,
    time::{Duration, Instant},
};

const RESPONSE: Duration = Duration::from_millis(350);
fn protocol(message: impl Into<String>) -> io::Error {
    io::Error::other(message.into())
}

pub struct Wire {
    file: File,
    packet: Vec<u8>,
    pub packet_size: usize,
    pub packets: u64,
    pub last_packet: Option<Vec<u8>>,
}

impl Wire {
    pub fn open(path: &Path) -> io::Result<Self> {
        Ok(Self::from_file(
            OpenOptions::new()
                .read(true)
                .write(true)
                .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(path)?,
        ))
    }

    pub fn from_file(file: File) -> Self {
        Self {
            file,
            packet: Vec::new(),
            packet_size: 3,
            packets: 0,
            last_packet: None,
        }
    }

    fn byte(&mut self, timeout: Duration) -> io::Result<u8> {
        let deadline = Instant::now() + timeout;
        loop {
            if !sys::poll_read(
                self.file.as_fd(),
                deadline.saturating_duration_since(Instant::now()),
            )? {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "TrackPoint response timed out",
                ));
            }
            let mut data = [0];
            match self.file.read(&mut data) {
                Ok(1) => return Ok(data[0]),
                Ok(_) => return Err(protocol("TrackPoint disconnected")),
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::Interrupted | io::ErrorKind::WouldBlock
                    ) && Instant::now() < deadline => {}
                Err(e) => return Err(e),
            }
        }
    }

    fn feed(&mut self, byte: u8) -> io::Result<()> {
        if self.packet.is_empty() && byte & 0xc8 != 8 {
            return Err(protocol(format!("Unexpected packet header {byte:02x}")));
        }
        self.packet.push(byte);
        if self.packet.len() == self.packet_size {
            self.last_packet = Some(std::mem::take(&mut self.packet));
            self.packets += 1;
        }
        Ok(())
    }

    fn ack(&mut self) -> io::Result<u8> {
        let deadline = Instant::now() + RESPONSE;
        while Instant::now() < deadline {
            let byte = self.byte(deadline.saturating_duration_since(Instant::now()))?;
            if self.packet.is_empty() && matches!(byte, 0xfa | 0xfe | 0xfc) {
                return Ok(byte);
            }
            self.feed(byte)?;
        }
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "TrackPoint acknowledgement timed out",
        ))
    }

    // Deliberately not exposed to the CLI; no general-purpose RAM/flash writer.
    pub(crate) fn command(&mut self, bytes: &[u8], count: usize) -> io::Result<Vec<u8>> {
        for &byte in bytes {
            let mut ack = 0;
            for _ in 0..3 {
                self.file.write_all(&[byte])?;
                ack = self.ack()?;
                if ack != 0xfe {
                    break;
                }
            }
            if ack != 0xfa {
                return Err(protocol(format!(
                    "Command byte {byte:02x} rejected: {ack:02x}"
                )));
            }
        }
        (0..count).map(|_| self.byte(RESPONSE)).collect()
    }

    fn drain(&mut self) -> io::Result<()> {
        let deadline = Instant::now() + Duration::from_millis(500);
        while Instant::now() < deadline
            && sys::poll_read(self.file.as_fd(), Duration::from_millis(20))?
        {
            let mut data = [0; 64];
            match self.file.read(&mut data) {
                Ok(0) => return Err(protocol("TrackPoint disconnected")),
                Ok(_) => (),
                Err(e)
                    if e.kind() == io::ErrorKind::WouldBlock
                        || e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) => return Err(e),
            }
        }
        self.packet.clear();
        Ok(())
    }

    pub fn force_reset(&mut self) -> io::Result<()> {
        self.packet.clear();
        for command in [0xf5, 0xff] {
            self.file.write_all(&[command])?;
            let deadline = Instant::now() + Duration::from_secs(1);
            let mut tail = [0; 3];
            while Instant::now() < deadline {
                let byte = match self.byte(deadline.saturating_duration_since(Instant::now())) {
                    Ok(b) => b,
                    Err(e) if e.kind() == io::ErrorKind::TimedOut => break,
                    Err(e) => return Err(e),
                };
                tail.rotate_left(1);
                tail[2] = byte;
                if command == 0xf5 && matches!(byte, 0xfa | 0xfc) {
                    break;
                }
                if command == 0xff && tail == [0xfa, 0xaa, 0] {
                    self.packet_size = 3;
                    return self.drain();
                }
            }
        }
        Err(protocol("Controller did not complete standard PS/2 reset"))
    }

    pub fn word(&mut self, address: u16) -> io::Result<u16> {
        if address >= 0x200 {
            return Err(protocol("Only controller SRAM reads are allowed"));
        }
        let bytes = self.command(&[0xf8, (address >> 8) as u8, 0xf8, address as u8, 0xe9], 3)?;
        Ok(u16::from_be_bytes([bytes[0], bytes[1]]))
    }

    pub fn query(&mut self, selector: u8) -> io::Result<Vec<u8>> {
        if !matches!(selector, 1 | 3 | 6) {
            return Err(protocol("Unsupported identification query"));
        }
        let mut cmd = vec![0xe6];
        for shift in [6, 4, 2, 0] {
            cmd.extend([0xe8, (selector >> shift) & 3]);
        }
        cmd.push(0xe9);
        self.command(&cmd, 3)
    }

    pub fn compat_read(&mut self, address: u8) -> io::Result<u8> {
        if !matches!(address, 0x28 | 0x7f | 0x4a) {
            return Err(protocol("Unsupported compatibility register"));
        }
        Ok(self.command(&[0xe2, 0x80, address], 1)?[0])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{os::fd::OwnedFd, os::unix::net::UnixStream, thread};

    fn pair() -> (Wire, UnixStream) {
        let (left, right) = UnixStream::pair().unwrap();
        right
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        (Wire::from_file(File::from(OwnedFd::from(left))), right)
    }

    #[test]
    fn packet_payload_ack_bytes_are_not_command_acknowledgements() {
        let (mut wire, mut controller) = pair();
        wire.packet_size = 6;
        controller
            .write_all(&[8, 0xfa, 0xfe, 0xfc, 127, 127, 0xfa])
            .unwrap();
        assert_eq!(wire.ack().unwrap(), 0xfa);
        assert_eq!(wire.last_packet, Some(vec![8, 0xfa, 0xfe, 0xfc, 127, 127]));
    }

    #[test]
    fn command_payload_is_not_mistaken_for_an_ack() {
        let (mut wire, mut controller) = pair();
        let task = thread::spawn(move || {
            for expected in [0xf8, 0, 0xf8, 0x22, 0xe9] {
                let mut byte = [0];
                controller.read_exact(&mut byte).unwrap();
                assert_eq!(byte, [expected]);
                controller.write_all(&[0xfa]).unwrap();
            }
            controller.write_all(&[0xfa, 0xfe, 0x64]).unwrap();
        });
        assert_eq!(wire.word(0x22).unwrap(), 0xfafe);
        task.join().unwrap();
    }

    #[test]
    fn resend_is_bounded_and_then_fails() {
        let (mut wire, mut controller) = pair();
        let task = thread::spawn(move || {
            for _ in 0..3 {
                let mut byte = [0];
                controller.read_exact(&mut byte).unwrap();
                assert_eq!(byte, [0xf5]);
                controller.write_all(&[0xfe]).unwrap();
            }
        });
        assert!(wire.command(&[0xf5], 0).is_err());
        task.join().unwrap();
    }

    #[test]
    fn reset_recovers_partial_packets_and_interrupted_parameters() {
        let (mut wire, mut controller) = pair();
        wire.packet_size = 6;
        wire.packet = vec![8, 127];
        let task = thread::spawn(move || {
            let mut byte = [0];
            controller.read_exact(&mut byte).unwrap();
            assert_eq!(byte, [0xf5]);
            controller.write_all(&[127, 127, 127, 0xfc]).unwrap();
            controller.read_exact(&mut byte).unwrap();
            assert_eq!(byte, [0xff]);
            controller.write_all(&[8, 0, 0xfa, 0xaa, 0]).unwrap();
            thread::sleep(Duration::from_millis(100));
        });
        wire.force_reset().unwrap();
        assert_eq!(wire.packet_size, 3);
        assert!(wire.packet.is_empty());
        task.join().unwrap();
    }

    #[test]
    fn reset_requires_the_complete_bat_response() {
        let (mut wire, mut controller) = pair();
        let task = thread::spawn(move || {
            let mut byte = [0];
            controller.read_exact(&mut byte).unwrap();
            controller.write_all(&[0xfa]).unwrap();
            controller.read_exact(&mut byte).unwrap();
            controller.write_all(&[0xfa, 0xaa]).unwrap();
        });
        assert!(wire.force_reset().is_err());
        task.join().unwrap();
    }

    #[test]
    fn flash_and_unknown_registers_are_rejected_before_io() {
        let (mut wire, _controller) = pair();
        for address in [0x200, 0x8000, 0xf800] {
            assert!(wire.word(address).is_err());
        }
        assert!(wire.compat_read(0x23).is_err());
        assert!(wire.query(2).is_err());
    }
}
