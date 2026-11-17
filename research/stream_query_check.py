#!/usr/bin/env python3
"""Check native reads while the six-byte diagnostic stream is active."""
import os
from pathlib import Path
import select
import sys
import time
from probe import Wire, log, main
from fast_read_check import fast_read


class StreamWire(Wire):
    def __init__(self,path):
        super().__init__(path)
        self.packet=bytearray()
        self.packets=[]
        self.packet_size=6

    def ack(self):
        deadline=time.monotonic()+.35
        while time.monotonic()<deadline:
            b=self.receive(max(0,deadline-time.monotonic()))
            if self.packet:
                self.packet.append(b)
                if len(self.packet)==self.packet_size:
                    self.packets.append((time.monotonic(),bytes(self.packet)))
                    self.packet.clear()
            elif b in (0xfa,0xfe,0xfc):
                return b
            elif b & 0xc8 == 8:
                self.packet.append(b)
            else:
                raise RuntimeError(f'Unframed byte {b:02x}')
        raise TimeoutError('No ACK')

    def command(self,command,count=0):
        for b in command:
            os.write(self.fd,bytes([b]))
            ack=self.ack()
            if ack!=0xfa:
                raise OSError(f'ACK {ack:02x} after {b:02x}')
        return [self.receive() for _ in range(count)]


def run(port):
    (port/'drvctl').write_text('serio_raw')
    time.sleep(.1)
    nodes=list(port.glob('misc/serio_raw*'))
    assert len(nodes)==1
    w=StreamWire(str(Path('/dev',nodes[0].name)))
    try:
        w.drain()
        w.command([0xf5])
        assert w.command([0xe1],2)==[3,0]
        w.command([0xe2,0x81,0x7f,0xa5])
        w.command([0xe2,0x81,0x28,0x30])
        w.command([0xf4])
        start=time.monotonic()
        for i in range(300):
            assert fast_read(w,0x72)==128
            x,y=[fast_read(w,a) for a in (0x17,0x18)]
            log(t=time.monotonic()-start,x=x,y=y,packets=len(w.packets))
        log(seconds=time.monotonic()-start,packets=len(w.packets),examples=[p.hex(' ') for t,p in w.packets[:10]])
    finally:
        try:
            w.command([0xf5])
            w.command([0xe2,0x81,0x28,0x20])
            w.command([0xe2,0x81,0x7f,0])
        finally:
            os.close(w.fd)


if __name__=='__main__':
    sys.exit(main(run,timeout=45))
