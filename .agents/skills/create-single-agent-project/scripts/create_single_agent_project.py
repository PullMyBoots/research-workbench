#!/usr/bin/env python3
"""Create one blank single-Agent research project from the repository template."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


PROJECT_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\Z")
BLANK_GOAL = "尚未设定。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-root", required=True, type=Path)
    parser.add_argument("--id", required=True, dest="project_id")
    parser.add_argument("--goal", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    topic = args.topic_root.resolve()
    project_id = args.project_id.strip()
    goal = " ".join(args.goal.split())
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise SystemExit("project id must use lowercase letters, digits, and internal hyphens")
    if not goal:
        raise SystemExit("project goal must not be empty")
    if not (topic / ".DiscoveryProgram").is_dir():
        raise SystemExit(f"Topic root is missing .DiscoveryProgram: {topic}")

    projects = topic / "subprojects-single"
    template = projects / ".single-agent-template"
    target = projects / project_id
    if not template.is_dir():
        raise SystemExit(f"single-Agent project template not found: {template}")
    if target.exists() or target.is_symlink():
        raise SystemExit(f"single-Agent project path already exists: {target}")

    shutil.copytree(template, target, symlinks=False)
    try:
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        memory = target / ".ResearchProject" / "memory" / "main.md"
        text = memory.read_text(encoding="utf-8")
        if text.count(BLANK_GOAL) != 1:
            raise ValueError("single-Agent project template has an invalid goal placeholder")
        memory.write_text(text.replace(BLANK_GOAL, f"项目：`{project_id}`\n\n目标：{goal}", 1), encoding="utf-8")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        shutil.rmtree(target)
        raise SystemExit(str(exc)) from None
    print(json.dumps({"id": project_id, "path": str(target), "goal": goal}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
