#!/usr/bin/env python3
"""Hardware integration check: kill worker, verify mode reset and stock input."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trackpoint_control.hardware import Lease
from trackpoint_control.wire import Wire


log_path = Path('diagnostics/recovery-check-driver.jsonl')
with log_path.open('w') as out:
    p = subprocess.Popen([sys.executable, '-m', 'trackpoint_control', 'trial', '--seconds', '60'],
                         stdout=out, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if '"state": "ready"' in log_path.read_text():
            break
        if p.poll() is not None:
            raise RuntimeError(log_path.read_text())
        time.sleep(.05)
    else:
        p.terminate()
        raise RuntimeError('Driver never became ready')
    pid = int(Path('/run/trackpoint-control/worker.pid').read_text())
    os.kill(pid, signal.SIGKILL)
    assert p.wait(timeout=10) == 1
port = Path('/sys/bus/serio/devices/serio1')
assert (port/'driver').resolve().name == 'psmouse'
assert (port/'sensitivity').read_text().strip() == '128'
with Lease() as lease:
    w=Wire(lease.node)
    try:
        # Stop reports but DO NOT RESET before verification; otherwise this
        # check would hide a failure of the supervisor's restoration.
        os.write(w.fd,b'\xf5')
        deadline=time.monotonic()+1
        while w.byte(deadline-time.monotonic()) != 0xfa:
            if time.monotonic() >= deadline:
                raise TimeoutError('No disable ACK')
        w.drain()
        mode,gate=w.compat_read(0x28),w.compat_read(0x7f)
        print(json.dumps({'mode':mode,'gate':gate,'worker_kill_recovered':mode==0x20 and gate==0}))
        assert (mode,gate)==(0x20,0)
    finally:
        w.close()
print('PASS: explicit reset cleared diagnostic mode and restored stock input')
