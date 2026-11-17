#!/usr/bin/env python3
"""Read pointing-device button events without grabbing or changing devices."""
import json
import os
from pathlib import Path
import select
import struct
import time

fmt = struct.Struct('llHHi')
fds = {}
for device in Path('/sys/class/input').glob('event*'):
    name = (device / 'device/name').read_text().strip()
    if 'TrackPoint' in name or 'ELAN0689' in name:
        fd = os.open('/dev/input/' + device.name, os.O_RDONLY | os.O_NONBLOCK)
        fds[fd] = name
print(json.dumps({'devices': fds}), flush=True)
started = time.monotonic()
try:
    while time.monotonic() - started < 45 and fds:
        for fd in select.select(list(fds), [], [], .5)[0]:
            try:
                data = os.read(fd, fmt.size * 64)
            except OSError:
                os.close(fd)
                del fds[fd]
                continue
            for a, b, kind, code, value in fmt.iter_unpack(data):
                if kind == 1 and 0x100 <= code < 0x120:
                    print(json.dumps({'t': time.monotonic()-started,
                        'device': fds[fd], 'key': hex(code), 'value': value}), flush=True)
finally:
    for fd in fds:
        os.close(fd)
