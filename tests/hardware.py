#!/usr/bin/env python3
"""Explicit root-only hardware acceptance: interrupts TrackPoint, then restores service."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--hardware', action='store_true', required=True)
p.add_argument('--binary', type=Path, default=Path('target/release/trackpoint-control'))
args = p.parse_args()
assert os.geteuid() == 0, 'Use sudo only on the intended test machine'
binary = str(args.binary.resolve())
runtime = Path('/run/trackpoint-control')
logs = Path('diagnostics/rust')
logs.mkdir(parents=True, exist_ok=True)
was_active = subprocess.run(['systemctl','is-active','--quiet','trackpoint-control']).returncode == 0
saved_config = Path('/etc/trackpoint-control.toml').read_bytes()
child = None
output = None
saved_settings = {}

def start(name, seconds, pointer=False):
    global child, output
    if output: output.close()
    path = logs / (name + '.log')
    output = path.open('w')
    command = [binary,'trial','--seconds',str(seconds),'--config','/etc/trackpoint-control.toml',
               '--record',str(logs / (name + '-samples.jsonl'))]
    if pointer: command.append('--enable-pointer')
    child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        if '"state":"ready"' in path.read_text(): return path
        assert child.poll() is None, path.read_text()
        time.sleep(.03)
    raise AssertionError('Worker did not become ready')

def stock():
    assert not (runtime/'lease.json').exists(), 'Lease remained after recovery'
    assert not (runtime/'worker.pid').exists(), 'PID remained after recovery'
    devices = [(p, n) for p in Path('/sys/bus/serio/devices').glob('serio*')
               for n in p.glob('input/input*/name') if n.read_text().strip()=='TPPS/2 Elan TrackPoint']
    assert len(devices)==1
    port = devices[0][0]
    assert (port/'driver').resolve().name=='psmouse'
    for name,value in saved_settings.items():
        assert (port/name).read_text().strip()==value, name

try:
    subprocess.run(['systemctl','stop','trackpoint-control'],check=True)
    initial_ports = [p for p in Path('/sys/bus/serio/devices').glob('serio*')
                     if any(n.read_text().strip()=='TPPS/2 Elan TrackPoint'
                            for n in p.glob('input/input*/name'))]
    assert len(initial_ports)==1, 'Expected one stock Elan TrackPoint'
    saved_settings = {name:(initial_ports[0]/name).read_text().strip()
                      for name in ('sensitivity','press_to_select','rate','resolution','bind_mode')
                      if (initial_ports[0]/name).exists()}
    path = start('normal', 9, pointer=True)
    assert any(p.read_text().strip()=='TrackPoint Control raw sensor'
               for p in Path('/sys/class/input').glob('event*/device/name'))
    # Neither a recovery command nor a second instance may steal the live lease.
    for command in ([binary,'restore'], [binary,'trial','--seconds','1']):
        result=subprocess.run(command, capture_output=True, text=True, timeout=3)
        assert result.returncode != 0 and 'already owned' in result.stderr, result
    subprocess.run([binary,'calibrate'],check=True)
    deadline=time.monotonic()+4
    while path.read_text().count('"calibrated_origin"') < 2 and time.monotonic()<deadline:
        time.sleep(.05)
    assert path.read_text().count('"calibrated_origin"') >= 2, path.read_text()
    assert child.wait(timeout=12)==0, path.read_text()
    stock()
    print('PASS: native input, ownership exclusion, calibration and normal exit',flush=True)

    path = start('worker-kill', 60)
    os.kill(int((runtime/'worker.pid').read_text()), signal.SIGKILL)
    assert child.wait(timeout=10)==1, path.read_text()
    stock()
    print('PASS: SIGKILL worker recovery',flush=True)

    path = start('watchdog', 60)
    os.kill(int((runtime/'worker.pid').read_text()), signal.SIGSTOP)
    assert child.wait(timeout=12)==1, path.read_text()
    assert '"watchdog_timeout":true' in path.read_text()
    stock()
    print('PASS: stalled worker watchdog and forced recovery',flush=True)

    path = start('interrupt', 60)
    child.send_signal(signal.SIGINT)
    assert child.wait(timeout=10)==0, path.read_text()
    stock()
    assert Path('/etc/trackpoint-control.toml').read_bytes()==saved_config
    print('PASS: Ctrl+C restoration and unchanged configuration',flush=True)
finally:
    if child and child.poll() is None:
        child.terminate()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    subprocess.run([binary,'restore'],check=True)
    if was_active: subprocess.run(['systemctl','start','trackpoint-control'],check=True)
    if output: output.close()
