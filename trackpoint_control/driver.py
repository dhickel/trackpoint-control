import json
import os
from pathlib import Path
import select
import signal
import statistics
import time

from .hardware import Lease, RUNTIME
from .motion import Motion, Settings, signed16
from .uinput import Pointer
from .wire import Wire, ProtocolError


def log(**values):
    print(json.dumps(values), flush=True)


def identify(wire):
    secondary = wire.command([0xe1], 2)
    identity = {
        'secondary': secondary.hex(), 'firmware': wire.query(1).hex(),
        'module': wire.query(3).hex(), 'iap': wire.query(6).hex(),
        'boot': [wire.word(a) for a in (7, 8, 9)],
        'parameters': [wire.word(a) for a in range(0x92, 0x97)],
    }
    expected = {'secondary': '0300', 'firmware': '000003',
                'module': '001000', 'iap': '230102', 'boot': [0, 1, 2],
                'parameters': [0x13, 0x26, 0x18, 0x40, 0xd2]}
    log(identity=identity, profile_match=identity == expected)
    if identity != expected:
        raise ProtocolError('No verified memory layout for this controller; refusing raw-sensor mode')
    if wire.word(0x72) != wire.compat_read(0x4a):
        raise ProtocolError('Sensitivity sanity check failed')
    return identity


class Stopped(Exception):
    pass


def worker(node, settings, duration, observe, heartbeat, record=None):
    stop = False
    calibrate = False
    buttons = 0
    last_packet = None
    def request_stop(*_):
        nonlocal stop
        stop = True
    def request_calibrate(*_):
        nonlocal calibrate
        calibrate = True
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGUSR1, request_calibrate)
    def packet(data):
        nonlocal buttons, last_packet
        buttons = data[0] & 7
        last_packet = list(data)
    wire = Wire(node, packet)
    pointer = None
    recording = None
    try:
        wire.force_reset()
        wire.command([0xf5])
        identity = identify(wire)
        # These are controller zero references at start, not a continuously
        # followed target. Later automatic recalibrations cannot move our zero.
        origin = tuple(signed16(wire.word(a)) for a in (0x17, 0x18))
        # Standard remote mode allows acquisition and button scanning while
        # suppressing unsolicited packets on the command transport.
        wire.command([0xf0])
        wire.command([0xf4])
        motion = Motion(origin, settings)
        if record:
            recording = open(record, 'w', buffering=1)
        started = previous = time.monotonic()
        next_log = started
        zero_samples = []
        startup_samples = []
        initialized = False
        log(state='running', origin=origin, observe=observe, duration=duration,
            settings=vars(settings), profile='elan-t16-0300-000003-001000-230102')
        while not stop and (duration == 0 or time.monotonic() - started < duration):
            sample = tuple(signed16(wire.word(a)) for a in (0x22, 0x23))
            # Requests can suppress the ordinary stream. Read its debounced
            # button state as well (old firmware 0x12b; this layout +3).
            buttons = wire.word(0x12e)
            if buttons & ~7:
                raise ProtocolError(f'Unexpected button-state value {buttons:04x}')
            now = time.monotonic()
            if not initialized:
                startup_samples.append(sample)
                delta = (0, 0)
                if now - started >= 2:
                    center = tuple(statistics.median(v) for v in zip(*startup_samples))
                    motion.calibrate(center)
                    initialized = True
                    if not observe:
                        pointer = Pointer()
                    log(state='ready', calibrated_origin=center, startup_samples=len(startup_samples))
            elif calibrate:
                zero_samples.append(sample)
                if len(zero_samples) >= 32:
                    motion.calibrate(tuple(statistics.median(v) for v in zip(*zero_samples)))
                    calibrate = False
                    zero_samples.clear()
                    log(calibrated_origin=motion.origin)
                delta = (0, 0)
            else:
                delta = motion.step(sample, now - previous, buttons)
            if pointer:
                pointer.emit(*delta, buttons)
            os.write(heartbeat, b'.')
            row = {'t': round(now - started, 4), 'raw': sample,
                   'origin': motion.origin[:], 'force': motion.filtered[:],
                   'delta': delta, 'buttons': buttons, 'packet': last_packet,
                   'packets': wire.packets}
            if recording:
                recording.write(json.dumps(row) + '\n')
            if now >= next_log and (observe or record):
                log(**row)
                next_log = now + 1
            previous = now
        return 0
    finally:
        if pointer:
            pointer.close()
        if recording:
            recording.close()
        # The independent supervisor resets the controller and reattaches
        # psmouse. Avoid a second, redundant protocol transaction here: on
        # this controller F5 at the end of remote polling can time out.
        wire.close()


def run(settings=Settings(), duration=60, observe=True, record=None):
    """Independent parent restores psmouse after worker exit/crash/timeout."""
    settings.validate()
    with Lease() as lease:
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                result = worker(lease.node, settings, duration, observe, write_fd, record)
            except BaseException as error:
                log(error=f'{type(error).__name__}: {error}')
                result = 1
            os._exit(result)
        os.close(write_fd)
        (RUNTIME / 'worker.pid').write_text(str(child))
        stopping = False
        def request_stop(*_):
            nonlocal stopping
            stopping = True
        old_int = signal.signal(signal.SIGINT, request_stop)
        old_term = signal.signal(signal.SIGTERM, request_stop)
        last_heartbeat = time.monotonic()
        status = 1 << 8
        try:
            while True:
                done, code = os.waitpid(child, os.WNOHANG)
                if done:
                    status = code
                    break
                if stopping or time.monotonic() - last_heartbeat > 5:
                    os.kill(child, signal.SIGTERM)
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline:
                        done, code = os.waitpid(child, os.WNOHANG)
                        if done:
                            status = code
                            break
                        time.sleep(.02)
                    if not done:
                        os.kill(child, signal.SIGKILL)
                        _, status = os.waitpid(child, 0)
                    break
                if select.select([read_fd], [], [], .5)[0]:
                    if os.read(read_fd, 1024):
                        last_heartbeat = time.monotonic()
        finally:
            os.close(read_fd)
            (RUNTIME / 'worker.pid').unlink(missing_ok=True)
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
            try:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
    log(state='restored', driver='psmouse', worker_status=os.waitstatus_to_exitcode(status))
    return max(0, os.waitstatus_to_exitcode(status)) if os.WIFEXITED(status) else 1
