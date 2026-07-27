#!/usr/bin/env python3
"""Validate and replace a configured relay repository with one task snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


SKILL_NAME = "chatgpt-handoff"
MAX_FILE_BYTES = 95 * 1024 * 1024
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


class HandoffError(RuntimeError):
    pass


def default_config_path() -> Path:
    override = os.environ.get("HANDOFF_CONFIG")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base.expanduser() / SKILL_NAME / "config.json"


def run_git(args: list[str], cwd: Optional[Path] = None, capture: bool = False) -> str:
    command = ["git", *args]
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise HandoffError("Git is not installed or not available in PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"Git command failed: {' '.join(command)}"
        raise HandoffError(f"{message}\n{detail}" if detail else message) from exc
    return result.stdout.strip() if capture else ""


def validate_branch(branch: str) -> None:
    if not branch:
        raise HandoffError("branch must not be empty")
    run_git(["check-ref-format", f"refs/heads/{branch}"], capture=True)


def validate_remote_url(remote_url: str) -> None:
    if not remote_url or "\n" in remote_url or "\r" in remote_url:
        raise HandoffError("remote_url is invalid")
    if remote_url.startswith(("http://", "https://")):
        parsed = urlsplit(remote_url)
        if parsed.username or parsed.password:
            raise HandoffError("do not store credentials inside remote_url")


def derive_web_url(remote_url: str) -> Optional[str]:
    scp_match = re.fullmatch(r"git@github\.com:(.+?)(?:\.git)?", remote_url)
    if scp_match:
        return f"https://github.com/{scp_match.group(1)}"

    parsed = urlsplit(remote_url)
    if parsed.hostname != "github.com":
        return None
    path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"https://github.com/{path}" if path else None


def read_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffError(f"cannot read configuration: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise HandoffError(f"configuration must be a JSON object: {path}")
    result: dict[str, str] = {}
    for key in ("remote_url", "web_url", "branch"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise HandoffError(f"configuration field must be a string: {key}")
        if value:
            result[key] = value
    return result


def write_config(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except OSError as exc:
        raise HandoffError(f"cannot write configuration: {path}: {exc}") from exc


def configure(args: argparse.Namespace) -> None:
    remote_url = args.remote_url
    validate_remote_url(remote_url)
    validate_branch(args.branch)
    web_url = args.web_url or derive_web_url(remote_url)
    data = {"remote_url": remote_url, "branch": args.branch}
    if web_url:
        data["web_url"] = web_url
    write_config(args.config, data)
    print(f"Configuration saved: {args.config}")
    print(f"Remote: {remote_url}")
    if web_url:
        print(f"Web: {web_url}")
    print(f"Branch: {args.branch}")


def resolve_publish_config(
    args: argparse.Namespace,
) -> tuple[Optional[str], Optional[str], str]:
    stored = read_config(args.config)
    remote_url = (
        args.remote_url
        or os.environ.get("HANDOFF_REMOTE_URL")
        or stored.get("remote_url")
    )
    web_url = (
        args.web_url
        or os.environ.get("HANDOFF_WEB_URL")
        or stored.get("web_url")
    )
    branch = (
        args.branch
        or os.environ.get("HANDOFF_BRANCH")
        or stored.get("branch")
        or "main"
    )
    if remote_url:
        validate_remote_url(remote_url)
        web_url = web_url or derive_web_url(remote_url)
    validate_branch(branch)
    return remote_url, web_url, branch


def validate_package(package_arg: str) -> Path:
    unresolved = Path(package_arg).expanduser()
    if unresolved.is_symlink():
        raise HandoffError("task package must not be a symbolic link")
    try:
        package = unresolved.resolve(strict=True)
    except OSError as exc:
        raise HandoffError(f"task package does not exist: {unresolved}") from exc
    if not package.is_dir():
        raise HandoffError(f"task package is not a directory: {package}")

    readme = package / "README.md"
    workspace = package / "workspace"
    if not readme.is_file() or readme.stat().st_size == 0:
        raise HandoffError("README.md is missing or empty")
    if not workspace.is_dir():
        raise HandoffError("workspace/ is missing")

    unexpected = sorted(
        entry.name for entry in package.iterdir()
        if entry.name not in {"README.md", "workspace"}
    )
    if unexpected:
        raise HandoffError(f"unexpected top-level entry: {unexpected[0]}")

    for path in package.rglob("*"):
        if path.is_symlink():
            raise HandoffError(f"symbolic links are not allowed: {path}")
        if path.name == ".git":
            raise HandoffError(f"nested Git metadata is not allowed: {path}")
        if not path.is_file():
            continue
        lowered = path.name.lower()
        if lowered in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            raise HandoffError(f"possible credential file is not allowed: {path}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise HandoffError(f"file exceeds the safe GitHub size limit: {path}")
    return package


def make_snapshot(package: Path, repository: Path) -> tuple[str, list[str]]:
    shutil.copytree(package, repository, copy_function=shutil.copy2)
    workspace = repository / "workspace"
    if not any(workspace.iterdir()):
        (workspace / ".gitkeep").touch()

    run_git(["init", "-q"], cwd=repository)
    run_git(["add", "--all"], cwd=repository)
    run_git(
        [
            "-c",
            "user.name=Codex Handoff",
            "-c",
            "user.email=codex-handoff@local.invalid",
            "commit",
            "-q",
            "-m",
            "Publish ChatGPT handoff snapshot",
        ],
        cwd=repository,
    )
    commit = run_git(["rev-parse", "HEAD"], cwd=repository, capture=True)
    raw_files = run_git(["ls-files", "-z"], cwd=repository, capture=True)
    files = [item for item in raw_files.split("\0") if item]
    return commit, files


def publish(args: argparse.Namespace) -> None:
    package = validate_package(args.task_package)
    remote_url, web_url, branch = resolve_publish_config(args)
    if not args.dry_run and not remote_url:
        raise HandoffError(
            "relay repository is not configured; run the configure command first"
        )

    with tempfile.TemporaryDirectory(prefix="chatgpt-handoff-") as temporary:
        repository = Path(temporary) / "repo"
        commit, files = make_snapshot(package, repository)
        if args.dry_run:
            print("Validation passed.")
            print(f"Snapshot commit: {commit}")
            print(f"Tracked files: {len(files)}")
            for path in files:
                print(path)
            return

        assert remote_url is not None
        run_git(
            ["push", "--force", remote_url, f"HEAD:refs/heads/{branch}"],
            cwd=repository,
        )
        print(f"Published: {web_url or remote_url}")
        print(f"Branch: {branch}")
        print(f"Snapshot commit: {commit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish an isolated task snapshot to a configured relay repository."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    configure_parser = commands.add_parser(
        "configure", help="save relay repository settings outside the Skill"
    )
    configure_parser.add_argument("--remote-url", required=True)
    configure_parser.add_argument("--web-url")
    configure_parser.add_argument("--branch", default="main")
    configure_parser.add_argument(
        "--config", type=Path, default=default_config_path()
    )
    configure_parser.set_defaults(handler=configure)

    publish_parser = commands.add_parser(
        "publish", help="validate and publish one task package"
    )
    publish_parser.add_argument("task_package")
    publish_parser.add_argument("--dry-run", action="store_true")
    publish_parser.add_argument("--remote-url")
    publish_parser.add_argument("--web-url")
    publish_parser.add_argument("--branch")
    publish_parser.add_argument(
        "--config", type=Path, default=default_config_path()
    )
    publish_parser.set_defaults(handler=publish)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.config = args.config.expanduser()
    try:
        args.handler(args)
    except HandoffError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
