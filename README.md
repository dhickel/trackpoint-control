# TrackPoint Control

An experimental Rust Linux driver that reads an Elan TrackPoint's sensor values
before its automatic zero subtraction. The host controls the zero reference,
dead zone, response curve, and thermal drift tracking. With release correction
disabled, a force outside the dead zone never becomes the new zero.

**Version 0.3.0: optional correction for drift after lifting the finger.**
The new release detector is disabled by default; see its tuning and tradeoff
below. With it disabled, motion calculations match the Python reference across
48,000 test samples. Movement and three-button behavior were verified on a
ThinkPad T16 Gen 1 AMD. The implementation remains experimental and accepts only
the controller profile below. The working
transport delivers approximately **19–20 X/Y/button samples per second**;
this is a practical latency limit of this version. Ordinary button clicks
worked, but events shorter than a polling interval can be missed. The Rust port
does not change that hardware transport limit.

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

Requires 64-bit Linux (x86_64 or aarch64), `serio_raw`, `uinput`, and systemd.
Build with Rust/Cargo 1.85 or newer. The installed driver is a native executable;
Python and Cargo are not needed to run it. Only x86_64 has been hardware-tested.

```sh
git clone https://github.com/dhickel/trackpoint-control.git
cd trackpoint-control
cargo build --release --locked
sudo bash install.sh
# Keep your hand off the stick during the first two seconds.
sudo trackpoint-control trial --seconds 120 --enable-pointer \
    --config /etc/trackpoint-control.toml
```

For an extracted source archive, run the build and install commands from its
directory. Fresh installations leave release correction disabled. To try the
post-release drift fix, edit `/etc/trackpoint-control.toml` using the
[tested configuration below](#drift-after-releasing-the-stick), then run the
bounded trial with that configuration.

The bounded trial restores the stock driver automatically. Ctrl+C also stops
it. Installation itself leaves the service stopped and does not newly enable
it at boot. Without `--enable-pointer`, a trial observes only and the TrackPoint
does not move the cursor during the capture. The old `sudo python3 install.py`
command remains a compatibility entry point, but `install.sh` requires no Python.

After the trial, start the service and inspect its status:

```sh
# Keep your hand off the stick during the first two seconds.
sudo systemctl start trackpoint-control
trackpoint-control status
journalctl -u trackpoint-control -n 20 --no-pager
```

After validating your hardware, `sudo systemctl enable trackpoint-control`
enables startup at boot. Suspend/resume has not been physically validated.
If resume or a protocol error stops
the service, it falls back to the stock driver; restart it explicitly to try
again.

### Upgrade an existing installation

From the repository directory:

```sh
git pull --ff-only
cargo build --release --locked
sudo bash install.sh
# Keep your hand off the stick during the first two seconds.
sudo systemctl start trackpoint-control
```

The installer stops an existing Python or Rust service, restores its hardware
lease, and replaces the executable and service units. It preserves
`/etc/trackpoint-control.toml` and boot enablement. New settings omitted from an
older configuration use their defaults, so upgrading alone does not enable
release correction. Set `release_correction = true` and the associated tuning
below to opt in, then restart the service or run a bounded trial.

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
| `release_correction` | Enable the optional rapid-unload detector described below; default false. |
| `release_radius` | Maximum remaining force eligible for release correction, in sensor counts; default 16. |
| `release_drop` | Minimum fall in force magnitude within 150 ms; default 12 counts. |
| `release_ratio` | Remaining force must be at most this fraction of recent force; default 0.5. |
| `release_settle_ms` | Time to suppress motion and check that a release candidate settles; default 300 ms. |
| `release_noise` | Maximum distance of settling samples from their median; default 3 counts. |
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

If the pointer creeps with the stick fully released, recalibrate first. Keep
your finger off the stick throughout the collection: pressure during startup
or explicit calibration can become part of the zero reference.

For a `deadzone` of 4, the following idle tuning lets thermal tracking use the
whole dead zone and follow small changes sooner:

```toml
# Change these entries in the existing [motion] section.
idle_radius = 4.0
idle_seconds = 2.0
drift_tau_seconds = 10.0
```

Restart the service with the stick released for its first two seconds. This
tuning leaves the movement threshold, gain, response curve, and smoothing
unchanged. Tracking still stops outside the dead zone and while a button is
held. It cannot automatically recover a resting offset already outside that
zone; use explicit calibration in that case. Tracking such an offset as idle
would also risk treating a slow intentional hold as the new zero.

### Drift after releasing the stick

The sensor's neutral reading can shift after pressure and continue to relax
after the finger lifts. If it stays outside `deadzone`, ordinary idle tracking
cannot catch it. Version 0.3.0 adds optional release correction.

This complete configuration was used in the successful T16 hold/release test.
For an existing setup, update its release and idle settings while retaining
your preferred gain, response curve, and axis directions:

```toml
[motion]
deadzone = 4.0
gain = 1.0
curve = 1.2
smoothing_ms = 5.0
idle_radius = 4.0
idle_seconds = 1.0
drift_tau_seconds = 3.0
max_speed = 1800.0
invert_x = false
invert_y = true

release_correction = true
release_radius = 16.0
release_drop = 12.0
release_ratio = 0.5
release_settle_ms = 300.0
release_noise = 3.0
```

Edit the existing `[motion]` section rather than appending duplicate entries.
Restart the service with the stick released for its first two seconds to apply
the settings:

```sh
sudo systemctl restart trackpoint-control
```

After at least half a second of movement, a force drop meeting all three
radius/drop/ratio conditions starts a candidate. Motion pauses for the settling
interval. If readings stay near their median, that median becomes the new zero;
ordinary idle tracking then follows small changes. Renewed pressure, excessive
variation, a held button, or a long sampling pause cancels the candidate. A
steady reading cannot start a candidate without the preceding rapid drop.
Setting `release_correction = false` and restarting disables this detector.

**This infers release; it does not sense finger contact.** A deliberate rapid
change from strong pressure to a small steady force can look the same as
release and can be paused or recalibrated. Smaller `release_radius` or
`release_ratio`, or a larger `release_drop`, makes detection more restrictive
but can miss real releases. Verify both deliberate movements and releases on
your controller before enabling this at startup. Automatic corrections are
logged as `release_corrected` in the service journal.

## Verified results

Version 0.3.0 passes 25 Rust tests, Clippy with warnings denied, and the locked
release build. With release correction disabled, 48,000 replay samples match
the Python reference's integer motion exactly.

On the tested T16, a controlled 20-second slow right hold followed by release
produced **zero X/Y motion across 537 samples over the next 28.67 seconds**.
The tester confirmed normal movement and stopping. The pre-hold portion still
contained 3 X / 16 Y counts, so this validates the reproduced hold/release case;
it does not establish drift-free behavior for every gesture or temperature.
See [the investigation notes](research/NOTES.md#release-correction-030) for the
capture scope and the distinction between replay and physical testing.

## Physical acceptance test

1. Start a bounded trial with your hand off the stick for its two-second zero.
2. Check all three buttons, scrolling, and the directions of both axes.
3. Hold a slow, steady movement for at least 20 seconds. It should continue in
   the same direction instead of slowing, stopping, or reversing after five
   seconds.
4. Release the stick. There should be no opposite-direction movement.
5. With release correction enabled, also try gradual pressure reductions,
   reversals, and rapid changes from firm to light pressure. Check for unwanted
   pauses or recalibration during deliberate movement.
6. Leave it untouched for several minutes to check thermal drift and noise.
7. Stop the trial and confirm the stock TrackPoint works again.

The physical steps cannot be established by synthetic input tests. Log a
trial with `--record /path/to/samples.jsonl` to compare raw measurements and
emitted motion. These records contain pointer sensor/button data, not
keyboard input.

## Remove / recover

```sh
sudo systemctl stop trackpoint-control
sudo bash install.sh --uninstall
```

Uninstall keeps your configuration file. If a manually launched process group
was forcibly killed, run `sudo trackpoint-control restore` before uninstalling.
The systemd unit does this through `ExecStopPost` and an independent failure
recovery unit, which can survive a kill of the main service's entire process
group. The separate recovery unit waits up to five seconds for an exiting
worker to release its lock; it never steals a live lock. Interactive `restore`
fails immediately on contention unless `--wait-seconds` is explicitly supplied.
A reboot also returns to the stock driver when the service is disabled at boot.

## Development

```sh
cargo test --locked
cargo clippy --all-targets --locked -- -D warnings
cargo build --release --locked
# Optional development checks; Python 3.11+ is used only for these scripts.
python3 tests/parity.py
python3 release.py
```

The tests cover long steady force, release, stationary noise, bounded thermal
tracking, pauses, configuration validation, input event framing, and PS/2
framing/reset recovery. Release tests cover post-pressure offsets, a relaxing
neutral reading, gradual pressure reduction, renewed motion, reversals outside
the release region, buttons, unstable candidates, and pauses.
`tests/parity.py` compares exact integer motion and
floating-point state against the original implementation. The Python reference
is retained under `reference/python/`; it is never installed or invoked by
the Rust driver.

Explicit hardware acceptance (root required, temporarily interrupts the
TrackPoint and restores the previously running service afterward):

```sh
sudo python3 tests/hardware.py --hardware
```

This exercises virtual input, calibration, exclusive ownership, normal exit,
Ctrl+C, a killed worker, and a stalled worker. Research and primary sources
are described in [research/NOTES.md](research/NOTES.md).
Downloaded proprietary firmware and private diagnostics are excluded from the
release archive and Git. `release.py` uses an explicit file allowlist and writes
a source archive and SHA-256 checksum to `dist/`. Share the archive or publish
the repository after review; build and install from an extracted archive.

The supervisor and worker remain separate processes. The Rust implementation
keeps the shared device lock across `fork`, bounds protocol waits, and restores
the stock driver only after the worker has exited. Calibration uses a process
start-time check and `pidfd` to avoid signaling a reused PID. Linux FFI is
restricted to documented syscall/ioctl boundaries; descriptors have owned Rust
lifetimes. The installed service does not retain movement recordings unless
explicitly started with `--record`.

MIT license for this project; the research disassembler retains its upstream
BSD-3-Clause license.
