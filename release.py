#!/usr/bin/env python3
"""Build a shareable source archive without firmware or private diagnostics."""
import gzip
import hashlib
from pathlib import Path
import tarfile
from trackpoint_control import __version__

root = Path(__file__).resolve().parent
name = f'trackpoint-control-{__version__}'
paths = [root / p for p in (
    'README.md', 'LICENSE', '.gitignore', 'pyproject.toml', 'config.example.toml',
    'install.py', 'release.py', 'research/NOTES.md', 'research/MAME-LICENSE',
    'research/edspdasm.cpp', 'research/edspdasm.h', 'research/emu.h',
    'research/disassemble.cpp',
)]
for directory, suffix in (('trackpoint_control', '*.py'), ('tests', '*.py'),
                          ('packaging', '*.service'), ('research', '*.py')):
    paths.extend(sorted((root / directory).glob(suffix)))
out = root / 'dist'
out.mkdir(exist_ok=True)
archive = out / f'{name}.tar.gz'
with archive.open('wb') as f:
    with gzip.GzipFile(fileobj=f, filename='', mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as tar:
            for path in sorted(set(paths)):
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError(f'Expected source file: {path}')
                info = tar.gettarinfo(str(path), f'{name}/{path.relative_to(root)}')
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 0
                info.mode = 0o644
                with path.open('rb') as source:
                    tar.addfile(info, source)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest}  {archive.name}\n')
print(archive)
print(f'SHA-256: {digest}')
