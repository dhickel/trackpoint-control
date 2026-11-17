#!/usr/bin/env python3
"""Compatibility entry point; the Rust installer itself does not require Python."""
import os
from pathlib import Path
import sys

os.execvp('bash', ['bash', str(Path(__file__).resolve().with_name('install.sh')), *sys.argv[1:]])
