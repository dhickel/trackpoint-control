"""Minimal standard Linux uinput relative pointer (no third-party modules)."""
import fcntl
import os
import struct


class Pointer:
    def __init__(self):
        self.fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.buttons = 0
        try:
            for event in (1, 2):
                fcntl.ioctl(self.fd, 0x40045564, event)  # UI_SET_EVBIT
            for key in (0x110, 0x111, 0x112):
                fcntl.ioctl(self.fd, 0x40045565, key)
            for axis in (0, 1):
                fcntl.ioctl(self.fd, 0x40045566, axis)
            for prop in (0, 5):  # POINTER, POINTING_STICK
                fcntl.ioctl(self.fd, 0x4004556e, prop)
            setup = struct.pack('HHHH80sI', 6, 0x17ef, 0x6060, 1,
                                b'TrackPoint Control raw sensor', 0)
            fcntl.ioctl(self.fd, 0x405c5503, setup)
            fcntl.ioctl(self.fd, 0x5501)  # UI_DEV_CREATE
        except BaseException:
            os.close(self.fd)
            self.fd = -1
            raise

    def emit(self, x, y, buttons):
        events = []
        for bit, key in ((1, 0x110), (2, 0x111), (4, 0x112)):
            if bool(buttons & bit) != bool(self.buttons & bit):
                events.append((1, key, int(bool(buttons & bit))))
        if x:
            events.append((2, 0, x))
        if y:
            events.append((2, 1, y))
        if events:
            events.append((0, 0, 0))
            payload = b''.join(struct.pack('llHHi', 0, 0, *event) for event in events)
            if os.write(self.fd, payload) != len(payload):
                raise OSError('Partial uinput event write')
        self.buttons = buttons

    def close(self):
        if self.fd >= 0:
            try:
                self.emit(0, 0, 0)
                fcntl.ioctl(self.fd, 0x5502)  # UI_DEV_DESTROY
            finally:
                os.close(self.fd)
                self.fd = -1
