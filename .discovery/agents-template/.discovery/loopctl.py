#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_STATE = {
    "phase": "work_loop",
    "last_version": None,
    "last_reflected_version": None,
    "eval_status": None,
    "active_eval": None,
    "last_error": None,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status",))
    args = parser.parse_args()

    path = Path(__file__).resolve().parent / "loop_state.json"
    state = dict(DEFAULT_STATE)
    if path.exists():
        state.update(json.loads(path.read_text(encoding="utf-8")))

    print(json.dumps(state, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
