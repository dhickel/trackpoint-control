//! Linux uinput ABI, with owned descriptors and automatic device destruction.
use std::{
    fs::{File, OpenOptions},
    io::{self, Write},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
};

#[repr(C)]
struct Setup {
    id: [u16; 4],
    name: [u8; 80],
    ff_effects_max: u32,
}

pub struct Pointer {
    file: File,
    buttons: u8,
}

fn ioctl_value(file: &File, request: libc::c_ulong, value: libc::c_int) -> io::Result<()> {
    // SAFETY: these uinput ioctls take an integer value, not a pointer; file
    // remains open throughout the call. Only constants from linux/uinput.h are used.
    if unsafe { libc::ioctl(file.as_raw_fd(), request, value) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub fn events(old_buttons: u8, buttons: u8, delta: [i32; 2]) -> Vec<(u16, u16, i32)> {
    let mut out = Vec::new();
    for (bit, code) in [(1, 0x110), (2, 0x111), (4, 0x112)] {
        if old_buttons & bit != buttons & bit {
            out.push((1, code, i32::from(buttons & bit != 0)));
        }
    }
    for (axis, value) in delta.into_iter().enumerate() {
        if value != 0 {
            out.push((2, axis as u16, value));
        }
    }
    if !out.is_empty() {
        out.push((0, 0, 0));
    }
    out
}

impl Pointer {
    pub fn new() -> io::Result<Self> {
        let file = OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open("/dev/uinput")?;
        for event in [1, 2] {
            ioctl_value(&file, 0x40045564, event)?;
        }
        for key in [0x110, 0x111, 0x112] {
            ioctl_value(&file, 0x40045565, key)?;
        }
        for axis in [0, 1] {
            ioctl_value(&file, 0x40045566, axis)?;
        }
        for property in [0, 5] {
            ioctl_value(&file, 0x4004556e, property)?;
        }
        let mut setup = Setup {
            id: [6, 0x17ef, 0x6060, 1],
            name: [0; 80],
            ff_effects_max: 0,
        };
        let name = b"TrackPoint Control raw sensor";
        setup.name[..name.len()].copy_from_slice(name);
        assert_eq!(std::mem::size_of::<Setup>(), 92);
        // SAFETY: Setup is repr(C), matches linux/uinput.h and is fully
        // initialized. ioctl copies from this valid, live object synchronously.
        if unsafe { libc::ioctl(file.as_raw_fd(), 0x405c5503 as libc::c_ulong, &setup) } < 0 {
            return Err(io::Error::last_os_error());
        }
        ioctl_value(&file, 0x5501, 0)?;
        Ok(Self { file, buttons: 0 })
    }

    pub fn emit(&mut self, delta: [i32; 2], buttons: u8) -> io::Result<()> {
        let mut data = Vec::new();
        for (kind, code, value) in events(self.buttons, buttons, delta) {
            // input_event: two native longs, two u16s and an i32. Serialize
            // explicitly so no uninitialized C-struct padding reaches the kernel.
            data.extend((0 as libc::c_long).to_ne_bytes());
            data.extend((0 as libc::c_long).to_ne_bytes());
            data.extend(kind.to_ne_bytes());
            data.extend(code.to_ne_bytes());
            data.extend(value.to_ne_bytes());
        }
        if !data.is_empty() && self.file.write(&data)? != data.len() {
            return Err(io::Error::other("Partial uinput event write"));
        }
        self.buttons = buttons;
        Ok(())
    }
}

impl Drop for Pointer {
    fn drop(&mut self) {
        let _ = self.emit([0, 0], 0);
        let _ = ioctl_value(&self.file, 0x5502, 0);
    }
}
