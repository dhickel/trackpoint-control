#!/usr/bin/env python3
"""Confirm the F8 shorthand for the read-only native word query."""
import os
from pathlib import Path
import sys
import time
from probe import Wire, log, main
from vendor_read import read_word


def fast_read(w, address):
    r=w.command([0xf8,address >> 8,0xf8,address & 255,0xe9],3)
    return (r[0]<<8)|r[1]


def run(port):
    (port/'drvctl').write_text('serio_raw')
    time.sleep(.1)
    nodes=list(port.glob('misc/serio_raw*'))
    assert len(nodes)==1
    w=Wire(str(Path('/dev',nodes[0].name)))
    try:
        w.drain()
        w.command([0xf5])
        assert w.command([0xe1],2)==[3,0]
        for a in (0x72,0x7,0x8,0x9,0x110,0x8000,0x8020):
            value=read_word(w,a)
            fast=fast_read(w,a)
            log(address=f'{a:04x}',sliced=value,fast=fast)
            assert fast==value
    finally:
        os.close(w.fd)


if __name__=='__main__':
    sys.exit(main(run,timeout=20))
