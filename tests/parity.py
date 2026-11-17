#!/usr/bin/env python3
"""Compare Rust output with the tested Python reference. No hardware or root."""
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'reference/python'))
from trackpoint_control.motion import Motion, Settings

binary = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'target/release/trackpoint-control').resolve()
rng = random.Random(60225)
scenarios = []
for settings in (Settings(), Settings(smoothing_ms=0, idle_radius=0),
                 Settings(gain=3.2, curve=1.8, smoothing_ms=40, invert_x=True, invert_y=False)):
    rows = []
    for i in range(16000):
        # Long hold, release, idle thermal tracking, mixed movement/buttons,
        # a suspend-sized gap, and an explicit recalibration.
        if i < 6500: sample = (105.0, 200.0)
        elif i < 6800: sample = (100.0, 200.0)
        elif i < 13000: sample = (100.6, 199.8)
        else: sample = (rng.uniform(-100, 250), rng.uniform(100, 300))
        row = {'sample': sample, 'dt': rng.uniform(.03, .07), 'buttons': 0 if i < 13000 else rng.randrange(8)}
        if i == 14000: row['dt'] = 10
        if i == 15000: row['calibrate'] = [110, 205]
        rows.append(row)
    scenarios.append((settings, rows))

checked = 0
with tempfile.TemporaryDirectory(prefix='trackpoint-parity-') as tmp:
    for n, (settings, rows) in enumerate(scenarios):
        path = Path(tmp) / f'{n}.jsonl'
        with path.open('w') as f:
            f.write(json.dumps({'origin': [100, 200], 'settings': vars(settings)}) + '\n')
            for row in rows: f.write(json.dumps(row) + '\n')
        result = subprocess.run([str(binary), 'replay', '--input', str(path)], check=True, capture_output=True, text=True)
        actual = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(actual) == len(rows)
        motion = Motion((100, 200), settings)
        for i, (row, got) in enumerate(zip(rows, actual)):
            if 'calibrate' in row: motion.calibrate(row['calibrate'])
            want = motion.step(row['sample'], row['dt'], row['buttons'])
            assert list(want) == got['delta'], (n, i, want, got)
            for key, expected in (('origin', motion.origin), ('force', motion.filtered)):
                assert all(abs(a-b) < 1e-9 for a,b in zip(expected, got[key])), (n,i,key)
            checked += 1
print(f'PASS: {checked} samples; exact relative motion matches Python, float states within 1e-9')
