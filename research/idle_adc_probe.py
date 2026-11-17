#!/usr/bin/env python3
"""Bounded read-only measurement of pre-calibration X/Y and their origins."""
import os
from pathlib import Path
import sys
import time
import statistics
from probe import Wire, log, main
from fast_read_check import fast_read


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
        started=time.monotonic()
        rows=[]
        for i in range(100):
            values=[fast_read(w,a) for a in (0x17,0x18,0x22,0x23)]
            log(t=time.monotonic()-started,values=values)
            rows.append(values)
        log(seconds=time.monotonic()-started,
            ranges=[(min(x),max(x)) for x in zip(*rows)])
    finally:
        os.close(w.fd)


if __name__=='__main__':
    sys.exit(main(run,timeout=30))
