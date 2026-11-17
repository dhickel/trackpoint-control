#!/usr/bin/env python3
"""Check if the confirmed diagnostic gate unlocks native sensitivity writes.

Uses only already identified mode, gate and sensitivity registers. Restores
all three before rescanning. No IAP or program-memory write commands.
"""
import os
from pathlib import Path
import sys
import time
from probe import Wire, main, log
from fast_read_check import fast_read
from native_write_check import write_word


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
        assert fast_read(w,0x72)==128
        for mode in (0x20,0x60,0x30):
            w.command([0xe2,0x81,0x7f,0xa5])
            w.command([0xe2,0x81,0x28,mode])
            try:
                write_word(w,0x72,130)
                log(mode=mode,native_sensitivity=fast_read(w,0x72))
            except (OSError,TimeoutError) as e:
                log(mode=mode,error=str(e))
                w.drain()
                w.command([0xf5])
            w.command([0xe2,0x81,0x4a,128])
            w.command([0xe2,0x81,0x28,0x20])
            w.command([0xe2,0x81,0x7f,0])
    finally:
        w.drain()
        w.command([0xf5])
        for a,v in ((0x4a,128),(0x28,0x20),(0x7f,0)):
            w.command([0xe2,0x81,a,v])
        os.close(w.fd)


if __name__=='__main__':
    sys.exit(main(run,timeout=20))
