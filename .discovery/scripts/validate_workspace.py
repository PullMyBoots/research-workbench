#!/usr/bin/env python3
"""Run the local Discovery workspace validator."""

from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    # `discovery` is the stable public launcher.  Older layouts exposed a
    # removed `.discovery/bin/discovery` helper, which made this validator fail
    # before it could report actual workspace integrity.
    subprocess.run([str(root / "discovery"), "doctor"], cwd=root, check=True)


if __name__ == "__main__":
    main()
