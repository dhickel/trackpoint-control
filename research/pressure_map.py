#!/usr/bin/env python3
"""Bounded SRAM-only capture to identify pressure-responsive sensor locations."""
import os
from pathlib import Path
import sys
import time
from probe import log, main
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reference/python"))
from trackpoint_control.wire import Wire
from trackpoint_control.driver import identify


def run(port):
    (port / 'drvctl').write_text('serio_raw')
    time.sleep(.1)
    nodes = list(port.glob('misc/serio_raw*'))
    assert len(nodes) == 1
    w = Wire(str(Path('/dev', nodes[0].name)))
    try:
        w.force_reset()
        identify(w)
        w.command([0xf5])
        addresses = list(range(0x10, 0x40)) + list(range(0x100, 0x150))
        started = time.monotonic()
        log(state='capturing', seconds=70, flags=w.compat_read(0x28))
        while time.monotonic() - started < 70:
            stamp = time.monotonic() - started
            row = {}
            for address in addresses:
                row[f'{address:03x}'] = w.word(address)
                # Allow the main firmware loop to run between commands.
                time.sleep(.003)
            log(t=round(stamp, 3), end=round(time.monotonic()-started, 3), words=row)
    finally:
        w.close()


if __name__ == '__main__':
    sys.exit(main(run, timeout=80))
