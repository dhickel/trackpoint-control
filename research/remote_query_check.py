#!/usr/bin/env python3
"""Check standard PS/2 remote packets alongside native sensor queries."""
from pathlib import Path
import sys
import time
from probe import main, log
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
        w.command([0xf0])
        w.command([0xf4])
        started = time.monotonic()
        for i in range(200):
            raw = [w.word(0x22), w.word(0x23)]
            packet = w.command([0xeb], 3)
            if not packet[0] & 8:
                raise RuntimeError(f'Unexpected remote packet {packet.hex()}')
            log(t=time.monotonic()-started, raw=raw, packet=list(packet))
        log(seconds=time.monotonic()-started)
    finally:
        w.close()


if __name__ == '__main__':
    sys.exit(main(run, timeout=20))
