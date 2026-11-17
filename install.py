#!/usr/bin/env python3
"""Install/remove this userspace driver without changing kernel or firmware.

Installation does not start the service or enable it at boot.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
DEST = Path('/usr/local/lib/trackpoint-control')
WRAPPER = Path('/usr/local/bin/trackpoint-control')
UNIT = Path('/etc/systemd/system/trackpoint-control.service')
RECOVERY_UNIT = Path('/etc/systemd/system/trackpoint-control-recover.service')
CONFIG = Path('/etc/trackpoint-control.toml')
MARKER = '.trackpoint-control-install'


def systemctl(*args):
    return subprocess.run(['systemctl', *args], check=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--uninstall', action='store_true')
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error('Use sudo')
    if DEST.is_symlink() or (DEST.exists() and not (DEST / MARKER).is_file()):
        p.error(f'Refusing to replace an unrecognized installation at {DEST}')
    if args.uninstall:
        if not (DEST / MARKER).is_file():
            p.error('No installation owned by this installer was found')
        systemctl('disable', '--now', 'trackpoint-control.service')
        subprocess.run([str(WRAPPER), 'restore'], check=True)
        WRAPPER.unlink(missing_ok=True)
        UNIT.unlink(missing_ok=True)
        RECOVERY_UNIT.unlink(missing_ok=True)
        shutil.rmtree(DEST)
        systemctl('daemon-reload')
        print(f'Uninstalled. Kept your settings in {CONFIG}.')
        return
    if (DEST / MARKER).exists():
        systemctl('stop', 'trackpoint-control.service')
        subprocess.run([str(WRAPPER), 'restore'], check=True)
    elif WRAPPER.exists() or UNIT.exists() or RECOVERY_UNIT.exists():
        p.error('Refusing to replace an unrelated command or systemd unit')
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE / 'trackpoint_control', DEST / 'trackpoint_control',
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (DEST / MARKER).write_text('trackpoint-control 0.1.0\n')
    WRAPPER.write_text('#!/usr/bin/python3\nimport sys\nsys.path.insert(0, "/usr/local/lib/trackpoint-control")\nfrom trackpoint_control.__main__ import main\nsys.exit(main())\n')
    WRAPPER.chmod(0o755)
    shutil.copyfile(SOURCE / 'packaging/trackpoint-control.service', UNIT)
    shutil.copyfile(SOURCE / 'packaging/trackpoint-control-recover.service', RECOVERY_UNIT)
    if not CONFIG.exists():
        shutil.copyfile(SOURCE / 'config.example.toml', CONFIG)
    systemctl('daemon-reload')
    print('Installed; service is stopped and not newly enabled at boot.')
    print('Trial: sudo trackpoint-control trial --seconds 120 --enable-pointer --config /etc/trackpoint-control.toml')
    print('Start: sudo systemctl start trackpoint-control')
    print('Stop:  sudo systemctl stop trackpoint-control')


if __name__ == '__main__':
    main()
