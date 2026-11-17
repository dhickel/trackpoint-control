# Elan TrackPoint investigation

Date: 2026-11-17. Machine: ThinkPad T16 Gen 1 AMD, type 21CH,
Fedora 44, kernel 7.2.7-200.fc44.x86_64. TrackPoint is on `i8042/serio1`;
touchpad is a separate I2C HID device. No serial number is needed for matching.

## Confirmed on this controller

- Linux exposes sensitivity and press-to-select. IBM reads at 0x23, 0x5f,
  0x63, 0x64, and 0x65 are rejected. A kernel sysfs addition cannot make those
  unsupported commands work.
- `E2 80 4A` reads sensitivity. `E2 81 4A value` changes it. Native SRAM
  address 0x72 was positively identified by changing sensitivity through the
  compatibility command and observing the native value, then restoring it.
- Native reads: send the high and low address bytes as Elan sliced commands,
  then E9; response contains the word in big-endian order and the sample rate.
  The shorter equivalent `F8 hi F8 lo E9` works. Each command byte needs FA.
- Direct native writes to SRAM address 0x72 are rejected, even with diagnostic
  gate A5 and mode flags 0x20, 0x60, or 0x30. No unrestricted write facility has
  been established.
- Compatibility writes `E2 81 7F A5`, then `E2 81 28 30` enable a six-byte
  diagnostic stream. Restore mode 0x20 and gate 0 when exiting normally.
- A standard PS/2 FF reset clears the diagnostic flags. Merely rescanning
  `psmouse` can leave them enabled. Crash recovery therefore explicitly resets
  the device, checks the reset completion, then rebinds the stock driver.
- A continuously busy native query stream runs at about 66 word reads/second.
  X, Y, and button queries yield about 22 combined samples/second. Pausing a
  query stream midway through a diagnostic packet caused a framing timeout;
  the final driver uses standard remote mode instead. Its working physical
  trial recorded 2,334 samples in 120.011 seconds, about 19.45 complete
  X/Y/button samples per second.
- The final driver sends F0 then F4 to keep sensor and button acquisition
  active without unsolicited reports. F5 alone did not provide working
  buttons in the earlier trial. No diagnostic-mode writes are needed.
- Omitting the high address byte from F8 reads returns standard status rather
  than SRAM data; it is not a valid speed optimization.

## Memory layout verified by physical pressure and button captures

The first inferred map was wrong. Physical captures established:

| Native SRAM | Meaning in the working driver |
| --- | --- |
| 0x22, 0x23 | X/Y measurements before software zero subtraction |
| 0x17, 0x18 | Firmware X/Y origins |
| 0x72 | Sensitivity; positively verified via compatibility command |
| 0x12e | Debounced button state, low three bits |

The older published image uses origins at 0x1a/0x1b and button state at 0x12b.
Its compatibility register block is also offset from the live device. Do not
apply old firmware addresses to another device without checking its layout.
The first prototype read the origins as if they were sensors and produced no
usable movement. A physical capture then showed 0x22/0x23 responding immediately
to pressure, while 0x17/0x18 lagged and shifted toward a held force. The driver
was corrected accordingly. Button bits 0, 1, and 2 were observed as Linux left,
right, and middle events, including simultaneous presses and releases.

## Reverse engineering

The published `PSTBIN_RANKA_.bin` is Elan's 16-bit eDSP instruction set. MAME's
eDSP disassembler provides a useful starting point. Local extensions decode
the RAM8 forms. BC/BD forms were initially labeled RAM16, but may address a
separate extended I/O space; their semantics remain uncertain. The disassembly
must not be treated as a complete or authoritative chip specification.

In the older image:

- Program 0x188c contains the PS/2 command dispatch table.
- 0x170f is the compatibility register translation table.
- 0x1a6d handles native word access. Reads bypass the compatibility map;
  ordinary writes are constrained to it.
- 0x1bea decodes diagnostic modes, gated by compatibility 0x7f = A5.
- 0x15c8 participates in long steady-force calibration; mode EA skips it,
  but other baseline adjustment at 0x1248 remains. This is not a complete
  drift-disable switch.
- 0x0a04 and 0x111a update origins from sensor readings. The new driver reads
  before that subtraction instead of trying to undo ambiguous motion deltas.

Lenovo n2hg705w embeds multiple firmware resources, including newer eDSP
images. It targets X1 Carbon/Yoga generations listed in its readme, not this
T16. It was extracted for comparison only. **No firmware was flashed.**

## Verification so far

- Hardware identification and readback checks succeeded.
- Unsupported commands were recorded as failures, not treated as successful
  because Linux cached a requested value.
- Initial stationary trials tested protocol and input creation but could not
  validate the sensor map. Only the physical capture exposed the swapped map.
- Killing the worker with SIGKILL initially exposed the diagnostic-mode
  restoration bug. After adding explicit FF reset, an independent read before
  resetting again confirmed mode 0x20 and gate 0, and stock sensitivity 128.
- Killing the entire systemd process group also killed ExecStopPost in one
  test. A separate OnFailure recovery unit was added and successfully restored
  the stock driver and removed the lease after the same kill test.
- The final remote-mode trial exposed a redundant F5 timeout during worker
  exit. Cleanup now closes input and transport; the parent alone resets and
  restores hardware. A bounded trial then exited successfully with stock input.
- Synthetic five-minute holds, release, noise, and bounded thermal tracking
  pass. The corrected physical trial produced working motion and all three
  buttons. After tuning speed and smoothing, the tester reported good operation.
- Large thermal or mechanical changes may still require explicit calibration.
  The software does not claim to distinguish every sensor bias change from
  an intentional steady force. Suspend/resume remains untested physically.

## Primary sources

- [Linux trackpoint.c](https://github.com/torvalds/linux/blob/master/drivers/input/mouse/trackpoint.c)
- [Linux trackpoint.h](https://github.com/torvalds/linux/blob/master/drivers/input/mouse/trackpoint.h)
- [IBM TrackPoint engineering specification](https://web.mit.edu/bbaren/Public/ykt3eext.pdf)
- [twelho's Elan firmware research](https://github.com/twelho/trackpoint)
- [Elan PS/2 updater source](https://github.com/jinglewu/epstps2iap)
- [MAME eDSP disassembler](https://github.com/mamedev/mame/blob/master/src/devices/cpu/edsp/edspdasm.cpp)
- [Lenovo n2hg705w readme](https://download.lenovo.com/pccbbs/mobiles/n2hg705w.txt)
- [Lenovo package page](https://pcsupport.lenovo.com/us/en/downloads/ds545434-elan-trackpoint-firmware-update-utility-for-windows-10-64-bit-thinkpad)
- [libinput pointer acceleration](https://wayland.freedesktop.org/libinput/doc/latest/pointer-acceleration.html)

The research helpers are bounded experiments, not a public configuration API.
Downloaded vendor binaries and disassemblies stay outside version control.
