#!/usr/bin/env python3
"""Verify native volatile-RAM writes against the known sensitivity field only.

The IBM command first proves the native address by changing it to 129.
The native command then sets 130. Finally the IBM command restores it.
No unknown RAM addresses or persistent memory are written.
"""
import os
from pathlib import Path
import sys
import time
from probe import Wire, log, main
from vendor_read import sliced, read_word


def write_word(w, address, value):
    if not 0 <= address < 0x400:
        raise ValueError('SRAM only')
    return w.command(sliced(address >> 8) + sliced(address & 255)
                     + sliced(value >> 8) + sliced(value & 255) + [0xe6])


def run(port):
    (port / 'drvctl').write_text('serio_raw')
    time.sleep(.1)
    nodes = list(port.glob('misc/serio_raw*'))
    assert len(nodes) == 1
    w = Wire(str(Path('/dev', nodes[0].name)))
    original = None
    try:
        w.drain()
        w.command([0xf5])
        assert w.command([0xe1], 2) == [3, 0]
        original = w.command([0xe2, 0x80, 0x4a], 1)[0]
        assert read_word(w, 0x72) == original
        w.command([0xe2, 0x81, 0x4a, 129])
        assert read_word(w, 0x72) == 129, 'Native sensitivity location not confirmed'
        log(native_sensitivity_confirmed='0072')
        write_word(w, 0x72, 130)
        value = read_word(w, 0x72)
        log(native_write_readback=value, success=value == 130)
        assert value == 130, 'Native write did not set sensitivity'
    finally:
        try:
            w.drain()
            w.command([0xf5])
            if original is not None:
                w.command([0xe2, 0x81, 0x4a, original])
                log(restored_sensitivity=read_word(w, 0x72))
        finally:
            os.close(w.fd)


if __name__ == '__main__':
    sys.exit(main(run, timeout=30))
