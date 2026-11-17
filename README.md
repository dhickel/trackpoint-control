# TrackPoint Control

An experimental Linux driver that reads an Elan TrackPoint's sensor values
before its automatic zero subtraction. The host controls the zero reference,
dead zone, response curve, and thermal drift tracking. Holding a constant force
outside the dead zone never makes that force the new zero.

**Version 0.1.0: physically tested on a ThinkPad T16 Gen 1 AMD.** Movement and
all three buttons have been verified. After reducing speed and smoothing, the
tester reported that operation felt good. The implementation remains
experimental and accepts only the controller profile below. The working
transport delivers approximately **19–20 X/Y/button samples per second**;
this is a practical latency limit of this version. Ordinary button clicks
worked, but events shorter than a polling interval can be missed.

## What it changes

The service temporarily replaces `psmouse` on the selected TrackPoint's PS/2
port with `serio_raw`. It selects standard PS/2 remote mode, reads sensor
X/Y and button state, and emits a normal Linux pointing-stick device through
`uinput`. The touchpad and keyboard keep their drivers.

It does not flash firmware, patch the running kernel, replace boot files, or
enable diagnostic packet mode. Stopping the service explicitly resets the PS/2
controller, reattaches `psmouse`, and restores the original sensitivity and
other saved attributes. Rebinding `psmouse` alone is insufficient on the
tested controller: diagnostic packet mode can survive that operation.

## Supported profile

Only the following experimentally identified memory layout is accepted:

| Identifier | Value |
| --- | --- |
| PS/2 name | `TPPS/2 Elan TrackPoint` |
| Secondary ID | `03 00` |
| Vendor firmware query | `00 00 03` |
| Module query | `00 10 00` |
| IAP information query | `23 01 02` |
| Boot words 7–9 | `0000 0001 0002` |
| Parameter fingerprint 0x92–0x96 | `0013 0026 0018 0040 00d2` |

These are identification reads; the program never enters IAP. A different
controller is rejected and restored. Other ThinkPads need a matching profile
or a separately validated memory map. IBM drift registers are not silently
assumed to work on Elan hardware. Native SRAM 0x22/0x23 contains the live X/Y
measurements, 0x17/0x18 the firmware origins, and 0x12e the button bits for this
profile. The firmware origins are not followed after startup calibration.

## Install and try

Requires Linux with `serio_raw`, `uinput`, systemd, and Python 3.11 or newer.
There are no Python package dependencies and no custom kernel module.

```sh
sudo python3 install.py
# Keep your hand off the stick during the first two seconds.
sudo trackpoint-control trial --seconds 120 --enable-pointer \
    --config /etc/trackpoint-control.toml
```

The bounded trial restores the stock driver automatically. Ctrl+C also stops
it. Installation itself leaves the service stopped and does not newly enable
it at boot. Without `--enable-pointer`, a trial observes only and the TrackPoint
does not move the cursor during the capture.

```sh
sudo systemctl start trackpoint-control
sudo systemctl stop trackpoint-control
trackpoint-control status
journalctl -u trackpoint-control
```

After validating your hardware, `sudo systemctl enable trackpoint-control`
enables startup at boot. Suspend/resume has not been physically validated.
If resume or a protocol error stops
the service, it falls back to the stock driver; restart it explicitly to try
again.

## Tune

Edit `/etc/trackpoint-control.toml`, then restart the service. All numeric
settings are validated before taking ownership of the hardware. The tested
defaults are deadzone 4, gain 1, curve 1.2, and smoothing 5 ms. The desktop's
global mouse settings are unchanged.

| Setting | Purpose |
| --- | --- |
| `deadzone` | Suppress force within this radius, in sensor counts. |
| `gain` | Cursor speed above the dead zone. |
| `curve` | 1 gives a linear response; larger values accelerate stronger force. |
| `smoothing_ms` | Filter sensor noise; larger values add latency. |
| `idle_radius` | Thermal tracking is allowed only inside this stationary radius. Set 0 to disable it. |
| `idle_seconds` | Time continuously within the stationary radius before tracking. |
| `drift_tau_seconds` | Time constant for gradual zero adjustment. |
| `max_speed` | Cap the emitted velocity in relative counts per second. |
| `invert_x`, `invert_y` | Axis direction. |

The desktop's own pointing-stick acceleration can still affect the final
cursor speed. Thermal tracking never adapts to a force outside `idle_radius`,
and that radius cannot exceed `deadzone`. This avoids the usual steady-force
recalibration mechanism, but cannot guarantee correction of arbitrarily large
temperature changes. Recalibrate explicitly if the resting sensor position
has moved beyond the dead zone:

```sh
# Release the stick first; collection takes about 1.6 seconds.
sudo trackpoint-control calibrate
```

## Physical acceptance test

1. Start a bounded trial with your hand off the stick for its two-second zero.
2. Check all three buttons, scrolling, and the directions of both axes.
3. Hold a slow, steady movement for at least 20 seconds. It should continue in
   the same direction instead of slowing, stopping, or reversing after five
   seconds.
4. Release the stick. There should be no opposite-direction movement.
5. Leave it untouched for several minutes to check thermal drift and noise.
6. Stop the trial and confirm the stock TrackPoint works again.

The physical steps cannot be established by synthetic input tests. Log a
trial with `--record /path/to/samples.jsonl` to compare raw measurements and
emitted motion. These records contain pointer sensor/button data, not
keyboard input.

## Remove / recover

```sh
sudo systemctl stop trackpoint-control
sudo python3 install.py --uninstall
```

Uninstall keeps your configuration file. If a manually launched process group
was forcibly killed, run `sudo trackpoint-control restore` before uninstalling.
The systemd unit does this through `ExecStopPost` and an independent failure
recovery unit, which can survive a kill of the main service's entire process
group. Recovery refuses to interfere while a live worker holds the device lock.
A reboot also returns to
the stock driver when the service is disabled at boot.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 release.py
```

The tests cover long steady force, release, stationary noise, bounded thermal
tracking, pauses, configuration validation, and PS/2 framing. Hardware research
and primary sources are described in [research/NOTES.md](research/NOTES.md).
Downloaded proprietary firmware and private diagnostics are excluded from the
release archive and Git. `release.py` uses an explicit file allowlist and writes
a source archive and SHA-256 checksum to `dist/`. Share the archive or publish
the repository after review; the installer works from an extracted archive.

MIT license for this project; the research disassembler retains its upstream
BSD-3-Clause license.
