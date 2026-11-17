#!/usr/bin/env python3
"""Check whether the short F8 query can omit a zero address byte. Reads only."""
from pathlib import Path
import sys
import time
from probe import main, log
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trackpoint_control.wire import Wire


def run(port):
    (port / 'drvctl').write_text('serio_raw')
    time.sleep(.1)
    node, = port.glob('misc/serio_raw*')
    w = Wire(str(Path('/dev', node.name)))
    try:
        for prefix in ([], [0xe6], [0xf8, 0]):
            for address in (0x72, 0x92, 0x93):
                w.force_reset()
                w.command([0xf5])
                expected = w.word(address)
                w.command([0xf5])
                command = prefix + [0xf8, address, 0xe9]
                try:
                    start = time.monotonic()
                    result = w.command(command, 3)
                    log(command=command, expected=expected,
                        value=int.from_bytes(result[:2], 'big'),
                        bytes=list(result), seconds=time.monotonic()-start)
                except OSError as error:
                    log(command=command, error=str(error))
    finally:
        w.close()


if __name__ == '__main__':
    sys.exit(main(run, timeout=20))
