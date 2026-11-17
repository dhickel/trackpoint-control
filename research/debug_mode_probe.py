#!/usr/bin/env python3
"""Bounded test of the firmware's volatile diagnostic mode (no flashing).

Only E2 compatibility RAM 28 (mode flags) and 7f (diagnostic gate) are
changed, following the paths at 1bea/1f8f in the published rank-A image.
They are restored in finally before the parent rescans the stock driver.
"""
import argparse
import os
from pathlib import Path
import select
import sys
import time
from probe import Wire, log, main
from vendor_read import read_word
from fast_read_check import fast_read


def run(port):
    (port / 'drvctl').write_text('serio_raw')
    time.sleep(.1)
    nodes = list(port.glob('misc/serio_raw*'))
    assert len(nodes) == 1
    w = Wire(str(Path('/dev', nodes[0].name)))
    saved = {}
    try:
        w.drain()
        w.command([0xf5])
        assert w.command([0xe1], 2) == [3, 0]
        for address in (0x28, 0x7f):
            saved[address] = w.command([0xe2, 0x80, address], 1)[0]
        assert saved == {0x28: 0x20, 0x7f: 0}, saved
        w.command([0xe2, 0x81, 0x7f, 0xa5])
        w.command([0xe2, 0x81, 0x28, 0x20 | args.bit])
        time.sleep(.05)
        for address in (0x28, 0x7f):
            log(mode_readback=hex(address), value=w.command([0xe2, 0x80, address],1)[0])
        if args.snapshot:
            for address in list(range(0x10,0x30)) + list(range(0x100,0x1b8)):
                log(address=f'{address:04x}',value=f'{fast_read(w,address):04x}')
        w.command([0xf4])
        started=time.monotonic()
        total=0
        while time.monotonic()-started < args.seconds:
            if select.select([w.fd],[],[],.1)[0]:
                data=os.read(w.fd,64)
                total += len(data)
                log(t=round(time.monotonic()-started,6), data=data.hex(' '))
        log(bytes_received=total)
    finally:
        # Streaming data can precede command ACK; consume it until ACK arrives.
        os.write(w.fd, b'\xf5')
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            try:
                if w.receive(.1)==0xfa:
                    break
            except TimeoutError:
                pass
        w.drain()
        for address in (0x28, 0x7f):
            if address in saved:
                w.command([0xe2,0x81,address,saved[address]])
                log(restored=hex(address),value=w.command([0xe2,0x80,address],1)[0])
        os.close(w.fd)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--bit',type=lambda s:int(s,0),choices=[1,2,16,64,128],default=1)
    p.add_argument('--seconds',type=float,default=8)
    p.add_argument('--snapshot',action='store_true')
    args=p.parse_args()
    assert 0 < args.seconds <= 30
    sys.exit(main(run,timeout=int(args.seconds)+20))
