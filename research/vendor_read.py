#!/usr/bin/env python3
"""Read-only Elan native word query derived from its published firmware.

E6 + four (E8, 2-bit fragment) pairs for the high address byte, repeated
for the low address byte, E9 to READ. Never sends E6 after an address/data
tuple (write), EA triples (IAP), or flash unlock sequences.
"""
import os
from pathlib import Path
import sys
import time
import argparse
import struct
from probe import Wire, log, main


def sliced(value):
    result = [0xe6]
    for shift in (6, 4, 2, 0):
        result.extend([0xe8, (value >> shift) & 3])
    return result


def read_word(wire, address):
    response = wire.command(sliced(address >> 8) + sliced(address & 255) + [0xe9], 3)
    return (response[0] << 8) | response[1]


def run(port):
    (port / 'drvctl').write_text('serio_raw')
    time.sleep(0.1)
    nodes = list(port.glob('misc/serio_raw*'))
    if len(nodes) != 1:
        raise RuntimeError('Raw node not uniquely identified')
    w = Wire(str(Path('/dev', nodes[0].name)))
    try:
        w.drain()
        w.command([0xf5])
        w.command([0xe1], 2)
        values = []
        started = time.monotonic()
        for addr in addresses:
            value = read_word(w, addr)
            values.append(value)
            log(address=f'{addr:04x}', value=f'{value:04x}')
        if output:
            Path(output).write_bytes(struct.pack('<' + 'H'*len(values), *values))
        log(seconds=time.monotonic()-started, count=len(values))
    finally:
        os.close(w.fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=lambda x:int(x,0))
    parser.add_argument('--count', type=int, default=16)
    parser.add_argument('--output')
    args = parser.parse_args()
    addresses = (0x0007, 0x0008, 0x0009, 0x000a, 0x0081, 0x0091,
                 0x00a5, 0x8000, 0x8001, 0x8002, 0x8080)
    if args.start is not None:
        if not (1 <= args.count <= 4096):
            parser.error('count out of bounds')
        addresses = range(args.start, args.start+args.count)
        if not all(0 <= a < 0x400 or 0x8000 <= a < 0x9000 for a in addresses):
            parser.error('only SRAM and program read ranges are allowed')
    output = args.output
    sys.exit(main(run, timeout=300))
