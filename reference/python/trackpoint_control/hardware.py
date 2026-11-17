"""Exclusive, reversible ownership of one already-identified TrackPoint port."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

RUNTIME = Path('/run/trackpoint-control')
ATTRS = ('sensitivity', 'press_to_select', 'rate', 'resolution', 'bind_mode')


def ports():
    found = []
    for port in Path('/sys/bus/serio/devices').glob('serio*'):
        names = [p.read_text().strip() for p in port.glob('input/input*/name')]
        if 'TPPS/2 Elan TrackPoint' in names:
            found.append(port)
    return found


def restore(state):
    name = state['port']
    if not re.fullmatch(r'serio[0-9]+', name):
        raise ValueError('Invalid saved port')
    port = Path('/sys/bus/serio/devices') / name
    if not port.exists():
        return  # unplugged; normal kernel discovery will handle reattachment
    # Never restore to a different port after a USB/PS2 topology change.
    if str(port.resolve()) != state['physical_path']:
        raise RuntimeError('Saved TrackPoint port no longer has the same physical path')
    # psmouse probing alone does not reliably clear Elan diagnostic mode.
    # A killed worker may have left six-byte packets enabled or an unfinished
    # command. Explicitly reset while we still own the raw transport.
    from .wire import Wire
    (port / 'drvctl').write_text('serio_raw')
    nodes = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        nodes = list(port.glob('misc/serio_raw*'))
        if len(nodes) == 1 and (Path('/dev') / nodes[0].name).exists():
            break
        time.sleep(.02)
    reset_error = None
    try:
        if len(nodes) != 1 or not (Path('/dev') / nodes[0].name).exists():
            raise OSError('Cannot find raw port for recovery reset')
        wire = Wire(str(Path('/dev', nodes[0].name)))
        try:
            wire.force_reset()
        finally:
            wire.close()
    except OSError as error:
        reset_error = error
    (port / 'bind_mode').write_text('auto')
    (port / 'drvctl').write_text('rescan')
    if (port / 'driver').resolve().name != 'psmouse':
        raise RuntimeError('Stock psmouse driver did not reattach')
    for name, value in state['settings'].items():
        if name not in ATTRS:
            raise ValueError('Invalid saved attribute')
        path = port / name
        if path.exists():
            path.write_text(value)
    if reset_error:
        raise RuntimeError(f'Stock driver rebound, but explicit reset failed: {reset_error}')


def recover():
    if not RUNTIME.exists():
        return
    fd = os.open(RUNTIME / 'lock', os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        # The worker also inherits this lock. A stale PID file is harmless;
        # a live process must release ownership before recovery can proceed.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = RUNTIME / 'lease.json'
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
                raise RuntimeError('Recovery state is not a root-owned regular file')
            state = json.loads(path.read_text())
            restore(state)
            path.unlink()
        (RUNTIME / 'worker.pid').unlink(missing_ok=True)
    finally:
        os.close(fd)


class Lease:
    def __enter__(self):
        if os.geteuid() != 0:
            raise PermissionError('Hardware access requires root')
        RUNTIME.mkdir(mode=0o755, exist_ok=True)
        if RUNTIME.is_symlink() or RUNTIME.stat().st_uid != 0:
            raise RuntimeError('Unsafe runtime directory')
        self.lock = os.open(RUNTIME / 'lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        found = ports()
        if len(found) != 1:
            raise RuntimeError(f'Expected one Elan PS/2 TrackPoint, found {len(found)}')
        self.port = found[0]
        if (self.port / 'driver').resolve().name != 'psmouse':
            raise RuntimeError('TrackPoint must initially use the stock psmouse driver')
        self.state = {
            'port': self.port.name, 'physical_path': str(self.port.resolve()),
            'settings': {a: (self.port / a).read_text().strip()
                         for a in ATTRS if (self.port / a).exists()},
        }
        path = RUNTIME / 'lease.json'
        fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(self.state, f)
            f.flush()
            os.fsync(f.fileno())
        try:
            subprocess.run(['/usr/sbin/modprobe', 'serio_raw'], check=True, timeout=5)
            (self.port / 'drvctl').write_text('serio_raw')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                nodes = list(self.port.glob('misc/serio_raw*'))
                if len(nodes) == 1:
                    self.node = Path('/dev') / nodes[0].name
                    if self.node.exists() and stat.S_ISCHR(self.node.stat().st_mode):
                        return self
                time.sleep(.02)
            raise RuntimeError('Raw TrackPoint node did not appear')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        try:
            restore(self.state)
            (RUNTIME / 'lease.json').unlink(missing_ok=True)
        finally:
            os.close(self.lock)
