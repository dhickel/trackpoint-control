import os
import socket
import threading
import unittest
from trackpoint_control.wire import Wire, ProtocolError


class WireTests(unittest.TestCase):
    def test_packets_can_precede_ack_and_contain_ack_bytes(self):
        left, right = socket.socketpair()
        w = object.__new__(Wire)
        w.fd = left.fileno()
        w.packet_size = 6
        w.packet = bytearray()
        w.packets = 0
        packets = []
        w.on_packet = packets.append
        try:
            # FA inside a framed packet is data; FA after it is the ACK.
            right.sendall(bytes([8, 0xfa, 0xfe, 0xfc, 127, 127, 0xfa]))
            self.assertEqual(w.ack(), 0xfa)
            self.assertEqual(packets, [bytes([8, 0xfa, 0xfe, 0xfc, 127, 127])])
        finally:
            left.close()
            right.close()

    def test_response_payload_is_not_mistaken_for_ack(self):
        left, right = socket.socketpair()
        w = object.__new__(Wire)
        w.fd = left.fileno()
        w.packet_size = 6
        w.packet = bytearray()
        w.packets = 0
        w.on_packet = lambda _: None
        def controller():
            for expected in (0xf8, 0, 0xf8, 0x17, 0xe9):
                self.assertEqual(right.recv(1), bytes([expected]))
                right.sendall(b'\xfa')
            right.sendall(b'\xfa\xfe\x64')
        t = threading.Thread(target=controller)
        t.start()
        try:
            self.assertEqual(w.word(0x17), 0xfafe)
        finally:
            t.join(timeout=1)
            left.close()
            right.close()

    def test_flash_and_unknown_registers_are_out_of_scope(self):
        w = object.__new__(Wire)
        for address in (0x8000, 0xf800, -1, 0x200):
            with self.assertRaises(ValueError):
                w.word(address)
        with self.assertRaises(ValueError):
            w.compat_read(0x23)

    def test_reset_recovers_unknown_packet_framing_and_pending_command(self):
        left, right = socket.socketpair()
        right.settimeout(2)
        w = object.__new__(Wire)
        w.fd = left.fileno()
        w.packet_size = 6
        w.packet = bytearray([8, 127])
        commands = []
        errors = []
        def controller():
            try:
                commands.append(right.recv(1))
                # Tail of an interrupted diagnostic report, then F5 rejected
                # as an invalid parameter to the interrupted command.
                right.sendall(b'\x7f\x7f\x7f\xfc')
                commands.append(right.recv(1))
                right.sendall(b'\x08\x00\xfa\xaa\x00')
            except Exception as error:
                errors.append(error)
        t = threading.Thread(target=controller)
        t.start()
        try:
            w.force_reset()
            self.assertEqual(w.packet_size, 3)
            self.assertEqual(w.packet, bytearray())
        finally:
            t.join(timeout=3)
            left.close()
            right.close()
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(commands, [b'\xf5', b'\xff'])


if __name__ == '__main__':
    unittest.main()
