#!/usr/bin/env python3
"""Replace with a Problem-specific, cheap Candidate interface check."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    candidate = Path(args.candidate)
    if not candidate.exists():
        raise SystemExit("Candidate does not exist")
    raise SystemExit(
        "Problem Candidate Check is not configured; Human/Main Agent must implement the public interface check"
    )


if __name__ == "__main__":
    main()
