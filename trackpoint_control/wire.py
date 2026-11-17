"""Elan PS/2 commands with strict timeouts and diagnostic packet framing."""
import os
import select
import time


class ProtocolError(OSError):
    pass


def sliced(value):
    out = [0xe6]
    for shift in (6, 4, 2, 0):
        out.extend((0xe8, (value >> shift) & 3))
    return out


class Wire:
    def __init__(self, path, on_packet=lambda packet: None):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.on_packet = on_packet
        self.packet = bytearray()
        self.packet_size = 3
        self.packets = 0

    def byte(self, timeout=.35):
        if not select.select([self.fd], [], [], max(0, timeout))[0]:
            raise TimeoutError('TrackPoint response timed out')
        data = os.read(self.fd, 1)
        if not data:
            raise ProtocolError('TrackPoint disconnected')
        return data[0]

    def feed(self, byte):
        if not self.packet:
            if byte & 0xc8 != 8:
                raise ProtocolError(f'Unexpected packet header {byte:02x}')
        self.packet.append(byte)
        if len(self.packet) == self.packet_size:
            self.on_packet(bytes(self.packet))
            self.packets += 1
            self.packet.clear()

    def ack(self):
        deadline = time.monotonic() + .35
        while time.monotonic() < deadline:
            b = self.byte(deadline - time.monotonic())
            if not self.packet and b in (0xfa, 0xfe, 0xfc):
                return b
            self.feed(b)
        raise TimeoutError('TrackPoint acknowledgement timed out')

    def command(self, command, count=0):
        for byte in command:
            for retry in range(3):
                os.write(self.fd, bytes([byte]))
                ack = self.ack()
                if ack != 0xfe:
                    break
            if ack != 0xfa:
                raise ProtocolError(f'Command byte {byte:02x} rejected: {ack:02x}')
        return bytes(self.byte() for _ in range(count))

    def drain(self):
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline and select.select([self.fd], [], [], .02)[0]:
            os.read(self.fd, 64)
        self.packet.clear()

    def force_reset(self):
        """Recover even if a worker died mid-command or in a six-byte stream.

        F5 stops reports; FF is the standard volatile PS/2 reset. Consume the
        reset response independently of packet framing, which is unknown here.
        This never enters IAP or alters nonvolatile firmware.
        """
        self.packet.clear()
        for command in (0xf5, 0xff):
            os.write(self.fd, bytes([command]))
            deadline = time.monotonic() + 1
            response = bytearray()
            while time.monotonic() < deadline:
                try:
                    byte = self.byte(deadline - time.monotonic())
                except TimeoutError:
                    break
                response.append(byte)
                if command == 0xf5 and byte in (0xfa, 0xfc):
                    break
                if command == 0xff and response[-3:] == b'\xfa\xaa\x00':
                    self.packet_size = 3
                    self.drain()
                    return
        raise ProtocolError('Controller did not complete standard PS/2 reset')

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not select.select([self.fd], [], [], deadline - time.monotonic())[0]:
                break
            self.feed(self.byte())

    def word(self, address):
        # No arbitrary reads of memory-mapped peripherals/program memory.
        if not 0 <= address < 0x200:
            raise ValueError('Only controller SRAM reads are allowed')
        response = self.command([0xf8, address >> 8, 0xf8, address & 255, 0xe9], 3)
        return int.from_bytes(response[:2], 'big')

    def query(self, selector):
        if selector not in (1, 3, 6):
            raise ValueError('Unsupported identification query')
        return self.command(sliced(selector) + [0xe9], 3)

    def compat_read(self, address):
        if address not in (0x28, 0x7f, 0x4a):
            raise ValueError('Unsupported compatibility register')
        return self.command([0xe2, 0x80, address], 1)[0]

    def diagnostic_mode(self, enable):
        # All writes are to two known volatile compatibility registers.
        # This class has no flash unlock, IAP, or general RAM-write primitive.
        if enable:
            if self.compat_read(0x28) != 0x20 or self.compat_read(0x7f) != 0:
                raise ProtocolError('Controller already has custom diagnostic flags')
            self.command([0xe2, 0x81, 0x7f, 0xa5])
            self.command([0xe2, 0x81, 0x28, 0x30])
            if self.compat_read(0x28) != 0x30 or self.compat_read(0x7f) != 0xa5:
                raise ProtocolError('Diagnostic mode readback failed')
            self.packet_size = 6
        else:
            self.command([0xf5])
            self.command([0xe2, 0x81, 0x28, 0x20])
            self.command([0xe2, 0x81, 0x7f, 0])
            self.packet_size = 3

    def close(self):
        os.close(self.fd)
