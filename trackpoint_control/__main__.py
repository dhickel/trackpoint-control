import argparse
import json
import os
import signal
import sys
import tomllib
from .hardware import RUNTIME, ports, recover
from .motion import Settings


def main():
    parser = argparse.ArgumentParser(description='Experimental Elan raw-sensor TrackPoint control')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    sub.add_parser('restore')
    sub.add_parser('calibrate', help='Release the stick, then collect a new zero for about 1.5 seconds')
    trial = sub.add_parser('trial')
    trial.add_argument('--seconds', type=int, default=60)
    trial.add_argument('--config')
    trial.add_argument('--record')
    trial.add_argument('--enable-pointer', action='store_true', help='Emit pointer movement; default is observation only')
    service = sub.add_parser('run', help='Run continuously; keep hands off the stick for the first two seconds')
    service.add_argument('--config')
    service.add_argument('--record')
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps({'stock_ports': [str(p) for p in ports()],
                          'lease_present': (RUNTIME / 'lease.json').exists()}, indent=2))
        return 0
    if os.geteuid() != 0:
        parser.error('This operation needs root; use sudo')
    if args.action == 'restore':
        recover()
        return 0
    if args.action == 'calibrate':
        pid = int((RUNTIME / 'worker.pid').read_text())
        os.kill(pid, signal.SIGUSR1)
        return 0
    if args.action == 'trial' and not 1 <= args.seconds <= 3600:
        parser.error('Trial duration must be 1..3600 seconds')
    settings = Settings()
    if args.config:
        with open(args.config, 'rb') as f:
            settings = Settings(**tomllib.load(f).get('motion', {}))
    from .driver import run
    return run(settings, args.seconds if args.action == 'trial' else 0,
               not args.enable_pointer if args.action == 'trial' else False, args.record)


if __name__ == '__main__':
    sys.exit(main())
