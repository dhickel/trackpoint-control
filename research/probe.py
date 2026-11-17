#!/usr/bin/env python3
"""Bounded, read-only TrackPoint protocol probe; always reattach psmouse.

Run with pkexec/sudo. Does not enter firmware update mode or write device RAM.
Only the selected pointing-stick port is temporarily rebound to serio_raw.
"""
import glob
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


def log(**record):
    print(json.dumps(record), flush=True)


class Wire:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def receive(self, timeout=0.35):
        if not select.select([self.fd], [], [], timeout)[0]:
            raise TimeoutError("No response from TrackPoint")
        data = os.read(self.fd, 1)
        if not data:
            raise OSError("TrackPoint disconnected")
        return data[0]

    def drain(self):
        deadline = time.monotonic() + .2
        while time.monotonic() < deadline and select.select([self.fd], [], [], 0.02)[0]:
            os.read(self.fd, 64)

    def command(self, command, count=0):
        received = []
        for byte in command:
            for attempt in range(3):
                os.write(self.fd, bytes([byte]))
                ack = self.receive()
                received.append(ack)
                if ack != 0xfe:
                    break
            if ack != 0xfa:
                raise OSError(f"Command {bytes(command).hex(' ')}: ACK {ack:02x} after {byte:02x}")
        for _ in range(count):
            received.append(self.receive())
        log(command=bytes(command).hex(' '), response=bytes(received).hex(' '))
        return received[-count:] if count else []


def run_probe(port):
    (port / "drvctl").write_text("serio_raw")
    deadline = time.monotonic() + 3
    nodes = []
    while time.monotonic() < deadline:
        nodes = list(port.glob("misc/serio_raw*"))
        if nodes and Path("/dev", nodes[0].name).exists():
            break
        time.sleep(0.02)
    if len(nodes) != 1:
        raise RuntimeError("Cannot identify this port's serio_raw node")
    w = Wire(str(Path("/dev", nodes[0].name)))
    try:
        w.drain()
        w.command([0xf5])
        w.drain()
        ident = w.command([0xe1], 2)
        if ident[0] not in (1, 2, 3, 4, 5, 6):
            raise RuntimeError(f"Not a recognized TrackPoint: {ident}")
        w.command([0xe9], 3)
        # Kernel reads these flag locations using the short E2 form.
        for address in (0x2c, 0x23):
            try:
                w.command([0xe2, address], 1)
            except (OSError, TimeoutError) as error:
                log(unsupported=f"short read {address:02x}", error=str(error))
                w.drain()
                w.command([0xf5])
        # IBM specification: read RAM, including the positive control 0x4a.
        for address in (0x4a, 0x5f, 0x63, 0x64, 0x65, 0x23):
            try:
                w.command([0xe2, 0x80, address], 1)
            except (OSError, TimeoutError) as error:
                log(unsupported=f"RAM read {address:02x}", error=str(error))
                w.drain()
                w.command([0xf5])
        # Read-only Elan queries from jinglewu/epstps2iap, not its updater.
        w.command([0xe6, 0xe6, 0xe6, 0xe9], 3)
        for selector in (1, 3, 6):
            cmd = [0xe6]
            for shift in (6, 4, 2, 0):
                cmd.extend([0xe8, (selector >> shift) & 3])
            w.command(cmd + [0xe9], 3)
    finally:
        os.close(w.fd)


def main(operation=run_probe, timeout=20):
    if os.geteuid() != 0:
        raise SystemExit("Use pkexec or sudo for this bounded hardware probe")
    ports = []
    for name in glob.glob("/sys/bus/serio/devices/serio*"):
        port = Path(name).resolve()
        names = [p.read_text().strip() for p in port.glob("input/input*/name")]
        if any("TrackPoint" in name for name in names):
            ports.append((port, names))
    if len(ports) != 1:
        raise SystemExit(f"Expected exactly one PS/2 TrackPoint, found {len(ports)}")
    port, names = ports[0]
    if (port / "driver").resolve().name != "psmouse":
        raise SystemExit("TrackPoint is not owned by psmouse")
    saved = {p.name: p.read_text().strip() for p in port.iterdir()
             if p.name in {"sensitivity", "press_to_select", "rate", "resolution", "bind_mode"}}
    log(port=str(port), devices=names, saved=saved)
    subprocess.run(["/usr/sbin/modprobe", "serio_raw"], check=True)
    child = os.fork()
    if child == 0:
        signal.alarm(timeout)
        try:
            operation(port)
        except Exception as error:
            log(error=str(error))
            os._exit(1)
        os._exit(0)
    def interrupted(signum, frame):
        raise InterruptedError(f"Signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    status = 1
    try:
        _, status = os.waitpid(child, 0)
    finally:
        try:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        # Parent owns restoration even if the probe child crashes or times out.
        # rescan uses driver-core probing and recreates psmouse's attribute groups;
        # explicitly binding via drvctl skips those generic groups on some kernels.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from trackpoint_control.hardware import restore
        restore({'port': port.name, 'physical_path': str(port), 'settings': saved})
        log(restored_driver=(port / "driver").resolve().name,
            probe_status=os.waitstatus_to_exitcode(status))
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
