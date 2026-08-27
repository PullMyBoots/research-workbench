#!/usr/bin/env python3
"""Discovery CLI for the simplified human-in-the-loop workspace."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import hmac
import http.server
import io
import json
import math
import os
import pwd
import re
import secrets
import selectors
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import tomllib
import traceback
import urllib.parse
import webbrowser
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Tests import this file by path, so its sibling query module is not otherwise
# guaranteed to be on sys.path.
_CLI_DIR = Path(__file__).resolve().parent
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))
import knowledge_query


ROOT_MARKER = ".DiscoveryConsole"
TOPIC_MARKER = ".DiscoveryProgram"
TEAM_SUBPROJECTS_DIR = "subprojects-team"
AGENT_NAME_RE = re.compile(r"agent[A-Za-z0-9_-]*\Z")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")
VERSION_RE = re.compile(r"version-[A-Za-z0-9_.-]+\Z")
ROUTE_CLI_PROTOCOL = 9
ROUTE_CLI_PROTOCOL_MARKER = f"explore-cli-protocol: {ROUTE_CLI_PROTOCOL}"
DEFAULT_ATTACHED_WAIT_TIMEOUT_SECONDS = 1500.0
# The Route client needs a little margin beyond the Broker's configured wait
# window to receive the terminal/handoff payload.  Codex itself is configured
# with a matching 30-minute background-terminal cap in the Route template.
ROUTE_CLIENT_WAIT_TIMEOUT_SECONDS = 1805.0
# Querying the full NVIDIA telemetry set can take a few seconds on an idle or
# recently initialized driver.  Do not treat that as an absent GPU.
NVIDIA_SMI_TIMEOUT_SECONDS = 10.0
NVIDIA_SMI_ATTEMPTS = 2
NVIDIA_SMI_RETRY_DELAY_SECONDS = 0.5
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Discovery",
    "GIT_AUTHOR_EMAIL": "discovery@example.local",
    "GIT_COMMITTER_NAME": "Discovery",
    "GIT_COMMITTER_EMAIL": "discovery@example.local",
}


class EvalCommandFailed(Exception):
    def __init__(self, returncode: int, log: str) -> None:
        self.returncode = returncode
        self.log = log
        super().__init__(f"eval command failed with return code {returncode}; see {log}")


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discovery",
        description="Discovery Human/Main control CLI.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="{start,doctor,maintain,knowledge}")

    start = sub.add_parser("start", help="start the Dashboard and background workers")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8765)
    start.add_argument("--no-browser", action="store_true")
    start.add_argument("--problem", default="", help="initial Problem shown by the Dashboard")

    doctor = sub.add_parser("doctor", help="diagnose workspace, evaluator, knowledge, and worker integrity")
    doctor.add_argument("--problem", default="", help="limit diagnosis to one Problem")

    maintain = sub.add_parser("maintain", help="maintain Main-owned knowledge and Problem Notices")
    maintain_sub = maintain.add_subparsers(dest="maintain_entity", required=True)

    maintain_item = maintain_sub.add_parser("item", help="add or delete one external Item")
    maintain_item_sub = maintain_item.add_subparsers(dest="maintain_action", required=True)
    maintain_item_add = maintain_item_sub.add_parser("add")
    maintain_item_add.add_argument("--scope", choices=("topic", "problem"), required=True)
    maintain_item_add.add_argument("--problem", default="")
    maintain_item_add.add_argument("--id", required=True)
    maintain_item_add.add_argument("--source", required=True)
    maintain_item_add.add_argument("--metadata", required=True)
    maintain_item_delete = maintain_item_sub.add_parser("delete")
    maintain_item_delete.add_argument("--scope", choices=("topic", "problem"), required=True)
    maintain_item_delete.add_argument("--problem", default="")
    maintain_item_delete.add_argument("--id", required=True)

    maintain_memory = maintain_sub.add_parser("memory", help="add one immutable Topic Memory Log")
    maintain_memory_sub = maintain_memory.add_subparsers(dest="maintain_action", required=True)
    maintain_memory_add = maintain_memory_sub.add_parser("add")
    maintain_memory_add.add_argument("--id", required=True)
    maintain_memory_add.add_argument("--file", required=True)

    maintain_notice = maintain_sub.add_parser("notice", help="add or delete one version-anchored Problem Notice")
    maintain_notice_sub = maintain_notice.add_subparsers(dest="maintain_action", required=True)
    maintain_notice_add = maintain_notice_sub.add_parser("add")
    maintain_notice_add.add_argument("--problem", required=True)
    maintain_notice_add.add_argument("--id", required=True)
    maintain_notice_add.add_argument("--file", required=True)
    maintain_notice_delete = maintain_notice_sub.add_parser("delete")
    maintain_notice_delete.add_argument("--problem", required=True)
    maintain_notice_delete.add_argument("--id", required=True)

    maintain_check = maintain_sub.add_parser("check", help="validate Main-owned knowledge")
    maintain_check.add_argument("--problem", default="")

    knowledge = sub.add_parser("knowledge", help="browse one Topic or Problem knowledge scope")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_action", required=True)
    browse = knowledge_sub.add_parser("browse", help="return compact read-only knowledge cards as JSON")
    browse.add_argument("--scope", choices=("topic", "problem"), required=True)
    browse.add_argument("--problem", default="")
    browse.add_argument("--view", choices=("external", "practice"), required=True)
    browse.add_argument("--query", default="")
    browse.add_argument("--metric", default="")
    browse.add_argument("--sort", default="")
    browse.add_argument("--route", default="")
    browse.add_argument("--limit", type=int, default=20)
    show = knowledge_sub.add_parser("show", help="show one canonical knowledge reference as JSON")
    show.add_argument("ref")

    return parser


def dispatch_private_cli(argv: list[str]) -> bool:
    if not argv or argv[0] not in {"_control", "_worker", "_supervise", "_supervise_direct", "_headless_goal", "_headless_campaign"}:
        return False
    cwd = Path.cwd()
    topic = find_topic_root(cwd)
    workspace = find_workspace(cwd, required=False)
    command = argv[0]
    if command == "_control":
        cmd_main(topic, workspace, cwd, argv[1:])
    elif command == "_worker":
        parser = argparse.ArgumentParser(prog="discovery _worker")
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=5.0)
        parser.add_argument("--problem", default="")
        args = parser.parse_args(argv[1:])
        workspace = resolve_problem_workspace(topic, workspace, str(args.problem or ""))
        cmd_worker(workspace, args)
    elif command == "_supervise":
        parser = argparse.ArgumentParser(prog="discovery _supervise")
        parser.add_argument("job_id")
        args = parser.parse_args(argv[1:])
        workspace = resolve_problem_workspace(topic, workspace, "")
        cmd_supervise(workspace, args.job_id)
    elif command == "_supervise_direct":
        parser = argparse.ArgumentParser(prog="discovery _supervise_direct")
        parser.add_argument("job_id")
        args = parser.parse_args(argv[1:])
        workspace = resolve_problem_workspace(topic, workspace, "")
        cmd_supervise_direct(workspace, args.job_id)
    elif command == "_headless_goal":
        parser = argparse.ArgumentParser(prog="discovery _headless_goal")
        parser.add_argument("run_id")
        args = parser.parse_args(argv[1:])
        workspace = resolve_problem_workspace(topic, workspace, "")
        cmd_headless_goal(workspace, args.run_id)
    else:
        parser = argparse.ArgumentParser(prog="discovery _headless_campaign")
        parser.add_argument("campaign_id")
        parser.add_argument("--poll-seconds", type=float, default=2.0)
        args = parser.parse_args(argv[1:])
        workspace = resolve_problem_workspace(topic, workspace, "")
        cmd_headless_campaign(workspace, args.campaign_id, poll_seconds=max(0.2, float(args.poll_seconds)))
    return True


def route_broker_endpoint_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "route_broker.json"


def route_broker_token_path(agent_dir: Path) -> Path:
    return agent_dir / ".discovery" / "broker_token"


def route_broker_server_token_path(agent_dir: Path) -> Path:
    return private(agent_dir.parent) / "route_broker_tokens" / f"{safe_id(agent_dir.name, 'Route id')}.token"


def route_broker_file_root(agent_dir: Path) -> Path:
    return agent_dir / ".tmp" / "route_broker"


def route_broker_is_available(workspace: Path, *, expected_pid: int | None = None) -> bool:
    endpoint = read_json(route_broker_endpoint_path(workspace), {})
    if not isinstance(endpoint, dict):
        return False
    pid = endpoint.get("pid")
    if not isinstance(pid, int) or not process_alive(pid):
        return False
    if expected_pid is not None and pid != expected_pid:
        return False
    if endpoint.get("transport") != "file":
        return False
    routes = [path for path in workspace.iterdir() if path.is_dir() and AGENT_NAME_RE.fullmatch(path.name)]
    return all(
        (route_broker_file_root(route) / "requests").is_dir()
        and (route_broker_file_root(route) / "responses").is_dir()
        for route in routes
    )


def ensure_route_broker_token(agent_dir: Path) -> str:
    client_path = route_broker_token_path(agent_dir)
    server_path = route_broker_server_token_path(agent_dir)
    client_path.parent.mkdir(parents=True, exist_ok=True)
    server_path.parent.mkdir(parents=True, exist_ok=True)
    if client_path.is_symlink() or server_path.is_symlink():
        raise SystemExit("Route broker token paths must not be symlinks")
    token = server_path.read_text(encoding="utf-8").strip() if server_path.is_file() else ""
    if len(token) < 32 and client_path.is_file():
        token = client_path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
    server_path.write_text(token + "\n", encoding="utf-8")
    server_path.chmod(0o600)
    if not client_path.is_file() or client_path.read_text(encoding="utf-8").strip() != token:
        client_path.write_text(token + "\n", encoding="utf-8")
    client_path.chmod(0o600)
    git_exclude = agent_dir / ".git" / "info" / "exclude"
    if git_exclude.parent.is_dir():
        existing = git_exclude.read_text(encoding="utf-8", errors="ignore") if git_exclude.exists() else ""
        rule = ".discovery/broker_token"
        if rule not in existing.splitlines():
            git_exclude.write_text(existing.rstrip() + "\n" + rule + "\n", encoding="utf-8")
    return token


def main() -> None:
    # A Route may end its Codex Turn while a long Job continues.  A closed
    # client socket must never terminate the shared Runtime/Broker process.
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    if dispatch_private_cli(sys.argv[1:]):
        return
    parser = build_cli_parser()
    cwd = Path.cwd()
    topic = find_topic_root(cwd)
    workspace = find_workspace(cwd, required=False)
    if workspace is not None and in_agent_workspace(workspace, cwd) is not None:
        raise SystemExit("`./discovery` is a Human/Main control CLI; use `./explore` from the Route workspace")
    args = parser.parse_args()

    if args.cmd == "start":
        cmd_start(topic, workspace, cwd, args)
        return
    if args.cmd == "doctor":
        cmd_doctor(topic, workspace, cwd, args)
        return
    if args.cmd == "maintain":
        cmd_maintain(topic, workspace, cwd, args)
        return
    if args.cmd == "knowledge":
        cmd_knowledge(topic, workspace, cwd, args)
        return


def query_contract(workspace: Path) -> dict[str, Any]:
    """Return only public contract data needed by the read-only query kernel."""
    contract = read_json(evaluation_contract_path(workspace), {})
    if not isinstance(contract, dict):
        return {}
    contract = dict(contract)
    if contract:
        contract["contract_digest"] = evaluation_contract_digest(contract)
    return contract


def query_scope(topic: Path, current_workspace: Path | None, scope: str, problem_id: str) -> tuple[Path, str, str, dict[str, Any], list[dict[str, Any]]]:
    if scope == "topic":
        if problem_id:
            raise SystemExit("--problem is valid only with --scope problem")
        scope_id = str(read_problem_registry(topic).get("topic_id") or topic.name)
        return program_root(topic) / "knowledge", "topic", scope_id, {}, []
    if not problem_id:
        raise SystemExit("--scope problem requires --problem <problem-id>")
    workspace = resolve_problem_workspace(topic, current_workspace, problem_id)
    baseline_rows, _ = load_dashboard_baseline_rows(workspace)
    return knowledge_root(workspace), "problem", current_problem_id(workspace), query_contract(workspace), baseline_rows


def validate_knowledge_browse(scope_kind: str, view: str, metric: str, sort: str, route: str, contract: dict[str, Any]) -> None:
    if scope_kind == "topic":
        if metric or route:
            raise SystemExit("--metric and --route are valid only for Problem practice knowledge")
        allowed = {"", "cited", "id"}
    elif view == "external":
        if metric or route:
            raise SystemExit("--metric and --route are valid only for Problem practice knowledge")
        allowed = {"", "cited", "id"}
    else:
        allowed = {"", "best", "gain", "cited", "latest"}
        if sort in {"best", "gain"} and not metric:
            raise SystemExit(f"--sort {sort} requires --metric")
        if metric and metric not in (contract.get("metrics") if isinstance(contract.get("metrics"), dict) else {}):
            raise SystemExit(f"unknown Problem metric: {metric}")
    if sort not in allowed:
        raise SystemExit(f"invalid --sort {sort!r} for this knowledge view")


def cmd_knowledge(topic: Path, current_workspace: Path | None, cwd: Path, args: argparse.Namespace) -> None:
    require_main_workspace(current_workspace, cwd, "knowledge query") if current_workspace is not None else None
    if args.knowledge_action == "show":
        ref = str(args.ref)
        match = knowledge_query.REF_RE.fullmatch(ref.strip())
        if not match:
            raise SystemExit("ref must be a canonical @item/@topic/@memory/@baseline/@version reference")
        qualified_problem = str(match.group("problem") or "")
        if qualified_problem:
            if match.group("kind") == "memory":
                raise SystemExit("Topic Memory Logs cannot be Problem-qualified")
            root, scope_kind, scope_id, contract, baselines = query_scope(topic, current_workspace, "problem", qualified_problem)
            local_ref = f"@{match.group('kind')}:{match.group('id')}"
        else:
            # Unqualified Main references resolve only in Topic knowledge.
            root, scope_kind, scope_id, contract, baselines = query_scope(topic, current_workspace, "topic", "")
            local_ref = ref
        try:
            payload = knowledge_query.show(root=root, scope_kind=scope_kind, scope_id=scope_id, ref=local_ref, contract=contract, baseline_rows=baselines)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        if qualified_problem:
            payload["ref"] = ref
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    root, scope_kind, scope_id, contract, baselines = query_scope(topic, current_workspace, str(args.scope), str(args.problem or ""))
    validate_knowledge_browse(scope_kind, str(args.view), str(args.metric or ""), str(args.sort or ""), str(args.route or ""), contract)
    try:
        payload = knowledge_query.browse(root=root, scope_kind=scope_kind, scope_id=scope_id, view=str(args.view), query=str(args.query or ""), metric=str(args.metric or "") or None, sort=str(args.sort or "") or None, route=str(args.route or "") or None, limit=int(args.limit), contract=contract, baseline_rows=baselines)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_main_control_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discovery _control", description="Private deterministic Problem setup API")
    parser.add_argument("--problem", default="", help="Problem id for Problem-scoped operations")
    sub = parser.add_subparsers(dest="main_cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("status")

    problem = sub.add_parser("problem")
    problem_sub = problem.add_subparsers(dest="problem_cmd", required=True)
    create = problem_sub.add_parser("create")
    create.add_argument("problem_id")
    create.add_argument("--title", default="")
    create.add_argument("--status", default="scoping")
    problem_sub.add_parser("list")
    show = problem_sub.add_parser("show")
    show.add_argument("problem_id")
    select = problem_sub.add_parser("select")
    select.add_argument("problem_id")
    eval_status = problem_sub.add_parser("eval-status")
    eval_status.add_argument("problem_id")
    activate = problem_sub.add_parser("activate-eval")
    activate.add_argument("problem_id")

    route = sub.add_parser("route")
    route_sub = route.add_subparsers(dest="agent_cmd", required=True)
    route_create = route_sub.add_parser("create")
    route_create.add_argument("name")
    route_create.add_argument("--force", action="store_true")
    route_sub.add_parser("list")

    return parser


def cmd_main(topic: Path, current_workspace: Path | None, cwd: Path, argv: list[str]) -> None:
    if current_workspace is not None and in_agent_workspace(current_workspace, cwd) is not None:
        raise SystemExit("Main control API is unavailable inside a Route workspace")
    parser = build_main_control_parser()
    args = parser.parse_args(argv)
    if args.main_cmd == "problem":
        cmd_problem(topic, args)
        return
    requested = str(getattr(args, "problem", "") or "")
    if args.main_cmd == "validate" and not requested and current_workspace is None:
        cmd_validate_topic(topic)
        return
    if args.main_cmd == "status" and not requested and current_workspace is None:
        cmd_status_topic(topic)
        return
    workspace = resolve_problem_workspace(topic, current_workspace, requested)
    if args.main_cmd == "validate":
        cmd_validate(workspace)
    elif args.main_cmd == "status":
        cmd_status(workspace)
    elif args.main_cmd == "route":
        cmd_agent(workspace, args)


def find_topic_root(start: Path) -> Path:
    for path in (start.resolve(), *start.resolve().parents):
        if (path / TOPIC_MARKER).is_dir():
            return path
    for path in (start.resolve(), *start.resolve().parents):
        launcher = path / ".discovery" / "bin" / "discovery"
        if launcher.exists():
            resolved = launcher.resolve()
            if len(resolved.parents) >= 3:
                return resolved.parents[2]
    raise SystemExit(f"could not find Topic root containing {TOPIC_MARKER}")


def find_workspace(start: Path, *, required: bool = True) -> Path | None:
    for path in (start.resolve(), *start.resolve().parents):
        # A Problem is identified by both its console and explicit metadata.
        # This avoids treating a stale/legacy runtime-created console shell at
        # the Topic root as a real Problem.
        console_visible = (path / ROOT_MARKER).is_dir() or (path / ROOT_MARKER / "pub").is_dir()
        if console_visible and (path / "problem.json").is_file():
            return path
    if required:
        raise SystemExit(f"could not find Problem root containing {ROOT_MARKER}")
    return None


def program_root(topic: Path) -> Path:
    return topic / TOPIC_MARKER


def problem_registry_path(topic: Path) -> Path:
    return program_root(topic) / "problem_registry.json"


def read_problem_registry(topic: Path) -> dict[str, Any]:
    data = read_json(problem_registry_path(topic), {"schema_version": 1, "default_problem": None, "problems": []})
    if not isinstance(data, dict):
        raise SystemExit("Problem registry must be a JSON object")
    data.setdefault("schema_version", 1)
    data.setdefault("default_problem", None)
    data.setdefault("problems", [])
    return data


def registered_problems(topic: Path) -> list[dict[str, Any]]:
    rows = read_problem_registry(topic).get("problems", [])
    return [dict(row) for row in rows if isinstance(row, dict) and row.get("id")]


def problem_workspace(topic: Path, problem_id: str) -> Path:
    safe_id(problem_id, "problem id")
    expected = (topic / TEAM_SUBPROJECTS_DIR / problem_id).resolve()
    for row in registered_problems(topic):
        if str(row.get("id")) == problem_id:
            raw = Path(str(row.get("path") or ""))
            path = (raw if raw.is_absolute() else topic / raw).resolve()
            if path != expected:
                raise SystemExit(
                    f"Problem {problem_id} must use canonical workspace "
                    f"{TEAM_SUBPROJECTS_DIR}/{problem_id}; registry points to {row.get('path') or '<missing>'}"
                )
            return expected
    raise SystemExit(f"unknown Problem: {problem_id}")


def resolve_problem_workspace(topic: Path, current: Path | None, requested: str = "") -> Path:
    if requested:
        return problem_workspace(topic, requested)
    if current is not None:
        problem_id = current_problem_id(current)
        canonical = problem_workspace(topic, problem_id)
        if current.resolve() != canonical:
            raise SystemExit(
                f"Problem {problem_id} must run from canonical workspace "
                f"{TEAM_SUBPROJECTS_DIR}/{problem_id}"
            )
        return canonical
    registry = read_problem_registry(topic)
    default = str(registry.get("default_problem") or "")
    if default:
        return problem_workspace(topic, default)
    problems = registered_problems(topic)
    if len(problems) == 1:
        return problem_workspace(topic, str(problems[0]["id"]))
    raise SystemExit("select a Problem with --problem or run the command inside subprojects-team/<problem-id>")


def topic_root(workspace: Path) -> Path:
    return find_topic_root(workspace)


def current_problem_id(workspace: Path) -> str:
    metadata = read_json(workspace / "problem.json", {})
    value = str(metadata.get("problem_id") or metadata.get("id") or workspace.name)
    return safe_id(value, "problem id")


def problem_runtime_activity(workspace: Path) -> dict[str, Any]:
    worker = read_dashboard_worker_state(workspace)
    jobs = [refresh_job(job) for job in read_jsonl(job_index(workspace))]
    active_jobs = [str(job.get("id")) for job in jobs if job.get("status") in {"queued", "starting", "running"}]
    active_headless = [str(run.get("id")) for run in read_headless_runs(workspace) if run.get("status") in {"starting", "running", "paused"}]
    active_campaigns = [
        str(campaign.get("id"))
        for campaign in read_headless_campaigns(workspace)
        if campaign.get("status") in {"starting", "running", "paused"}
    ]
    return {
        "worker": worker.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES,
        "worker_status": worker.get("status"),
        "jobs": active_jobs,
        "headless": active_headless,
        "campaigns": active_campaigns,
    }


def assert_problem_runtime_exclusive(workspace: Path) -> None:
    try:
        topic = topic_root(workspace)
    except SystemExit:
        return
    if not problem_registry_path(topic).is_file():
        return
    conflicts: list[str] = []
    for row in registered_problems(topic):
        other = problem_workspace(topic, str(row["id"]))
        if other.resolve() == workspace.resolve():
            continue
        activity = problem_runtime_activity(other)
        if activity["worker"] or activity["jobs"] or activity["headless"] or activity["campaigns"]:
            conflicts.append(f"{row['id']}={json.dumps(activity, ensure_ascii=False, sort_keys=True)}")
    if conflicts:
        raise SystemExit("another Problem still has active runtime state; stop or drain it before starting this Problem:\n" + "\n".join(conflicts))


def cmd_start(topic: Path, current_workspace: Path | None, cwd: Path, args: argparse.Namespace) -> None:
    if current_workspace is not None and in_agent_workspace(current_workspace, cwd) is not None:
        raise SystemExit("start Discovery from the Topic or Problem control workspace, not from a Route")
    if str(args.host) not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Dashboard control APIs may bind only to loopback (127.0.0.1 or localhost)")
    problems = registered_problems(topic)
    if not problems:
        raise SystemExit("no registered Problem exists; Main Agent must create the first Problem before the Dashboard can start")
    requested = str(args.problem or "")
    workspace = resolve_problem_workspace(topic, current_workspace, requested)
    structural_errors: list[str] = []
    topic_missing = [rel(topic, path) for path in topic_required_paths(topic) if not path.exists()]
    if topic_missing:
        structural_errors.append(f"topic: missing {', '.join(topic_missing)}")
    topic_knowledge = unified_knowledge_integrity_report(topic, root=program_root(topic) / "knowledge", versions_workspace=None)
    if not topic_knowledge.get("ok"):
        structural_errors.append(f"topic: knowledge integrity issues {json.dumps(topic_knowledge.get('issues', []), ensure_ascii=False)}")
    route_skill = topic / ".discovery" / "agents-template" / ".agents" / "skills" / "explore-cli" / "SKILL.md"
    query_skill = topic / ".discovery" / "agents-template" / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
    try:
        route_skill_ready = ROUTE_CLI_PROTOCOL_MARKER in route_skill.read_text(encoding="utf-8") and "knowledge-query-protocol: 1" in query_skill.read_text(encoding="utf-8")
    except OSError:
        route_skill_ready = False
    if not route_skill_ready:
        structural_errors.append(f"topic: Route template does not declare {ROUTE_CLI_PROTOCOL_MARKER}")
    missing = [rel(topic, path) for path in required_paths(workspace) if not path.exists()]
    if missing:
        structural_errors.append(f"{current_problem_id(workspace)}: missing {', '.join(missing)}")
    knowledge_report = unified_knowledge_integrity_report(workspace, root=knowledge_root(workspace), versions_workspace=workspace)
    if not knowledge_report.get("ok"):
        structural_errors.append(
            f"{current_problem_id(workspace)}: knowledge integrity issues "
            + json.dumps(knowledge_report.get("issues", []), ensure_ascii=False)
        )
    resources = resource_integrity_report(workspace, probe_enforcement=True)
    if not resources.get("ok"):
        structural_errors.append(
            f"{current_problem_id(workspace)}: resource integrity issues "
            + json.dumps(resources.get("issues", []), ensure_ascii=False)
        )
    if structural_errors:
        raise SystemExit("Discovery start refused because workspace validation failed:\n" + "\n".join(structural_errors))
    assert_problem_runtime_exclusive(workspace)
    state = read_dashboard_worker_state(workspace)
    worker_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
    if state.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES and route_broker_is_available(workspace, expected_pid=worker_pid):
        worker_result = {"problem": current_problem_id(workspace), "status": state.get("status"), "pid": worker_pid, "reused": True}
    else:
        if state.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES:
            control_dashboard_worker(workspace, "stop")
            deadline = time.time() + 10
            while time.time() < deadline:
                state = read_dashboard_worker_state(workspace)
                if state.get("status") not in ACTIVE_DASHBOARD_WORKER_STATUSES:
                    break
                time.sleep(0.2)
            if state.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES:
                raise SystemExit("Discovery Runtime is active but its Route Broker is unavailable; wait for active jobs to drain, then restart")
        launched = launch_dashboard_worker(workspace)
        worker_result = {"problem": current_problem_id(workspace), "status": launched["worker"]["status"], "pid": launched["worker"]["pid"], "reused": False}
        deadline = time.time() + 10
        while time.time() < deadline and not route_broker_is_available(workspace, expected_pid=int(launched["worker"]["pid"])):
            time.sleep(0.1)
        if not route_broker_is_available(workspace, expected_pid=int(launched["worker"]["pid"])):
            raise SystemExit("Discovery Runtime started but its Route Broker did not become ready")
    reconcile_headless_campaigns(workspace)
    print(json.dumps({"status": "starting", "worker": worker_result}, ensure_ascii=False, indent=2, sort_keys=True))
    serve_dashboard(workspace, str(args.host), int(args.port), 2.0, bool(args.no_browser))


def cmd_doctor(topic: Path, current_workspace: Path | None, cwd: Path, args: argparse.Namespace) -> None:
    if current_workspace is not None and in_agent_workspace(current_workspace, cwd) is not None:
        raise SystemExit("doctor is a Human/Main diagnostic and is unavailable inside a Route workspace")
    requested = str(args.problem or "")
    rows = registered_problems(topic)
    if requested:
        rows = [row for row in rows if str(row.get("id")) == requested]
        if not rows:
            raise SystemExit(f"unknown Problem: {requested}")
    problem_reports: list[dict[str, Any]] = []
    route_sandbox = route_sandbox_report(refresh=True)
    overall_ok = bool(route_sandbox.get("available"))
    for index, row in enumerate(rows):
        workspace = problem_workspace(topic, str(row["id"]))
        missing = [rel(topic, path) for path in required_paths(workspace) if not path.exists()]
        contract = read_json(evaluation_contract_path(workspace), {})
        registry = read_json(evaluation_registry_path(workspace), {})
        contract_digest = evaluation_contract_digest(contract) if isinstance(contract, dict) and contract else None
        knowledge_report = unified_knowledge_integrity_report(workspace, root=knowledge_root(workspace), versions_workspace=workspace)
        route_client = route_client_integrity_report(workspace)
        worker = read_dashboard_worker_state(workspace)
        worker_healthy = worker.get("status") != "failed"
        resources = resource_integrity_report(workspace, probe_enforcement=index == 0)
        evaluator_ok = bool(
            isinstance(contract, dict)
            and isinstance(registry, dict)
            and contract.get("configured") is True
            and registry.get("configured") is True
            and contract_digest
            and registry.get("public_contract_digest") == contract_digest
        )
        report = {
            "problem_id": row["id"],
            "structure": {"ok": not missing, "missing": missing},
            "evaluator": {
                "active": evaluator_ok,
                "public_configured": contract.get("configured") if isinstance(contract, dict) else None,
                "private_configured": registry.get("configured") if isinstance(registry, dict) else None,
                "digest_matches": bool(contract_digest and isinstance(registry, dict) and registry.get("public_contract_digest") == contract_digest),
            },
            "knowledge": knowledge_report,
            "route_client": route_client,
            "resources": resources,
            "worker": {"status": worker.get("status"), "pid": worker.get("pid"), "reason": worker.get("reason"), "healthy": worker_healthy},
        }
        report["ok"] = not missing and knowledge_report.get("ok", False) and route_client.get("ok", False) and resources.get("ok", False) and worker_healthy
        overall_ok = overall_ok and bool(report["ok"])
        problem_reports.append(report)
    topic_knowledge = unified_knowledge_integrity_report(topic, root=program_root(topic) / "knowledge", versions_workspace=None)
    overall_ok = overall_ok and bool(topic_knowledge.get("ok"))
    payload = {
        "status": "ok" if overall_ok else "issues_found",
        "topic": str(topic),
        "route_sandbox": route_sandbox,
        "topic_knowledge": topic_knowledge,
        "problems": problem_reports,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if not overall_ok:
        raise SystemExit(1)


def build_route_context(workspace: Path, agent_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    agent = agent_dir.name
    contract = read_json(evaluation_contract_path(workspace), {})
    loop_state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
    notices = read_jsonl(main_agent_notices_path(workspace))
    jobs = [refresh_job(job) for job in read_jsonl(job_index(workspace)) if job.get("agent") == agent]
    jobs.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    practices = [row for row in read_versions(knowledge_root(workspace)) if row.get("agent") == agent]
    practices.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    config = load_resource_config(workspace)
    runtime_state = read_dashboard_worker_state(workspace).get("job_runtime", {})
    runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
    job_summaries = [
        {
            "id": job.get("id"),
            "kind": job.get("kind") or job.get("launcher"),
            "status": job.get("status"),
            "reason": job.get("reason"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "log": job.get("log"),
            "resources": job.get("resources"),
            "practice_version": job.get("practice_version"),
            "runtime": runtime_state.get(str(job.get("id"))),
        }
        for job in jobs[: max(0, int(args.limit))]
    ]
    practice_summaries = [
        {
            "id": row.get("id"),
            "created_at": row.get("created_at"),
            "summary": row.get("summary"),
            "space": row.get("space"),
            "metrics": row.get("metrics"),
            "metric_roles": row.get("metric_roles"),
            "ai_review": row.get("ai_review"),
        }
        for row in practices[: max(0, int(args.limit))]
    ]
    has_objective = bool(contract.get("metrics")) if isinstance(contract, dict) else False
    has_ai_review = isinstance(contract.get("ai_review"), dict) if isinstance(contract, dict) else False
    evaluation_mode = "Hybrid" if has_objective and has_ai_review else "Objective" if has_objective else "AI Review" if has_ai_review else "Unconfigured"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "problem": {
            "id": current_problem_id(workspace),
            "brief": "pub/README.md",
            "evaluation_api": "pub/evaluation/API.md",
            "evaluation_contract": contract,
            "evaluation_mode": evaluation_mode,
        },
        "route": {"id": agent, "workspace": str(agent_dir), "loop": loop_state},
        "notices": notices,
        "resources": {"policy": agent_resource_policy(config, agent)},
        "jobs": job_summaries,
        "practice": practice_summaries,
    }
    if args.job:
        job = get_job(workspace, str(args.job))
        if job.get("agent") != agent:
            raise SystemExit("a Route may inspect only its own jobs")
        refreshed = refresh_job(job)
        payload["job"] = {
            "id": refreshed.get("id"),
            "kind": refreshed.get("kind") or refreshed.get("launcher"),
            "status": refreshed.get("status"),
            "reason": refreshed.get("reason"),
            "returncode": refreshed.get("returncode"),
            "created_at": refreshed.get("created_at"),
            "started_at": refreshed.get("started_at"),
            "finished_at": refreshed.get("finished_at"),
            "resources": refreshed.get("resources"),
            "practice_version": refreshed.get("practice_version"),
            "log": refreshed.get("log"),
            "log_tail": summarize_job_log(workspace, refreshed, 80),
            "runtime": runtime_state.get(str(refreshed.get("id"))),
        }
    return payload


def cmd_context(workspace: Path, cwd: Path, args: argparse.Namespace) -> None:
    payload = build_route_context(workspace, find_agent_dir(workspace, cwd), args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def problem_summary(topic: Path, row: dict[str, Any]) -> dict[str, Any]:
    problem_id = str(row.get("id") or "")
    workspace = problem_workspace(topic, problem_id)
    practices = read_versions(knowledge_root(workspace))
    knowledge_state = load_knowledge(knowledge_root(workspace))
    knowledge = knowledge_state.get("items", {})
    agents = sorted(p.name for p in workspace.iterdir() if p.is_dir() and AGENT_NAME_RE.fullmatch(p.name))
    jobs = read_jsonl(job_index(workspace))
    return {
        **row,
        "id": problem_id,
        "path": rel(topic, workspace),
        "agents": agents,
        "agent_count": len(agents),
        "practice_versions": len(practices),
        "knowledge_items": len(knowledge),
        "knowledge_topics": len(knowledge_state.get("topics", {})),
        "queued_jobs": sum(1 for job in jobs if job.get("status") == "queued"),
        "running_jobs": sum(1 for job in jobs if job.get("status") in {"running", "starting"}),
    }


def now() -> str:
    return datetime.now(UTC).isoformat()


def console(workspace: Path) -> Path:
    return workspace / ".DiscoveryConsole"


def pub(workspace: Path) -> Path:
    return console(workspace) / "pub"


def private(workspace: Path) -> Path:
    return console(workspace) / "private"


def rel(workspace: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def require_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise SystemExit(f"{label} must stay under {root}") from None
    return resolved


def safe_id(value: str, label: str, pattern: re.Pattern[str] = SAFE_ID_RE) -> str:
    if not pattern.fullmatch(value):
        raise SystemExit(f"{label} contains unsafe characters: {value}")
    return value


def agent_relative_path(agent_dir: Path, raw_path: str, label: str, must_exist: bool = True) -> Path:
    raw = Path(raw_path)
    path = raw if raw.is_absolute() else agent_dir / raw
    resolved = require_under(path, agent_dir, label)
    if must_exist and not resolved.exists():
        raise SystemExit(f"{label} not found: {raw_path}")
    return resolved


def require_not_private(workspace: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    if is_relative_to(resolved, private(workspace)):
        raise SystemExit(f"{label} must not point into .DiscoveryConsole/private")
    return resolved


def in_agent_workspace(workspace: Path, cwd: Path) -> Path | None:
    try:
        return find_agent_dir(workspace, cwd)
    except SystemExit:
        return None


def require_main_workspace(workspace: Path, cwd: Path, action: str) -> None:
    if in_agent_workspace(workspace, cwd) is not None:
        raise SystemExit(f"{action} must be run by the main workspace, not a search-agent workspace")


def required_paths(workspace: Path) -> list[Path]:
    topic = topic_root(workspace)
    return [
        workspace / "AGENTS.md",
        workspace / "problem.json",
        topic / ".agents" / "skills" / "create-exploration-problem" / "SKILL.md",
        topic / ".agents" / "skills" / "maintain-discovery" / "SKILL.md",
        topic / "discovery",
        topic / ".discovery" / "cli" / "discovery.py",
        topic / ".discovery" / "problem-template" / "problem.json",
        topic / ".discovery" / "problem-template" / ".DiscoveryConsole" / "resources.json",
        topic / ".discovery" / "agents-template" / "AGENTS.md",
        topic / ".discovery" / "reviewer-template" / "AGENTS.md",
        topic / ".discovery" / "reviewer-template" / "review",
        topic / ".discovery" / "agents-template" / "explore",
        topic / ".discovery" / "agents-template" / "notebook.md",
        topic / ".discovery" / "agents-template" / ".codex" / "config.toml",
        topic / ".discovery" / "agents-template" / ".discovery" / "loop_state.json",
        topic / ".discovery" / "agents-template" / ".discovery" / "loopctl.py",
        topic / ".discovery" / "agents-template" / "goals" / "README.md",
        topic / ".discovery" / "agents-template" / "goals" / "route_builder.md",
        topic / ".discovery" / "agents-template" / "goals" / "route_auditor.md",
        topic / ".discovery" / "agents-template" / "goals" / "route_debug_eval.md",
        topic / ".discovery" / "agents-template" / "notebooks" / "README.md",
        topic / ".discovery" / "agents-template" / ".agents" / "skills" / "explore-cli" / "SKILL.md",
        topic / ".discovery" / "agents-template" / "experiments",
        topic / ".discovery" / "agents-template" / "inspect",
        topic / ".discovery" / "agents-template" / "results",
        topic / ".discovery" / "agents-template" / "src",
        pub(workspace) / "README.md",
        pub(workspace) / "evaluation" / "API.md",
        pub(workspace) / "evaluation" / "contract.json",
        pub(workspace) / "evaluation" / "check_candidate.py",
        pub(workspace) / "development_space",
        pub(workspace) / "baseline",
        pub(workspace) / "knowledge" / "items",
        pub(workspace) / "knowledge" / "items.json",
        pub(workspace) / "knowledge" / "topics.json",
        pub(workspace) / "knowledge" / "versions",
        pub(workspace) / "notices.jsonl",
        pub(workspace) / "log",
        resource_config_path(workspace),
        private(workspace) / "validation_space",
        private(workspace) / "test_space",
        private(workspace) / "main_review",
        private(workspace) / "evaluation_registry.json",
        private(workspace) / "eval_submissions",
    ]


def topic_required_paths(topic: Path) -> list[Path]:
    return [
        topic / "AGENTS.md",
        topic / "discovery",
        topic / TOPIC_MARKER / "README.md",
        topic / TOPIC_MARKER / "problem_registry.json",
        topic / TOPIC_MARKER / "knowledge" / "items",
        topic / TOPIC_MARKER / "knowledge" / "items.json",
        topic / TOPIC_MARKER / "knowledge" / "topics.json",
        topic / TOPIC_MARKER / "memory" / "main.md",
        topic / TOPIC_MARKER / "memory" / "logs",
        topic / TOPIC_MARKER / "integration",
        topic / TOPIC_MARKER / "log",
        topic / "subprojects-main",
        topic / "subprojects-single",
        topic / "subprojects-single" / ".single-agent-template" / "AGENTS.md",
        topic / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "memory" / "main.md",
        topic / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "memory" / "logs",
        topic / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "knowledge" / "README.md",
        topic / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "knowledge" / "items",
        topic / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "knowledge" / "items.json",
        topic / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "knowledge" / "topics.json",
        topic / ".agents" / "skills" / "create-single-agent-project" / "SKILL.md",
        topic / ".agents" / "skills" / "create-single-agent-project" / "scripts" / "create_single_agent_project.py",
        topic / TEAM_SUBPROJECTS_DIR,
        topic / ".discovery" / "problem-template" / "problem.json",
    ]


def conflict_paths(workspace: Path) -> list[Path]:
    return []


def cmd_validate(workspace: Path) -> None:
    missing = [rel(workspace, p) for p in required_paths(workspace) if not p.exists()]
    conflicts = [rel(workspace, p) for p in conflict_paths(workspace) if p.exists()]
    knowledge = unified_knowledge_integrity_report(workspace, root=knowledge_root(workspace), versions_workspace=workspace)
    route_client = route_client_integrity_report(workspace)
    status = "ok" if not missing and not conflicts and knowledge.get("ok") and route_client.get("ok") else "invalid"
    print(json.dumps({"status": status, "missing": missing, "conflicts": conflicts, "knowledge": knowledge, "route_client": route_client}, indent=2, sort_keys=True))
    if status != "ok":
        raise SystemExit(1)


def cmd_validate_topic(topic: Path) -> None:
    missing = [rel(topic, path) for path in topic_required_paths(topic) if not path.exists()]
    topic_knowledge = unified_knowledge_integrity_report(topic, root=program_root(topic) / "knowledge", versions_workspace=None)
    route_skill = topic / ".discovery" / "agents-template" / ".agents" / "skills" / "explore-cli" / "SKILL.md"
    query_skill = topic / ".discovery" / "agents-template" / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
    try:
        route_cli_protocol = ROUTE_CLI_PROTOCOL_MARKER in route_skill.read_text(encoding="utf-8") and "knowledge-query-protocol: 1" in query_skill.read_text(encoding="utf-8")
    except OSError:
        route_cli_protocol = False
    problems: list[dict[str, Any]] = []
    for row in registered_problems(topic):
        problem_id = str(row["id"])
        workspace = problem_workspace(topic, problem_id)
        problem_missing = [rel(topic, path) for path in required_paths(workspace) if not path.exists()]
        problem_knowledge = unified_knowledge_integrity_report(workspace, root=knowledge_root(workspace), versions_workspace=workspace)
        route_client = route_client_integrity_report(workspace)
        problems.append({"id": problem_id, "path": rel(topic, workspace), "status": "ok" if not problem_missing and problem_knowledge.get("ok") and route_client.get("ok") else "invalid", "missing": problem_missing, "knowledge": problem_knowledge, "route_client": route_client})
    # A newly distributed workbench deliberately has no Problem yet. Its Topic
    # structure is valid as long as its reusable surfaces are intact; `start`
    # continues to require a registered Problem before it launches anything.
    status = "ok" if not missing and topic_knowledge.get("ok") and route_cli_protocol and all(row["status"] == "ok" for row in problems) else "invalid"
    print(json.dumps({"status": status, "topic": str(topic), "empty_workbench": not problems, "missing": missing, "route_cli_protocol": ROUTE_CLI_PROTOCOL if route_cli_protocol else None, "topic_knowledge": topic_knowledge, "problems": problems}, indent=2, sort_keys=True))
    if status != "ok":
        raise SystemExit(1)


def cmd_status(workspace: Path) -> None:
    agents = sorted(p.name for p in workspace.iterdir() if p.is_dir() and p.name.startswith("agent"))
    practices = read_versions(knowledge_root(workspace))
    knowledge_state = load_knowledge(knowledge_root(workspace))
    knowledge = knowledge_state.get("items", {})
    jobs = read_jsonl(job_index(workspace))
    running = [j for j in jobs if j.get("status") == "running" and process_alive(j.get("pid"))]
    print(
        json.dumps(
            {
                "workspace": str(workspace),
                "problem_id": current_problem_id(workspace),
                "agents": agents,
                "knowledge_items": len(knowledge),
                "knowledge_topics": len(knowledge_state.get("topics", {})),
                "practice_versions": len(practices),
                "running_jobs": len(running),
            },
            indent=2,
            sort_keys=True,
        )
    )


def cmd_status_topic(topic: Path) -> None:
    registry = read_problem_registry(topic)
    problems = [problem_summary(topic, row) for row in registered_problems(topic)]
    print(json.dumps({"topic": str(topic), "default_problem": registry.get("default_problem"), "problem_count": len(problems), "problems": problems}, ensure_ascii=False, indent=2, sort_keys=True))


def write_problem_registry(topic: Path, registry: dict[str, Any]) -> None:
    write_json(problem_registry_path(topic), registry)


def cmd_problem(topic: Path, args: argparse.Namespace) -> None:
    registry = read_problem_registry(topic)
    if args.problem_cmd == "list":
        print(json.dumps([problem_summary(topic, row) for row in registered_problems(topic)], ensure_ascii=False, indent=2, sort_keys=True))
        return
    problem_id = safe_id(str(args.problem_id), "problem id")
    if args.problem_cmd == "show":
        for row in registered_problems(topic):
            if str(row.get("id")) == problem_id:
                print(json.dumps(problem_summary(topic, row), ensure_ascii=False, indent=2, sort_keys=True))
                return
        raise SystemExit(f"unknown Problem: {problem_id}")
    if args.problem_cmd == "select":
        problem_workspace(topic, problem_id)
        registry["default_problem"] = problem_id
        write_problem_registry(topic, registry)
        print(json.dumps({"default_problem": problem_id}, indent=2))
        return
    if args.problem_cmd == "eval-status":
        workspace = problem_workspace(topic, problem_id)
        contract = read_json(evaluation_contract_path(workspace), {})
        registry_node = read_json(evaluation_registry_path(workspace), {})
        actual_digest = evaluation_contract_digest(contract) if isinstance(contract, dict) and contract else None
        print(
            json.dumps(
                {
                    "problem_id": problem_id,
                    "public_configured": contract.get("configured") if isinstance(contract, dict) else None,
                    "private_configured": registry_node.get("configured") if isinstance(registry_node, dict) else None,
                    "evidence_level": contract.get("evidence_level") if isinstance(contract, dict) else None,
                    "search_space": evaluation_search_space(contract) if isinstance(contract, dict) and contract.get("evidence_level") in {"L1", "L2", "L3"} else None,
                    "contract_digest": actual_digest,
                    "registry_digest": registry_node.get("public_contract_digest") if isinstance(registry_node, dict) else None,
                    "digest_matches": bool(actual_digest and isinstance(registry_node, dict) and registry_node.get("public_contract_digest") == actual_digest),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if args.problem_cmd == "activate-eval":
        workspace = problem_workspace(topic, problem_id)
        contract = read_json(evaluation_contract_path(workspace), {})
        registry_node = read_json(evaluation_registry_path(workspace), {})
        if not isinstance(contract, dict) or not isinstance(registry_node, dict):
            raise SystemExit("evaluation contract and registry must be JSON objects")
        contract["configured"] = True
        registry_node["configured"] = True
        registry_node["public_contract_digest"] = evaluation_contract_digest(contract)
        validate_evaluation_contract_data(
            workspace,
            contract,
            require_configured=True,
            require_metric_roles=True,
        )
        validate_evaluation_registry_data(workspace, registry_node, require_configured=True)
        validate_evaluation_pair(workspace, contract, registry_node)
        atomic_activate_evaluation(workspace, contract, registry_node)
        print(
            json.dumps(
                {
                    "problem_id": problem_id,
                    "configured": True,
                    "evidence_level": contract["evidence_level"],
                    "search_space": evaluation_search_space(contract),
                    "contract_digest": registry_node["public_contract_digest"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if any(str(row.get("id")) == problem_id for row in registered_problems(topic)):
        raise SystemExit(f"Problem already exists: {problem_id}")
    template = topic / ".discovery" / "problem-template"
    target = topic / TEAM_SUBPROJECTS_DIR / problem_id
    if target.exists():
        raise SystemExit(f"Problem path already exists: {target}")
    if not template.is_dir():
        raise SystemExit(f"Problem template not found: {template}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, target, symlinks=True)
    replace_token(target, "{problem_id}", problem_id)
    replace_token(target, "{problem_title}", str(args.title or problem_id))
    replace_token(target, "{problem_status}", str(args.status or "scoping"))
    for link, destination in ((target / ".discovery", Path("..") / ".." / ".discovery"), (target / ".agents", Path("..") / ".." / ".agents"), (target / "AGENTS.md", Path("..") / ".." / "AGENTS.md")):
        if link.exists() or link.is_symlink():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.symlink_to(destination)
    if (topic / "data").exists() and not (target / "data").exists():
        (target / "data").symlink_to(Path("..") / ".." / "data")
    row = {
        "id": problem_id,
        "title": str(args.title or problem_id),
        "status": str(args.status or "scoping"),
        "path": str(Path(TEAM_SUBPROJECTS_DIR) / problem_id),
        "created_at": now(),
    }
    registry.setdefault("problems", []).append(row)
    registry["default_problem"] = registry.get("default_problem") or problem_id
    write_problem_registry(topic, registry)
    print(json.dumps({"created": problem_id, "path": str(target), "status": row["status"]}, ensure_ascii=False, indent=2, sort_keys=True))


# Metrics are Problem-specific. The dashboard preserves contract order when a
# Problem supplies metrics instead of inheriting an unrelated example Topic.
KEY_DASHBOARD_METRICS: list[str] = []

DASHBOARD_TEXT_PREVIEW_CHARS = 1600
BASELINE_METRIC_VALIDITY_STATUSES = {"pending_review", "valid", "invalid", "not_applicable"}


def dashboard_text_preview(value: Any, limit: int = DASHBOARD_TEXT_PREVIEW_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n..."


def load_codex_dashboard_defaults(workspace: Path) -> tuple[str, str]:
    config: dict[str, Any] = {}
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    for path in (codex_home / "config.toml", workspace / ".codex" / "config.toml"):
        if not path.is_file():
            continue
        try:
            node = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(node, dict):
            config.update(node)
    return str(config.get("model") or ""), str(config.get("model_reasoning_effort") or "")


def build_headless_model_config(workspace: Path) -> dict[str, Any]:
    configured_model, configured_effort = load_codex_dashboard_defaults(workspace)
    models: list[dict[str, Any]] = []
    error = ""
    try:
        result = subprocess.run(
            ["codex", "debug", "models"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        catalog = json.loads(result.stdout)
        raw_models = catalog.get("models", []) if isinstance(catalog, dict) else []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                continue
            model_id = str(raw_model.get("slug") or "").strip()
            if not model_id:
                continue
            reasoning_efforts = []
            for raw_level in raw_model.get("supported_reasoning_levels", []):
                if not isinstance(raw_level, dict):
                    continue
                effort = str(raw_level.get("effort") or "").strip()
                if effort and all(level["id"] != effort for level in reasoning_efforts):
                    reasoning_efforts.append(
                        {
                            "id": effort,
                            "label": effort,
                            "description": str(raw_level.get("description") or ""),
                        }
                    )
            if not reasoning_efforts:
                continue
            model_default = str(raw_model.get("default_reasoning_level") or "")
            supported = {level["id"] for level in reasoning_efforts}
            if model_default not in supported:
                model_default = reasoning_efforts[0]["id"]
            models.append(
                {
                    "id": model_id,
                    "label": str(raw_model.get("display_name") or model_id),
                    "default_reasoning_effort": model_default,
                    "reasoning_efforts": reasoning_efforts,
                }
            )
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        error = f"Could not load Codex model catalog: {exc}"

    model_ids = {model["id"] for model in models}
    if configured_model and configured_model not in model_ids:
        fallback_efforts = [configured_effort] if configured_effort else ["medium"]
        models.insert(
            0,
            {
                "id": configured_model,
                "label": configured_model,
                "default_reasoning_effort": fallback_efforts[0],
                "reasoning_efforts": [
                    {"id": effort, "label": effort, "description": "Configured Codex default"}
                    for effort in fallback_efforts
                ],
            },
        )
    default_model = configured_model if configured_model in {model["id"] for model in models} else (models[0]["id"] if models else "")
    selected = next((model for model in models if model["id"] == default_model), None)
    supported_efforts = {level["id"] for level in selected["reasoning_efforts"]} if selected else set()
    default_effort = configured_effort if configured_effort in supported_efforts else (selected["default_reasoning_effort"] if selected else "")
    return {
        "models": models,
        "default_model": default_model,
        "default_reasoning_effort": default_effort,
        "error": error or None,
    }


def resolve_headless_model_selection(
    workspace: Path,
    model: str,
    reasoning_effort: str,
    *,
    _config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    config = _config if _config is not None else build_headless_model_config(workspace)
    selected_model = model.strip() or str(config.get("default_model") or "")
    model_node = next((node for node in config["models"] if node["id"] == selected_model), None)
    if model_node is None:
        raise SystemExit(f"unsupported Codex model: {selected_model or '(none)'}")
    supported = {level["id"] for level in model_node["reasoning_efforts"]}
    selected_effort = reasoning_effort.strip()
    if not selected_effort:
        if selected_model == config.get("default_model"):
            selected_effort = str(config.get("default_reasoning_effort") or "")
        selected_effort = selected_effort or str(model_node.get("default_reasoning_effort") or "")
    if selected_effort not in supported:
        raise SystemExit(f"unsupported reasoning effort for {selected_model}: {selected_effort or '(none)'}")
    return selected_model, selected_effort


HEADLESS_STAGE_KEYS = ("auditor", "builder", "debug_eval")
HEADLESS_ACTION_STAGE_KEYS = {
    "start_auditor": "auditor",
    "start_builder": "builder",
    "start_debug": "debug_eval",
}


def resolve_headless_stage_configs(
    workspace: Path,
    stage_configs: Any = None,
    *,
    model: str = "",
    reasoning_effort: str = "",
) -> dict[str, dict[str, str]]:
    """Validate and freeze the Codex selection for each agent role.

    The legacy single model/effort pair remains a supported input and expands
    to all roles.  Campaigns persist the expanded mapping so later Dashboard
    changes cannot alter an already running campaign.
    """
    model_config = build_headless_model_config(workspace)
    if stage_configs is None:
        selected_model, selected_effort = resolve_headless_model_selection(
            workspace,
            model,
            reasoning_effort,
            _config=model_config,
        )
        return {
            key: {"model": selected_model, "reasoning_effort": selected_effort}
            for key in HEADLESS_STAGE_KEYS
        }
    if not isinstance(stage_configs, dict):
        raise SystemExit("Headless stage_configs must be an object")
    resolved: dict[str, dict[str, str]] = {}
    for key in HEADLESS_STAGE_KEYS:
        raw = stage_configs.get(key)
        if not isinstance(raw, dict):
            raise SystemExit(f"missing Headless stage configuration: {key}")
        selected_model, selected_effort = resolve_headless_model_selection(
            workspace,
            str(raw.get("model") or ""),
            str(raw.get("reasoning_effort") or ""),
            _config=model_config,
        )
        resolved[key] = {"model": selected_model, "reasoning_effort": selected_effort}
    return resolved


def headless_campaign_stage_config(campaign: dict[str, Any], runner_action: str) -> tuple[str, str]:
    stage_key = HEADLESS_ACTION_STAGE_KEYS.get(runner_action, "builder")
    stage_configs = campaign.get("stage_configs")
    if isinstance(stage_configs, dict):
        stage = stage_configs.get(stage_key)
        if isinstance(stage, dict) and stage.get("model"):
            return str(stage.get("model") or ""), str(stage.get("reasoning_effort") or "")
    # Compatibility with campaigns created before per-stage selection existed.
    return str(campaign.get("model") or ""), str(campaign.get("model_reasoning_effort") or "")


def dashboard_human_ai_review(workspace: Path, version: dict[str, Any]) -> dict[str, Any] | None:
    """Restore private rationales for the Human dashboard without publishing them to Routes."""
    public_review = version.get("ai_review")
    public_dimensions = public_review.get("dimensions") if isinstance(public_review, dict) else None
    if not isinstance(public_dimensions, dict):
        return None
    dimensions = {
        ident: dict(value)
        for ident, value in public_dimensions.items()
        if isinstance(ident, str) and isinstance(value, dict)
    }
    run_info = version.get("eval_run")
    submission_id = str(run_info.get("submission_id") or "") if isinstance(run_info, dict) else ""
    if SAFE_ID_RE.fullmatch(submission_id):
        review_path = private(workspace) / "eval_submissions" / submission_id / "review" / "result.json"
        private_review = read_json(review_path, {})
        private_dimensions = private_review.get("dimensions") if isinstance(private_review, dict) else None
        if isinstance(private_dimensions, dict):
            for ident, public_value in dimensions.items():
                private_value = private_dimensions.get(ident)
                if not isinstance(private_value, dict) or private_value.get("score") != public_value.get("score"):
                    continue
                rationale = private_value.get("rationale")
                if isinstance(rationale, str) and rationale.strip():
                    public_value["rationale"] = rationale.strip()
    return {"dimensions": dimensions}


def build_dashboard_payload(workspace: Path, refresh_seconds: float = 0) -> dict[str, Any]:
    reconcile_headless_campaigns(workspace)
    evaluation_contract = read_json(evaluation_contract_path(workspace), {})
    contract_metric_specs = evaluation_contract.get("metrics") if isinstance(evaluation_contract, dict) else {}
    if not isinstance(contract_metric_specs, dict):
        contract_metric_specs = {}
    practice_versions, load_errors = load_dashboard_versions(workspace)
    baseline_rows, baseline_errors = load_dashboard_baseline_rows(workspace)
    load_errors.extend(baseline_errors)
    practice_versions.sort(key=dashboard_version_sort_key)
    baseline_rows.sort(key=lambda row: str(row.get("method") or row.get("id") or ""))
    comparison_rows = [*practice_versions, *baseline_rows]
    comparison_rows.sort(key=dashboard_version_sort_key)
    agents = sorted({p.name for p in workspace.iterdir() if p.is_dir() and AGENT_NAME_RE.fullmatch(p.name)} | {str(v.get("agent")) for v in practice_versions if v.get("agent")})
    metric_names = dashboard_metric_names(comparison_rows)
    metric_names.extend(metric for metric in contract_metric_specs if metric not in metric_names)
    metric_roles = {
        metric: str(contract_metric_specs[metric]["role"])
        for metric in metric_names
        if isinstance(contract_metric_specs.get(metric), dict)
        and contract_metric_specs[metric].get("role") in {"breakthrough", "guardrail"}
    }
    metric_shortcuts = {
        role: [metric for metric in metric_names if metric_roles.get(metric) == role]
        for role in ("breakthrough", "guardrail")
    }
    directions, direction_sources = dashboard_metric_directions(comparison_rows, metric_names)
    for metric, spec in contract_metric_specs.items():
        if metric in directions and isinstance(spec, dict) and spec.get("direction") in {"higher", "lower"}:
            directions[metric] = str(spec["direction"])
            direction_sources[metric] = "problem_contract"
    baseline_best_row = build_baseline_best_metric_row(workspace, baseline_rows, metric_names, directions)
    if baseline_best_row:
        baseline_rows.append(baseline_best_row)
        comparison_rows = [*practice_versions, *baseline_rows]
        comparison_rows.sort(key=dashboard_version_sort_key)
    metric_stats = dashboard_metric_stats(comparison_rows, metric_names, directions)
    latest_ids = latest_version_ids_by_agent(practice_versions)
    previous_values = dashboard_previous_values(practice_versions, metric_names)

    enriched: list[dict[str, Any]] = []
    for version in comparison_rows:
        reported_metrics = {k: float(v) for k, v in version.get("metrics", {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        raw_metrics = dashboard_numeric_metrics(version)
        version_id = str(version.get("id", ""))
        agent = str(version.get("agent", ""))
        row_type = str(version.get("row_type") or "practice")
        ranks: dict[str, Any] = {}
        deltas: dict[str, Any] = {}
        best: dict[str, Any] = {}
        normalized: dict[str, float | None] = {}
        for metric in metric_names:
            if metric not in raw_metrics:
                continue
            value = raw_metrics[metric]
            direction = directions.get(metric, "higher")
            stats = metric_stats.get(metric, {})
            normalized[metric] = normalize_dashboard_metric(value, stats, direction)
            ranks[metric] = {
                "global": dashboard_rank(comparison_rows, version_id, metric, direction),
                "own": dashboard_rank([v for v in practice_versions if v.get("agent") == agent], version_id, metric, direction) if row_type == "practice" else {"rank": 0, "of": 0},
            }
            previous = previous_values.get((version_id, metric))
            delta_value = None if previous is None else value - previous
            deltas[metric] = {
                "previous": previous,
                "value": delta_value,
                "improved": None if delta_value is None else is_dashboard_improvement(delta_value, direction),
                "regressed": None if delta_value is None else is_dashboard_regression(delta_value, direction),
            }
            own_rows = [v for v in practice_versions if v.get("agent") == agent and dashboard_numeric_metric(v, metric) is not None]
            best[metric] = {
                "global": summarize_dashboard_best(dashboard_best_version(comparison_rows, metric, direction), metric),
                "own": summarize_dashboard_best(dashboard_best_version(own_rows, metric, direction), metric),
            }
        feedback_metrics = version.get("eval_feedback", {}).get("metrics", {}) if isinstance(version.get("eval_feedback"), dict) else {}
        enriched.append(
            {
                "id": version_id,
                "agent": agent,
                "method": version.get("method", ""),
                "method_kind": version.get("method_kind", ""),
                "row_type": row_type,
                "created_at": version.get("created_at"),
                "summary": version.get("summary", ""),
                "space": version.get("space", ""),
                "metrics": raw_metrics,
                "reported_metrics": reported_metrics,
                "metric_validity": version.get("metric_validity", {}),
                "metric_directions": {metric: directions.get(metric, "higher") for metric in raw_metrics},
                "metric_roles": {metric: metric_roles[metric] for metric in raw_metrics if metric in metric_roles},
                "normalized_metrics": normalized,
                "ranks": ranks,
                "deltas": deltas,
                "best": best,
                "eval_feedback": version.get("eval_feedback"),
                "ai_review": dashboard_human_ai_review(workspace, version),
                "eval_run": version.get("eval_run"),
                "snapshot": version.get("snapshot"),
                "note": dashboard_text_preview(version.get("note", "")),
                "next_plan": dashboard_text_preview(version.get("next_plan", "")),
                "note_truncated": len(str(version.get("note", "") or "")) > DASHBOARD_TEXT_PREVIEW_CHARS,
                "next_plan_truncated": len(str(version.get("next_plan", "") or "")) > DASHBOARD_TEXT_PREVIEW_CHARS,
                "reflected_at": version.get("reflected_at"),
                "notebook_archive": version.get("notebook_archive"),
                "path": version.get("path"),
                "is_latest_for_agent": row_type == "practice" and latest_ids.get(agent) == version_id,
                "feedback_metrics": feedback_metrics,
            }
        )

    latest_by_agent = [v for v in enriched if v["is_latest_for_agent"]]
    latest_by_agent.sort(key=lambda v: v.get("agent", ""))
    baseline_enriched = [v for v in enriched if v.get("row_type") == "baseline"]
    latest_with_baselines = [*latest_by_agent, *baseline_enriched]
    best_by_metric = {metric: summarize_dashboard_best(dashboard_best_version(comparison_rows, metric, directions.get(metric, "higher")), metric) for metric in metric_names}
    latest_eval_time = max((str(v.get("created_at")) for v in practice_versions if v.get("created_at")), default=None)
    agent_statuses = build_dashboard_agent_statuses(workspace, agents)
    route_sandbox = route_sandbox_report()
    headless_model_config = build_headless_model_config(workspace)
    metadata = read_json(workspace / "problem.json", {})
    has_metrics = bool(evaluation_contract.get("metrics")) if isinstance(evaluation_contract, dict) else False
    has_review = isinstance(evaluation_contract.get("ai_review"), dict) if isinstance(evaluation_contract, dict) else False
    evaluation_mode = "Hybrid" if has_metrics and has_review else ("AI Review" if has_review else "Objective")
    topic = topic_root(workspace)
    problem_rows = [problem_summary(topic, row) for row in registered_problems(topic)] if (topic / TOPIC_MARKER).is_dir() else []
    return {
        "schema_version": 1,
        "workspace": str(workspace),
        "topic_root": str(topic),
        "problem_id": current_problem_id(workspace),
        "problem_title": metadata.get("title") or current_problem_id(workspace),
        "problem_metadata": metadata,
        "evaluation_contract": evaluation_contract,
        "evaluation_mode": evaluation_mode,
        "notices": read_jsonl(main_agent_notices_path(workspace)),
        "problems": problem_rows,
        "generated_at": now(),
        "refresh_seconds": refresh_seconds,
        "agents": agents,
        "agent_statuses": agent_statuses,
        "route_sandbox": route_sandbox,
        "headless_model_config": headless_model_config,
        "agent_count": len(agents),
        "practice_version_count": len(practice_versions),
        "baseline_count": len(baseline_enriched),
        "version_count": len(enriched),
        "latest_eval_time": latest_eval_time,
        "key_metrics": KEY_DASHBOARD_METRICS,
        "metric_shortcuts": metric_shortcuts,
        "metrics": [
            {
                "name": metric,
                "direction": directions.get(metric, "higher"),
                "direction_source": direction_sources.get(metric, "default_higher"),
                "role": metric_roles.get(metric),
            }
            for metric in metric_names
        ],
        "versions": enriched,
        "baseline_rows": baseline_enriched,
        "latest_by_agent": latest_by_agent,
        "latest_with_baselines": latest_with_baselines,
        "best_by_metric": best_by_metric,
        "load_errors": load_errors,
    }


def load_dashboard_versions(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    versions_dir = knowledge_versions_dir(knowledge_root(workspace))
    versions: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    if versions_dir.exists():
        for path in sorted(versions_dir.glob("*.json")):
            try:
                node = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append({"path": rel(workspace, path), "error": str(exc)})
                continue
            if not isinstance(node, dict):
                errors.append({"path": rel(workspace, path), "error": "practice version JSON is not an object"})
                continue
            version_id = node.get("id")
            if not isinstance(version_id, str):
                version_id = path.stem
                node["id"] = version_id
            node["path"] = rel(workspace, path)
            versions[version_id] = node
    return list(versions.values()), errors


def load_dashboard_baseline_rows(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    registry_path = pub(workspace) / "baseline" / "baselines.json"
    registry = read_json(registry_path, {})
    if not isinstance(registry, dict):
        return [], [{"path": rel(workspace, registry_path), "error": "Baseline entity registry is not an object"}]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    source_cache: dict[Path, Any] = {}
    contract = read_json(evaluation_contract_path(workspace), {})
    compatible_digests: set[str] = set()
    current_space = ""
    if isinstance(contract, dict) and contract:
        current_digest = evaluation_contract_digest(contract)
        compatible_digests.add(current_digest)
        declared = contract.get("compatible_contract_digests")
        if isinstance(declared, list):
            compatible_digests.update(str(value) for value in declared)
        current_space = "development" if contract.get("evidence_level") == "L1" else "validation"
    for baseline_id, registered in registry.items():
        if not isinstance(registered, dict):
            errors.append({"path": rel(workspace, registry_path), "error": f"Baseline {baseline_id} is not an object"})
            continue
        info = dict(registered)
        info.setdefault("id", baseline_id)
        if str(info.get("id")) != str(baseline_id):
            errors.append({"path": rel(workspace, registry_path), "error": f"Baseline key/id mismatch: {baseline_id}"})
            continue
        metrics_data = info.get("metrics")
        source = info.get("metrics_source")
        source_path: Path | None = None
        if isinstance(source, dict):
            raw_path = str(source.get("path") or "")
            source_path = pub(workspace) / raw_path
            if source_path not in source_cache:
                source_cache[source_path] = read_json(source_path, None)
            metrics_data = source_cache[source_path]
            keys = source.get("keys") if isinstance(source.get("keys"), list) else []
            for key in keys:
                if not isinstance(metrics_data, dict):
                    break
                metrics_data = metrics_data.get(str(key))
        if not isinstance(metrics_data, dict):
            errors.append({"path": rel(workspace, source_path or registry_path), "error": f"Baseline {baseline_id} metrics are unavailable"})
            continue
        metrics = numeric_metrics(metrics_data)
        directions = metric_directions_from_metrics(metrics_data)
        metric_validity, validity_errors = normalize_baseline_metric_validity(
            str(baseline_id), metrics, info.get("metric_validity")
        )
        errors.extend({"path": rel(workspace, registry_path), "error": error} for error in validity_errors)
        locator = info.get("locator") if isinstance(info.get("locator"), dict) else {}
        score_report = str(source.get("path") or "") if isinstance(source, dict) else ""
        contract_digest = str(info.get("contract_digest") or "")
        evidence_space = str(info.get("evidence_space") or info.get("space") or "")
        comparison_eligible = bool(
            contract_digest in compatible_digests
            and evidence_space == current_space
        )
        rows.append(
            {
                **info,
                "id": f"baseline:{baseline_id}",
                "entity_type": "baseline",
                "agent": "baseline",
                "method": str(baseline_id),
                "method_kind": str(info.get("method_kind") or "baseline"),
                "row_type": "baseline",
                "created_at": info.get("created_at"),
                "summary": str(info.get("summary") or f"{baseline_id} baseline"),
                "space": info.get("evidence_space"),
                "comparison_eligible": comparison_eligible,
                "metrics": metrics,
                "metric_validity": metric_validity,
                "metric_directions": directions,
                "eval_feedback": None,
                "eval_run": {
                    "id": str(info.get("evaluation_id") or "baseline-evaluation"),
                    "log": "",
                    "returncode": 0,
                    "source_summary": str(info.get("summary") or ""),
                },
                "snapshot": None,
                "note": "",
                "next_plan": "",
                "locator": {"path": "baseline/", **locator, **({"score_report": score_report} if score_report else {})},
                "path": str(locator.get("path") or "baseline/"),
            }
        )
    return rows, errors


def normalize_baseline_metric_validity(
    baseline_id: str,
    metrics: dict[str, float],
    raw_validity: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    reviews = raw_validity if isinstance(raw_validity, dict) else {}
    default = reviews.get("default") if isinstance(reviews.get("default"), dict) else None
    normalized: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for metric in metrics:
        explicit = reviews.get(metric)
        review = explicit if isinstance(explicit, dict) else default
        if not isinstance(review, dict):
            normalized[metric] = {
                "status": "pending_review",
                "reason": "Main Agent has not reviewed this Baseline metric value",
            }
            continue
        status = str(review.get("status") or "pending_review")
        reason = str(review.get("reason") or "").strip()
        if status not in BASELINE_METRIC_VALIDITY_STATUSES:
            errors.append(f"Baseline {baseline_id} metric {metric} has unknown validity status {status}")
            status = "pending_review"
        if explicit is None and status != "pending_review":
            errors.append(f"Baseline {baseline_id} metric {metric} must be explicitly reviewed before status {status}")
            status = "pending_review"
        if not reason:
            errors.append(f"Baseline {baseline_id} metric {metric} validity must include a reason")
            status = "pending_review"
            reason = "Validity review is incomplete"
        normalized_review: dict[str, Any] = {"status": status, "reason": reason}
        evidence = review.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            normalized_review["evidence"] = evidence.strip()
        normalized[metric] = normalized_review
    return normalized, errors


def route_cli_protocol_ready(workspace: Path, agent: str) -> bool:
    """Fail closed when a Route does not carry the matching explore client contract."""
    route = workspace / agent
    skill = route / ".agents" / "skills" / "explore-cli" / "SKILL.md"
    query_skill = route / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
    try:
        text = skill.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        query_text = query_skill.read_text(encoding="utf-8")
    except OSError:
        return False
    return ROUTE_CLI_PROTOCOL_MARKER in text and "knowledge-query-protocol: 1" in query_text and (route / "explore").is_file()


ROUTE_PROMPT_BUNDLE = (
    "AGENTS.md",
    "goals/README.md",
    "goals/route_builder.md",
    "goals/route_auditor.md",
    "goals/route_debug_eval.md",
    "headless_goals/README.md",
    "headless_goals/route_builder.md",
    "headless_goals/route_auditor.md",
    "headless_goals/route_debug_eval.md",
)


def route_prompt_bundle_issues(workspace: Path, route: Path) -> list[dict[str, str]]:
    """Detect operational Route prompts that have drifted from the template."""
    try:
        template = topic_root(workspace) / ".discovery" / "agents-template"
    except SystemExit:
        # Lightweight tests and standalone fixtures may place the template next
        # to the Problem without a registered Topic root.
        template = workspace / ".discovery" / "agents-template"
    issues: list[dict[str, str]] = []
    for relative in ROUTE_PROMPT_BUNDLE:
        source = template / relative
        target = route / relative
        if not source.is_file() or not target.is_file():
            issues.append({"route": route.name, "issue": "missing_route_prompt_file", "path": relative})
            continue
        try:
            matches = source.read_bytes() == target.read_bytes()
        except OSError:
            matches = False
        if not matches:
            issues.append({"route": route.name, "issue": "stale_route_prompt_file", "path": relative})
    return issues


def route_client_integrity_report(workspace: Path) -> dict[str, Any]:
    topic_runtime = str((topic_root(workspace) / ".discovery").resolve())
    issues: list[dict[str, str]] = []
    for route in sorted(path for path in workspace.iterdir() if path.is_dir() and AGENT_NAME_RE.fullmatch(path.name)):
        issues.extend(route_prompt_bundle_issues(workspace, route))
        client = route / "explore"
        if not route_cli_protocol_ready(workspace, route.name):
            issues.append({"route": route.name, "issue": "missing_or_incompatible_explore_client"})
        if client.is_file() and client.stat().st_mode & 0o222:
            issues.append({"route": route.name, "issue": "explore_client_is_writable"})
        if client.is_file():
            expected_hint = f'PROBLEM_ROOT_HINT = "{workspace.resolve()}"'
            if expected_hint not in client.read_text(encoding="utf-8", errors="ignore"):
                issues.append({"route": route.name, "issue": "explore_client_problem_root_is_stale"})
        pub_link = route / "pub"
        try:
            public_target = pub_link.readlink()
        except OSError:
            public_target = None
        if public_target is None or not public_target.is_absolute() or public_target != pub(workspace).resolve():
            issues.append({"route": route.name, "issue": "route_pub_link_is_not_canonical"})
        config = route / ".codex" / "config.toml"
        if config.is_file() and topic_runtime in config.read_text(encoding="utf-8", errors="ignore"):
            issues.append({"route": route.name, "issue": "route_permission_reads_topic_runtime"})
    return {"ok": not issues, "protocol": ROUTE_CLI_PROTOCOL, "issues": issues}


def build_dashboard_agent_statuses(workspace: Path, agents: list[str], *, include_campaigns: bool = True) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    evaluation_contract = read_json(evaluation_contract_path(workspace), {})
    evaluator_active = isinstance(evaluation_contract, dict) and evaluation_contract.get("configured") is True
    sandbox = route_sandbox_report()
    runtime_by_job = read_dashboard_worker_state(workspace).get("job_runtime", {})
    runtime_by_job = runtime_by_job if isinstance(runtime_by_job, dict) else {}
    for agent in agents:
        cli_protocol_ready = route_cli_protocol_ready(workspace, agent)
        prompt_bundle_issues = route_prompt_bundle_issues(workspace, workspace / agent)
        prompt_bundle_ready = not prompt_bundle_issues
        path = workspace / agent / ".discovery" / "loop_state.json"
        state = read_json(path, {}) if path.exists() else {}
        phase = str(state.get("phase") or "unknown")
        last_version = state.get("last_version")
        last_reflected = state.get("last_reflected_version")
        eval_status = state.get("eval_status")
        active_eval = state.get("active_eval")
        last_error = state.get("last_error")
        completed_stage = "none"
        waiting_for = "Builder"
        status_label = "Builder ready"
        status_detail = "No completed eval yet"
        runner_action = "start_builder"
        goal_file = "headless_goals/route_builder.md"
        should_start_codex = True
        active_headless = latest_active_headless_run(workspace, agent)
        active_route_run = latest_active_route_run(workspace, agent)
        active_queued_route_job = latest_active_queued_route_job(workspace, agent)
        if active_route_run is not None:
            active_route_run = {
                **active_route_run,
                "runtime": runtime_by_job.get(str(active_route_run.get("id"))),
                "last_activity_at": (runtime_by_job.get(str(active_route_run.get("id"))) or {}).get("sampled_at"),
            }
        if active_queued_route_job is not None:
            active_queued_route_job = {
                **active_queued_route_job,
                "runtime": runtime_by_job.get(str(active_queued_route_job.get("id"))),
                "last_activity_at": (runtime_by_job.get(str(active_queued_route_job.get("id"))) or {}).get("sampled_at"),
            }
        last_headless = latest_headless_run(workspace, agent)
        active_campaign = latest_active_headless_campaign(workspace, agent) if include_campaigns else None
        last_campaign = latest_headless_campaign(workspace, agent) if include_campaigns else None
        can_pause = False
        can_resume = False
        can_stop = False

        if active_headless is not None:
            completed_stage = "none"
            is_paused = active_headless.get("status") == "paused"
            waiting_for = "Resume" if is_paused else "Codex"
            headless_jobs = route_builder_jobs(workspace, agent, headless_run_id=str(active_headless.get("id") or ""))
            active_headless_jobs = [job for job in headless_jobs if job.get("status") in {"queued", "starting", "running", "paused"}]
            if active_headless_jobs:
                job = active_headless_jobs[-1]
                job_status = str(job.get("status") or "")
                if job_status == "queued":
                    status_label = "Waiting for resources"
                    status_detail = "Job is queued; Codex continues this Turn when it starts and finishes."
                else:
                    status_label = "Running code"
                    status_detail = "Codex is retained in an attached wait; completion continues this Turn."
            else:
                status_label = "Paused" if is_paused else "Codex working"
                status_detail = str(active_headless.get("goal_file") or active_headless.get("run_id") or "headless goal")
            runner_action = "codex_paused" if is_paused else "codex_running"
            goal_file = active_headless.get("goal_file")
            should_start_codex = False
            can_pause = active_headless.get("status") == "running" and (active_headless.get("pgid") is not None or active_headless.get("pid") is not None)
            can_resume = is_paused
            can_stop = active_headless.get("status") in {"running", "paused"} and (active_headless.get("pgid") is not None or active_headless.get("pid") is not None)
        elif active_route_run is not None:
            completed_stage = "auditor"
            waiting_for = "Development run"
            handoff = active_route_run.get("completion_mode") == "detach" or bool(active_route_run.get("wait_timed_out_at"))
            status_label = "Background compute" if handoff else "Running code"
            if active_route_run.get("status") == "paused":
                status_label = "Paused"
            status_detail = (
                "Model has handed off; a fresh Headless Turn resumes after this Job is terminal."
                if handoff
                else "Codex is retained in an attached wait; completion continues this Turn."
            )
            runner_action = "wait_builder_run"
            goal_file = None
            should_start_codex = False
        elif active_queued_route_job is not None:
            completed_stage = "auditor"
            job_status = str(active_queued_route_job.get("status") or "queued")
            waiting_for = "Queued development"
            if job_status == "queued":
                status_label = "Waiting for resources"
            elif job_status == "paused":
                status_label = "Paused"
            elif active_queued_route_job.get("completion_mode") == "detach" or active_queued_route_job.get("wait_timed_out_at"):
                status_label = "Background compute"
            else:
                status_label = "Running code"
            status_detail = (
                "Job is waiting for resources; no model polling is occurring."
                if job_status == "queued"
                else "Model has handed off; a fresh Headless Turn resumes after this Job is terminal."
                if status_label == "Background compute"
                else "Codex is retained in an attached wait; completion continues this Turn."
            )
            runner_action = "wait_builder_job"
            goal_file = None
            should_start_codex = False
        elif eval_status == "queued" and isinstance(active_eval, dict) and active_eval:
            completed_stage = "builder"
            waiting_for = "Worker"
            status_label = "Formal evaluation"
            status_detail = str(active_eval.get("job") or active_eval.get("id") or active_eval.get("command") or "queued eval")
            runner_action = "wait_eval"
            goal_file = None
            should_start_codex = False
        elif eval_status == "running" and isinstance(active_eval, dict) and active_eval:
            completed_stage = "builder"
            waiting_for = "Worker"
            status_label = "Formal evaluation"
            status_detail = str(active_eval.get("job") or active_eval.get("id") or active_eval.get("command") or "running eval")
            runner_action = "wait_eval"
            goal_file = None
            should_start_codex = False
        elif eval_status in {"main_review", "failed"}:
            completed_stage = "builder"
            waiting_for = "Human/Main"
            status_label = "Main review required"
            status_detail = str(last_error)
            runner_action = "wait_main"
            goal_file = None
            should_start_codex = False
        elif eval_status == "check_failed":
            completed_stage = "none"
            waiting_for = "Builder repair"
            status_label = "Needs attention"
            status_detail = str(last_error)
            runner_action = "start_debug"
            goal_file = "headless_goals/route_debug_eval.md"
        elif phase == "reflection_loop":
            completed_stage = "builder"
            waiting_for = "Auditor"
            status_label = "Auditor ready"
            status_detail = f"Eval submitted: {last_version}" if last_version else "Eval submitted"
            runner_action = "start_auditor"
            goal_file = "headless_goals/route_auditor.md"
        elif phase == "work_loop" and last_headless is not None and last_headless.get("runner_action") in {"start_builder", "start_debug"}:
            completed_stage = "none"
            waiting_for = "Builder"
            status_label = "Builder stopped"
            status_detail = headless_stop_detail(last_headless)
            runner_action = "start_debug" if eval_status == "check_failed" else "start_builder"
            goal_file = "headless_goals/route_debug_eval.md" if eval_status == "check_failed" else "headless_goals/route_builder.md"
        elif phase == "work_loop" and last_version and last_reflected == last_version:
            completed_stage = "auditor"
            waiting_for = "Builder"
            status_label = "Auditor done"
            status_detail = f"Reflection complete: {last_reflected}"
            runner_action = "start_builder"
            goal_file = "headless_goals/route_builder.md"
        elif phase == "work_loop":
            completed_stage = "auditor" if last_reflected else "none"
            waiting_for = "Builder"
            status_label = "Builder ready"
            status_detail = f"Last reflected: {last_reflected}" if last_reflected else "No reflected version yet"
            runner_action = "start_builder"
            goal_file = "headless_goals/route_builder.md"
        elif phase == "done":
            completed_stage = "done"
            waiting_for = "Done"
            status_label = "Complete"
            status_detail = str(last_version or "")
            runner_action = "done"
            goal_file = None
            should_start_codex = False
        else:
            waiting_for = "Unknown"
            status_label = phase
            status_detail = str(last_version or "")
            runner_action = "inspect"
            goal_file = None
            should_start_codex = False

        if not sandbox.get("available") and active_headless is None:
            waiting_for = "Main/Human"
            status_label = "Needs attention"
            status_detail = str(sandbox.get("detail") or sandbox.get("remediation") or "bubblewrap preflight failed")
            runner_action = "blocked_sandbox"
            goal_file = None
            should_start_codex = False
        elif not evaluator_active and active_headless is None:
            waiting_for = "Main/Human"
            status_label = "Needs attention"
            status_detail = "Complete validation and activate the registered evaluator before running Route goals"
            runner_action = "blocked_evaluator"
            goal_file = None
            should_start_codex = False
        elif not cli_protocol_ready and active_headless is None:
            waiting_for = "Main/Human"
            status_label = "Needs attention"
            status_detail = "Upgrade this legacy Route instruction bundle before running Route goals"
            runner_action = "blocked_cli_protocol"
            goal_file = None
            should_start_codex = False
        elif not prompt_bundle_ready and active_headless is None and should_start_codex:
            waiting_for = "Main/Human"
            status_label = "Needs attention"
            status_detail = "Sync this Route instruction bundle from .discovery/agents-template"
            runner_action = "blocked_prompt_bundle"
            goal_file = None
            should_start_codex = False

        if active_campaign is not None:
            campaign_status = str(active_campaign.get("status") or "running")
            should_start_codex = False
            can_pause = campaign_status == "running"
            can_resume = campaign_status == "paused"
            can_stop = campaign_status in {"running", "paused"}
            if campaign_status == "paused":
                waiting_for = "Resume"
                status_label = "Paused"
                status_detail = "Automatic phase chaining is paused"
            elif status_label in {"Builder ready", "Auditor ready", "Auditor done", "Builder stopped"}:
                status_label = "Continuing"
                status_detail = "Campaign is preparing the next fresh role Turn."
        elif (
            last_campaign is not None
            and str(last_campaign.get("status") or "") == "blocked"
            and should_start_codex
        ):
            # A terminal Campaign is history, not a persistent Route gate. Keep
            # its reason visible while allowing the current loop role to start
            # a fresh Campaign. Real current blockers above already set
            # should_start_codex=False and remain authoritative.
            previous_reason = str(last_campaign.get("reason") or "Campaign stopped before completing its target")
            role_label = {
                "start_builder": "Builder ready",
                "start_auditor": "Auditor ready",
                "start_debug": "Debug ready",
            }.get(runner_action, status_label)
            status_label = role_label
            status_detail = f"Previous Campaign stopped: {previous_reason}. Start begins a new Campaign."

        statuses.append(
            {
                "agent": agent,
                "phase": phase,
                "completed_stage": completed_stage,
                "waiting_for": waiting_for,
                "status_label": status_label,
                "status_detail": status_detail,
                "last_version": last_version,
                "last_reflected_version": last_reflected,
                "eval_status": eval_status,
                "active_eval": active_eval,
                "active_eval_job": active_eval.get("job") if isinstance(active_eval, dict) else None,
                "runner_action": runner_action,
                "goal_file": goal_file,
                "should_start_codex": should_start_codex,
                "cli_protocol_ready": cli_protocol_ready,
                "prompt_bundle_ready": prompt_bundle_ready,
                "prompt_bundle_issues": prompt_bundle_issues,
                "can_pause_codex": can_pause,
                "can_resume_codex": can_resume,
                "can_stop_codex": can_stop,
                "headless_run": active_headless,
                "active_route_run": active_route_run,
                "active_queued_route_job": active_queued_route_job,
                "last_headless_run": last_headless,
                "headless_campaign": ({**active_campaign, "progress": headless_campaign_progress(active_campaign)} if active_campaign else None),
                "last_headless_campaign": ({**last_campaign, "progress": headless_campaign_progress(last_campaign)} if last_campaign else None),
                "updated_at": state.get("updated_at"),
            }
        )
    return statuses


def headless_run_index(workspace: Path) -> Path:
    path = pub(workspace) / "log" / "headless_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def headless_campaign_index(workspace: Path) -> Path:
    path = pub(workspace) / "log" / "headless_campaigns.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def headless_campaign_lock_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "headless_campaigns.lock"


def headless_run_infrastructure_reason(workspace: Path, run: dict[str, Any]) -> str:
    """Recover deterministic infrastructure failures hidden by a clean model exit."""
    raw_log = str(run.get("log") or "")
    if not raw_log:
        return ""
    try:
        log_path = require_under(workspace / raw_log, pub(workspace) / "log", "headless log")
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if "Explore broker is unavailable; ask Human/Main to run" in text:
        return "headless_route_broker_unavailable"
    return ""


def with_headless_campaign_lock(workspace: Path):
    path = headless_campaign_lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def read_headless_campaigns(workspace: Path) -> list[dict[str, Any]]:
    # A missing Supervisor is recoverable state, not a Campaign failure.  The
    # reconciler relaunches it from this durable record on Runtime/Dashboard
    # startup; callers must therefore see the original active Campaign here.
    return read_jsonl(headless_campaign_index(workspace))


def reconcile_headless_campaigns(workspace: Path) -> list[dict[str, Any]]:
    """Idempotently restart lost Campaign supervisors from persistent state."""
    with with_headless_campaign_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = headless_campaign_index(workspace)
        rows = read_jsonl(path)
        changed = False
        for row in rows:
            if row.get("status") not in {"starting", "running", "paused"}:
                continue
            if process_alive(row.get("supervisor_pid")):
                continue
            campaign_id = str(row.get("id") or "")
            if not campaign_id:
                row.update({"status": "blocked", "reason": "campaign_id_missing", "finished_at": now()})
                changed = True
                continue
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "_headless_campaign", campaign_id],
                    cwd=workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    start_new_session=True,
                )
            except OSError as exc:
                row.update({"status": "blocked", "reason": "campaign_supervisor_recovery_failed", "error": str(exc), "updated_at": now()})
            else:
                row.update({"supervisor_pid": proc.pid, "recovered_at": now(), "updated_at": now()})
            changed = True
        if changed:
            write_jsonl(path, rows)
        return [dict(row) for row in rows]


def upsert_headless_campaign(workspace: Path, campaign: dict[str, Any]) -> None:
    with with_headless_campaign_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = headless_campaign_index(workspace)
        rows = read_jsonl(path)
        out = [campaign if row.get("id") == campaign.get("id") else row for row in rows]
        if not any(row.get("id") == campaign.get("id") for row in rows):
            out.append(campaign)
        write_jsonl(path, out)


def get_headless_campaign(workspace: Path, campaign_id: str) -> dict[str, Any]:
    return find_row(read_headless_campaigns(workspace), campaign_id)


def update_headless_campaign(workspace: Path, campaign_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with with_headless_campaign_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = headless_campaign_index(workspace)
        rows = read_jsonl(path)
        found: dict[str, Any] | None = None
        for row in rows:
            if row.get("id") == campaign_id:
                row.update(updates)
                found = row
                break
        if found is None:
            raise SystemExit(f"id not found: {campaign_id}")
        write_jsonl(path, rows)
        return dict(found)


def latest_active_headless_campaign(workspace: Path, agent: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in read_headless_campaigns(workspace)
        if row.get("agent") == agent and row.get("status") in {"starting", "running", "paused"}
    ]
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows[-1]


def latest_headless_campaign(workspace: Path, agent: str) -> dict[str, Any] | None:
    rows = [row for row in read_headless_campaigns(workspace) if row.get("agent") == agent]
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows[-1]


def validate_campaign_iterations(value: Any) -> int:
    if isinstance(value, bool):
        raise SystemExit("Headless iterations must be an integer from 1 to 20")
    try:
        iterations = int(value)
    except (TypeError, ValueError):
        raise SystemExit("Headless iterations must be an integer from 1 to 20") from None
    if iterations < 1 or iterations > 20:
        raise SystemExit("Headless iterations must be an integer from 1 to 20")
    return iterations


def headless_campaign_progress(campaign: dict[str, Any]) -> dict[str, Any]:
    target = max(1, int(campaign.get("target_iterations") or 1))
    completed_iterations = max(0, min(target, int(campaign.get("completed_iterations") or 0)))
    status = str(campaign.get("status") or "")
    raw_stage = str(campaign.get("current_stage") or "start_auditor")
    stages = ["Auditor", "Builder", "Evaluation"]
    if raw_stage == "start_debug":
        stages.insert(2, "Debug Eval")
    if status == "done" or completed_iterations >= target:
        stage_states = ["done" for _ in stages]
        stage_label = "Complete"
    elif raw_stage == "start_auditor":
        stage_states = ["active", *["pending"] * (len(stages) - 1)]
        stage_label = "Auditor"
    elif raw_stage in {"start_builder", "wait_builder_run", "wait_builder_job"}:
        stage_states = ["done", "active", *["pending"] * (len(stages) - 2)]
        stage_label = "Builder"
    elif raw_stage == "start_debug":
        stage_states = ["done", "done", "active", "pending"]
        stage_label = "Debug Eval"
    else:
        stage_states = ["done", "done", *["active"] + ["pending"] * (len(stages) - 3)]
        stage_label = "Evaluation"
    return {
        "completed_versions": completed_iterations,
        "target_iterations": target,
        "current_iteration": min(target, completed_iterations + 1) if status != "done" else target,
        "stages": [{"label": label, "state": state} for label, state in zip(stages, stage_states)],
        "stage_label": stage_label,
    }


def observe_campaign_reflection(campaign: dict[str, Any], last_version: Any) -> bool:
    version = str(last_version or "")
    if not version or version == str(campaign.get("current_version") or ""):
        return False
    completed = [str(item) for item in campaign.get("completed_versions", []) if str(item)]
    if version not in completed:
        completed.append(version)
    campaign.update(
        {
            "current_version": version,
            "completed_versions": completed,
            "completed_iterations": len(completed),
            "debug_attempts": 0,
            "no_progress_attempts": 0,
            "updated_at": now(),
        }
    )
    return True


def headless_threads_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "headless_threads.json"


def read_headless_threads(workspace: Path) -> dict[str, str]:
    data = read_json(headless_threads_path(workspace), {})
    return data if isinstance(data, dict) else {}


def write_headless_thread(workspace: Path, agent: str, thread_id: str) -> None:
    data = read_headless_threads(workspace)
    data[agent] = thread_id
    write_json(headless_threads_path(workspace), data)


def headless_usage_from_event(event: dict[str, Any]) -> dict[str, int] | None:
    """Extract only reported token counters; never infer cache hits from time."""
    candidates = [event.get("usage"), event.get("token_usage")]
    item = event.get("item")
    if isinstance(item, dict):
        candidates.extend([item.get("usage"), item.get("token_usage")])
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        normalized: dict[str, int] = {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                normalized[key] = value
        if normalized:
            return normalized
    return None


def read_headless_runs(workspace: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(headless_run_index(workspace))
    changed = False
    refreshed: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") in {"starting", "running", "paused"}:
            pid = row.get("supervisor_pid") or row.get("pid")
            if pid is not None and not process_alive(pid):
                row = dict(row)
                row.update({"status": "stale", "finished_at": now(), "reason": "lost_headless_supervisor"})
                changed = True
        refreshed.append(row)
    if changed:
        write_jsonl(headless_run_index(workspace), refreshed)
    return refreshed


def upsert_headless_run(workspace: Path, run: dict[str, Any]) -> None:
    path = headless_run_index(workspace)
    rows = read_jsonl(path)
    out: list[dict[str, Any]] = []
    replaced = False
    for row in rows:
        if row.get("id") == run.get("id"):
            out.append(run)
            replaced = True
        else:
            out.append(row)
    if not replaced:
        out.append(run)
    write_jsonl(path, out)


def get_headless_run(workspace: Path, run_id: str) -> dict[str, Any]:
    return find_row(read_headless_runs(workspace), run_id)


def update_headless_run(workspace: Path, run_id: str, updates: dict[str, Any]) -> None:
    run = get_headless_run(workspace, run_id)
    run.update(updates)
    upsert_headless_run(workspace, run)


def latest_active_headless_run(workspace: Path, agent: str) -> dict[str, Any] | None:
    rows = [row for row in read_headless_runs(workspace) if row.get("agent") == agent and row.get("status") in {"starting", "running", "paused"}]
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows[-1]


def latest_headless_run(workspace: Path, agent: str) -> dict[str, Any] | None:
    rows = [row for row in read_headless_runs(workspace) if row.get("agent") == agent]
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows[-1]


def headless_stop_detail(run: dict[str, Any]) -> str:
    status = run.get("status") or "unknown"
    reason = run.get("reason") or "no phase/eval transition"
    goal = run.get("goal_file") or "headless goal"
    return f"{goal} {status}: {reason}"


def launch_dashboard_headless_goal(
    workspace: Path,
    agent: str,
    model: str = "",
    reasoning_effort: str = "",
    *,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    assert_problem_runtime_exclusive(workspace)
    if not AGENT_NAME_RE.fullmatch(agent):
        raise SystemExit("invalid agent name")
    agent_dir = workspace / agent
    if not agent_dir.is_dir():
        raise SystemExit(f"agent workspace not found: {agent}")
    ensure_route_broker_token(agent_dir)
    if not route_broker_is_available(workspace):
        raise SystemExit("Route broker is unavailable; restart Discovery Dashboard")
    if latest_active_headless_run(workspace, agent) is not None:
        raise SystemExit(f"{agent} already has a running headless Codex goal")
    active_campaign = latest_active_headless_campaign(workspace, agent)
    if active_campaign is not None and str(active_campaign.get("id") or "") != str(campaign_id or ""):
        raise SystemExit(f"{agent} already has an active Headless campaign")
    status_rows = build_dashboard_agent_statuses(workspace, [agent], include_campaigns=campaign_id is None)
    status = status_rows[0] if status_rows else {}
    if not status.get("should_start_codex"):
        raise SystemExit(f"{agent} is not startable now: {status.get('status_label') or status.get('phase')}")
    goal_file = str(status.get("goal_file") or "")
    if not goal_file:
        raise SystemExit(f"{agent} has no goal file for runner action {status.get('runner_action')}")
    goal_path = require_under(agent_dir / goal_file, agent_dir, "headless goal file")
    if not goal_path.is_file():
        raise SystemExit(f"{agent} headless goal file not found: {goal_file}")
    selected_model, selected_effort = resolve_headless_model_selection(workspace, model, reasoning_effort)
    run_id = next_job_id("headless")
    log_path = pub(workspace) / "log" / f"{run_id}.jsonl"
    prompt = f"Follow the instructions in ./{goal_file}"
    if campaign_id:
        campaign_jobs = route_builder_jobs(workspace, agent, campaign_id=str(campaign_id))[-8:]
        if campaign_jobs:
            job_facts = [
                {
                    "id": job.get("id"),
                    "status": job.get("status"),
                    "reason": job.get("reason"),
                    "returncode": job.get("returncode"),
                    "completion_mode": job.get("completion_mode"),
                    "finished_at": job.get("finished_at"),
                    "log": job.get("log"),
                }
                for job in campaign_jobs
            ]
            prompt += (
                "\n\nRuntime-authoritative Job facts for this Campaign follow. "
                "They override stale Notebook descriptions. A terminal Job is already finished: "
                "inspect its result and continue the scheduled role; do not hand it off as running.\n"
                + json.dumps(job_facts, ensure_ascii=False, indent=2, sort_keys=True)
            )
    run = {
        "id": run_id,
        "status": "starting",
        "agent": agent,
        "goal_file": goal_file,
        "runner_action": status.get("runner_action"),
        "prompt": prompt,
        "model": selected_model,
        "model_reasoning_effort": selected_effort,
        "thread_id": None,
        "cwd": rel(workspace, agent_dir),
        "log": rel(workspace, log_path),
        "created_at": now(),
        "reason": None,
        "returncode": None,
        "supervisor_pid": None,
        "pid": None,
        "resources": free_run_resources(load_resource_config(workspace), agent),
        "campaign_id": campaign_id,
        "loop_state_before": read_json(agent_dir / ".discovery" / "loop_state.json", {}),
    }
    upsert_headless_run(workspace, run)
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_headless_goal", run_id], cwd=workspace, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    update_headless_run(workspace, run_id, {"status": "running", "supervisor_pid": proc.pid, "started_at": now()})
    run.update({"status": "running", "supervisor_pid": proc.pid})
    return {
        "ok": True,
        "run": run_id,
        "agent": agent,
        "goal_file": goal_file,
        "model": selected_model,
        "model_reasoning_effort": selected_effort,
        "thread_id": None,
        "log": rel(workspace, log_path),
    }


def launch_dashboard_headless_campaign(
    workspace: Path,
    agent: str,
    model: str = "",
    reasoning_effort: str = "",
    iterations: Any = 1,
    *,
    stage_configs: Any = None,
) -> dict[str, Any]:
    assert_problem_runtime_exclusive(workspace)
    if not AGENT_NAME_RE.fullmatch(agent):
        raise SystemExit("invalid agent name")
    agent_dir = workspace / agent
    if not agent_dir.is_dir():
        raise SystemExit(f"agent workspace not found: {agent}")
    ensure_route_broker_token(agent_dir)
    if not route_broker_is_available(workspace):
        raise SystemExit("Route broker is unavailable; restart Discovery Dashboard")
    if latest_active_headless_campaign(workspace, agent) is not None:
        raise SystemExit(f"{agent} already has an active Headless campaign")
    if latest_active_headless_run(workspace, agent) is not None:
        raise SystemExit(f"{agent} already has a running Headless Codex goal")
    status_rows = build_dashboard_agent_statuses(workspace, [agent])
    status = status_rows[0] if status_rows else {}
    if not status.get("should_start_codex"):
        raise SystemExit(f"{agent} is not startable now: {status.get('status_label') or status.get('phase')}")
    selected_stages = resolve_headless_stage_configs(
        workspace,
        stage_configs,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    # Keep the legacy pair for old readers.  New supervisors always use the
    # role-specific frozen mapping below.
    selected_model = selected_stages["auditor"]["model"]
    selected_effort = selected_stages["auditor"]["reasoning_effort"]
    target_iterations = validate_campaign_iterations(iterations)
    loop_state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
    campaign_id = next_job_id("campaign")
    campaign = {
        "id": campaign_id,
        "status": "starting",
        "agent": agent,
        "model": selected_model,
        "model_reasoning_effort": selected_effort,
        "stage_configs": selected_stages,
        "target_iterations": target_iterations,
        "completed_iterations": 0,
        "completed_versions": [],
        "start_version": loop_state.get("last_reflected_version"),
        "current_version": loop_state.get("last_reflected_version"),
        "current_stage": status.get("runner_action"),
        "active_run_id": None,
        "last_processed_run_id": None,
        "waiting_route_jobs": [],
        "debug_attempts": 0,
        "max_debug_attempts": 3,
        "no_progress_attempts": 0,
        "max_no_progress_attempts": 1,
        "created_at": now(),
        "updated_at": now(),
        "started_at": None,
        "finished_at": None,
        "reason": None,
        "supervisor_pid": None,
    }
    upsert_headless_campaign(workspace, campaign)
    try:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_headless_campaign", campaign_id],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        update_headless_campaign(
            workspace,
            campaign_id,
            {"status": "failed", "reason": "campaign_supervisor_launch_failed", "error": str(exc), "finished_at": now()},
        )
        raise SystemExit(f"failed to launch Headless campaign: {exc}") from None
    update_headless_campaign(
        workspace,
        campaign_id,
        {"status": "running", "supervisor_pid": proc.pid, "started_at": now(), "updated_at": now()},
    )
    return {
        "ok": True,
        "campaign": campaign_id,
        "agent": agent,
        "iterations": target_iterations,
        "model": selected_model,
        "model_reasoning_effort": selected_effort,
        "stage_configs": selected_stages,
    }


def signal_headless_process(run: dict[str, Any], sig: signal.Signals) -> None:
    pgid = run.get("pgid")
    pid = run.get("pid")
    try:
        if isinstance(pgid, int):
            os.killpg(pgid, sig)
        elif isinstance(pid, int):
            os.kill(pid, sig)
        else:
            raise SystemExit("headless run has no pid/pgid yet")
    except ProcessLookupError:
        raise SystemExit("headless process is no longer running") from None
    except PermissionError:
        raise SystemExit("permission denied while signaling headless process") from None
    except OSError as exc:
        raise SystemExit(f"failed to signal headless process: {exc}") from None


def campaign_builder_jobs(workspace: Path, agent: str, campaign_id: str) -> list[dict[str, Any]]:
    return [
        job
        for job in route_builder_jobs(workspace, agent, campaign_id=campaign_id)
        if job.get("status") in {"queued", "starting", "running", "paused"}
    ]


def signal_route_run(job: dict[str, Any], sig: signal.Signals) -> bool:
    target = job.get("pgid") if isinstance(job.get("pgid"), int) else job.get("pid")
    if not isinstance(target, int):
        return False
    try:
        os.killpg(target, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def control_dashboard_headless_goal(workspace: Path, agent: str, action: str) -> dict[str, Any]:
    if not AGENT_NAME_RE.fullmatch(agent):
        raise SystemExit("invalid agent name")
    if action not in {"pause", "resume", "stop"}:
        raise SystemExit("agent control action must be pause, resume, or stop")
    campaign = latest_active_headless_campaign(workspace, agent)
    run = latest_active_headless_run(workspace, agent)
    if campaign is not None:
        campaign_id = str(campaign.get("id") or "")
        campaign_status = str(campaign.get("status") or "")
        builder_jobs = campaign_builder_jobs(workspace, agent, campaign_id)
        if action == "pause":
            if campaign_status != "running":
                raise SystemExit(f"cannot pause Headless campaign in status {campaign_status}")
            update_headless_campaign(workspace, campaign_id, {"status": "paused", "paused_at": now(), "reason": "paused_by_user", "updated_at": now()})
            # Codex must remain able to receive a terminal tool result.  A
            # pause prevents future fresh Turns; it never SIGSTOPs Codex.
            for builder_job in builder_jobs:
                if builder_job.get("status") == "running" and signal_route_run(builder_job, signal.SIGSTOP):
                    update_job(workspace, str(builder_job.get("id") or ""), {"status": "paused", "paused_at": now(), "reason": "campaign_paused"})
        elif action == "resume":
            if campaign_status != "paused":
                raise SystemExit(f"cannot resume Headless campaign in status {campaign_status}")
            for builder_job in builder_jobs:
                if builder_job.get("status") == "paused" and signal_route_run(builder_job, signal.SIGCONT):
                    update_job(workspace, str(builder_job.get("id") or ""), {"status": "running", "resumed_at": now(), "reason": None})
            update_headless_campaign(workspace, campaign_id, {"status": "running", "resumed_at": now(), "reason": None, "updated_at": now()})
        else:
            if campaign_status not in {"running", "paused"}:
                raise SystemExit(f"cannot stop Headless campaign in status {campaign_status}")
            update_headless_campaign(workspace, campaign_id, {"status": "stopped", "stopped_at": now(), "finished_at": now(), "reason": "stopped_by_user", "updated_at": now()})
            if run is not None and run.get("status") in {"running", "paused"}:
                if run.get("status") == "paused":
                    try:
                        signal_headless_process(run, signal.SIGCONT)
                    except SystemExit:
                        pass
                kill_process_group(run.get("pgid") if isinstance(run.get("pgid"), int) else run.get("pid"))
                update_headless_run(workspace, str(run.get("id") or ""), {"status": "stopped", "stopped_at": now(), "finished_at": now(), "reason": "campaign_stopped", "returncode": None})
            for builder_job in builder_jobs:
                cancel_job(workspace, str(builder_job.get("id") or ""), emit=False)
        updated_campaign = get_headless_campaign(workspace, campaign_id)
        return {"ok": True, "agent": agent, "action": action, "campaign": campaign_id, "status": updated_campaign.get("status")}
    if run is None:
        raise SystemExit(f"{agent} has no active Headless goal or campaign")
    run_id = str(run.get("id") or "")
    status = str(run.get("status") or "")
    if action == "pause":
        raise SystemExit("pause is available for a Headless campaign; it never suspends an active Codex process")
    elif action == "resume":
        raise SystemExit("resume is available for a Headless campaign")
    else:
        if status not in {"running", "paused"}:
            raise SystemExit(f"cannot stop headless run in status {status}")
        if status == "paused":
            try:
                signal_headless_process(run, signal.SIGCONT)
            except SystemExit:
                pass
        kill_process_group(run.get("pgid") if isinstance(run.get("pgid"), int) else run.get("pid"))
        update_headless_run(workspace, run_id, {"status": "stopped", "stopped_at": now(), "finished_at": now(), "reason": "stopped_by_user", "returncode": None})
    updated = get_headless_run(workspace, run_id)
    return {"ok": True, "agent": agent, "action": action, "run": run_id, "status": updated.get("status")}


ACTIVE_DASHBOARD_WORKER_STATUSES = {"starting", "running", "draining"}
_ROUTE_SANDBOX_REPORT: dict[str, Any] | None = None


def route_sandbox_report(*, refresh: bool = False) -> dict[str, Any]:
    """Probe the namespace primitive used by Codex before any Route is launched."""
    global _ROUTE_SANDBOX_REPORT
    if _ROUTE_SANDBOX_REPORT is not None and not refresh:
        return dict(_ROUTE_SANDBOX_REPORT)
    command = ["bwrap", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "/usr/bin/true"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        detail = (result.stderr or result.stdout or "").strip()
        available = result.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        detail = str(exc)
        available = False
    _ROUTE_SANDBOX_REPORT = {
        "available": available,
        "backend": "bubblewrap",
        "detail": detail,
        "remediation": (
            "Enable the distribution-provided AppArmor profile /etc/apparmor.d/bwrap-userns-restrict "
            "or otherwise permit unprivileged user namespaces; never fall back to danger-full-access."
            if not available
            else ""
        ),
    }
    return dict(_ROUTE_SANDBOX_REPORT)


def dashboard_worker_state_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "dashboard_worker.json"


def read_dashboard_worker_state(workspace: Path) -> dict[str, Any]:
    state = read_json(dashboard_worker_state_path(workspace), {})
    if not isinstance(state, dict) or not state:
        return {
            "status": "stopped",
            "pid": None,
            "current_job": None,
            "active_jobs": [],
            "reason": "not_started",
            "log": ".DiscoveryConsole/pub/log/dashboard-worker.log",
        }
    status = str(state.get("status") or "stopped")
    if status in ACTIVE_DASHBOARD_WORKER_STATUSES and not process_alive(state.get("pid")):
        state.update(
            {
                "status": "stopped" if state.get("stop_requested_at") else "failed",
                "current_job": None,
                "active_jobs": [],
                "finished_at": state.get("finished_at") or now(),
                "reason": "stop_requested" if state.get("stop_requested_at") else "worker_exited",
            }
        )
        write_json(dashboard_worker_state_path(workspace), state)
    return state


def update_dashboard_worker_for_pid(workspace: Path, pid: int, updates: dict[str, Any]) -> None:
    state = read_json(dashboard_worker_state_path(workspace), {})
    if not isinstance(state, dict) or state.get("pid") != pid:
        return
    state.update(updates)
    write_json(dashboard_worker_state_path(workspace), state)


def managed_worker_stop_requested(workspace: Path, pid: int) -> bool:
    state = read_json(dashboard_worker_state_path(workspace), {})
    return isinstance(state, dict) and state.get("pid") == pid and bool(state.get("stop_requested_at"))


def launch_dashboard_worker(workspace: Path) -> dict[str, Any]:
    assert_problem_runtime_exclusive(workspace)
    state = read_dashboard_worker_state(workspace)
    if state.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES:
        raise SystemExit(f"dashboard worker is already {state.get('status')}")
    log_path = pub(workspace) / "log" / "dashboard-worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "_worker", "--poll-seconds", "2"]
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    state = {
        "status": "running",
        "pid": proc.pid,
        "current_job": None,
        "active_jobs": [],
        "started_at": now(),
        "finished_at": None,
        "stop_requested_at": None,
        "reason": None,
        "command": command,
        "log": rel(workspace, log_path),
    }
    write_json(dashboard_worker_state_path(workspace), state)
    return {"ok": True, "action": "start", "worker": state}


def control_dashboard_worker(workspace: Path, action: str) -> dict[str, Any]:
    if action == "start":
        return launch_dashboard_worker(workspace)
    if action != "stop":
        raise SystemExit("worker control action must be start or stop")
    state = read_dashboard_worker_state(workspace)
    if state.get("status") not in ACTIVE_DASHBOARD_WORKER_STATUSES:
        raise SystemExit("dashboard worker is not running")
    active_campaigns = [
        str(row.get("id") or row.get("agent") or "campaign")
        for row in read_headless_campaigns(workspace)
        if row.get("status") in {"starting", "running", "paused"}
    ]
    worker_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
    if active_campaigns and route_broker_is_available(workspace, expected_pid=worker_pid):
        raise SystemExit("cannot stop Discovery Runtime while Headless campaigns are active: " + ", ".join(active_campaigns))
    if not state.get("stop_requested_at"):
        state.update({"status": "draining", "stop_requested_at": now(), "reason": "stop_requested"})
        write_json(dashboard_worker_state_path(workspace), state)
    return {"ok": True, "action": "stop", "worker": state}


def dashboard_job_summary(
    workspace: Path,
    job: dict[str, Any],
    queue_position: int | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(job.get("status") or "unknown")
    resources = job.get("resources", {}) if isinstance(job.get("resources"), dict) else {}
    runtime_snapshot = dict(runtime) if isinstance(runtime, dict) else None
    if runtime_snapshot is not None and status in TERMINAL_JOB_STATUSES:
        runtime_snapshot.update({"activity": status, "process_alive": False, "supervisor_alive": False})
    return {
        "id": job.get("id"),
        "agent": job.get("agent"),
        "kind": job.get("kind") or job.get("launcher") or "job",
        "status": status,
        "can_cancel": status in {"queued", "starting", "running", "paused"},
        "reason": job.get("reason"),
        "command": job.get("display_command") or command_display(list(job.get("command", []))),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "last_activity_at": (runtime_snapshot or {}).get("sampled_at") or job.get("finished_at") or job.get("started_at") or job.get("created_at"),
        "completion_mode": job.get("completion_mode"),
        "wait_deadline": job.get("wait_deadline"),
        "wait_timed_out_at": job.get("wait_timed_out_at"),
        "queue_position": queue_position,
        "worker_pid": job.get("worker_pid"),
        "pid": job.get("pid"),
        "resources": {
            "cpus": resources.get("cpus"),
            "memory_gb": resources.get("memory_gb"),
            "gpus": resources.get("gpus", []),
            "timeout_seconds": resources.get("timeout_seconds"),
        },
        "log": job.get("log"),
        "log_tail": summarize_job_log(workspace, job, 30) if status in {"running", "failed"} else "",
        "runtime": runtime_snapshot,
    }


def build_dashboard_worker_payload(workspace: Path) -> dict[str, Any]:
    with with_resource_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        refresh_all_jobs(workspace)
        resource_state = read_resource_state(workspace)
        available, host_pressure = resource_availability(workspace, resource_state)
        jobs = read_jsonl(job_index(workspace))
    queued = [job for job in jobs if job.get("status") == "queued"]
    running = [job for job in jobs if job.get("status") in {"starting", "running"}]
    terminal = [job for job in jobs if job.get("status") in TERMINAL_JOB_STATUSES]
    queue_positions = {str(job.get("id")): index + 1 for index, job in enumerate(queued)}
    visible_jobs = [*running, *queued, *list(reversed(terminal[-12:]))]
    worker = read_dashboard_worker_state(workspace)
    runtime_by_job = worker.get("job_runtime", {}) if isinstance(worker.get("job_runtime"), dict) else {}
    if isinstance(worker.get("pid"), int):
        active_job_ids = [str(job.get("id")) for job in running]
        worker["active_jobs"] = active_job_ids
        worker["current_job"] = active_job_ids[0] if active_job_ids else None
    worker_log = str(worker.get("log") or "")
    worker_log_tail = ""
    if worker_log:
        try:
            worker_log_path = require_under(workspace / worker_log, pub(workspace) / "log", "dashboard worker log")
            worker_log_tail = tail_text(worker_log_path, 30)
        except SystemExit:
            worker_log_tail = ""
    return {
        "generated_at": now(),
        "worker": {
            **worker,
            "active": worker.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES,
            "can_start": worker.get("status") not in ACTIVE_DASHBOARD_WORKER_STATUSES,
            "can_stop": worker.get("status") in {"starting", "running"},
            "log_tail": worker_log_tail,
        },
        "counts": {"queued": len(queued), "running": len(running), "terminal": len(terminal)},
        "available": available,
        "machine": host_resources(),
        "capacity": queue_capacity(load_resource_config(workspace)),
        "host_pressure": {
            "external_busy_gpus": host_pressure.get("external_busy_gpus", []),
            "cpu_load": host_pressure.get("cpu_load", {}),
            "memory_available_gb": host_pressure.get("memory_available_gb"),
            "nvidia_smi_available": host_pressure.get("nvidia_smi_available"),
            "gpu_details": host_pressure.get("gpu_details", {}),
            "gpu_compute_apps": host_pressure.get("gpu_compute_apps", []),
        },
        "leases": running_leases(workspace, resource_state),
        "jobs": [
            dashboard_job_summary(
                workspace,
                job,
                queue_positions.get(str(job.get("id"))),
                runtime_by_job.get(str(job.get("id"))),
            )
            for job in visible_jobs
        ],
    }


def build_dashboard_knowledge_payload(workspace: Path, scope: str = "problem") -> dict[str, Any]:
    if scope == "topic":
        owner = topic_root(workspace)
        root = program_root(owner) / "knowledge"
        scope_id = str(read_problem_registry(owner).get("topic_id") or owner.name)
    else:
        owner = workspace
        root = knowledge_root(workspace)
        scope_id = current_problem_id(workspace)
    knowledge = load_knowledge(root)
    items = sorted(
        [dict(item) for item in knowledge.get("items", {}).values()],
        key=lambda row: str(row.get("id") or ""),
    )
    topics = sorted(
        [dict(topic) for topic in knowledge.get("topics", {}).values()],
        key=lambda row: str(row.get("id") or ""),
    )
    memory_logs = read_memory_logs(root) if scope == "topic" else []
    integrity = unified_knowledge_integrity_report(
        owner,
        root=root,
        versions_workspace=None if scope == "topic" else workspace,
    )
    contract = {} if scope == "topic" else query_contract(workspace)
    baselines = [] if scope == "topic" else load_dashboard_baseline_rows(workspace)[0]
    external_query = knowledge_query.browse(root=root, scope_kind=scope, scope_id=scope_id, view="external", limit=100, contract=contract, baseline_rows=baselines)
    practice_query = knowledge_query.browse(root=root, scope_kind=scope, scope_id=scope_id, view="practice", limit=100, contract=contract, baseline_rows=baselines)
    return {
        "generated_at": now(),
        "scope": scope,
        "scope_id": scope_id,
        "integrity": integrity,
        "items": items,
        "topics": topics,
        "memory_logs": memory_logs,
        # Dashboard rendering consumes these same query envelopes as both CLIs;
        # legacy keys above remain only for its existing detail components.
        "external_query": external_query,
        "practice_query": practice_query,
    }


@contextmanager
def route_broker_lock(workspace: Path, agent: str):
    lock_path = private(workspace) / "broker_locks" / f"{safe_id(agent, 'agent name')}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def route_broker_action(workspace: Path, data: dict[str, Any], credential: str) -> dict[str, Any]:
    route = safe_id(str(data.get("route") or ""), "Route id")
    if not AGENT_NAME_RE.fullmatch(route):
        raise SystemExit("invalid Route id")
    agent_dir = require_under(workspace / route, workspace, "Route workspace")
    if not agent_dir.is_dir():
        raise SystemExit(f"Route workspace not found: {route}")
    expected = ensure_route_broker_token(agent_dir)
    if not credential or not hmac.compare_digest(expected, credential):
        raise SystemExit("invalid Route broker credential")
    action = str(data.get("action") or "")
    # Read-only requests and foreground development runs must remain available
    # concurrently.  In particular, a long run.local request must not hold the
    # Route's control-state lock and make context/knowledge look unavailable.
    if action == "context":
        result = build_route_context(
            workspace,
            agent_dir,
            argparse.Namespace(
                job=str(data.get("job") or ""),
                limit=int(data.get("limit") or 10),
            ),
        )
    elif action == "knowledge.browse":
        view = str(data.get("view") or "")
        metric = str(data.get("metric") or "")
        sort = str(data.get("sort") or "")
        route_filter = str(data.get("route_filter") or "")
        validate_knowledge_browse("problem", view, metric, sort, route_filter, query_contract(workspace))
        try:
            result = knowledge_query.browse(
                root=knowledge_root(workspace), scope_kind="problem", scope_id=current_problem_id(workspace), view=view,
                query=str(data.get("query") or ""), metric=metric or None, sort=sort or None, route=route_filter or None,
                limit=int(data.get("limit") or 20), contract=query_contract(workspace), baseline_rows=load_dashboard_baseline_rows(workspace)[0],
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    elif action == "knowledge.show":
        ref = str(data.get("ref") or "")
        if "/" in ref:
            raise SystemExit("Route knowledge show accepts only unqualified local references")
        try:
            result = knowledge_query.show(root=knowledge_root(workspace), scope_kind="problem", scope_id=current_problem_id(workspace), ref=ref, contract=query_contract(workspace), baseline_rows=load_dashboard_baseline_rows(workspace)[0])
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    elif action == "run.local":
        command = data.get("command")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise SystemExit("foreground Route command must be an argv array")
        result = cmd_route_run_local(
            workspace,
            agent_dir,
            argparse.Namespace(
                resources=str(data.get("resources") or ""),
                command=command,
                completion=str(data.get("completion") or ""),
                defer_wait=True,
                headless_run_id=str(data.get("headless_run_id") or ""),
                campaign_id=str(data.get("campaign_id") or ""),
            ),
            emit=False,
        )
    else:
        # These operations mutate Route/control state and remain serialized.
        with route_broker_lock(workspace, route):
            if action == "eval":
                result = cmd_eval(
                    workspace,
                    agent_dir,
                    argparse.Namespace(message=str(data.get("message") or ""), candidate=str(data.get("candidate") or "")),
                    emit=False,
                )
            elif action == "reflect":
                result = cmd_reflect(
                    workspace,
                    agent_dir,
                    argparse.Namespace(
                        version=str(data.get("version") or ""),
                        summary_file=str(data.get("summary_file") or ""),
                        note_file=str(data.get("note_file") or ""),
                        next_plan_file=str(data.get("next_plan_file") or ""),
                    ),
                    emit=False,
                )
            elif action == "run.queued":
                command = data.get("command")
                if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
                    raise SystemExit("queued Route command must be an argv array")
                result = cmd_submit(
                    workspace,
                    agent_dir,
                    argparse.Namespace(
                        resources=str(data.get("resources") or ""),
                        command=command,
                        completion=str(data.get("completion") or ""),
                        defer_wait=True,
                        headless_run_id=str(data.get("headless_run_id") or ""),
                        campaign_id=str(data.get("campaign_id") or ""),
                    ),
                    emit=False,
                )
            else:
                raise SystemExit(f"unsupported Route broker action: {action}")
    return {"ok": True, "action": action, "route": route, "result": result}


def dashboard_main_action(workspace: Path, data: dict[str, Any]) -> dict[str, Any]:
    action = str(data.get("action") or "")
    if action == "notice.add":
        args = argparse.Namespace(
            notice_cmd="add",
            id=str(data.get("id") or ""),
            priority=str(data.get("priority") or "high"),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            tag=[str(tag) for tag in data.get("tags", [])] if isinstance(data.get("tags"), list) else [],
        )
        cmd_notice(workspace, workspace, args, emit=False)
        return {"ok": True, "action": action, "dashboard": build_dashboard_payload(workspace)}
    if action == "notice.delete":
        cmd_notice(workspace, workspace, argparse.Namespace(notice_cmd="delete", id=str(data.get("id") or "")), emit=False)
        return {"ok": True, "action": action, "dashboard": build_dashboard_payload(workspace)}
    if action == "job.cancel":
        cancel_job(workspace, str(data.get("job_id") or ""), emit=False)
        return {"ok": True, "action": action, "worker": build_dashboard_worker_payload(workspace)}
    raise SystemExit(f"unsupported Dashboard action: {action}")


def build_baseline_best_metric_row(
    workspace: Path,
    baseline_rows: list[dict[str, Any]],
    metric_names: list[str],
    directions: dict[str, str],
) -> dict[str, Any] | None:
    if not baseline_rows:
        return None
    metrics: dict[str, float] = {}
    metric_sources: dict[str, dict[str, Any]] = {}
    for metric in metric_names:
        direction = directions.get(metric, "higher")
        best = dashboard_best_version(baseline_rows, metric, direction)
        if not best:
            continue
        value = dashboard_numeric_metric(best, metric)
        if value is None:
            continue
        tied_methods = sorted(
            str(row.get("method") or row.get("id"))
            for row in baseline_rows
            if (candidate := dashboard_numeric_metric(row, metric)) is not None
            and math.isclose(candidate, value, rel_tol=1e-12, abs_tol=1e-12)
        )
        metrics[metric] = value
        metric_sources[metric] = {
            "method": tied_methods[0],
            "methods": tied_methods,
            "value": value,
            "direction": direction,
        }
    if not metrics:
        return None
    source_rows = [
        row for row in baseline_rows
        if str(row.get("method") or row.get("id")) in {
            str(method)
            for source in metric_sources.values()
            for method in source.get("methods", [source["method"]])
        }
    ]
    source_spaces = {str(row.get("space") or "") for row in source_rows if row.get("space")}
    source_digests = {str(row.get("contract_digest") or "") for row in source_rows if row.get("contract_digest")}
    evidence_space = next(iter(source_spaces)) if len(source_spaces) == 1 else "mixed"
    contract_digest = next(iter(source_digests)) if len(source_digests) == 1 else None
    created_at = max((str(row.get("created_at")) for row in baseline_rows if row.get("created_at")), default=None)
    return {
        "id": "baseline:best_per_metric",
        "agent": "baseline",
        "method": "baseline_best_per_metric",
        "method_kind": "baseline_best_per_metric",
        "row_type": "baseline",
        "created_at": created_at,
        "summary": "Best valid Baseline value per metric; each value retains its source method",
        "space": evidence_space,
        "contract_digest": contract_digest,
        "comparison_eligible": True,
        "metrics": metrics,
        "metric_validity": {
            metric: {
                "status": "valid",
                "reason": "Selected from the reviewed valid value of "
                + ", ".join(metric_sources[metric]["methods"]),
            }
            for metric in metrics
        },
        "metric_directions": {metric: directions.get(metric, "higher") for metric in metrics},
        "metric_sources": metric_sources,
        "eval_feedback": None,
        "eval_run": {
            "id": "baseline_best_per_metric",
            "log": "",
            "returncode": 0,
            "source_summary": "Synthetic view assembled only from reviewed valid Baseline metric values.",
        },
        "snapshot": None,
        "note": json.dumps(metric_sources, ensure_ascii=False, indent=2, sort_keys=True),
        "next_plan": "",
        "path": rel(workspace, pub(workspace) / "baseline" / "baselines.json"),
    }


def dashboard_version_sort_key(version: dict[str, Any]) -> tuple[str, str]:
    return (str(version.get("created_at") or ""), str(version.get("id") or ""))


def dashboard_metric_names(versions: list[dict[str, Any]]) -> list[str]:
    seen = set()
    names: list[str] = []
    for metric in KEY_DASHBOARD_METRICS:
        if any(dashboard_numeric_metric(version, metric) is not None for version in versions):
            names.append(metric)
            seen.add(metric)
    extras = sorted({metric for version in versions for metric in dashboard_numeric_metrics(version)} - seen)
    return [*names, *extras]


def dashboard_numeric_metrics(version: dict[str, Any]) -> dict[str, float]:
    metrics = version.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    return {
        str(k): float(v)
        for k, v in metrics.items()
        if isinstance(v, (int, float))
        and not isinstance(v, bool)
        and baseline_metric_is_valid(version, str(k))
    }


def dashboard_numeric_metric(version: dict[str, Any], metric: str) -> float | None:
    metrics = version.get("metrics")
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(metric)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and baseline_metric_is_valid(version, metric):
        return float(value)
    return None


def baseline_metric_is_valid(row: dict[str, Any], metric: str) -> bool:
    if str(row.get("row_type") or "") != "baseline":
        return True
    if row.get("comparison_eligible") is False:
        return False
    review_map = row.get("metric_validity")
    if not isinstance(review_map, dict):
        return False
    review = review_map.get(metric)
    return isinstance(review, dict) and review.get("status") == "valid"


def dashboard_metric_directions(versions: list[dict[str, Any]], metric_names: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    directions: dict[str, str] = {}
    sources: dict[str, str] = {}
    for metric in metric_names:
        for version in reversed(versions):
            data = version.get("metric_directions")
            if isinstance(data, dict):
                direction = normalize_direction(str(data.get(metric, "")))
                if direction:
                    directions[metric] = direction
                    sources[metric] = "practice_metric_directions"
                    break
        if metric in directions:
            continue
        for version in reversed(versions):
            feedback = version.get("eval_feedback")
            if not isinstance(feedback, dict):
                continue
            feedback_metrics = feedback.get("metrics")
            if not isinstance(feedback_metrics, dict):
                continue
            metric_feedback = feedback_metrics.get(metric)
            if isinstance(metric_feedback, dict):
                direction = normalize_direction(str(metric_feedback.get("direction", "")))
                if direction:
                    directions[metric] = direction
                    sources[metric] = "eval_feedback"
                    break
        if metric not in directions:
            directions[metric] = "higher"
            sources[metric] = "default_higher"
    return directions, sources


def dashboard_metric_stats(versions: list[dict[str, Any]], metric_names: list[str], directions: dict[str, str]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for metric in metric_names:
        values = [value for version in versions if (value := dashboard_numeric_metric(version, metric)) is not None]
        if not values:
            continue
        direction = directions.get(metric, "higher")
        stats[metric] = {
            "min": min(values),
            "max": max(values),
            "best": max(values) if direction == "higher" else min(values),
            "worst": min(values) if direction == "higher" else max(values),
            "count": len(values),
        }
    return stats


def normalize_dashboard_metric(value: float, stats: dict[str, Any], direction: str) -> float | None:
    min_value = stats.get("min")
    max_value = stats.get("max")
    if not isinstance(min_value, (int, float)) or not isinstance(max_value, (int, float)):
        return None
    span = float(max_value) - float(min_value)
    if span <= 0:
        return 0.5
    if direction == "lower":
        return (float(max_value) - value) / span
    return (value - float(min_value)) / span


def latest_version_ids_by_agent(versions: list[dict[str, Any]]) -> dict[str, str]:
    latest: dict[str, dict[str, Any]] = {}
    for version in versions:
        agent = str(version.get("agent", ""))
        if not agent:
            continue
        if agent not in latest or dashboard_version_sort_key(version) >= dashboard_version_sort_key(latest[agent]):
            latest[agent] = version
    return {agent: str(version.get("id", "")) for agent, version in latest.items()}


def dashboard_previous_values(versions: list[dict[str, Any]], metric_names: list[str]) -> dict[tuple[str, str], float | None]:
    previous: dict[tuple[str, str], float | None] = {}
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for version in versions:
        by_agent.setdefault(str(version.get("agent", "")), []).append(version)
    for rows in by_agent.values():
        rows.sort(key=dashboard_version_sort_key)
        last: dict[str, float] = {}
        for version in rows:
            version_id = str(version.get("id", ""))
            for metric in metric_names:
                previous[(version_id, metric)] = last.get(metric)
            for metric, value in dashboard_numeric_metrics(version).items():
                last[metric] = value
    return previous


def dashboard_rank(rows: list[dict[str, Any]], version_id: str, metric: str, direction: str) -> dict[str, int]:
    current = next((row for row in rows if row.get("id") == version_id), None)
    current_value = dashboard_numeric_metric(current or {}, metric)
    metric_rows = [row for row in rows if dashboard_numeric_metric(row, metric) is not None]
    if current_value is None:
        return {"rank": 0, "of": len(metric_rows)}
    better = 0
    for row in metric_rows:
        value = dashboard_numeric_metric(row, metric)
        if value is not None and is_better(value, current_value, direction):
            better += 1
    return {"rank": better + 1, "of": len(metric_rows)}


def dashboard_best_version(rows: list[dict[str, Any]], metric: str, direction: str) -> dict[str, Any] | None:
    metric_rows = [row for row in rows if dashboard_numeric_metric(row, metric) is not None]
    if not metric_rows:
        return None
    return sorted(metric_rows, key=lambda row: dashboard_numeric_metric(row, metric) or 0.0, reverse=direction == "higher")[0]


def summarize_dashboard_best(version: dict[str, Any] | None, metric: str) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "version": version.get("id"),
        "agent": version.get("agent"),
        "value": dashboard_numeric_metric(version, metric),
        "summary": version.get("summary", ""),
    }


def is_dashboard_improvement(delta: float, direction: str) -> bool:
    return delta > 0 if direction == "higher" else delta < 0


def is_dashboard_regression(delta: float, direction: str) -> bool:
    return delta < 0 if direction == "higher" else delta > 0


class FileRouteBrokerServer:
    """Worker-owned broker using Route-local request/response files."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.stop_event = threading.Event()

    def prepare(self) -> None:
        for route in sorted(path for path in self.workspace.iterdir() if path.is_dir() and AGENT_NAME_RE.fullmatch(path.name)):
            root = route_broker_file_root(route)
            (root / "requests").mkdir(parents=True, exist_ok=True)
            (root / "responses").mkdir(parents=True, exist_ok=True)

    def _handle(self, route: Path, processing: Path, request_id: str) -> None:
        try:
            if processing.is_symlink() or not processing.is_file() or processing.stat().st_size > 1024 * 1024:
                raise SystemExit("Route broker request is invalid or exceeds 1 MiB")
            data = json.loads(processing.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise SystemExit("Route broker request must be a JSON object")
            if str(data.get("problem") or "") != current_problem_id(self.workspace):
                raise SystemExit("Route broker Problem id mismatch")
            data["route"] = route.name
            payload = route_broker_action(self.workspace, data, str(data.get("token") or ""))
        except SystemExit as exc:
            payload = {"ok": False, "error": str(exc)}
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        finally:
            processing.unlink(missing_ok=True)
        write_json(route_broker_file_root(route) / "responses" / f"{request_id}.json", payload)

    def serve_forever(self) -> None:
        self.prepare()
        while not self.stop_event.wait(0.05):
            for route in sorted(path for path in self.workspace.iterdir() if path.is_dir() and AGENT_NAME_RE.fullmatch(path.name)):
                request_dir = route_broker_file_root(route) / "requests"
                request_dir.mkdir(parents=True, exist_ok=True)
                for request_path in request_dir.glob("*.json"):
                    request_id = request_path.stem
                    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
                        request_path.unlink(missing_ok=True)
                        continue
                    processing = request_path.with_suffix(".processing")
                    try:
                        request_path.replace(processing)
                    except OSError:
                        continue
                    threading.Thread(
                        target=self._handle,
                        args=(route, processing, request_id),
                        name=f"discovery-route-request-{route.name}-{request_id[:8]}",
                        daemon=True,
                    ).start()

    def shutdown(self) -> None:
        self.stop_event.set()

    def server_close(self) -> None:
        return


def start_route_broker_server(workspace: Path) -> tuple[FileRouteBrokerServer, threading.Thread]:
    server = FileRouteBrokerServer(workspace)
    server.prepare()
    thread = threading.Thread(target=server.serve_forever, name=f"discovery-route-broker-{current_problem_id(workspace)}", daemon=True)
    thread.start()
    return server, thread


def dashboard_cookie_authorized(cookie_header: str, session_token: str) -> bool:
    for part in cookie_header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == "discovery_session":
            return secrets.compare_digest(value, session_token)
    return False


def serve_dashboard(workspace: Path, host: str, port: int, refresh_seconds: float, no_browser: bool) -> None:
    topic = topic_root(workspace)
    session_token = secrets.token_urlsafe(32)

    class DashboardHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.route_request(send_body=True)

        def do_HEAD(self) -> None:
            self.route_request(send_body=False)

        def do_POST(self) -> None:
            if not dashboard_cookie_authorized(self.headers.get("Cookie", ""), session_token):
                self.send_error(403, "dashboard session required")
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in {"/api/agent-run", "/api/agent-control", "/api/worker-control", "/api/main-action"}:
                self.send_error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
                if not isinstance(data, dict):
                    raise ValueError("request body must be a JSON object")
                request_workspace = resolve_problem_workspace(topic, workspace, str(data.get("problem") or ""))
                if parsed.path == "/api/agent-run":
                    payload = launch_dashboard_headless_campaign(
                        request_workspace,
                        str(data.get("agent", "")),
                        str(data.get("model", "")),
                        str(data.get("reasoning_effort", "")),
                        data.get("iterations", 1),
                        stage_configs=data.get("stage_configs"),
                    )
                elif parsed.path == "/api/agent-control":
                    payload = control_dashboard_headless_goal(request_workspace, str(data.get("agent", "")), str(data.get("action", "")))
                elif parsed.path == "/api/main-action":
                    payload = dashboard_main_action(request_workspace, data)
                else:
                    payload = control_dashboard_worker(request_workspace, str(data.get("action", "")))
                self.send_json(payload)
            except SystemExit as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)

        def route_request(self, send_body: bool) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            supplied_token = str((query.get("token") or [""])[0])
            if path in {"/", "/index.html"} and supplied_token and secrets.compare_digest(supplied_token, session_token):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"discovery_session={session_token}; HttpOnly; SameSite=Strict; Path=/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if not dashboard_cookie_authorized(self.headers.get("Cookie", ""), session_token):
                self.send_error(403, "dashboard session required")
                return
            try:
                request_workspace = resolve_problem_workspace(topic, workspace, str((query.get("problem") or [""])[0]))
            except SystemExit as exc:
                if path.startswith("/api/"):
                    self.send_json({"ok": False, "error": str(exc)}, status=400, send_body=send_body)
                else:
                    self.send_error(400, str(exc))
                return
            if path in {"/", "/index.html"}:
                self.send_text(DASHBOARD_HTML, "text/html; charset=utf-8", send_body=send_body)
            elif path == "/dashboard.css":
                self.send_text(DASHBOARD_CSS, "text/css; charset=utf-8", send_body=send_body)
            elif path == "/dashboard.js":
                self.send_text(DASHBOARD_JS, "application/javascript; charset=utf-8", send_body=send_body)
            elif path == "/api/dashboard.json":
                payload = build_dashboard_payload(request_workspace, refresh_seconds=refresh_seconds)
                self.send_json(payload, send_body=send_body)
            elif path == "/api/worker-status.json":
                payload = build_dashboard_worker_payload(request_workspace)
                self.send_json(payload, send_body=send_body)
            elif path == "/api/knowledge.json":
                scope = "topic" if str((query.get("scope") or ["problem"])[0]) == "topic" else "problem"
                payload = build_dashboard_knowledge_payload(request_workspace, scope)
                self.send_json(payload, send_body=send_body)
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404, "not found")

        def send_text(self, text: str, content_type: str, send_body: bool = True) -> None:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def send_json(self, data: dict[str, Any], send_body: bool = True, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("dashboard: " + format % args + "\n")

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server: ThreadingHTTPServer | None = None
    selected_port = port
    last_error: OSError | None = None
    for candidate in range(port, port + 50):
        try:
            server = ThreadingHTTPServer((host, candidate), DashboardHandler)
            selected_port = candidate
            break
        except OSError as exc:
            last_error = exc
    if server is None:
        raise SystemExit(f"could not bind dashboard on {host}:{port}-{port + 49}: {last_error}")
    display_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
    url = f"http://{display_host}:{selected_port}"
    authenticated_url = f"{url}/?token={urllib.parse.quote(session_token)}"
    if not route_broker_is_available(workspace):
        server.server_close()
        raise SystemExit("Discovery Runtime Route Broker is unavailable; restart `./discovery start`")
    runtime_stop = threading.Event()

    def runtime_watchdog() -> None:
        missing_broker_checks = 0
        while not runtime_stop.wait(2.0):
            state = read_dashboard_worker_state(workspace)
            pid = state.get("pid") if isinstance(state.get("pid"), int) else None
            healthy = (
                state.get("status") in ACTIVE_DASHBOARD_WORKER_STATUSES
                and process_alive(pid)
                and route_broker_is_available(workspace, expected_pid=pid)
            )
            if healthy:
                missing_broker_checks = 0
                continue
            missing_broker_checks += 1
            if missing_broker_checks < 3:
                continue
            try:
                if process_alive(pid):
                    kill_process_group(pid)
                launched = launch_dashboard_worker(workspace)
                deadline = time.time() + 10
                launched_pid = int(launched["worker"]["pid"])
                while time.time() < deadline and not route_broker_is_available(workspace, expected_pid=launched_pid):
                    if runtime_stop.wait(0.1):
                        return
                if not route_broker_is_available(workspace, expected_pid=launched_pid):
                    raise RuntimeError("restarted Runtime did not publish its Route Broker")
                missing_broker_checks = 0
            except BaseException as exc:
                state = read_json(dashboard_worker_state_path(workspace), {})
                if isinstance(state, dict):
                    state["recovery_error"] = str(exc)
                    state["recovery_attempted_at"] = now()
                    write_json(dashboard_worker_state_path(workspace), state)

    watchdog = threading.Thread(
        target=runtime_watchdog,
        name=f"discovery-runtime-watchdog-{current_problem_id(workspace)}",
        daemon=True,
    )
    watchdog.start()
    if selected_port != port:
        print(f"port {port} was unavailable; using {selected_port}")
    print(f"Discovery dashboard for {workspace}")
    print(f"URL: {authenticated_url}")
    print("Press Ctrl-C to stop.")
    if not no_browser:
        try:
            webbrowser.open(authenticated_url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping dashboard")
    finally:
        runtime_stop.set()
        watchdog.join(timeout=3)
        server.server_close()


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Discovery Eval Dashboard</title>
  <link rel="stylesheet" href="/dashboard.css">
</head>
<body>
  <header class="topbar">
    <div class="status-grid">
      <div>
        <div class="label">Problem</div>
        <select id="problemSelect" class="select" aria-label="Problem"></select>
      </div>
      <div>
        <div class="label">Agents</div>
        <div id="agentCount" class="stat">0</div>
      </div>
      <div>
        <div class="label">Practice Versions</div>
        <div id="versionCount" class="stat">0</div>
      </div>
      <div>
        <div class="label">Baselines</div>
        <div id="baselineCount" class="stat">0</div>
      </div>
      <div>
        <div class="label">Server Time (UTC)</div>
        <div id="serverTime" class="mono truncate">-</div>
      </div>
      <button id="refreshButton" class="button">Refresh</button>
    </div>
  </header>

  <nav class="tabs" aria-label="Dashboard views">
    <button class="tab" data-view="control">Control</button>
    <button class="tab active" data-view="latest">Polygon</button>
    <button class="tab" data-view="trends">Trends & Rankings</button>
    <button class="tab" data-view="knowledge">Knowledge</button>
    <button class="tab" data-view="workers">Runtime & Queue</button>
  </nav>

  <main class="shell">
    <section id="mainPanel" class="main-panel"></section>
    <aside id="detailDrawer" class="drawer" aria-live="polite">
      <div class="drawer-empty">Select a version to inspect details.</div>
    </aside>
  </main>

  <script src="/dashboard.js"></script>
</body>
</html>
"""


DASHBOARD_CSS = """
:root {
  --bg: #f7f8fa;
  --panel: #ffffff;
  --line: #d9dee7;
  --text: #18202f;
  --muted: #657084;
  --green: #16834a;
  --red: #b23b3b;
  --yellow: #8d6a13;
  --blue: #2563eb;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
}

.topbar {
  border-bottom: 1px solid var(--line);
  background: #ffffff;
  padding: 12px 16px;
}

.status-grid {
  display: grid;
  grid-template-columns: minmax(190px, 1fr) 80px 130px 90px minmax(190px, 260px) auto;
  gap: 12px;
  align-items: end;
}

.label {
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0;
  margin-bottom: 3px;
}

.stat {
  font-size: 22px;
  font-weight: 650;
  line-height: 1;
}

.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.button, .tab, .select, .toggle {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text);
  border-radius: 6px;
  min-height: 32px;
}

.button, .tab, .toggle {
  cursor: pointer;
  padding: 7px 10px;
  font-weight: 600;
}

.button:hover, .tab:hover, .toggle:hover { border-color: #9aa7bb; }

.button:disabled, .tab:disabled, .toggle:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.tabs {
  display: flex;
  gap: 6px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
}

.tab.active {
  background: #1f2937;
  border-color: #1f2937;
  color: white;
}

.agent-status-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: #f8fafc;
}

.worker-section .agent-status-panel {
  padding: 0;
  border-bottom: 0;
  background: transparent;
}

.headless-launch-config {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(120px, 160px);
  gap: 10px;
  align-items: start;
  border-bottom: 1px solid var(--line);
  padding: 0 0 10px;
  min-width: 0;
}

.headless-launch-title {
  align-self: center;
  font-weight: 700;
  white-space: nowrap;
}

.headless-launch-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 32px;
  min-width: 0;
}

.headless-unified-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.headless-stage-list {
  grid-column: 1 / -1;
  display: grid;
  gap: 7px;
}

.headless-stage-config {
  display: grid;
  grid-template-columns: minmax(110px, 0.55fr) minmax(220px, 1.55fr) minmax(180px, 1fr);
  gap: 10px;
  align-items: end;
  padding: 8px 10px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 6px;
}

.headless-stage-name {
  align-self: center;
  font-weight: 650;
}

.headless-stage-purpose {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 400;
}

.headless-launch-field {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.headless-launch-field .select {
  width: 100%;
  max-width: none;
  min-height: 32px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--text);
}

.headless-launch-detail {
  grid-column: 1 / -1;
  align-self: center;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.35;
  min-width: 0;
}

.headless-launch-error {
  color: var(--red);
}

.agent-status-card {
  display: flex;
  flex-direction: column;
  gap: 9px;
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 6px;
  padding: 13px 14px;
  min-width: 0;
  min-height: 172px;
}

.agent-status-card .agent-name {
  font-weight: 700;
  font-size: 14px;
}

.agent-status-card-head {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
}

.agent-status-identity {
  display: flex;
  gap: 9px;
  align-items: center;
  min-width: 0;
  flex-wrap: wrap;
}

.agent-status-detail {
  color: #344054;
  font-size: 12px;
  line-height: 1.45;
  padding: 8px 9px;
  border-radius: 5px;
  background: #f8fafc;
  border: 1px solid #e7ebf0;
  overflow-wrap: anywhere;
  white-space: normal;
}

.campaign-progress {
  display: grid;
  gap: 5px;
}

.campaign-progress-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: #475569;
  font-size: 11px;
}

.campaign-stage-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  font-size: 11px;
}

.campaign-stage {
  border-radius: 999px;
  padding: 2px 6px;
  background: #eef2f6;
  color: #64748b;
}

.campaign-stage-done { background: #dff6e8; color: #12653b; }
.campaign-stage-active { background: #dbeafe; color: #1d4ed8; font-weight: 700; }
.campaign-stage-error { background: #fee2e2; color: #991b1b; }
.campaign-stage-arrow { color: #94a3b8; }

.campaign-progress-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #e2e8f0;
}

.campaign-progress-fill {
  height: 100%;
  border-radius: inherit;
  background: #2563eb;
  transition: width 180ms ease;
}

.status-pill {
  justify-self: start;
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.3;
}

.status-builder {
  color: #92400e;
  background: #fef3c7;
}

.status-auditor {
  color: #075985;
  background: #e0f2fe;
}

.status-running {
  color: #166534;
  background: #dcfce7;
}

.status-error {
  color: #991b1b;
  background: #fee2e2;
}

.status-neutral {
  color: #475569;
  background: #e2e8f0;
}

.agent-status-meta {
  color: var(--muted);
  font-size: 11px;
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
}

.agent-run-button {
  min-height: 28px;
  padding: 5px 8px;
  font-size: 11px;
}

.agent-control-buttons {
  display: flex;
  gap: 4px;
  align-items: center;
  justify-content: flex-end;
}

.agent-control-buttons .toggle {
  min-height: 28px;
  padding: 5px 8px;
  font-size: 11px;
}

.shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  min-height: calc(100vh - 109px);
}

.main-panel {
  min-width: 0;
  padding: 16px;
  overflow: auto;
}

.drawer {
  border-left: 1px solid var(--line);
  background: #fff;
  padding: 14px;
  overflow: auto;
  max-height: calc(100vh - 109px);
}

.drawer-empty { color: var(--muted); margin-top: 8px; }

.section-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 10px;
}

.section-head h1 {
  font-size: 18px;
  margin: 0;
}

.controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.select {
  padding: 6px 8px;
  max-width: 360px;
}

.table-wrap {
  border: 1px solid var(--line);
  background: var(--panel);
  overflow: auto;
}

.polygon-panel {
  border: 1px solid var(--line);
  background: var(--panel);
  margin-bottom: 14px;
  padding: 12px;
}

.version-slider-panel {
  border: 1px solid var(--line);
  background: var(--panel);
  margin-bottom: 14px;
  padding: 12px;
}

.version-slider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 8px;
}

.version-slider-control {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px;
  background: #fbfcfe;
  min-width: 0;
}

.version-slider-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 7px;
}

.version-slider-control input[type="range"] {
  width: 100%;
  margin: 2px 0 6px;
}

.viz-panel {
  border: 1px solid var(--line);
  background: var(--panel);
  margin-bottom: 14px;
  padding: 12px;
}

.viz-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 12px;
  align-items: start;
}

.trend-svg, .ranking-svg {
  width: 100%;
  height: auto;
  min-height: 340px;
  overflow: visible;
}

.ranking-svg {
  min-height: 520px;
}

.inline-controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.mini-label {
  color: var(--muted);
  font-size: 11px;
}

.series-table {
  display: grid;
  gap: 5px;
  max-height: 170px;
  overflow: auto;
}

.polygon-layout {
  display: grid;
  grid-template-columns: minmax(360px, 520px) minmax(260px, 1fr);
  gap: 12px;
  align-items: start;
}

.polygon-svg {
  width: 100%;
  height: auto;
  min-height: 390px;
  overflow: visible;
}

.polygon-controls {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}

.control-block {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px;
  background: #fbfcfe;
}

.control-title {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 7px;
}

.control-title span {
  display: inline-flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.control-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 6px;
  max-height: 220px;
  overflow: auto;
}

.knowledge-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 8px;
}

.check-row {
  display: flex;
  gap: 7px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 6px;
  background: #fbfcfe;
}

.swatch {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.hint {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
  margin: 0 0 8px;
}

.overview-mode .shell {
  grid-template-columns: minmax(0, 1fr);
}

.overview-mode .drawer {
  display: none;
}

.worker-mode .drawer {
  display: none;
}

.worker-mode .shell {
  grid-template-columns: minmax(0, 1fr);
}

.worker-page {
  display: grid;
  gap: 14px;
}

.worker-page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.worker-page-head h1 {
  margin: 0;
  font-size: 18px;
}

.worker-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.worker-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(110px, 1fr));
  border: 1px solid var(--line);
  background: var(--panel);
}

.worker-summary-item {
  min-width: 0;
  padding: 10px 12px;
  border-right: 1px solid var(--line);
}

.worker-summary-item:last-child {
  border-right: 0;
}

.worker-summary-value {
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.worker-section {
  min-width: 0;
}

.worker-section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 7px;
}

.worker-section-head h2 {
  margin: 0;
  font-size: 15px;
}

.worker-job-id {
  min-width: 190px;
  max-width: 260px;
  overflow-wrap: anywhere;
}

.worker-job-table {
  min-width: 1080px;
}

.worker-command {
  min-width: 280px;
  max-width: 520px;
  white-space: normal;
  overflow-wrap: anywhere;
}

.worker-log-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 10px;
}

.worker-log-panel {
  min-width: 0;
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 10px;
}

.worker-log-panel pre {
  margin: 8px 0 0;
  min-height: 100px;
  max-height: 280px;
}

.gpu-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
}

.gpu-card {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  padding: 11px;
}

.gpu-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.gpu-name {
  font-weight: 700;
  min-width: 0;
}

.gpu-meters {
  display: grid;
  gap: 8px;
}

.gpu-meter-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: var(--muted);
  font-size: 11px;
}

.gpu-meter-track {
  height: 7px;
  overflow: hidden;
  border-radius: 4px;
  background: #e5e9f0;
}

.gpu-meter-fill {
  height: 100%;
  background: var(--blue);
}

.gpu-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}

.gpu-fact-value {
  font-weight: 700;
  overflow-wrap: anywhere;
}

.gpu-process-list {
  display: grid;
  gap: 5px;
  padding-top: 9px;
}

.gpu-process-row {
  display: grid;
  grid-template-columns: minmax(80px, 0.8fr) 72px minmax(120px, 1.5fr) auto;
  gap: 7px;
  align-items: baseline;
  font-size: 11px;
  min-width: 0;
}

.gpu-process-row > * {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-done {
  color: #166534;
  background: #dcfce7;
}

.status-queued {
  color: #854d0e;
  background: #fef9c3;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}

th, td {
  border-bottom: 1px solid var(--line);
  padding: 7px 8px;
  text-align: left;
  vertical-align: top;
}

th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #eef2f7;
  color: #263143;
  font-size: 12px;
  white-space: nowrap;
}

th.sortable { cursor: pointer; }
tr.version-row { cursor: pointer; }
tr.version-row:hover { background: #f1f5f9; }

.summary-cell {
  min-width: 260px;
  max-width: 460px;
  white-space: normal;
}

.metric-cell {
  min-width: 116px;
  white-space: nowrap;
  border-left: 1px solid rgba(217, 222, 231, 0.7);
}

.badges {
  display: inline-flex;
  gap: 4px;
  margin-left: 6px;
  vertical-align: middle;
}

.badge {
  border-radius: 4px;
  padding: 1px 4px;
  font-size: 10px;
  font-weight: 700;
  background: #edf2f7;
  color: #344054;
}

.badge.best { background: #dff6e8; color: #12653b; }
.badge.own { background: #e8eefc; color: #27418b; }
.delta-up { color: var(--green); font-weight: 700; }
.delta-down { color: var(--red); font-weight: 700; }
.delta-flat { color: var(--muted); }

.empty {
  border: 1px solid var(--line);
  background: #fff;
  padding: 32px;
  color: var(--muted);
}

.drawer h2 {
  margin: 0 0 4px;
  font-size: 17px;
}

.drawer .sub {
  color: var(--muted);
  margin-bottom: 12px;
}

.drawer-section {
  border-top: 1px solid var(--line);
  padding-top: 10px;
  margin-top: 10px;
}

.drawer-section h3 {
  margin: 0 0 8px;
  font-size: 13px;
}

.kv {
  display: grid;
  grid-template-columns: 94px minmax(0, 1fr);
  gap: 5px 8px;
  margin-bottom: 8px;
}

.kv div:nth-child(odd) { color: var(--muted); }

pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #f6f8fb;
  border: 1px solid var(--line);
  padding: 8px;
  max-height: 220px;
  overflow: auto;
}

@media (max-width: 980px) {
  .status-grid { grid-template-columns: minmax(0, 1fr) 80px 120px; }
  .status-grid > * { min-width: 0; }
  .shell { grid-template-columns: 1fr; }
  .drawer { border-left: 0; border-top: 1px solid var(--line); max-height: none; }
  .polygon-layout { grid-template-columns: 1fr; }
  .viz-grid { grid-template-columns: 1fr; }
  .headless-stage-config { grid-template-columns: minmax(100px, 0.5fr) minmax(180px, 1.4fr) minmax(150px, 1fr); }
  .worker-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .worker-summary-item:nth-child(3) { border-right: 0; }
  .worker-summary-item:nth-child(-n+3) { border-bottom: 1px solid var(--line); }
}

@media (max-width: 640px) {
  .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .status-grid > :first-child { grid-column: 1 / -1; }
  .status-grid .button { justify-self: start; }
  .headless-launch-config { grid-template-columns: 1fr; align-items: stretch; }
  .headless-launch-title, .headless-launch-detail { grid-column: auto; }
  .headless-launch-title-row { align-items: flex-start; flex-direction: column; gap: 5px; }
  .headless-stage-list { grid-column: auto; }
  .headless-stage-config { grid-template-columns: 1fr; align-items: stretch; }
  .agent-status-panel { grid-template-columns: minmax(0, 1fr); }
  .worker-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .worker-summary-item:nth-child(3) { border-right: 1px solid var(--line); }
  .worker-summary-item:nth-child(even) { border-right: 0; }
  .worker-summary-item:nth-child(-n+4) { border-bottom: 1px solid var(--line); }
  .worker-log-grid { grid-template-columns: minmax(0, 1fr); }
  .gpu-grid { grid-template-columns: minmax(0, 1fr); }
  .gpu-process-row { grid-template-columns: minmax(70px, 0.8fr) 60px minmax(100px, 1.4fr) auto; }
}
"""


DASHBOARD_JS = r"""
const state = {
  data: null,
  problemId: new URLSearchParams(window.location.search).get("problem") || "",
  view: "latest",
  selectedId: null,
  sortMetric: null,
  sortField: null,
  sortDir: "desc",
  rankingMetric: null,
  polygonIds: null,
  polygonMetricIds: null,
  trendAgentIds: null,
  trendMetricIds: null,
  trendBaselineIds: null,
  trendScaleMode: "auto",
  agentVersionIndexes: null,
  headlessUnifiedStages: true,
  headlessStageConfigs: {
    auditor: {model: null, reasoning_effort: null},
    builder: {model: null, reasoning_effort: null},
    debug_eval: {model: null, reasoning_effort: null}
  },
  headlessIterations: 1,
  workerData: null,
  workerError: null,
  workerLoading: false,
  scrollPositions: {},
  knowledgeData: null,
  knowledgeError: null,
  knowledgeLoading: false,
  knowledgeScope: "problem",
  knowledgeQuery: ""
};

const mainPanel = document.getElementById("mainPanel");
const drawer = document.getElementById("detailDrawer");
let workerPollTimer = null;
let campaignPollTimer = null;
let deferredMainRender = false;
const problemSelect = document.getElementById("problemSelect");

function mainSelectIsActive() {
  const active = document.activeElement;
  return Boolean(active && mainPanel.contains(active) && active.matches("select"));
}

mainPanel.addEventListener("focusout", () => {
  if (!deferredMainRender) return;
  window.setTimeout(() => {
    if (!deferredMainRender || mainSelectIsActive()) return;
    deferredMainRender = false;
    render();
  }, 0);
});

function apiUrl(path) {
  if (!state.problemId) return path;
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}problem=${encodeURIComponent(state.problemId)}`;
}

problemSelect.addEventListener("change", () => {
  state.problemId = problemSelect.value;
  state.selectedId = null;
  state.rankingMetric = null;
  state.workerData = null;
  state.knowledgeData = null;
  const url = new URL(window.location.href);
  url.searchParams.set("problem", state.problemId);
  window.history.replaceState({}, "", url);
  loadData(false);
  if (state.view === "workers") loadWorkerData();
  if (state.view === "knowledge") loadKnowledgeData();
});

document.getElementById("refreshButton").addEventListener("click", () => {
  loadData(true);
  if (state.view === "workers") loadWorkerData();
  if (state.view === "knowledge") loadKnowledgeData();
});
document.querySelectorAll(".tab").forEach(button => {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab === button));
    render();
    setWorkerPolling(state.view === "workers");
    if (state.view === "knowledge") loadKnowledgeData();
  });
});

loadData(false);

async function loadData(keepSelection) {
  const response = await fetch(apiUrl("/api/dashboard.json"), {cache: "no-store"});
  if (!response.ok) throw new Error(`dashboard API failed: ${response.status}`);
  state.data = await response.json();
  state.problemId = state.data.problem_id || state.problemId;
  syncHeadlessSelection();
  if (!keepSelection) state.selectedId = null;
  if (!state.rankingMetric) {
    state.rankingMetric = metricNames()[0] || "";
  }
  updateStatus();
  if (keepSelection && mainSelectIsActive()) {
    deferredMainRender = true;
  } else {
    deferredMainRender = false;
    render();
  }
  scheduleCampaignRefresh();
  if (!keepSelection && state.data.refresh_seconds > 0) {
    window.setInterval(() => loadData(true), state.data.refresh_seconds * 1000);
  }
}

function scheduleCampaignRefresh() {
  if (campaignPollTimer) {
    window.clearTimeout(campaignPollTimer);
    campaignPollTimer = null;
  }
  const active = (state.data?.agent_statuses || []).some(status => Boolean(status.headless_campaign));
  if (!active) return;
  campaignPollTimer = window.setTimeout(() => {
    campaignPollTimer = null;
    loadData(true).catch(() => scheduleCampaignRefresh());
  }, 5000);
}

function updateStatus() {
  const problems = state.data.problems || [];
  problemSelect.innerHTML = problems.map(problem => `<option value="${escapeHtml(problem.id || "")}">${escapeHtml(problem.title || problem.id || "")}</option>`).join("");
  problemSelect.value = state.problemId;
  problemSelect.disabled = problems.length <= 1;
  document.getElementById("agentCount").textContent = state.data.agent_count ?? state.data.agents.length;
  document.getElementById("versionCount").textContent = state.data.practice_version_count ?? state.data.versions.length;
  document.getElementById("baselineCount").textContent = state.data.baseline_count ?? 0;
  document.getElementById("serverTime").textContent = formatServerTime(state.data.generated_at) || "-";
}

function renderAgentStatusPanel() {
  const panel = document.getElementById("agentStatusPanel");
  if (!panel) return;
  const statuses = state.data.agent_statuses || [];
  const launchConfig = headlessLaunchConfigHtml();
  if (!statuses.length) {
    panel.innerHTML = `${launchConfig}<div class="agent-status-card"><span class="status-pill status-neutral">No agents</span><span class="agent-status-meta">No route workspaces found.</span></div>`;
    bindHeadlessLaunchConfig(panel);
    return;
  }
  panel.innerHTML = launchConfig + statuses.map(status => `
    <div class="agent-status-card">
      <div class="agent-status-card-head">
        <div class="agent-status-identity">
          <div class="agent-name mono">${escapeHtml(status.agent || "")}</div>
          <span class="status-pill ${agentStatusClass(status)}">${escapeHtml(status.status_label || status.phase || "unknown")}</span>
        </div>
        ${agentControlHtml(status)}
      </div>
      <div class="agent-status-meta"><strong>Next role:</strong> ${escapeHtml(status.waiting_for || "-")}</div>
      ${agentCampaignLabel(status) ? `<div class="agent-status-meta"><strong>Campaign:</strong> ${escapeHtml(agentCampaignLabel(status))}</div>` : ""}
      ${agentCampaignProgressHtml(status)}
      ${agentActivityMeta(status)}
      <div class="agent-status-meta"><strong>Codex:</strong> ${escapeHtml(agentRunConfigLabel(status) || "No previous Headless run")}</div>
      <div class="agent-status-detail">${escapeHtml(status.status_detail || "No additional status detail.")}</div>
    </div>
  `).join("");
  bindHeadlessLaunchConfig(panel);
  panel.querySelectorAll("[data-agent-run]").forEach(button => {
    button.addEventListener("click", () => startAgentGoal(button.dataset.agentRun, button));
  });
  panel.querySelectorAll("[data-agent-control]").forEach(button => {
    button.addEventListener("click", () => controlAgentGoal(button.dataset.agentControl, button.dataset.agentAction, button));
  });
}

function agentActivityMeta(status) {
  const source = status.active_route_run || status.active_queued_route_job || status.headless_run || status.active_eval || {};
  const runtime = source.runtime || {};
  const started = source.started_at || source.created_at || null;
  const activity = source.last_activity_at || source.usage_recorded_at || source.started_at || source.created_at || null;
  if (!started && !activity) return "";
  const parts = [];
  if (started) parts.push(`elapsed ${relativeElapsed(started)}`);
  if (activity) parts.push(`last activity ${formatDate(activity)}`);
  if (runtime.activity) parts.push(runtime.activity);
  if (runtime.cpu_percent != null) parts.push(`${formatNumber(runtime.cpu_percent)}% CPU (${formatNumber(runtime.cpu_cores)} cores)`);
  if (runtime.memory_current_gb != null) parts.push(`${formatNumber(runtime.memory_current_gb)} GB RAM`);
  if (runtime.output_quiet_seconds != null) parts.push(`stdout quiet ${Math.floor(Number(runtime.output_quiet_seconds))}s`);
  return `<div class="agent-status-meta"><strong>Activity:</strong> ${escapeHtml(parts.join(" · "))}</div>`;
}

function relativeElapsed(value) {
  const parsed = Date.parse(value || "");
  if (!Number.isFinite(parsed)) return "-";
  const seconds = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function syncHeadlessSelection() {
  const config = state.data?.headless_model_config || {};
  const models = config.models || [];
  for (const stage of ["auditor", "builder", "debug_eval"]) {
    const selection = state.headlessStageConfigs[stage] || {};
    let model = models.find(item => item.id === selection.model);
    if (!model) {
      selection.model = config.default_model || models[0]?.id || "";
      model = models.find(item => item.id === selection.model);
      selection.reasoning_effort = selection.model === config.default_model ? config.default_reasoning_effort : null;
    }
    const efforts = model?.reasoning_efforts || [];
    if (!efforts.some(item => item.id === selection.reasoning_effort)) {
      selection.reasoning_effort = model?.default_reasoning_effort || efforts[0]?.id || "";
    }
    state.headlessStageConfigs[stage] = selection;
  }
  if (state.headlessUnifiedStages) copyHeadlessStageSelection("auditor");
}

function copyHeadlessStageSelection(sourceStage) {
  const source = state.headlessStageConfigs[sourceStage] || {model: "", reasoning_effort: ""};
  for (const stage of ["auditor", "builder", "debug_eval"]) {
    state.headlessStageConfigs[stage] = {...source};
  }
}

function headlessStageRowHtml(stage, label, purpose) {
  const config = state.data?.headless_model_config || {};
  const models = config.models || [];
  const selection = state.headlessStageConfigs[stage] || {};
  const selectedModel = models.find(item => item.id === selection.model);
  const efforts = selectedModel?.reasoning_efforts || [];
  const disabled = models.length ? "" : "disabled";
  return `<div class="headless-stage-config">
    <div class="headless-stage-name">${escapeHtml(label)}<span class="headless-stage-purpose">${escapeHtml(purpose)}</span></div>
    <label class="headless-launch-field">
      <span class="label">Model</span>
      <select class="select" data-stage-model="${escapeHtml(stage)}" ${disabled}>
        ${models.map(model => `<option value="${escapeHtml(model.id)}" ${model.id === selection.model ? "selected" : ""}>${escapeHtml(model.label || model.id)}</option>`).join("")}
      </select>
    </label>
    <label class="headless-launch-field">
      <span class="label">Reasoning</span>
      <select class="select" data-stage-reasoning="${escapeHtml(stage)}" ${disabled || !efforts.length ? "disabled" : ""}>
        ${efforts.map(level => `<option value="${escapeHtml(level.id)}" ${level.id === selection.reasoning_effort ? "selected" : ""}>${escapeHtml(level.label || level.id)}</option>`).join("")}
      </select>
    </label>
  </div>`;
}

function headlessLaunchConfigHtml() {
  const config = state.data?.headless_model_config || {};
  const launchHint = "Settings are frozen when a Campaign starts. Each successful formal Version counts as one iteration.";
  const detail = [launchHint, config.error || ""].filter(Boolean).join(" ");
  const stageRows = state.headlessUnifiedStages
    ? headlessStageRowHtml("auditor", "All stages", "Auditor · Builder · Debug Eval")
    : [
        headlessStageRowHtml("auditor", "Auditor", "Audit evidence and set the next target"),
        headlessStageRowHtml("builder", "Builder", "Explore, implement, and submit"),
        headlessStageRowHtml("debug_eval", "Debug Eval", "Repair a failed formal submission")
      ].join("");
  return `
    <div class="headless-launch-config">
      <div class="headless-launch-title-row">
        <div class="headless-launch-title">Next Headless Launch</div>
        <label class="headless-unified-toggle"><input id="headlessUnifiedStagesInput" type="checkbox" ${state.headlessUnifiedStages ? "checked" : ""}> Use one setup for all stages</label>
      </div>
      <label class="headless-launch-field">
        <span class="label">Iterations</span>
        <input id="headlessIterationsInput" class="select" type="number" min="1" max="20" step="1" value="${escapeHtml(state.headlessIterations || 1)}">
      </label>
      <div class="headless-stage-list">${stageRows}</div>
      <div class="headless-launch-detail ${config.error ? "headless-launch-error" : ""}" title="${escapeHtml(detail)}">${escapeHtml(detail)}</div>
    </div>`;
}

function bindHeadlessLaunchConfig(panel) {
  const unifiedInput = panel.querySelector("#headlessUnifiedStagesInput");
  const iterationsInput = panel.querySelector("#headlessIterationsInput");
  unifiedInput?.addEventListener("change", () => {
    state.headlessUnifiedStages = unifiedInput.checked;
    if (state.headlessUnifiedStages) copyHeadlessStageSelection("auditor");
    renderAgentStatusPanel();
  });
  panel.querySelectorAll("[data-stage-model]").forEach(select => {
    select.addEventListener("change", () => {
      const stage = select.dataset.stageModel;
      const selection = state.headlessStageConfigs[stage] || {};
      selection.model = select.value;
      const model = (state.data.headless_model_config?.models || []).find(item => item.id === selection.model);
      selection.reasoning_effort = model?.default_reasoning_effort || model?.reasoning_efforts?.[0]?.id || "";
      state.headlessStageConfigs[stage] = selection;
      if (state.headlessUnifiedStages) copyHeadlessStageSelection(stage);
      renderAgentStatusPanel();
    });
  });
  panel.querySelectorAll("[data-stage-reasoning]").forEach(select => {
    select.addEventListener("change", () => {
      const stage = select.dataset.stageReasoning;
      state.headlessStageConfigs[stage].reasoning_effort = select.value;
      if (state.headlessUnifiedStages) copyHeadlessStageSelection(stage);
      renderAgentStatusPanel();
    });
  });
  iterationsInput?.addEventListener("change", () => {
    const parsed = Number.parseInt(iterationsInput.value, 10);
    state.headlessIterations = Number.isFinite(parsed) ? Math.max(1, Math.min(20, parsed)) : 1;
    renderAgentStatusPanel();
  });
}

function agentCampaignLabel(status) {
  const campaign = status.headless_campaign;
  if (campaign) {
    const progress = campaign.progress || {};
    return `Iteration ${progress.current_iteration || 1}/${progress.target_iterations || campaign.target_iterations || 1} · ${progress.stage_label || campaign.current_stage || "Starting"}`;
  }
  const last = status.last_headless_campaign;
  if (!last) return "";
  const completed = Number(last.completed_iterations || 0);
  const target = Number(last.target_iterations || 0);
  const reason = last.reason ? ` · ${last.reason}` : "";
  return `last ${last.status || "unknown"} ${completed}/${target}${reason}`;
}

function agentCampaignProgressHtml(status) {
  const campaign = status.headless_campaign;
  const progress = campaign?.progress || currentRouteProgress(status);
  if (!progress) return "";
  const detail = campaign
    ? `Version goal ${progress.completed_versions || 0} / ${progress.target_iterations || campaign.target_iterations || 1}`
    : `Current loop${status.last_version ? ` · ${status.last_version}` : ""}`;
  const stages = Array.isArray(progress.stages) ? progress.stages : [];
  return `<div class="campaign-progress" aria-label="Campaign ${escapeHtml(detail)}">
    <div class="campaign-progress-head"><strong>${escapeHtml(detail)}</strong></div>
    <div class="campaign-stage-row">${stages.map(stage => `<span class="campaign-stage campaign-stage-${escapeHtml(stage.state || "pending")}">${escapeHtml(stage.label || "Stage")}</span>`).join('<span class="campaign-stage-arrow">→</span>')}</div>
  </div>`;
}

function currentRouteProgress(status) {
  const phase = status.phase || "";
  const evalStatus = status.eval_status || "";
  if (phase === "done") {
    return {stages: ["Auditor", "Builder", "Evaluation"].map(label => ({label, state: "done"}))};
  }
  if (phase === "reflection_loop") {
    return {stages: [
      {label: "Auditor", state: "active"},
      {label: "Builder", state: "done"},
      {label: "Evaluation", state: "done"},
    ]};
  }
  if (["queued", "running"].includes(evalStatus)) {
    return {stages: [
      {label: "Auditor", state: "done"},
      {label: "Builder", state: "done"},
      {label: "Evaluation", state: "active"},
    ]};
  }
  if (["main_review", "failed"].includes(evalStatus)) {
    return {stages: [
      {label: "Auditor", state: "done"},
      {label: "Builder", state: "done"},
      {label: "Evaluation", state: "error"},
      {label: "Main review", state: "active"},
    ]};
  }
  if (evalStatus === "check_failed") {
    return {stages: [
      {label: "Auditor", state: "done"},
      {label: "Builder", state: "done"},
      {label: "Debug Eval", state: "active"},
      {label: "Evaluation", state: "pending"},
    ]};
  }
  if (phase === "work_loop") {
    return {stages: [
      {label: "Auditor", state: status.last_reflected_version ? "done" : "pending"},
      {label: "Builder", state: "active"},
      {label: "Evaluation", state: "pending"},
    ]};
  }
  return null;
}

function agentRunConfigLabel(status) {
  const active = status.headless_run;
  if (active?.model) {
    const prefix = active.status === "paused" || status.status_label === "Codex paused" ? "Paused" : "Running";
    return `${prefix}: ${active.model} / ${active.model_reasoning_effort || "default"}${headlessUsageLabel(active)}`;
  }
  const last = status.last_headless_run;
  if (!last?.model) return "";
  return `Last run: ${last.model} / ${last.model_reasoning_effort || "default"}${headlessUsageLabel(last)}`;
}

function headlessUsageLabel(run) {
  const usage = run?.usage;
  if (!usage || !Number.isFinite(Number(usage.input_tokens))) return "";
  const input = Number(usage.input_tokens);
  const cached = Number.isFinite(Number(usage.cached_input_tokens)) ? Number(usage.cached_input_tokens) : null;
  const nonCached = cached === null ? null : Math.max(0, input - cached);
  const output = Number.isFinite(Number(usage.output_tokens)) ? Number(usage.output_tokens) : null;
  const ratio = cached === null || input <= 0 ? null : `${(100 * cached / input).toFixed(1)}% cache`;
  const parts = [`in ${input}`];
  if (cached !== null) parts.push(`cached ${cached}`, `new ${nonCached}`);
  if (output !== null) parts.push(`out ${output}`);
  if (ratio) parts.push(ratio);
  return ` · ${parts.join(", ")}`;
}

function headlessStageConfigsAreValid() {
  return ["auditor", "builder", "debug_eval"].every(stage => {
    const selection = state.headlessStageConfigs[stage] || {};
    return Boolean(selection.model && selection.reasoning_effort);
  });
}

function headlessLaunchConfigLabel() {
  if (!headlessStageConfigsAreValid()) return "Select model settings";
  if (state.headlessUnifiedStages) {
    const selection = state.headlessStageConfigs.auditor;
    return `${selection.model} / ${selection.reasoning_effort} for all stages`;
  }
  return "stage-specific model settings";
}

function agentControlHtml(status) {
  const agent = escapeHtml(status.agent || "");
  const buttons = [];
  if (status.should_start_codex) {
    const disabled = headlessStageConfigsAreValid() ? "" : "disabled";
    const launchConfig = headlessLaunchConfigLabel();
    const iterations = Math.max(1, Number(state.headlessIterations || 1));
    buttons.push(`<button class="toggle agent-run-button" data-agent-run="${agent}" title="Start ${escapeHtml(String(iterations))} iteration(s) with ${escapeHtml(launchConfig)}" ${disabled}>Start${iterations > 1 ? ` ×${iterations}` : ""}</button>`);
  }
  if (status.can_pause_codex) {
    buttons.push(`<button class="toggle" data-agent-control="${agent}" data-agent-action="pause">Pause</button>`);
  }
  if (status.can_resume_codex) {
    buttons.push(`<button class="toggle" data-agent-control="${agent}" data-agent-action="resume">Resume</button>`);
  }
  if (status.can_stop_codex) {
    buttons.push(`<button class="toggle" data-agent-control="${agent}" data-agent-action="stop">Stop</button>`);
  }
  return buttons.length ? `<div class="agent-control-buttons">${buttons.join("")}</div>` : "";
}

async function startAgentGoal(agent, button) {
  if (!agent || !button) return;
  button.disabled = true;
  button.textContent = "Starting";
  try {
    const response = await fetch("/api/agent-run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        problem: state.problemId,
        agent,
        iterations: state.headlessIterations,
        stage_configs: state.headlessStageConfigs
      })
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `start failed: ${response.status}`);
    }
    await loadData(true);
  } catch (error) {
    button.textContent = "Error";
    button.title = String(error);
  } finally {
    window.setTimeout(() => loadData(true), 1500);
  }
}

async function controlAgentGoal(agent, action, button) {
  if (!agent || !action || !button) return;
  button.disabled = true;
  button.textContent = action === "pause" ? "Pausing" : (action === "resume" ? "Resuming" : "Stopping");
  try {
    const response = await fetch("/api/agent-control", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({problem: state.problemId, agent, action})
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `${action} failed: ${response.status}`);
    }
    await loadData(true);
  } catch (error) {
    button.textContent = "Error";
    button.title = String(error);
  } finally {
    window.setTimeout(() => loadData(true), 1000);
  }
}

function agentStatusClass(status) {
  if (status.headless_campaign?.status === "paused") return "status-neutral";
  if (status.headless_campaign) return "status-running";
  if (["failed", "blocked"].includes(status.last_headless_campaign?.status)) return "status-error";
  if (status.status_label === "Eval failed, debug needed" || status.status_label === "Eval check failed") return "status-error";
  if (status.status_label === "Codex paused") return "status-neutral";
  if (status.status_label === "Eval running" || status.status_label === "Eval queued") return "status-running";
  if (status.completed_stage === "builder") return "status-builder";
  if (status.completed_stage === "auditor") return "status-auditor";
  return "status-neutral";
}

function render() {
  if (!state.data) return;
  captureScrollPositions();
  try {
  const workerMode = state.view === "workers";
  document.body.classList.toggle("worker-mode", workerMode);
  document.body.classList.toggle("overview-mode", state.view === "latest" || state.view === "trends" || state.view === "knowledge" || state.view === "control");
  if (workerMode) {
    renderWorkers();
    renderDrawer(null);
    return;
  }
  if (state.view === "knowledge") {
    renderKnowledge();
    renderDrawer(null);
    return;
  }
  if (state.view === "control") {
    renderControl();
    renderDrawer(null);
    return;
  }
  if (!state.data.versions.length) {
    mainPanel.innerHTML = `<div class="empty">No practice versions found in this workspace.</div>`;
    renderDrawer(null);
    return;
  }
  if (state.view === "latest") renderLatest();
  if (state.view === "trends") renderTrendsRankings();
  if (state.view === "all") renderVersionTable("All Versions", state.data.versions, keyMetrics());
  if (state.view === "rankings") renderRankings();
  if (state.view === "latest" || state.view === "trends") {
    renderDrawer(null);
  } else {
    renderDrawer(selectedVersion());
  }
  } finally {
    restoreScrollPositions();
  }
}

function renderControl() {
  const metadata = state.data.problem_metadata || {};
  const contract = state.data.evaluation_contract || {};
  const routeSandbox = state.data.route_sandbox || {};
  const notices = state.data.notices || [];
  mainPanel.innerHTML = `<div class="worker-page">
    <div class="worker-page-head"><div><h1>Research Control</h1><div class="agent-status-meta mono">${escapeHtml(state.data.problem_id || "")}</div></div>
      <span class="status-pill ${contract.configured ? "status-done" : "status-queued"}">${contract.configured ? "Evaluator active" : "Evaluator inactive"}</span></div>
    <div class="worker-summary">
      ${workerSummaryItem("Problem status", metadata.status || "unknown")}
      ${workerSummaryItem("Evidence level", contract.evidence_level || "unset")}
      ${workerSummaryItem("Routes", state.data.agent_count || 0)}
      ${workerSummaryItem("Route sandbox", routeSandbox.available ? "ready" : "blocked")}
    </div>
    ${routeSandbox.available ? "" : `<div class="headless-launch-error"><strong>Route sandbox unavailable.</strong> ${escapeHtml(routeSandbox.detail || routeSandbox.remediation || "Bubblewrap preflight failed")}</div>`}
    <section class="worker-section"><div class="worker-section-head"><h2>Headless Routes</h2></div>
      <div id="agentStatusPanel" class="agent-status-panel" aria-label="Headless Route controls"></div>
    </section>
    <section class="worker-section"><div class="worker-section-head"><h2>Main Notices</h2><button class="toggle" id="addNotice">Add notice</button></div>
      <div class="knowledge-cards">${notices.length ? notices.map(notice => `<article class="control-block"><strong>${escapeHtml(notice.title || notice.id)}</strong><p class="hint">${escapeHtml(notice.body || "")}</p><div class="inline-controls"><span class="mono agent-status-meta">${escapeHtml(notice.id)}</span><button class="toggle" data-delete-notice="${escapeHtml(notice.id)}">Delete</button></div></article>`).join("") : `<div class="empty">No Main Notices.</div>`}</div>
    </section>
  </div>`;
  renderAgentStatusPanel();
  document.getElementById("addNotice")?.addEventListener("click", () => { const id = window.prompt("Notice id"); if (!id) return; const title = window.prompt("Notice title"); if (!title) return; const body = window.prompt("Notice body"); if (!body) return; runControlAction({action: "notice.add", id, title, body, priority: "high", tags: []}); });
  mainPanel.querySelectorAll("[data-delete-notice]").forEach(button => button.addEventListener("click", () => runControlAction({action: "notice.delete", id: button.dataset.deleteNotice})));
}

async function runControlAction(payload) {
  try {
    const response = await fetch("/api/main-action", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({problem: state.problemId, ...payload})});
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.error || `action failed: ${response.status}`);
    await loadData(true);
  } catch (error) {
    window.alert(String(error));
  }
}

async function loadKnowledgeData() {
  if (state.knowledgeLoading) return;
  state.knowledgeLoading = true;
  try {
    const response = await fetch(apiUrl(`/api/knowledge.json?scope=${encodeURIComponent(state.knowledgeScope)}`), {cache: "no-store"});
    if (!response.ok) throw new Error(`knowledge API failed: ${response.status}`);
    state.knowledgeData = await response.json();
    state.knowledgeError = null;
  } catch (error) {
    state.knowledgeError = String(error);
  } finally {
    state.knowledgeLoading = false;
    if (state.view === "knowledge") renderKnowledge();
  }
}

async function postMainAction(payload) {
  const response = await fetch("/api/main-action", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({problem: state.problemId, scope: state.knowledgeScope, ...payload})
  });
  const result = await response.json();
  if (!response.ok || result.ok === false) throw new Error(result.error || `action failed: ${response.status}`);
  if (result.knowledge) state.knowledgeData = result.knowledge;
  if (state.view === "knowledge") renderKnowledge();
  return result;
}

function renderKnowledge() {
  const data = state.knowledgeData;
  if (!data) {
    mainPanel.innerHTML = `<div class="empty">${escapeHtml(state.knowledgeError || "Loading knowledge...")}</div>`;
    return;
  }
  const externalSections = (data.external_query && data.external_query.sections) || [];
  const externalCards = externalSections.flatMap(section => section.cards || []);
  const items = externalCards.filter(card => card.entity_type === "item");
  const topics = externalCards.filter(card => card.entity_type === "topic");
  const practiceSections = (data.practice_query && data.practice_query.sections) || [];
  const practiceByRef = new Map(practiceSections.flatMap(section => section.cards || []).map(card => [card.ref, card]));
  const baselines = [...practiceByRef.values()].filter(card => card.entity_type === "baseline");
  const versions = [...practiceByRef.values()].filter(card => card.entity_type === "version");
  const memoryLogs = data.memory_logs || [];
  const query = (state.knowledgeQuery || "").trim().toLowerCase();
  const matchesQuery = row => !query || JSON.stringify(row).toLowerCase().includes(query);
  const visibleItems = items.filter(matchesQuery);
  const visibleTopics = topics.filter(matchesQuery);
  const visibleMemoryLogs = memoryLogs.filter(matchesQuery);
  const visibleBaselines = baselines.filter(matchesQuery);
  const visibleVersions = versions.filter(matchesQuery);
  const integrity = data.integrity || {};
  const metricBrief = card => Object.entries(card.metric_cards || {}).slice(0, 4).map(([name, metric]) => `${name}: ${formatNumber(metric.value)}`).join(" · ");
  mainPanel.innerHTML = `<div class="worker-page">
    <div class="worker-page-head"><div><h1>Knowledge</h1><div class="agent-status-meta">${state.knowledgeScope === "topic" ? "Items · Topics · Memory Logs" : "Items · Topics · Baselines · Versions"}</div></div>
      <div class="inline-controls"><select id="knowledgeScope" class="select"><option value="problem" ${state.knowledgeScope === "problem" ? "selected" : ""}>Problem</option><option value="topic" ${state.knowledgeScope === "topic" ? "selected" : ""}>Research Topic</option></select><input id="knowledgeSearch" class="select" value="${escapeHtml(state.knowledgeQuery || "")}" placeholder="Filter items, topics, and memory logs"><span class="status-pill ${integrity.ok ? "status-done" : "status-error"}">${integrity.ok ? "consistent" : `${(integrity.issues || []).length} issues`}</span></div></div>
    <section class="worker-section"><div class="worker-section-head"><h2>Knowledge Topics</h2><span>${visibleTopics.length}/${topics.length}</span></div>
      <div class="knowledge-cards">${visibleTopics.map(topic => `<article class="control-block"><strong>${escapeHtml(topic.title || topic.id)}</strong><div class="mono agent-status-meta">${escapeHtml(topic.id)}</div>
      <p class="hint">${escapeHtml(topic.lead || topic.text || "No synthesis")}</p><div class="agent-status-meta">${escapeHtml((topic.item_refs || topic.items || []).join(", "))} · cited ${escapeHtml(topic.reference_count || 0)}</div>
      </article>`).join("")}</div>
    </section>
    <section class="worker-section"><div class="worker-section-head"><h2>Popular Items</h2><span>${visibleItems.length}/${items.length}</span></div>
      <div class="table-wrap"><table><thead><tr><th>Item</th><th>Title</th><th>Path</th><th>Summary</th></tr></thead><tbody>
      ${visibleItems.map(item => `<tr><td class="mono">${escapeHtml(item.ref || item.id)}</td><td>${escapeHtml(item.title || "")}</td><td class="mono">${escapeHtml((item.locator || {}).path || item.path || "")}</td><td>${escapeHtml(item.summary || "")}</td></tr>`).join("")}
      </tbody></table></div>
    </section>
    ${state.knowledgeScope === "topic" ? `<section class="worker-section"><div class="worker-section-head"><h2>Memory Logs</h2><span>${visibleMemoryLogs.length}/${memoryLogs.length}</span></div>
      <div class="knowledge-cards">${visibleMemoryLogs.map(memory => `<article class="control-block"><strong>${escapeHtml(memory.summary || memory.id)}</strong><div class="mono agent-status-meta">${escapeHtml(memory.id)}</div>
      <p class="hint">${escapeHtml(memory.report || "")}</p></article>`).join("")}</div>
    </section>` : `<section class="worker-section"><div class="worker-section-head"><h2>Baseline Group</h2><span>${visibleBaselines.length}/${baselines.length}</span></div>
      <div class="knowledge-cards">${visibleBaselines.map(card => `<article class="control-block"><strong>${escapeHtml(card.title || card.id)}</strong><div class="mono agent-status-meta">${escapeHtml(card.ref || "")}</div>
      <p class="hint">${escapeHtml(card.summary || "")}</p><div class="agent-status-meta">${escapeHtml(metricBrief(card))}</div><div class="mono agent-status-meta">${escapeHtml((card.locator || {}).path || "baseline/")} · cited ${escapeHtml(card.reference_count || 0)}</div></article>`).join("")}</div>
    </section>
    <section class="worker-section"><div class="worker-section-head"><h2>Practice Versions</h2><span>${visibleVersions.length}/${versions.length}</span></div>
      <div class="knowledge-cards">${visibleVersions.map(card => `<article class="control-block"><strong>${escapeHtml(card.title || card.id)}</strong><div class="mono agent-status-meta">${escapeHtml(card.ref || "")}</div>
      <p class="hint">${escapeHtml(card.summary || "")}</p><div class="agent-status-meta">${escapeHtml(metricBrief(card))}</div><div class="mono agent-status-meta">${escapeHtml((card.locator || {}).path || "")} · cited ${escapeHtml(card.reference_count || 0)}</div></article>`).join("")}</div>
    </section>`}</div>`;
  bindKnowledgeNavigation();
}

function bindKnowledgeNavigation() {
  document.getElementById("knowledgeScope")?.addEventListener("change", event => {
    state.knowledgeScope = event.target.value === "topic" ? "topic" : "problem";
    state.knowledgeData = null;
    loadKnowledgeData();
  });
  document.getElementById("knowledgeSearch")?.addEventListener("change", event => {
    state.knowledgeQuery = event.target.value;
    renderKnowledge();
  });
}

function setWorkerPolling(enabled) {
  if (workerPollTimer) {
    window.clearInterval(workerPollTimer);
    workerPollTimer = null;
  }
  if (!enabled) return;
  loadWorkerData();
  workerPollTimer = window.setInterval(loadWorkerData, 2000);
}

async function loadWorkerData() {
  if (state.workerLoading) return;
  state.workerLoading = true;
  try {
    const response = await fetch(apiUrl("/api/worker-status.json"), {cache: "no-store"});
    if (!response.ok) throw new Error(`worker API failed: ${response.status}`);
    state.workerData = await response.json();
    state.workerError = null;
  } catch (error) {
    state.workerError = String(error);
  } finally {
    state.workerLoading = false;
    if (state.view === "workers") renderWorkers();
  }
}

function renderWorkers() {
  captureScrollPositions();
  const data = state.workerData;
  if (!data) {
    mainPanel.innerHTML = `<div class="empty">${escapeHtml(state.workerError || "Loading worker status...")}</div>`;
    restoreScrollPositions();
    return;
  }
  const worker = data.worker || {};
  const activeJobs = Array.isArray(worker.active_jobs) ? worker.active_jobs : (worker.current_job ? [worker.current_job] : []);
  const counts = data.counts || {};
  const available = data.available || {};
  const capacity = data.capacity || {};
  const jobs = data.jobs || [];
  const runningJobs = jobs.filter(job => job.status === "running" || job.status === "starting");
  const workerStatusClass = worker.status === "running" ? "status-running" : (worker.status === "draining" ? "status-queued" : (worker.status === "failed" ? "status-error" : "status-neutral"));
  mainPanel.innerHTML = `
    <div class="worker-page">
      <div class="worker-page-head">
        <div>
          <h1>Runtime & Queue</h1>
          <div class="agent-status-meta">Updated ${escapeHtml(formatDate(data.generated_at) || "-")}</div>
        </div>
        <div class="worker-actions">
          <span class="status-pill ${workerStatusClass}">${escapeHtml(worker.status || "stopped")}</span>
          <span class="agent-status-meta">Managed automatically by Discovery</span>
        </div>
      </div>
      ${state.workerError ? `<div class="headless-launch-error">${escapeHtml(state.workerError)}</div>` : ""}
      <div class="worker-summary">
        ${workerSummaryItem("Worker PID", worker.pid || "-")}
        ${workerSummaryItem("Runtime heartbeat", formatDate(worker.heartbeat_at) || "Starting")}
        ${workerSummaryItem("Active jobs", activeJobs.length ? activeJobs.join(", ") : "Idle")}
        ${workerSummaryItem("Queued", counts.queued ?? 0)}
        ${workerSummaryItem("Running", counts.running ?? 0)}
        ${workerSummaryItem("CPU available", `${available.cpus ?? 0} / ${capacity.cpus ?? 0}`)}
        ${workerSummaryItem("Memory available", `${formatNumber(available.memory_gb ?? 0)} GB`)}
      </div>
      ${renderGpuInventory(data)}
      <section class="worker-section">
        <div class="worker-section-head">
          <h2>Job Queue</h2>
          <span class="agent-status-meta">${jobs.length} visible jobs</span>
        </div>
        <div class="table-wrap" data-scroll-key="runtime-job-queue">
          <table class="worker-job-table">
            <thead><tr><th>State</th><th>Position</th><th>Job</th><th>Agent</th><th>Kind</th><th>Allocated</th><th>Live use</th><th>Created</th><th>Command</th><th>Action</th></tr></thead>
            <tbody>${jobs.length ? jobs.map(workerJobRow).join("") : `<tr><td colspan="10" class="agent-status-meta">No queued, running, or recent jobs.</td></tr>`}</tbody>
          </table>
        </div>
      </section>
      <section class="worker-section">
        <div class="worker-section-head"><h2>Live Logs</h2><span class="agent-status-meta">Last 30 lines</span></div>
        <div class="worker-log-grid">
          ${runningJobs.map(job => workerLogPanel(job.id, job.log_tail, job.log)).join("")}
          ${workerLogPanel("Worker", worker.log_tail, worker.log)}
        </div>
      </section>
    </div>`;
  mainPanel.querySelectorAll("[data-job-cancel]").forEach(button => {
    button.addEventListener("click", () => cancelDashboardJob(button.dataset.jobCancel, button));
  });
  restoreScrollPositions();
}

function renderGpuInventory(data) {
  const pressure = data.host_pressure || {};
  const details = Object.values(pressure.gpu_details || {}).sort((a, b) => Number(a.index) - Number(b.index));
  const apps = pressure.gpu_compute_apps || [];
  const available = new Set(data.available?.gpus || []);
  const externallyBusy = new Set(pressure.external_busy_gpus || []);
  const leased = new Set((data.leases || []).flatMap(lease => lease.allocated_gpus || []));
  const configuredCount = Array.isArray(data.capacity?.gpus) ? data.capacity.gpus.length : details.length;
  if (pressure.nvidia_smi_available === false) {
    return `<section class="worker-section"><div class="worker-section-head"><h2>GPU Inventory</h2><span class="agent-status-meta">${configuredCount} configured</span></div><div class="empty">nvidia-smi is unavailable.</div></section>`;
  }
  return `<section class="worker-section">
    <div class="worker-section-head"><h2>GPU Inventory</h2><span class="agent-status-meta">${details.length} detected / ${available.size} schedulable</span></div>
    <div class="gpu-grid">${details.map(gpu => {
      const index = Number(gpu.index);
      const status = leased.has(index) ? "leased" : (externallyBusy.has(index) ? "external busy" : (available.has(index) ? "available" : "unavailable"));
      const statusClass = status === "available" ? "status-done" : (status === "leased" ? "status-running" : "status-queued");
      const gpuApps = apps.filter(app => Number(app.gpu_index) === index);
      const total = Number(gpu.memory_total_gb || 0);
      const used = Number(gpu.memory_used_gb ?? Math.max(0, total - Number(gpu.memory_free_gb || 0)));
      const memoryPercent = total > 0 ? Math.min(100, used * 100 / total) : 0;
      const utilization = Math.max(0, Math.min(100, Number(gpu.utilization_percent || 0)));
      return `<article class="gpu-card">
        <div class="gpu-card-head"><div class="gpu-name">GPU ${escapeHtml(index)} / ${escapeHtml(gpu.name || "NVIDIA GPU")}</div><span class="status-pill ${statusClass}">${escapeHtml(status)}</span></div>
        ${gpuMeter("GPU utilization", utilization, `${formatGpuNumber(utilization, 0)}%`)}
        ${gpuMeter("Memory", memoryPercent, `${formatGpuNumber(used, 2)} / ${formatGpuNumber(total, 2)} GB`)}
        <div class="gpu-facts">
          ${gpuFact("Temperature", gpu.temperature_c == null ? "-" : `${formatGpuNumber(gpu.temperature_c, 0)} C`)}
          ${gpuFact("Power", gpu.power_draw_w == null ? "-" : `${formatGpuNumber(gpu.power_draw_w, 1)} W`)}
          ${gpuFact("Processes", gpuApps.length)}
        </div>
        <div class="gpu-process-list">
          ${gpuApps.length ? gpuApps.map(app => `<div class="gpu-process-row"><span title="${escapeHtml(app.user || "unknown")}">${escapeHtml(app.user || "unknown")}</span><span class="mono">PID ${escapeHtml(app.pid || "-")}</span><span class="mono" title="${escapeHtml(app.process_name || "")}">${escapeHtml(app.process_name || "process")}</span><span class="mono">${formatGpuNumber(app.used_memory_gb || 0, 2)} GB</span></div>`).join("") : `<span class="agent-status-meta">No compute processes</span>`}
        </div>
      </article>`;
    }).join("")}</div>
  </section>`;
}

function gpuMeter(label, percent, value) {
  return `<div class="gpu-meters"><div class="gpu-meter-head"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div><div class="gpu-meter-track"><div class="gpu-meter-fill" style="width:${Math.max(0, Math.min(100, Number(percent) || 0))}%"></div></div></div>`;
}

function gpuFact(label, value) {
  return `<div><div class="label">${escapeHtml(label)}</div><div class="gpu-fact-value mono">${escapeHtml(value)}</div></div>`;
}

function formatGpuNumber(value, digits) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(digits);
}

function workerSummaryItem(label, value) {
  return `<div class="worker-summary-item"><div class="label">${escapeHtml(label)}</div><div class="worker-summary-value mono">${escapeHtml(value)}</div></div>`;
}

function workerJobRow(job) {
  const statusClass = job.status === "done" ? "status-done" : (job.status === "queued" ? "status-queued" : (job.status === "failed" ? "status-error" : "status-running"));
  const resources = job.resources || {};
  const gpus = Array.isArray(resources.gpus) && resources.gpus.length ? ` / GPU ${resources.gpus.join(",")}` : "";
  const runtime = job.runtime || {};
  const live = runtime.activity
    ? `${runtime.activity} / ${runtime.cpu_percent == null ? "CPU sampling" : `${formatNumber(runtime.cpu_percent)}% CPU`} / ${runtime.memory_current_gb == null ? "memory sampling" : `${formatNumber(runtime.memory_current_gb)} GB RAM`}`
    : (job.status === "queued" ? "waiting for resources" : "-");
  return `<tr>
    <td><span class="status-pill ${statusClass}">${escapeHtml(job.status || "unknown")}</span></td>
    <td class="mono">${escapeHtml(job.queue_position || "-")}</td>
    <td class="mono worker-job-id">${escapeHtml(job.id || "")}</td>
    <td class="mono">${escapeHtml(job.agent || "-")}</td>
    <td>${escapeHtml(job.kind || "job")}</td>
    <td class="mono">${escapeHtml(`${resources.cpus ?? "-"} CPU / ${formatNumber(resources.memory_gb ?? 0)} GB${gpus}`)}</td>
    <td class="mono">${escapeHtml(live)}</td>
    <td class="mono">${escapeHtml(formatDate(job.created_at) || "-")}</td>
    <td class="mono worker-command">${escapeHtml(job.command || "")}</td>
    <td>${job.can_cancel ? `<button class="toggle" data-job-cancel="${escapeHtml(job.id || "")}">Cancel</button>` : "-"}</td>
  </tr>`;
}

async function cancelDashboardJob(jobId, button) {
  if (!jobId || !window.confirm(`Cancel job ${jobId}?`)) return;
  button.disabled = true;
  try {
    await postMainAction({action: "job.cancel", job_id: jobId});
    await loadWorkerData();
  } catch (error) {
    window.alert(String(error));
    button.disabled = false;
  }
}

function workerLogPanel(title, content, path) {
  return `<div class="worker-log-panel">
    <div class="worker-section-head"><strong class="mono">${escapeHtml(title)}</strong><span class="agent-status-meta mono">${escapeHtml(path || "")}</span></div>
    <pre data-scroll-key="runtime-log:${escapeHtml(title)}">${escapeHtml(content || "Waiting for log output...")}</pre>
  </div>`;
}

async function controlWorker(action, button) {
  if (!action || !button) return;
  button.disabled = true;
  button.textContent = action === "start" ? "Starting" : "Stopping";
  try {
    const response = await fetch("/api/worker-control", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({problem: state.problemId, action})
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `${action} failed: ${response.status}`);
    await loadWorkerData();
  } catch (error) {
    state.workerError = String(error);
    renderWorkers();
  }
}

function metricNames() {
  return (state.data?.metrics || []).map(metric => metric.name);
}

function keyMetrics() {
  const available = new Set(metricNames());
  const keys = (state.data.key_metrics || []).filter(metric => available.has(metric));
  return keys.length ? keys : metricNames();
}

function metricShortcut(name) {
  const available = new Set(keyMetrics());
  return (state.data.metric_shortcuts?.[name] || []).filter(metric => available.has(metric));
}

function metricInfo(name) {
  return (state.data.metrics || []).find(metric => metric.name === name) || {name, direction: "higher"};
}

function rowLabel(row) {
  if (row.row_type === "baseline") return row.method || row.id;
  return `${row.agent} ${row.id.replace(`version-${row.agent}-`, "v")}`;
}

function rowKind(row) {
  if (row.row_type === "baseline") return row.method_kind || "baseline";
  return "agent";
}

function sortedRows(rows) {
  const copy = rows.slice();
  if (state.sortMetric) {
    const direction = metricInfo(state.sortMetric).direction;
    copy.sort((a, b) => compareMetric(a, b, state.sortMetric, direction));
    if (state.sortDir === "asc") copy.reverse();
  } else if (state.sortField) {
    copy.sort((a, b) => String(a[state.sortField] || "").localeCompare(String(b[state.sortField] || "")));
    if (state.sortDir === "desc") copy.reverse();
  } else {
    copy.sort((a, b) => String(a.agent).localeCompare(String(b.agent)) || String(a.created_at).localeCompare(String(b.created_at)));
  }
  return copy;
}

function compareMetric(a, b, metric, direction) {
  const av = a.metrics[metric];
  const bv = b.metrics[metric];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  return direction === "higher" ? bv - av : av - bv;
}

function setMetricSort(metric) {
  if (state.sortMetric === metric) {
    state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
  } else {
    state.sortMetric = metric;
    state.sortField = null;
    state.sortDir = "desc";
  }
  render();
}

function renderLatest() {
  const rows = latestRowsWithBaselines();
  const polygon = renderPolygonPanel(rows);
  mainPanel.innerHTML = renderAgentVersionSliders() + `<div id="polygonPanelMount">${polygon}</div>`;
  bindAgentVersionSliders();
  bindPolygonInteractions();
}

function refreshPolygonPanel() {
  const mount = document.getElementById("polygonPanelMount");
  if (!mount) {
    render();
    return;
  }
  mount.innerHTML = renderPolygonPanel(latestRowsWithBaselines());
  bindPolygonInteractions(mount);
}

function latestRowsWithBaselines() {
  ensureAgentVersionDefaults();
  reconcilePolygonIdsWithAgentSliders();
  return [
    ...selectedAgentVersionRows(),
    ...(state.data.baseline_rows || [])
  ];
}

function ensureAgentVersionDefaults() {
  if (!state.agentVersionIndexes) state.agentVersionIndexes = {};
  (state.data.agents || []).forEach(agent => {
    const rows = practiceRowsForAgent(agent);
    if (!rows.length) return;
    const maxIndex = rows.length - 1;
    const current = Number(state.agentVersionIndexes[agent]);
    if (!Number.isInteger(current) || current < 0 || current > maxIndex) {
      state.agentVersionIndexes[agent] = maxIndex;
    }
  });
}

function selectedAgentVersionRows() {
  ensureAgentVersionDefaults();
  return (state.data.agents || [])
    .map(agent => {
      const rows = practiceRowsForAgent(agent);
      if (!rows.length) return null;
      const index = Math.max(0, Math.min(rows.length - 1, Number(state.agentVersionIndexes[agent] ?? rows.length - 1)));
      return rows[index];
    })
    .filter(Boolean);
}

function reconcilePolygonIdsWithAgentSliders() {
  if (!state.polygonIds) return;
  selectedAgentVersionRows().forEach(row => {
    const agentRows = practiceRowsForAgent(row.agent);
    const selectedHistorical = agentRows.some(item => state.polygonIds.has(item.id));
    const selectedCurrent = state.polygonIds.has(row.id);
    if (selectedHistorical && !selectedCurrent) {
      agentRows.forEach(item => state.polygonIds.delete(item.id));
      state.polygonIds.add(row.id);
    }
  });
}

function renderAgentVersionSliders() {
  ensureAgentVersionDefaults();
  const controls = (state.data.agents || []).map(agent => {
    const rows = practiceRowsForAgent(agent);
    if (!rows.length) return "";
    const index = Math.max(0, Math.min(rows.length - 1, Number(state.agentVersionIndexes[agent] ?? rows.length - 1)));
    const row = rows[index];
    return `
      <div class="version-slider-control">
        <div class="version-slider-head">
          <strong class="mono">${escapeHtml(agent)}</strong>
          <span class="label version-slider-count">${index + 1}/${rows.length}</span>
        </div>
        <input type="range" data-agent-version="${escapeHtml(agent)}" min="0" max="${rows.length - 1}" step="1" value="${index}" ${rows.length <= 1 ? "disabled" : ""}>
        <div class="mono truncate version-slider-label">${escapeHtml(rowLabel(row))}</div>
        <div class="label version-slider-date">${escapeHtml(formatDate(row.created_at))}</div>
      </div>
    `;
  }).join("");
  return `
    <section class="version-slider-panel">
      <div class="section-head">
        <h1>Agent Version Sliders</h1>
        <div class="label">${selectedAgentVersionRows().length} selected agent versions</div>
      </div>
      <div class="version-slider-grid">${controls}</div>
    </section>
  `;
}

function bindAgentVersionSliders() {
  mainPanel.querySelectorAll("[data-agent-version]").forEach(input => {
    input.addEventListener("input", () => updateAgentVersionFromSlider(input));
  });
}

function updateAgentVersionFromSlider(input) {
  const agent = input.dataset.agentVersion;
  const rows = practiceRowsForAgent(agent);
  if (!rows.length) return;
  const oldSelected = !state.polygonIds || rows.some(row => state.polygonIds.has(row.id));
  const index = Math.max(0, Math.min(rows.length - 1, Number(input.value)));
  if (!state.agentVersionIndexes) state.agentVersionIndexes = {};
  state.agentVersionIndexes[agent] = index;
  if (state.polygonIds) {
    rows.forEach(row => state.polygonIds.delete(row.id));
    if (oldSelected) state.polygonIds.add(rows[index].id);
  }
  const row = rows[index];
  const card = input.closest(".version-slider-control");
  if (card) {
    const count = card.querySelector(".version-slider-count");
    const label = card.querySelector(".version-slider-label");
    const date = card.querySelector(".version-slider-date");
    if (count) count.textContent = `${index + 1}/${rows.length}`;
    if (label) label.textContent = rowLabel(row);
    if (date) date.textContent = formatDate(row.created_at);
  }
  refreshPolygonPanel();
}

function renderVersionTable(title, rows, metrics) {
  mainPanel.innerHTML = versionTableHtml(title, rows, metrics);
  bindTableInteractions();
}

function versionTableHtml(title, rows, metrics) {
  const body = sortedRows(rows).map(row => `
    <tr class="version-row" data-version="${escapeHtml(row.id)}">
      <td><div class="mono">${escapeHtml(rowLabel(row))}</div><div class="label">${escapeHtml(rowKind(row))}</div></td>
      <td class="mono">${escapeHtml(row.id)}</td>
      <td class="mono">${escapeHtml(formatDate(row.created_at))}</td>
      <td class="summary-cell">${escapeHtml(row.summary || "")}</td>
      ${metrics.map(metric => renderMetricCell(row, metric)).join("")}
    </tr>
  `).join("");
  return `
    <div class="section-head">
      <h1>${escapeHtml(title)}</h1>
      <div class="controls"><span class="label">Click metric headers to sort</span></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>agent</th><th>version</th><th>created_at</th><th>summary</th>
            ${metrics.map(metric => `<th class="sortable" data-sort-metric="${escapeHtml(metric)}">${escapeHtml(metric)} ${sortMark(metric)}</th>`).join("")}
          </tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function renderPolygonPanel(rows) {
  const metrics = polygonMetrics();
  if (!state.polygonIds) {
    const defaults = rows
      .filter(row => row.row_type === "practice" || row.row_type === "baseline")
      .slice(0, 8)
      .map(row => row.id);
    state.polygonIds = new Set(defaults);
  }
  if (!state.polygonMetricIds) {
    state.polygonMetricIds = new Set(keyMetrics());
  }
  const selected = rows.filter(row => state.polygonIds.has(row.id));
  const svg = metrics.length >= 3 ? radarSvg(selected, metrics) : `<div class="empty">Select at least 3 metrics to draw the polygon.</div>`;
  const objectControls = rows.map((row, index) => `
    <label class="check-row">
      <input type="checkbox" data-polygon-id="${escapeHtml(row.id)}" ${state.polygonIds.has(row.id) ? "checked" : ""}>
      <span class="swatch" style="background:${seriesColor(index)}"></span>
      <span><span class="mono">${escapeHtml(rowLabel(row))}</span><br><span class="label">${escapeHtml(rowKind(row))}</span></span>
    </label>
  `).join("");
  const metricControls = keyMetrics().map(metric => `
    <label class="check-row">
      <input type="checkbox" data-polygon-metric="${escapeHtml(metric)}" ${state.polygonMetricIds.has(metric) ? "checked" : ""}>
      <span><span class="mono">${escapeHtml(shortMetricLines(metric).join(" / "))}</span><br><span class="label">${escapeHtml(metricInfo(metric).direction)}</span></span>
    </label>
  `).join("");
  return `
    <section class="polygon-panel">
      <div class="section-head">
        <h1>Normalized Metric Polygon</h1>
        <div class="label">${metrics.length} / ${keyMetrics().length} metrics · ${selected.length} objects</div>
      </div>
      <p class="hint">Values are direction-aware normalized per metric for shape only. Use metric checkboxes for polygon dimensions and object checkboxes for compared agents/baselines.</p>
      <div class="polygon-layout">
        <div>${svg}</div>
        <div class="polygon-controls">
          <div class="control-block">
            <div class="control-title"><strong>Metrics</strong><span><button class="toggle" data-metric-action="breakthrough">Breakthrough</button> <button class="toggle" data-metric-action="guardrail">Guardrail</button> <button class="toggle" data-metric-action="all">All</button> <button class="toggle" data-metric-action="clear">Clear</button></span></div>
            <div class="control-grid" data-scroll-key="polygon-metrics">${metricControls}</div>
          </div>
          <div class="control-block">
            <div class="control-title"><strong>Objects</strong><span class="label">choose compared rows</span></div>
            <div class="control-grid" data-scroll-key="polygon-objects">${objectControls}</div>
          </div>
        </div>
      </div>
    </section>
  `;
}

function polygonMetrics() {
  if (!state.polygonMetricIds) return keyMetrics();
  return keyMetrics().filter(metric => state.polygonMetricIds.has(metric));
}

function radarSvg(rows, metrics) {
  const width = 560;
  const height = 450;
  const centerX = 255;
  const centerY = 230;
  const radius = 138;
  const levels = [0.25, 0.5, 0.75, 1.0];
  const axes = metrics.map((metric, i) => {
    const angle = -Math.PI / 2 + i * Math.PI * 2 / metrics.length;
    return {metric, angle, x: centerX + Math.cos(angle) * radius, y: centerY + Math.sin(angle) * radius};
  });
  const grid = levels.map(level => {
    const points = axes.map(axis => `${centerX + Math.cos(axis.angle) * radius * level},${centerY + Math.sin(axis.angle) * radius * level}`).join(" ");
    return `<polygon points="${points}" fill="none" stroke="#d9dee7" stroke-width="1"/>`;
  }).join("");
  const axisLines = axes.map(axis => `<line x1="${centerX}" y1="${centerY}" x2="${axis.x}" y2="${axis.y}" stroke="#d9dee7" stroke-width="1"/>`).join("");
  const labels = axes.map(axis => {
    const lx = centerX + Math.cos(axis.angle) * (radius + 58);
    const ly = centerY + Math.sin(axis.angle) * (radius + 42);
    const anchor = lx < centerX - 10 ? "end" : lx > centerX + 10 ? "start" : "middle";
    const lines = shortMetricLines(axis.metric);
    const lineHeight = 12;
    const y0 = ly - ((lines.length - 1) * lineHeight) / 2;
    return `<text x="${lx}" y="${y0}" text-anchor="${anchor}" font-size="10" fill="#344054"><title>${escapeHtml(axis.metric)}</title>${lines.map((line, idx) => `<tspan x="${lx}" dy="${idx === 0 ? 0 : lineHeight}">${escapeHtml(line)}</tspan>`).join("")}</text>`;
  }).join("");
  const polygons = rows.map((row, index) => {
    const color = seriesColor(indexForRow(row));
    const points = axes.map(axis => {
      const norm = row.normalized_metrics[axis.metric];
      const value = Math.max(0, Math.min(1, Number(norm ?? 0)));
      return `${centerX + Math.cos(axis.angle) * radius * value},${centerY + Math.sin(axis.angle) * radius * value}`;
    }).join(" ");
    return `<polygon points="${points}" fill="${color}" fill-opacity="0.10" stroke="${color}" stroke-width="2"><title>${escapeHtml(rowLabel(row))}</title></polygon>`;
  }).join("");
  const legend = rows.map((row, index) => {
    const y = 18 + index * 17;
    const color = seriesColor(indexForRow(row));
    return `<g><rect x="8" y="${y - 9}" width="9" height="9" fill="${color}"/><text x="23" y="${y}" font-size="11" fill="#18202f">${escapeHtml(rowLabel(row))}</text></g>`;
  }).join("");
  return `<svg class="polygon-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Normalized metric polygon">${grid}${axisLines}${polygons}${labels}${legend}</svg>`;
}

function bindPolygonInteractions(root = mainPanel) {
  root.querySelectorAll("[data-polygon-id]").forEach(input => {
    input.addEventListener("change", () => {
      if (!state.polygonIds) state.polygonIds = new Set();
      if (input.checked) state.polygonIds.add(input.dataset.polygonId);
      else state.polygonIds.delete(input.dataset.polygonId);
      render();
    });
  });
  root.querySelectorAll("[data-polygon-metric]").forEach(input => {
    input.addEventListener("change", () => {
      if (!state.polygonMetricIds) state.polygonMetricIds = new Set(keyMetrics());
      if (input.checked) state.polygonMetricIds.add(input.dataset.polygonMetric);
      else state.polygonMetricIds.delete(input.dataset.polygonMetric);
      render();
    });
  });
  root.querySelectorAll("[data-metric-action]").forEach(button => {
    button.addEventListener("click", () => {
      const action = button.dataset.metricAction;
      if (action === "all") {
        state.polygonMetricIds = new Set(keyMetrics());
      } else if (action === "clear") {
        state.polygonMetricIds = new Set();
      } else {
        state.polygonMetricIds = new Set(metricShortcut(action));
      }
      render();
    });
  });
}

function renderTrendsRankings() {
  ensureTrendDefaults();
  const metrics = selectedTrendMetrics();
  const agents = selectedTrendAgents();
  const baselines = selectedTrendBaselines();
  const mode = effectiveTrendMode(metrics);
  mainPanel.innerHTML = `
    <section class="viz-panel">
      <div class="section-head">
        <h1>Version Trends</h1>
        <div class="inline-controls">
          <span class="mini-label">Metric</span>
          <select id="trendMetricSelect" class="select">
            ${trendMetricOptions()}
          </select>
          <span class="mini-label">Scale</span>
          <select id="trendScaleMode" class="select">
            ${["auto", "raw", "normalized", "delta"].map(name => `<option value="${name}" ${state.trendScaleMode === name ? "selected" : ""}>${name}</option>`).join("")}
          </select>
        </div>
      </div>
      <p class="hint">Lines show route progress by version index for one selected metric. Agents remain multi-select; baseline rows are horizontal reference lines for the same metric.</p>
      <div class="viz-grid">
        <div>${trendLineSvg(agents, metrics, baselines, mode)}</div>
        <div class="polygon-controls">
          <div class="control-block">
            <div class="control-title"><strong>Agents</strong><span><button class="toggle" data-trend-agent-action="all">All</button> <button class="toggle" data-trend-agent-action="clear">Clear</button></span></div>
            <div class="control-grid">${trendAgentControls()}</div>
          </div>
          <div class="control-block">
            <div class="control-title"><strong>Baseline Lines</strong><span class="label">horizontal references</span></div>
            <div class="control-grid">${trendBaselineControls()}</div>
          </div>
        </div>
      </div>
    </section>
    <section class="viz-panel">
      <div class="section-head">
        <h1>Metric Ranking</h1>
        <div class="inline-controls">
          <span class="mini-label">Metric</span>
          <select id="rankingMetric" class="select">
            ${metricNames().map(name => `<option value="${escapeHtml(name)}" ${name === state.rankingMetric ? "selected" : ""}>${escapeHtml(shortMetricLines(name).join(" / "))} (${escapeHtml(metricInfo(name).direction)}${metricInfo(name).role ? `; ${escapeHtml(metricInfo(name).role)}` : ""})</option>`).join("")}
          </select>
        </div>
      </div>
      <p class="hint">Bars are sorted best to worst using the metric direction. Practice versions and baseline rows share the same ranking axis.</p>
      ${rankingBarSvg(state.rankingMetric || metricNames()[0] || "")}
    </section>
  `;
  bindTrendInteractions();
}

function ensureTrendDefaults() {
  if (!state.trendAgentIds) {
    state.trendAgentIds = new Set(state.data.agents || []);
  }
  const availableMetrics = keyMetrics();
  const currentTrendMetric = availableMetrics.find(metric => state.trendMetricIds?.has(metric));
  if (currentTrendMetric) {
    state.trendMetricIds = new Set([currentTrendMetric]);
  } else {
    const preferred = availableMetrics[0] || "";
    state.trendMetricIds = new Set(preferred ? [preferred] : []);
    if (!state.rankingMetric) state.rankingMetric = preferred;
  }
  if (!state.trendBaselineIds) {
    state.trendBaselineIds = new Set((state.data.baseline_rows || []).map(row => row.id));
  }
}

function selectedTrendAgents() {
  return (state.data.agents || []).filter(agent => state.trendAgentIds?.has(agent));
}

function selectedTrendMetrics() {
  const selected = keyMetrics().find(metric => state.trendMetricIds?.has(metric));
  return selected ? [selected] : [];
}

function selectedTrendBaselines() {
  return (state.data.baseline_rows || []).filter(row => state.trendBaselineIds?.has(row.id));
}

function effectiveTrendMode(metrics) {
  if (state.trendScaleMode === "auto") return metrics.length <= 1 ? "raw" : "normalized";
  return state.trendScaleMode;
}

function trendAgentControls() {
  return (state.data.agents || []).map((agent, index) => `
    <label class="check-row">
      <input type="checkbox" data-trend-agent="${escapeHtml(agent)}" ${state.trendAgentIds.has(agent) ? "checked" : ""}>
      <span class="swatch" style="background:${seriesColor(index)}"></span>
      <span class="mono">${escapeHtml(agent)}</span>
    </label>
  `).join("");
}

function trendMetricOptions() {
  const selected = selectedTrendMetrics()[0] || "";
  return keyMetrics().map(metric => `
    <option value="${escapeHtml(metric)}" ${metric === selected ? "selected" : ""}>${escapeHtml(shortMetricLines(metric).join(" / "))} (${escapeHtml(metricInfo(metric).direction)})</option>
  `).join("");
}

function trendBaselineControls() {
  return (state.data.baseline_rows || []).map(row => `
    <label class="check-row">
      <input type="checkbox" data-trend-baseline="${escapeHtml(row.id)}" ${state.trendBaselineIds.has(row.id) ? "checked" : ""}>
      <span><span class="mono">${escapeHtml(rowLabel(row))}</span><br><span class="label">${escapeHtml(rowKind(row))}</span></span>
    </label>
  `).join("");
}

function practiceRowsForAgent(agent) {
  const practiceRows = state.data.practice_versions || (state.data.versions || []).filter(row => row.row_type === "practice");
  return practiceRows
    .filter(row => row.agent === agent)
    .slice()
    .sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")) || String(a.id).localeCompare(String(b.id)));
}

function trendValue(row, metric, mode, previousRaw) {
  const raw = row.metrics?.[metric];
  if (raw == null) return null;
  if (mode === "normalized") return row.normalized_metrics?.[metric] ?? null;
  if (mode === "delta") {
    if (previousRaw == null) return 0;
    const direction = metricInfo(metric).direction;
    return direction === "lower" ? previousRaw - raw : raw - previousRaw;
  }
  return raw;
}

function trendLineSvg(agents, metrics, baselines, mode) {
  if (!agents.length || !metrics.length) return `<div class="empty">Select at least one agent and one metric.</div>`;
  const width = 980;
  const height = 440;
  const left = 72;
  const right = 190;
  const top = 28;
  const bottom = 58;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const series = [];
  const values = [];
  let maxX = 0;
  agents.forEach((agent, agentIndex) => {
    const rows = practiceRowsForAgent(agent);
    metrics.forEach((metric, metricIndex) => {
      let previousRaw = null;
      const points = [];
      rows.forEach((row, index) => {
        const value = trendValue(row, metric, mode, previousRaw);
        const raw = row.metrics?.[metric];
        if (raw != null) previousRaw = raw;
        if (value == null) return;
        points.push({xIndex: index, value, row, metric});
        values.push(value);
        maxX = Math.max(maxX, index);
      });
      if (points.length) {
        series.push({
          label: `${agent} / ${shortMetricLines(metric).join(" ")}`,
          color: seriesColor(agentIndex * Math.max(1, metrics.length) + metricIndex),
          points,
        });
      }
    });
  });
  const baselineLines = [];
  if (mode !== "delta") {
    baselines.forEach((row, baselineIndex) => {
      metrics.forEach(metric => {
        const value = trendValue(row, metric, mode, null);
        if (value == null) return;
        values.push(value);
        baselineLines.push({row, metric, value, color: baselineColor(baselineIndex)});
      });
    });
  }
  if (!values.length) return `<div class="empty">No numeric values for the selected trend.</div>`;
  let minY = Math.min(...values);
  let maxY = Math.max(...values);
  if (minY === maxY) {
    const pad = Math.abs(minY || 1) * 0.08;
    minY -= pad;
    maxY += pad;
  } else {
    const pad = (maxY - minY) * 0.08;
    minY -= pad;
    maxY += pad;
  }
  const xFor = index => left + (maxX <= 0 ? plotW / 2 : (index / maxX) * plotW);
  const yFor = value => top + ((maxY - value) / (maxY - minY)) * plotH;
  const yTicks = [0, 1, 2, 3, 4].map(i => minY + (maxY - minY) * i / 4);
  const grid = yTicks.map(value => {
    const y = yFor(value);
    return `<line x1="${left}" y1="${y}" x2="${left + plotW}" y2="${y}" stroke="#e5e9f0"/><text x="${left - 10}" y="${y + 4}" text-anchor="end" font-size="11" fill="#657084">${escapeHtml(formatNumber(value))}</text>`;
  }).join("");
  const xTicks = Array.from({length: maxX + 1}, (_, index) => {
    const x = xFor(index);
    return `<line x1="${x}" y1="${top + plotH}" x2="${x}" y2="${top + plotH + 5}" stroke="#9aa7bb"/><text x="${x}" y="${top + plotH + 22}" text-anchor="middle" font-size="11" fill="#657084">v${index + 1}</text>`;
  }).join("");
  const baselineSvg = baselineLines.map((line, index) => {
    const y = yFor(line.value);
    const labelY = Math.max(top + 12, Math.min(top + plotH - 4, y - 4 + (index % 4) * 12));
    return `<line x1="${left}" y1="${y}" x2="${left + plotW}" y2="${y}" stroke="${line.color}" stroke-width="1.5" stroke-dasharray="5 4"/><text x="${left + plotW + 8}" y="${labelY}" font-size="10" fill="${line.color}">${escapeHtml(rowLabel(line.row))} ${escapeHtml(shortMetricLines(line.metric).join(" "))}: ${escapeHtml(formatNumber(line.value))}</text>`;
  }).join("");
  const lineSvg = series.map(item => {
    const path = item.points.map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(point.xIndex)} ${yFor(point.value)}`).join(" ");
    const circles = item.points.map(point => `<circle cx="${xFor(point.xIndex)}" cy="${yFor(point.value)}" r="3" fill="${item.color}"><title>${escapeHtml(rowLabel(point.row))} ${escapeHtml(point.metric)} ${escapeHtml(formatNumber(point.value))}</title></circle>`).join("");
    return `<path d="${path}" fill="none" stroke="${item.color}" stroke-width="2.2"/>${circles}`;
  }).join("");
  const legend = series.slice(0, 18).map((item, index) => {
    const y = top + index * 17;
    return `<g><line x1="${width - right + 42}" y1="${y}" x2="${width - right + 56}" y2="${y}" stroke="${item.color}" stroke-width="2.2"/><text x="${width - right + 62}" y="${y + 4}" font-size="10" fill="#18202f">${escapeHtml(item.label)}</text></g>`;
  }).join("");
  return `<svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Version trend line chart">
    <rect x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="#ffffff" stroke="#d9dee7"/>
    ${grid}${xTicks}${baselineSvg}${lineSvg}${legend}
    <text x="${left}" y="${height - 12}" font-size="11" fill="#657084">Version index within each agent</text>
    <text x="${left}" y="14" font-size="12" fill="#657084">Mode: ${escapeHtml(mode)}${mode === "normalized" ? " (direction-aware, higher is better)" : ""}</text>
  </svg>`;
}

function rankingBarSvg(metric) {
  if (!metric) return `<div class="empty">No metrics available.</div>`;
  const direction = metricInfo(metric).direction;
  const rows = (state.data.versions || [])
    .filter(row => row.metrics?.[metric] != null)
    .sort((a, b) => compareMetric(a, b, metric, direction));
  if (!rows.length) return `<div class="empty">No rows have ${escapeHtml(metric)}.</div>`;
  const width = 1060;
  const left = 250;
  const right = 120;
  const top = 28;
  const barH = 18;
  const gap = 8;
  const height = top + rows.length * (barH + gap) + 34;
  const plotW = width - left - right;
  const bars = rows.map((row, index) => {
    const norm = Math.max(0, Math.min(1, Number(row.normalized_metrics?.[metric] ?? 0)));
    const y = top + index * (barH + gap);
    const w = Math.max(2, norm * plotW);
    const color = row.row_type === "baseline" ? (row.method_kind === "baseline_best_per_metric" ? "#1f2937" : "#64748b") : seriesColor(indexForAgent(row.agent));
    return `<g>
      <text x="${left - 10}" y="${y + 13}" text-anchor="end" font-size="11" fill="#18202f">${escapeHtml(rowLabel(row))}</text>
      <rect x="${left}" y="${y}" width="${w}" height="${barH}" rx="3" fill="${color}" fill-opacity="${row.row_type === "baseline" ? "0.72" : "0.86"}"/>
      <text x="${left + w + 7}" y="${y + 13}" font-size="11" fill="#18202f">#${index + 1} ${escapeHtml(formatNumber(row.metrics[metric]))}</text>
    </g>`;
  }).join("");
  return `<svg class="ranking-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Metric ranking bar chart">
    <text x="${left}" y="14" font-size="12" fill="#657084">${escapeHtml(metric)} · ${escapeHtml(direction)} is better · bars sorted best to worst</text>
    <line x1="${left}" y1="${top - 8}" x2="${left + plotW}" y2="${top - 8}" stroke="#d9dee7"/>
    ${bars}
  </svg>`;
}

function bindTrendInteractions() {
  const trendMetricSelect = document.getElementById("trendMetricSelect");
  if (trendMetricSelect) {
    trendMetricSelect.addEventListener("change", event => {
      state.trendMetricIds = new Set([event.target.value]);
      deferredMainRender = false;
      render();
    });
  }
  const scaleSelect = document.getElementById("trendScaleMode");
  if (scaleSelect) {
    scaleSelect.addEventListener("change", event => {
      state.trendScaleMode = event.target.value;
      deferredMainRender = false;
      render();
    });
  }
  const rankingSelect = document.getElementById("rankingMetric");
  if (rankingSelect) {
    rankingSelect.addEventListener("change", event => {
      state.rankingMetric = event.target.value;
      if (!state.trendMetricIds || state.trendMetricIds.size === 0) {
        state.trendMetricIds = new Set([state.rankingMetric]);
      }
      deferredMainRender = false;
      render();
    });
  }
  mainPanel.querySelectorAll("[data-trend-agent]").forEach(input => {
    input.addEventListener("change", () => {
      if (!state.trendAgentIds) state.trendAgentIds = new Set();
      if (input.checked) state.trendAgentIds.add(input.dataset.trendAgent);
      else state.trendAgentIds.delete(input.dataset.trendAgent);
      render();
    });
  });
  mainPanel.querySelectorAll("[data-trend-baseline]").forEach(input => {
    input.addEventListener("change", () => {
      if (!state.trendBaselineIds) state.trendBaselineIds = new Set();
      if (input.checked) state.trendBaselineIds.add(input.dataset.trendBaseline);
      else state.trendBaselineIds.delete(input.dataset.trendBaseline);
      render();
    });
  });
  mainPanel.querySelectorAll("[data-trend-agent-action]").forEach(button => {
    button.addEventListener("click", () => {
      state.trendAgentIds = button.dataset.trendAgentAction === "all" ? new Set(state.data.agents || []) : new Set();
      render();
    });
  });
}

function indexForAgent(agent) {
  const index = (state.data.agents || []).findIndex(name => name === agent);
  return index >= 0 ? index : 0;
}

function baselineColor(index) {
  const colors = ["#111827", "#64748b", "#0f766e", "#b45309", "#7c2d12", "#475569", "#4c1d95"];
  return colors[index % colors.length];
}

function renderHeatmap() {
  const rows = state.heatmapScope === "latest" ? (state.data.latest_with_baselines || state.data.latest_by_agent) : state.data.versions;
  const metrics = metricNames();
  mainPanel.innerHTML = `
    <div class="section-head">
      <h1>Metric Heatmap</h1>
      <div class="controls">
        <button class="toggle" id="latestHeatmap">Latest per agent</button>
        <button class="toggle" id="allHeatmap">All versions</button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>agent/version</th>
            ${metrics.map(metric => `<th class="sortable" data-sort-metric="${escapeHtml(metric)}">${escapeHtml(metric)} ${sortMark(metric)}</th>`).join("")}
          </tr>
        </thead>
        <tbody>
          ${sortedRows(rows).map(row => `
            <tr class="version-row" data-version="${escapeHtml(row.id)}">
              <td><div class="mono">${escapeHtml(rowLabel(row))}</div><div class="label">${escapeHtml(rowKind(row))}</div></td>
              ${metrics.map(metric => renderMetricCell(row, metric, true)).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  document.getElementById("latestHeatmap").addEventListener("click", () => { state.heatmapScope = "latest"; render(); });
  document.getElementById("allHeatmap").addEventListener("click", () => { state.heatmapScope = "all"; render(); });
  bindTableInteractions();
}

function renderRankings() {
  const metrics = metricNames();
  const metric = state.rankingMetric || metrics[0] || "";
  const direction = metricInfo(metric).direction;
  const rows = state.data.versions
    .filter(row => row.metrics[metric] != null)
    .sort((a, b) => compareMetric(a, b, metric, direction));
  mainPanel.innerHTML = `
    <div class="section-head">
      <h1>Metric Ranking</h1>
      <div class="controls">
        <select id="rankingMetric" class="select">
          ${metrics.map(name => `<option value="${escapeHtml(name)}" ${name === metric ? "selected" : ""}>${escapeHtml(name)} (${escapeHtml(metricInfo(name).direction)}${metricInfo(name).role ? `; ${escapeHtml(metricInfo(name).role)}` : ""})</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>rank</th><th>agent</th><th>version</th><th>value</th><th>delta</th><th>summary</th></tr></thead>
        <tbody>
          ${rows.map(row => `
            <tr class="version-row" data-version="${escapeHtml(row.id)}">
              <td>${row.ranks[metric]?.global?.rank ?? ""}</td>
              <td class="mono">${escapeHtml(rowLabel(row))}</td>
              <td class="mono">${escapeHtml(row.id)}</td>
              <td>${formatNumber(row.metrics[metric])}</td>
              <td>${renderDelta(row, metric)}</td>
              <td class="summary-cell">${escapeHtml(row.summary || "")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  document.getElementById("rankingMetric").addEventListener("change", event => {
    state.rankingMetric = event.target.value;
    render();
  });
  bindTableInteractions();
}

function renderMetricCell(row, metric, compact = false) {
  const value = row.metrics[metric];
  if (value == null) return `<td class="metric-cell"></td>`;
  const norm = row.normalized_metrics[metric];
  const style = norm == null ? "" : ` style="background:${heatColor(norm)}"`;
  const rank = row.ranks[metric]?.global;
  const ownRank = row.ranks[metric]?.own;
  const best = row.best[metric];
  const isBest = best?.global?.version === row.id;
  const isOwnBest = best?.own?.version === row.id;
  const title = [
    `${metric}: ${formatNumber(value)}`,
    `direction: ${metricInfo(metric).direction}`,
    rank ? `global rank: ${rank.rank}/${rank.of}` : "",
    ownRank ? `own rank: ${ownRank.rank}/${ownRank.of}` : "",
    `delta: ${plainDelta(row, metric)}`,
    best?.global ? `global best: ${best.global.version} ${formatNumber(best.global.value)}` : "",
    best?.own ? `own best: ${best.own.version} ${formatNumber(best.own.value)}` : ""
  ].filter(Boolean).join("\n");
  return `<td class="metric-cell" title="${escapeHtml(title)}"${style}>
    <span>${formatNumber(value)}</span>
    ${compact ? "" : renderBadges(isBest, isOwnBest)}
    ${renderDelta(row, metric)}
  </td>`;
}

function renderBadges(isBest, isOwnBest) {
  const badges = [];
  if (isBest) badges.push(`<span class="badge best">Best</span>`);
  if (isOwnBest) badges.push(`<span class="badge own">Own best</span>`);
  return badges.length ? `<span class="badges">${badges.join("")}</span>` : "";
}

function renderDelta(row, metric) {
  const delta = row.deltas[metric];
  if (!delta || delta.value == null) return ` <span class="delta-flat">-</span>`;
  const cls = delta.improved ? "delta-up" : delta.regressed ? "delta-down" : "delta-flat";
  const arrow = delta.improved ? "up" : delta.regressed ? "down" : "flat";
  return ` <span class="${cls}">${arrow} ${formatNumber(delta.value)}</span>`;
}

function plainDelta(row, metric) {
  const delta = row.deltas[metric];
  return delta && delta.value != null ? formatNumber(delta.value) : "-";
}

function bindTableInteractions() {
  mainPanel.querySelectorAll("[data-sort-metric]").forEach(header => {
    header.addEventListener("click", () => setMetricSort(header.dataset.sortMetric));
  });
  mainPanel.querySelectorAll("[data-version]").forEach(row => {
    row.addEventListener("click", () => {
      state.selectedId = row.dataset.version;
      renderDrawer(selectedVersion());
    });
  });
}

function selectedVersion() {
  if (!state.selectedId) return null;
  return state.data.versions.find(version => version.id === state.selectedId) || null;
}

function renderDrawer(version) {
  if (!version) {
    drawer.innerHTML = `<div class="drawer-empty">Select a version to inspect details.</div>`;
    return;
  }
  const displayedMetrics = version.reported_metrics || version.metrics;
  const metricRows = Object.keys(displayedMetrics).sort().map(metric => `
    <tr>
      <td class="mono">${escapeHtml(metric)}</td>
      <td>${formatNumber(displayedMetrics[metric])}</td>
      <td>${escapeHtml(metricInfo(metric).direction)}</td>
      <td>${escapeHtml(version.metric_validity?.[metric]?.status || (version.row_type === "baseline" ? "pending_review" : "evaluated"))}</td>
      <td>${escapeHtml(version.metric_validity?.[metric]?.reason || "")}</td>
      <td>${version.ranks[metric]?.global?.rank ?? ""}/${version.ranks[metric]?.global?.of ?? ""}</td>
      <td>${version.ranks[metric]?.own?.rank ?? ""}/${version.ranks[metric]?.own?.of ?? ""}</td>
      <td>${plainDelta(version, metric)}</td>
    </tr>
  `).join("");
  const reviewRows = Object.entries(version.ai_review?.dimensions || {}).sort(([a], [b]) => a.localeCompare(b)).map(([dimension, value]) => `<tr><td class="mono">${escapeHtml(dimension)}</td><td>${escapeHtml(String(value.score ?? ""))}</td><td>${escapeHtml(value.rationale || "Rationale unavailable")}</td></tr>`).join("");
  drawer.innerHTML = `
    <h2 class="mono">${escapeHtml(version.id)}</h2>
    <div class="sub">${escapeHtml(rowLabel(version))} - ${escapeHtml(formatDate(version.created_at))}</div>
    <div>${escapeHtml(version.summary || "")}</div>
    <div class="drawer-section">
      <h3>Provenance</h3>
      <div class="kv">
        <div>Type</div><div>${escapeHtml(version.row_type || "")}</div>
        <div>Kind</div><div>${escapeHtml(rowKind(version))}</div>
        <div>Path</div><div class="mono">${escapeHtml(version.path || "")}</div>
        <div>Space</div><div>${escapeHtml(version.space || "")}</div>
        <div>Eval run</div><div class="mono">${escapeHtml(version.eval_run?.id || "")}</div>
        <div>Log</div><div class="mono">${escapeHtml(version.eval_run?.log || "")}</div>
        <div>Returncode</div><div>${escapeHtml(String(version.eval_run?.returncode ?? ""))}</div>
        <div>Snapshot</div><div class="mono">${escapeHtml(version.snapshot?.tag || version.snapshot?.commit || "")}</div>
      </div>
    </div>
    <div class="drawer-section">
      <h3>Metrics</h3>
      <div class="table-wrap">
        <table><thead><tr><th>metric</th><th>value</th><th>dir</th><th>validity</th><th>reason</th><th>global</th><th>own</th><th>delta</th></tr></thead><tbody>${metricRows}</tbody></table>
      </div>
    </div>
    <div class="drawer-section">
      <h3>Eval Feedback</h3>
      <pre>${escapeHtml(JSON.stringify(version.eval_feedback || {}, null, 2))}</pre>
    </div>
    ${reviewRows ? `<div class="drawer-section"><h3>AI Review</h3><div class="table-wrap"><table><thead><tr><th>dimension</th><th>score</th><th>rationale</th></tr></thead><tbody>${reviewRows}</tbody></table></div></div>` : ""}
    <div class="drawer-section">
      <h3>Reflection Note</h3>
      <pre>${escapeHtml(shortText(version.note || ""))}</pre>
    </div>
    <div class="drawer-section">
      <h3>Next Target Brief</h3>
      <pre>${escapeHtml(shortText(version.next_plan || ""))}</pre>
    </div>
  `;
}

function heatColor(norm) {
  const n = Math.max(0, Math.min(1, Number(norm)));
  if (n >= 0.55) {
    const light = 94 - (n - 0.55) * 42;
    return `hsl(145 45% ${light}%)`;
  }
  if (n >= 0.38) {
    return "hsl(45 45% 91%)";
  }
  const light = 95 - (0.38 - n) * 34;
  return `hsl(2 55% ${light}%)`;
}

function sortMark(metric) {
  if (state.sortMetric !== metric) return "";
  return state.sortDir === "desc" ? "v" : "^";
}

function captureScrollPositions() {
  if (!mainPanel || !state.scrollPositions) return;
  state.scrollPositions["main-panel"] = {
    left: mainPanel.scrollLeft,
    top: mainPanel.scrollTop,
    atBottom: mainPanel.scrollHeight - mainPanel.clientHeight - mainPanel.scrollTop <= 4,
  };
  mainPanel.querySelectorAll("[data-scroll-key]").forEach(element => {
    state.scrollPositions[element.dataset.scrollKey] = {
      left: element.scrollLeft,
      top: element.scrollTop,
      atBottom: element.scrollHeight - element.clientHeight - element.scrollTop <= 4,
    };
  });
}

function restoreScrollPositions() {
  if (!mainPanel || !state.scrollPositions) return;
  window.requestAnimationFrame(() => {
    const mainPosition = state.scrollPositions["main-panel"];
    if (mainPosition) {
      mainPanel.scrollLeft = mainPosition.left || 0;
      mainPanel.scrollTop = mainPosition.atBottom ? mainPanel.scrollHeight : (mainPosition.top || 0);
    }
    mainPanel.querySelectorAll("[data-scroll-key]").forEach(element => {
      const position = state.scrollPositions[element.dataset.scrollKey];
      if (!position) return;
      element.scrollLeft = position.left || 0;
      element.scrollTop = position.atBottom ? element.scrollHeight : (position.top || 0);
    });
  });
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return "";
  const num = Number(value);
  if (Math.abs(num) >= 100) return num.toFixed(1);
  if (Math.abs(num) >= 10) return num.toFixed(2);
  if (Math.abs(num) >= 1) return num.toFixed(3);
  return num.toPrecision(4);
}

function formatDate(value) {
  if (!value) return "";
  return String(value).replace("T", " ").replace("+00:00", "Z");
}

function formatServerTime(value) {
  if (!value) return "";
  return String(value).replace("T", " ").replace(/(?:\+00:00|Z)$/, " UTC");
}

function shortText(value) {
  const text = String(value || "");
  return text.length > 4000 ? `${text.slice(0, 4000)}\n...` : text;
}

function shortMetricLines(metric) {
  const words = String(metric).replaceAll("_", " ").split(/\s+/).filter(Boolean);
  if (words.length <= 2) return [words.join(" ")];
  const midpoint = Math.ceil(words.length / 2);
  return [words.slice(0, midpoint).join(" "), words.slice(midpoint).join(" ")];
}

function indexForRow(row) {
  const rows = state.data.latest_with_baselines || state.data.versions || [];
  const index = rows.findIndex(item => item.id === row.id);
  return index >= 0 ? index : 0;
}

function seriesColor(index) {
  const colors = ["#2563eb", "#16a34a", "#7c3aed", "#dc2626", "#0891b2", "#ca8a04", "#be185d", "#4b5563", "#ea580c", "#0f766e", "#9333ea", "#64748b", "#65a30d", "#b45309"];
  return colors[index % colors.length];
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
"""


def cmd_agent(workspace: Path, args: argparse.Namespace) -> None:
    if args.agent_cmd == "list":
        agents = sorted(p.name for p in workspace.iterdir() if p.is_dir() and p.name.startswith("agent"))
        print(json.dumps(agents, indent=2))
        return

    name = args.name
    contract = load_evaluation_contract(workspace, require_configured=True)
    registry = load_evaluation_registry(workspace, require_configured=True)
    validate_evaluation_pair(workspace, contract, registry)
    if not AGENT_NAME_RE.fullmatch(name):
        raise SystemExit("agent name must match agent[A-Za-z0-9_-]*")
    template = topic_root(workspace) / ".discovery" / "agents-template"
    target = workspace / name
    if target.exists():
        if not args.force:
            raise SystemExit(f"{name} already exists")
        if target.is_symlink() or not target.is_dir():
            raise SystemExit(f"refusing to replace non-directory agent path: {name}")
        require_under(target, workspace, "agent target")
        shutil.rmtree(target)
    shutil.copytree(template, target)
    # A source template may itself have been made read-only after a previous
    # Route was rendered.  Restore owner-write permission until placeholders
    # have been rendered, then make the client immutable again below.
    explore_client = target / "explore"
    explore_client.chmod(explore_client.stat().st_mode | stat.S_IWUSR)
    pub_link = target / "pub"
    if pub_link.exists() or pub_link.is_symlink():
        pub_link.unlink()
    # Keep pub usable when Codex presents the writable Route through a
    # synthetic mount with a different parent directory.
    pub_link.symlink_to(pub(workspace).resolve(), target_is_directory=True)
    install_root = codex_install_root()
    if install_root is None:
        raise SystemExit("Codex installation root is unavailable; cannot render Route permission config")
    replacements = {
        "{agent_name}": name,
        "{python_prefix}": str(Path(sys.prefix).resolve()),
        "{codex_install_root}": str(install_root.resolve()),
        "{problem_root}": str(workspace.resolve()),
    }
    for token, value in replacements.items():
        replace_token(target, token, value)
    explore_client.chmod(0o555)
    ensure_agent_git(target)
    ensure_route_broker_token(target)
    print(json.dumps({"created": name, "problem_id": current_problem_id(workspace), "path": str(target), "pub": f"pub -> {pub(workspace).resolve()}"}, indent=2))


def replace_token(root: Path, token: str, value: str) -> None:
    for path in root.rglob("*"):
        if path.is_file() and (path.suffix in {".md", ".sh", ".json", ".py", ".toml"} or path.name in {"explore", "review"}):
            text = path.read_text(encoding="utf-8")
            if token in text:
                path.write_text(text.replace(token, value), encoding="utf-8")


def cmd_eval(workspace: Path, cwd: Path, args: argparse.Namespace, *, emit: bool = True) -> dict[str, Any]:
    agent_dir = find_agent_dir(workspace, cwd)
    require_reflection_complete(agent_dir)
    state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
    if state.get("phase") != "work_loop":
        raise SystemExit("formal eval can be submitted only when loop_state phase is work_loop")
    if state.get("eval_status") in {"queued", "running"}:
        raise SystemExit(f"formal eval already {state.get('eval_status')}; wait for the active job to finish")
    failure_stage = "formal_eval"
    try:
        contract = load_evaluation_contract(workspace, require_configured=True)
        registry = load_evaluation_registry(workspace, require_configured=True)
        validate_evaluation_pair(workspace, contract, registry)
        enforce_validation_information_budget(workspace, agent_dir.name, contract)
        failure_stage = "check"
        candidate = resolve_candidate(agent_dir, str(args.candidate))
        validate_candidate_for_contract(candidate, contract)
        check_info = run_registered_candidate_check(workspace, agent_dir, candidate, contract)
        submission = snapshot_candidate(workspace, agent_dir, candidate, contract)
        ai_review = contract.get("ai_review") if isinstance(contract.get("ai_review"), dict) else None
        job_info = queue_formal_eval_job(
            workspace,
            agent_dir,
            {
                "message": args.message,
                "submission_id": submission["submission_id"],
                "candidate_digest": submission["digest"],
                "contract_digest": evaluation_contract_digest(contract),
                "review_prompt_digest": ai_review.get("prompt_digest") if ai_review else None,
                "review_knowledge_digest": evaluation_knowledge_digest(workspace) if ai_review else None,
                "review_baseline_digest": evaluation_baseline_digest(workspace) if ai_review else None,
                "evaluation_space": evaluation_search_space(contract),
                "check": check_info,
            },
            state,
        )
        set_loop_state(
            agent_dir,
            "work_loop",
            eval_status="queued",
            active_eval={
                "job": job_info["job"],
                "id": job_info["job"],
                "submission_id": submission["submission_id"],
                "candidate_digest": submission["digest"],
                "log": job_info["log"],
                "resources": job_info["resources"],
                "queued_at": now(),
            },
            last_error=None,
        )
        if emit:
            print(json.dumps(job_info, indent=2, sort_keys=True))
        return job_info
    except EvalCommandFailed as exc:
        record_eval_failure(agent_dir, exc, stage="check")
        if emit:
            print(str(exc), file=sys.stderr)
            raise SystemExit(shell_exit_code(exc.returncode)) from None
        raise SystemExit(str(exc)) from None
    except SystemExit as exc:
        record_eval_failure(agent_dir, exc, stage=failure_stage)
        raise
    except Exception as exc:
        record_eval_failure(agent_dir, exc, stage=failure_stage)
        raise SystemExit(str(exc)) from None


def require_reflection_complete(agent_dir: Path) -> None:
    state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
    if state.get("phase") != "reflection_loop":
        return
    last_version = state.get("last_version")
    if last_version and state.get("last_reflected_version") != last_version:
        raise SystemExit(
            "reflection required before the next formal eval; run "
            f"`./explore reflect --version {last_version} --summary-file <summary.md> --note-file <reflection.md> --next-plan-file <next_plan.md>`"
        )


def evaluation_contract_path(workspace: Path) -> Path:
    return pub(workspace) / "evaluation" / "contract.json"


def evaluation_registry_path(workspace: Path) -> Path:
    return private(workspace) / "evaluation_registry.json"


def load_evaluation_contract(workspace: Path, *, require_configured: bool) -> dict[str, Any]:
    path = evaluation_contract_path(workspace)
    data = read_json(path, {})
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"Problem evaluation contract is missing or invalid: {rel(workspace, path)}")
    return validate_evaluation_contract_data(workspace, data, require_configured=require_configured)


def validate_evaluation_contract_data(
    workspace: Path,
    data: dict[str, Any],
    *,
    require_configured: bool,
    require_metric_roles: bool = False,
) -> dict[str, Any]:
    if int(data.get("schema_version") or 0) != 1:
        raise SystemExit("Problem evaluation contract schema_version must be 1")
    if str(data.get("problem_id") or "") != current_problem_id(workspace):
        raise SystemExit("Problem evaluation contract problem_id does not match this Problem")
    if require_configured and data.get("configured") is not True:
        raise SystemExit("Problem evaluator is not configured; Human/Main Agent must complete and register the evaluation contract")
    level = str(data.get("evidence_level") or "")
    if level not in {"L1", "L2", "L3"}:
        raise SystemExit("evaluation contract evidence_level must be L1, L2, or L3")
    candidate = data.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("kind") not in {"file", "directory", "file_or_directory"}:
        raise SystemExit("evaluation contract candidate.kind must be file, directory, or file_or_directory")
    if candidate.get("reject_symlinks") is not True:
        raise SystemExit("evaluation contract candidate.reject_symlinks must be true")
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        raise SystemExit("evaluation contract metrics must be an object")
    for name, spec in metrics.items():
        if not isinstance(name, str) or not SAFE_ID_RE.fullmatch(name) or not isinstance(spec, dict):
            raise SystemExit("evaluation contract metrics must use safe names and object specifications")
        if spec.get("direction") not in {"higher", "lower"}:
            raise SystemExit(f"evaluation metric {name} must declare direction higher or lower")
        role = spec.get("role")
        if role is not None and role not in {"breakthrough", "guardrail"}:
            raise SystemExit(f"evaluation metric {name} role must be breakthrough or guardrail")
        if require_metric_roles and role is None:
            raise SystemExit(f"evaluation metric {name} must declare role breakthrough or guardrail before activation")
        forbidden = sorted({"description", "floor_gate", "floor_passed", "floor_status", "weight"} & set(spec))
        if forbidden:
            raise SystemExit(
                f"evaluation metric {name} contains research-judgment fields that belong in pub/README.md: "
                + ", ".join(forbidden)
            )
    ai_review = data.get("ai_review")
    if ai_review is not None:
        validate_ai_review_contract(workspace, ai_review)
    if not metrics and ai_review is None:
        raise SystemExit("evaluation contract must declare at least one metric or ai_review dimension")
    compatible_digests = data.get("compatible_contract_digests", [])
    if not isinstance(compatible_digests, list) or not all(
        isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for digest in compatible_digests
    ):
        raise SystemExit("evaluation contract compatible_contract_digests must be a list of SHA-256 hex digests")
    check = data.get("check")
    if not isinstance(check, dict) or not valid_command_array(check.get("command")):
        raise SystemExit("evaluation contract check.command must be a non-empty argv array")
    if "{candidate}" not in "\n".join(check["command"]):
        raise SystemExit("evaluation contract check.command must consume {candidate}")
    check_text = "\n".join(check["command"])
    if ".DiscoveryConsole/private" in check_text or str(private(workspace)) in check_text:
        raise SystemExit("public Candidate Check must not reference the Problem private space")
    require_not_private(workspace, registered_cwd(workspace, check.get("cwd"), "candidate check cwd"), "candidate check cwd")
    feedback = data.get("feedback")
    expected_space = "development" if level == "L1" else "validation"
    if not isinstance(feedback, dict) or feedback.get("search_space") != expected_space:
        raise SystemExit(f"evaluation contract feedback.search_space must be {expected_space} for {level}")
    if level in {"L2", "L3"}:
        validate_information_budget(feedback.get("information_budget"), metrics_enabled=bool(metrics), review_enabled=ai_review is not None)
    return data


def validate_ai_review_contract(workspace: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("evaluation contract ai_review must be an object")
    raw_prompt = value.get("prompt")
    if not isinstance(raw_prompt, str) or not raw_prompt:
        raise SystemExit("evaluation contract ai_review.prompt must be a non-empty public path")
    prompt_path = require_not_private(workspace, require_under(workspace / raw_prompt, pub(workspace), "reviewer prompt"), "reviewer prompt")
    if not prompt_path.is_file():
        raise SystemExit("evaluation contract ai_review.prompt does not exist")
    if "discovery-reviewer-rubric-placeholder" in prompt_path.read_text(encoding="utf-8"):
        raise SystemExit("evaluation contract ai_review.prompt is still the template placeholder")
    digest = value.get("prompt_digest")
    actual = file_digest(prompt_path)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != actual:
        raise SystemExit("evaluation contract ai_review.prompt_digest must match reviewer_prompt.md")
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        raise SystemExit("evaluation contract ai_review.dimensions must be a non-empty object")
    for dimension, spec in dimensions.items():
        if not isinstance(dimension, str) or not SAFE_ID_RE.fullmatch(dimension) or not isinstance(spec, dict):
            raise SystemExit("ai_review dimensions must use safe ids and object specifications")
        if not isinstance(spec.get("label"), str) or not spec["label"].strip():
            raise SystemExit(f"ai_review dimension {dimension} must declare a non-empty label")
        if set(spec) - {"label"}:
            raise SystemExit(f"ai_review dimension {dimension} only permits label")
    if set(value) - {"prompt", "prompt_digest", "dimensions"}:
        raise SystemExit("evaluation contract ai_review contains unsupported fields")
    return value


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_evaluation_registry(workspace: Path, *, require_configured: bool) -> dict[str, Any]:
    path = evaluation_registry_path(workspace)
    data = read_json(path, {})
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"Problem evaluation registry is missing or invalid: {rel(workspace, path)}")
    return validate_evaluation_registry_data(workspace, data, require_configured=require_configured)


def validate_evaluation_registry_data(workspace: Path, data: dict[str, Any], *, require_configured: bool) -> dict[str, Any]:
    if int(data.get("schema_version") or 0) != 1:
        raise SystemExit("Problem evaluation registry schema_version must be 1")
    if str(data.get("problem_id") or "") != current_problem_id(workspace):
        raise SystemExit("Problem evaluation registry problem_id does not match this Problem")
    if require_configured and data.get("configured") is not True:
        raise SystemExit("Problem private evaluator registry is not configured")
    evaluators = data.get("evaluators")
    if not isinstance(evaluators, dict):
        raise SystemExit("Problem evaluation registry evaluators must be an object")
    for space, node in evaluators.items():
        if space not in {"development", "validation", "test"} or not isinstance(node, dict):
            raise SystemExit("evaluation registry has an invalid evaluator space")
        if "ai_reviewer" in node:
            reviewer = node["ai_reviewer"]
            if space == "test":
                raise SystemExit("test evaluator must not register ai_reviewer")
            if not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), str) or not reviewer["id"].strip():
                raise SystemExit(f"ai_reviewer for {space} must declare id")
            model, effort = str(reviewer.get("model") or ""), str(reviewer.get("reasoning_effort") or "")
            resolve_headless_model_selection(workspace, model, effort)
            if set(reviewer) - {"id", "backend", "model", "reasoning_effort"} or reviewer.get("backend") != "codex":
                raise SystemExit(f"ai_reviewer for {space} must use the codex backend")
    return data


def valid_command_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(part, str) and part for part in value)


def evaluation_search_space(contract: dict[str, Any]) -> str:
    return "development" if contract.get("evidence_level") == "L1" else "validation"


def evaluation_contract_digest(contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_activate_evaluation(workspace: Path, contract: dict[str, Any], registry: dict[str, Any]) -> None:
    contract_path = evaluation_contract_path(workspace)
    registry_path = evaluation_registry_path(workspace)
    contract_tmp = contract_path.with_name(contract_path.name + ".activate-tmp")
    registry_tmp = registry_path.with_name(registry_path.name + ".activate-tmp")
    try:
        write_json(contract_tmp, contract)
        write_json(registry_tmp, registry)
        # Registry-first is fail-closed: until the public contract is replaced,
        # Route submissions still see configured=false.
        os.replace(registry_tmp, registry_path)
        os.replace(contract_tmp, contract_path)
    finally:
        contract_tmp.unlink(missing_ok=True)
        registry_tmp.unlink(missing_ok=True)


def validate_information_budget(value: Any, *, metrics_enabled: bool = True, review_enabled: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("L2/L3 evaluation contract must declare a structured feedback.information_budget")
    maximum = value.get("max_submissions_per_route")
    precision = value.get("precision_decimals")
    released = value.get("released_fields")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise SystemExit("information_budget.max_submissions_per_route must be a positive integer")
    if metrics_enabled and (not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 12):
        raise SystemExit("information_budget.precision_decimals must be an integer from 0 to 12")
    if not metrics_enabled and precision is not None:
        raise SystemExit("information_budget.precision_decimals is only valid when metrics are enabled")
    if not isinstance(released, list) or not released or not all(isinstance(field, str) for field in released):
        raise SystemExit("information_budget.released_fields must be a non-empty string array")
    allowed = {"metrics", "ai_review_scores"}
    unknown = sorted(set(released) - allowed)
    if unknown:
        raise SystemExit("information_budget.released_fields contains unsupported fields: " + ", ".join(unknown))
    required = ({"metrics"} if metrics_enabled else set()) | ({"ai_review_scores"} if review_enabled else set())
    if not required.issubset(set(released)):
        raise SystemExit("information_budget.released_fields must include every enabled evaluation channel")
    return value


def enforce_validation_information_budget(workspace: Path, agent: str, contract: dict[str, Any]) -> None:
    if contract.get("evidence_level") not in {"L2", "L3"}:
        return
    budget = validate_information_budget(
        contract["feedback"].get("information_budget"),
        metrics_enabled=bool(contract.get("metrics")),
        review_enabled=isinstance(contract.get("ai_review"), dict),
    )
    used = sum(
        1
        for job in read_jsonl(job_index(workspace))
        if job.get("kind") == "formal_eval" and job.get("agent") == agent and job.get("status") != "cancelled"
    )
    maximum = int(budget["max_submissions_per_route"])
    if used >= maximum:
        raise SystemExit(f"Validation information budget exhausted for {agent}: {used}/{maximum} formal submissions")


def validate_evaluation_pair(workspace: Path, contract: dict[str, Any], registry: dict[str, Any]) -> None:
    space = evaluation_search_space(contract)
    required_spaces = [space, "test"] if contract.get("evidence_level") == "L3" else [space]
    for required_space in required_spaces:
        evaluator = registry.get("evaluators", {}).get(required_space)
        if not isinstance(evaluator, dict):
            raise SystemExit(f"private evaluator for {required_space} is not registered")
        objective = valid_command_array(evaluator.get("command"))
        reviewer = isinstance(evaluator.get("ai_reviewer"), dict)
        if required_space == "test":
            reviewer = False
        if not objective and not reviewer:
            raise SystemExit(f"private evaluator for {required_space} has no enabled channel")
        if required_space == space and bool(contract.get("metrics")) != objective:
            raise SystemExit(f"private evaluator for {required_space} does not match the contract metrics channel")
        if required_space == space and (contract.get("ai_review") is not None) != reviewer:
            raise SystemExit(f"private evaluator for {required_space} does not match the contract ai_review channel")
        if objective:
            joined = "\n".join(evaluator["command"])
            if "{candidate}" not in joined or "{report}" not in joined:
                raise SystemExit(f"private evaluator for {required_space} must consume {{candidate}} and produce {{report}}")
            registered_cwd(workspace, evaluator.get("cwd"), f"{required_space} evaluator cwd")
    registry_digest = str(registry.get("public_contract_digest") or "")
    actual_digest = evaluation_contract_digest(contract)
    if registry_digest and registry_digest != actual_digest:
        raise SystemExit("private evaluator registry is stale: public_contract_digest does not match contract.json")


def resolve_candidate(agent_dir: Path, raw_path: str) -> Path:
    raw = Path(raw_path)
    lexical = raw if raw.is_absolute() else agent_dir / raw
    if lexical.is_symlink():
        raise SystemExit("Candidate path must not be a symlink")
    candidate = agent_relative_path(agent_dir, raw_path, "Candidate")
    if not candidate.is_file() and not candidate.is_dir():
        raise SystemExit("Candidate must be a regular file or directory")
    return candidate


def validate_candidate_for_contract(candidate: Path, contract: dict[str, Any]) -> None:
    kind = contract["candidate"]["kind"]
    if kind == "file" and not candidate.is_file():
        raise SystemExit("Candidate contract requires a file")
    if kind == "directory" and not candidate.is_dir():
        raise SystemExit("Candidate contract requires a directory")
    files = candidate_files(candidate)
    max_files, max_bytes = candidate_limits(contract)
    total_bytes = sum(path.stat().st_size for _, path in files)
    if len(files) > max_files:
        raise SystemExit(f"Candidate contains {len(files)} files; contract limit is {max_files}")
    if total_bytes > max_bytes:
        raise SystemExit(f"Candidate contains {total_bytes} bytes; contract limit is {max_bytes}")


def expand_registered_command(command: list[str], values: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for part in command:
        value = part
        for name, replacement in values.items():
            value = value.replace("{" + name + "}", replacement)
        expanded.append(value)
    return expanded


def registered_cwd(workspace: Path, raw: Any, label: str) -> Path:
    value = str(raw or ".")
    path = Path(value)
    resolved = path if path.is_absolute() else workspace / path
    resolved = require_under(resolved, workspace, label)
    if not resolved.is_dir():
        raise SystemExit(f"{label} is not a directory: {value}")
    return resolved


def run_registered_candidate_check(workspace: Path, agent_dir: Path, candidate: Path, contract: dict[str, Any]) -> dict[str, Any]:
    check = contract["check"]
    command = expand_registered_command(
        list(check["command"]),
        {
            "candidate": str(candidate),
            "workspace": str(workspace),
            "agent": agent_dir.name,
        },
    )
    job_id = next_job_id("eval-check")
    log_path = pub(workspace) / "log" / f"{job_id}.log"
    resources = eval_check_resources(workspace, agent_dir)
    validate_resource_request(workspace, agent_dir.name, resources, "run")
    job = {
        "id": job_id,
        "status": "running",
        "kind": "eval_check",
        "reason": None,
        "returncode": None,
        "pid": None,
        "pgid": None,
        "agent": agent_dir.name,
        "command": ["<problem-registered-candidate-check>"],
        "display_command": "Problem-registered Candidate check",
        "cwd": rel(workspace, registered_cwd(workspace, check.get("cwd"), "candidate check cwd")),
        "resources": resources,
        "allocated_gpus": [] if resources["gpus"] == "any" else resources["gpus"],
        "log": rel(workspace, log_path),
        "created_at": now(),
        "launcher": "eval-check",
        "enforcement": "systemd_cgroup_v2",
    }
    upsert_job(workspace, job)
    execution_job = dict(job)
    execution_job["command"] = command
    execution_job["cwd"] = str(registered_cwd(workspace, check.get("cwd"), "candidate check cwd"))
    result = launch_and_wait_job(workspace, execution_job, allocated_gpus=job["allocated_gpus"], stream_output=True)
    info = {"id": job_id, "returncode": result.get("returncode"), "status": result.get("status"), "reason": result.get("reason"), "log": rel(workspace, log_path), "resources": resources}
    if result.get("status") != "done" or int(result.get("returncode") or 0) != 0:
        raise EvalCommandFailed(int(result.get("returncode") or 1), str(info["log"]))
    return info


def candidate_limits(contract: dict[str, Any]) -> tuple[int, int]:
    candidate = contract["candidate"]
    max_files = candidate.get("max_files", 10000)
    max_bytes = candidate.get("max_bytes", 1073741824)
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files <= 0:
        raise SystemExit("evaluation contract candidate.max_files must be a positive integer")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise SystemExit("evaluation contract candidate.max_bytes must be a positive integer")
    return max_files, max_bytes


def candidate_files(candidate: Path) -> list[tuple[str, Path]]:
    if candidate.is_file():
        return [(candidate.name, candidate)]
    files: list[tuple[str, Path]] = []
    for path in sorted(candidate.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"Candidate must not contain symlinks: {path.relative_to(candidate)}")
        if path.is_file():
            files.append((str(path.relative_to(candidate)), path))
        elif not path.is_dir():
            raise SystemExit(f"Candidate contains an unsupported filesystem object: {path.relative_to(candidate)}")
    return files


def candidate_digest(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for relative, path in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def snapshot_candidate(workspace: Path, agent_dir: Path, candidate: Path, contract: dict[str, Any]) -> dict[str, Any]:
    validate_candidate_for_contract(candidate, contract)
    files = candidate_files(candidate)
    max_files, max_bytes = candidate_limits(contract)
    total_bytes = sum(path.stat().st_size for _, path in files)
    if len(files) > max_files:
        raise SystemExit(f"Candidate contains {len(files)} files; contract limit is {max_files}")
    if total_bytes > max_bytes:
        raise SystemExit(f"Candidate contains {total_bytes} bytes; contract limit is {max_bytes}")
    digest = candidate_digest(files)
    submission_id = next_job_id("submission")
    submission_root = private(workspace) / "eval_submissions" / submission_id
    require_under(submission_root, private(workspace) / "eval_submissions", "submission directory")
    submission_root.mkdir(parents=True, exist_ok=False)
    snapshot_root = submission_root / "candidate"
    if candidate.is_dir():
        shutil.copytree(candidate, snapshot_root)
        candidate_entry = snapshot_root
    else:
        snapshot_root.mkdir()
        candidate_entry = snapshot_root / candidate.name
        shutil.copy2(candidate, candidate_entry)
    frozen_tree = freeze_route_tree(agent_dir)
    manifest = {
        "schema_version": 1,
        "problem_id": current_problem_id(workspace),
        "submission_id": submission_id,
        "agent": agent_dir.name,
        "source": str(candidate.relative_to(agent_dir)),
        "candidate_entry": str(candidate_entry.relative_to(submission_root)),
        "candidate_kind": "directory" if candidate.is_dir() else "file",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "digest": digest,
        "tree": frozen_tree["tree"],
        "parent": frozen_tree["parent"],
        "snapshot_rule": "temporary_git_index_full_route_tree_excluding_gitignored",
        "created_at": now(),
    }
    write_json(submission_root / "submission.json", manifest)
    for path in sorted(snapshot_root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    snapshot_root.chmod(0o555)
    return manifest


def freeze_route_tree(agent_dir: Path) -> dict[str, Any]:
    """Capture the submitted Route tree without changing its real Git index."""
    ensure_agent_git(agent_dir)
    parent = git_stdout(agent_dir, ["rev-parse", "--verify", "HEAD"], check=False).strip() or None
    index_path = agent_dir / ".git" / f"discovery-submission-{secrets.token_hex(12)}.index"
    env = dict(GIT_ENV)
    env["GIT_INDEX_FILE"] = str(index_path)
    try:
        if parent:
            run(["git", "read-tree", parent], cwd=agent_dir, env=env, quiet=True)
        else:
            # An absent index means an empty index; an empty *file* is invalid.
            index_path.unlink(missing_ok=True)
        run(["git", "add", "-A"], cwd=agent_dir, env=env, quiet=True)
        proc = subprocess.run(["git", "write-tree"], cwd=agent_dir, env={**os.environ, **env}, capture_output=True, text=True, check=True)
        tree = proc.stdout.strip()
        if not tree:
            raise SystemExit("could not create frozen Route tree")
        return {"tree": tree, "parent": parent}
    finally:
        index_path.unlink(missing_ok=True)


def queue_formal_eval_job(workspace: Path, agent_dir: Path, metadata: dict[str, Any], loop_state: dict[str, Any]) -> dict[str, Any]:
    resources = eval_formal_resources(workspace, agent_dir.name)
    validate_resource_request(workspace, agent_dir.name, resources, "formal_eval")
    job_id = next_job_id("eval")
    log_path = pub(workspace) / "log" / f"{job_id}.log"
    job = {
        "id": job_id,
        "status": "queued",
        "kind": "formal_eval",
        "reason": None,
        "returncode": None,
        "pid": None,
        "pgid": None,
        "agent": agent_dir.name,
        "agent_dir": rel(workspace, agent_dir),
        "command": ["<problem-registered-formal-evaluator>"],
        "display_command": "Problem-registered formal evaluator",
        "cwd": ".",
        "resources": resources,
        "allocated_gpus": None,
        "log": rel(workspace, log_path),
        "created_at": now(),
        "launcher": "eval",
        "enforcement": "systemd_cgroup_v2",
        "created_from_loop_state": loop_state,
        "formal_eval_metadata": metadata,
    }
    upsert_job(workspace, job)
    return {
        "job": job_id,
        "status": "queued",
        "submission_id": metadata.get("submission_id"),
        "candidate_digest": metadata.get("candidate_digest"),
        "log": rel(workspace, log_path),
        "resources": resources,
        "check": metadata.get("check"),
    }


def finalize_eval_practice(
    workspace: Path,
    agent_dir: Path,
    report_path: Path | None,
    review_result: dict[str, Any] | None,
    message: str,
    space: str,
    contract: dict[str, Any],
    run_info: dict[str, Any],
    validated_metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    agent = agent_dir.name
    version = next_version_id(workspace, agent)
    if validated_metrics is not None:
        metrics = dict(validated_metrics)
    elif report_path is not None:
        metrics = validate_registered_eval_report(report_path, contract)
    else:
        metrics = {}
    if metrics and contract.get("evidence_level") in {"L2", "L3"}:
        precision = int(validate_information_budget(contract["feedback"].get("information_budget"))["precision_decimals"])
        metrics = {name: round(value, precision) for name, value in metrics.items()}
    metric_specs = contract["metrics"]
    metric_directions = {name: str(metric_specs[name]["direction"]) for name in metrics}
    metric_roles = {
        name: str(metric_specs[name]["role"])
        for name in metrics
        if metric_specs[name].get("role") in {"breakthrough", "guardrail"}
    }
    metric_direction_sources = {name: "problem_contract" for name in metrics}
    node = {
        "entity_type": "version",
        "id": version,
        "scope": f"problem:{current_problem_id(workspace)}",
        "problem_id": current_problem_id(workspace),
        "agent": agent,
        "space": space,
        "metrics": metrics,
        "metric_directions": metric_directions,
        "submission_message": message,
        "summary": "",
        "knowledge_status": "awaiting_reflection",
        "note": "",
        "snapshot": {"type": "git", "repo": agent_dir.name, "tree": run_info.get("tree"), "parent": run_info.get("parent"), "commit": None, "tag": None},
        "contract_digest": evaluation_contract_digest(contract),
        "evidence_space": space,
        "eval_run": run_info,
        "created_at": now(),
    }
    if metric_roles:
        node["metric_roles"] = metric_roles
    if review_result is not None:
        node["ai_review"] = published_ai_review(contract, review_result)
    node["eval_feedback"] = build_eval_feedback(workspace, node, metric_directions, metric_direction_sources)
    write_practice(workspace, node)
    return {"version": version, "metrics": metrics, "ai_review": node.get("ai_review"), "eval_feedback": node["eval_feedback"], "snapshot": node["snapshot"], "eval_run": run_info}


def validate_registered_eval_report(report_path: Path, contract: dict[str, Any]) -> dict[str, float]:
    data = read_report(report_path)
    if not isinstance(data, dict):
        raise SystemExit("registered evaluator report must be a JSON object")
    raw_metrics = data.get("metrics")
    if not isinstance(raw_metrics, dict):
        raise SystemExit("registered evaluator report must contain a metrics object")
    metrics = numeric_metrics(raw_metrics)
    declared = set(contract["metrics"])
    actual = set(metrics)
    missing = sorted(declared - actual)
    unknown = sorted(actual - declared)
    nonnumeric = sorted(set(raw_metrics) & declared - actual)
    if missing or unknown or nonnumeric:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        if nonnumeric:
            details.append("nonnumeric=" + ",".join(nonnumeric))
        raise SystemExit("registered evaluator report violates the metric contract: " + "; ".join(details))
    return metrics


def validate_ai_review_result(path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    data = read_report(path)
    if not isinstance(data, dict) or set(data) != {"schema_version", "dimensions"} or data.get("schema_version") != 1:
        raise SystemExit("reviewer result must contain only schema_version and dimensions")
    raw_dimensions = data.get("dimensions")
    expected = (contract.get("ai_review") or {}).get("dimensions", {})
    if not isinstance(raw_dimensions, dict) or set(raw_dimensions) != set(expected):
        raise SystemExit("reviewer result dimensions do not match the review contract")
    dimensions: dict[str, dict[str, Any]] = {}
    for ident, value in raw_dimensions.items():
        if not isinstance(value, dict) or set(value) != {"score", "rationale"}:
            raise SystemExit(f"reviewer result dimension {ident} must contain only score and rationale")
        score, rationale = value.get("score"), value.get("rationale")
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
            raise SystemExit(f"reviewer result dimension {ident} score must be an integer from 1 to 10")
        if not isinstance(rationale, str) or not rationale.strip():
            raise SystemExit(f"reviewer result dimension {ident} rationale must be non-empty")
        dimensions[ident] = {"score": score, "rationale": rationale.strip()}
    return {"schema_version": 1, "dimensions": dimensions}


def published_ai_review(contract: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    dimensions = review.get("dimensions", {})
    if contract.get("evidence_level") == "L1":
        return {"dimensions": dimensions}
    return {"dimensions": {ident: {"score": value["score"]} for ident, value in dimensions.items()}}


def evaluation_knowledge_digest(workspace: Path) -> str:
    """Digest the only Problem knowledge visible to an AI Reviewer."""
    root = knowledge_root(workspace)
    digest = hashlib.sha256()
    for path in [root / "items.json", root / "topics.json", *sorted((root / "items").rglob("*"))]:
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8")); digest.update(b"\0")
        digest.update(path.read_bytes()); digest.update(b"\0")
    return digest.hexdigest()


def evaluation_baseline_digest(workspace: Path) -> str:
    """Digest the public Baseline material visible to an AI Reviewer."""
    root = pub(workspace) / "baseline"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8")); digest.update(b"\0")
        digest.update(path.read_bytes()); digest.update(b"\0")
    return digest.hexdigest()


def record_eval_failure(agent_dir: Path, error: BaseException, stage: str = "eval", active_eval: dict[str, Any] | None = None) -> None:
    message = str(error)
    code: int | None
    if isinstance(error, EvalCommandFailed):
        code = error.returncode
    else:
        code = error.code if isinstance(error, SystemExit) and isinstance(error.code, int) else None
    if stage == "check" and active_eval is None and isinstance(error, EvalCommandFailed):
        active_eval = {"job": Path(error.log).stem, "log": error.log, "status": "failed"}
    set_loop_state(
        agent_dir,
        "work_loop",
        eval_status="check_failed" if stage == "check" else "main_review",
        active_eval=active_eval,
        last_error={"stage": stage, "message": message, "exit_code": code, "at": now()},
    )


def find_agent_dir(workspace: Path, cwd: Path) -> Path:
    for path in (cwd.resolve(), *cwd.resolve().parents):
        if path.parent == workspace.resolve() and AGENT_NAME_RE.fullmatch(path.name):
            return path
    raise SystemExit("run this command inside an agent workspace")


def ensure_agent_git(agent_dir: Path) -> None:
    if not (agent_dir / ".git").exists():
        run(["git", "init"], cwd=agent_dir, quiet=True)


def next_version_id(workspace: Path, agent: str) -> str:
    prefix = f"version-{agent}-"
    numbers: list[int] = []
    for row in read_versions(knowledge_root(workspace)):
        value = str(row.get("id", ""))
        if value.startswith(prefix):
            try:
                numbers.append(int(value.rsplit("-", 1)[1]))
            except ValueError:
                pass
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def snapshot_code(agent_dir: Path, version: str, tree: str, parent: str | None, summary: str) -> dict[str, Any]:
    """Materialize the already-evaluated frozen tree only after Reflection."""
    if not tree:
        raise SystemExit("provisional Version has no frozen Git tree")
    command = ["git", "commit-tree", tree]
    if parent:
        command.extend(["-p", parent])
    command.extend(["-m", f"version: {version}\n\n{summary}"])
    proc = subprocess.run(command, cwd=agent_dir, env={**os.environ, **GIT_ENV}, capture_output=True, text=True, check=True)
    commit = proc.stdout.strip()
    tag = f"snapshot-{version}"
    run(["git", "tag", tag, commit], cwd=agent_dir, quiet=True)
    return {"type": "git", "repo": agent_dir.name, "tree": tree, "commit": commit, "parent": parent, "tag": tag}


def read_report(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise
        return json.loads(lines[-1])


def numeric_metrics(data: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in data.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
        elif isinstance(value, dict) and isinstance(value.get("value"), (int, float)) and not isinstance(value.get("value"), bool):
            out[key] = float(value["value"])
    return out


def metric_directions_from_metrics(data: dict[str, Any]) -> dict[str, str]:
    directions: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        direction = value.get("direction") or value.get("goal")
        if isinstance(direction, str):
            normalized = normalize_direction(direction)
            if normalized:
                directions[key] = normalized
        higher_is_better = value.get("higher_is_better")
        if isinstance(higher_is_better, bool):
            directions[key] = "higher" if higher_is_better else "lower"
    return directions


def normalize_direction(value: str) -> str | None:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"higher", "high", "maximize", "max", "larger", "greater", "up"}:
        return "higher"
    if normalized in {"lower", "low", "minimize", "min", "smaller", "less", "down"}:
        return "lower"
    return None


def build_eval_feedback(workspace: Path, node: dict[str, Any], directions: dict[str, str], direction_sources: dict[str, str]) -> dict[str, Any]:
    prior_rows = read_versions(knowledge_root(workspace))
    all_rows = [*prior_rows, node]
    metrics = node.get("metrics", {})
    feedback: dict[str, Any] = {"generated_at": now(), "metrics": {}}
    if isinstance(node.get("ai_review"), dict):
        feedback["ai_review"] = node["ai_review"]
    if not isinstance(metrics, dict):
        return feedback

    for metric, value in metrics.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        direction = directions.get(metric, "higher")
        own_rows = rows_with_metric(all_rows, metric, agent=str(node.get("agent", "")))
        global_rows = rows_with_metric(all_rows, metric)
        previous = previous_metric_value(prior_rows, metric, str(node.get("agent", "")))
        own_rank = rank_row(own_rows, str(node.get("id", "")), metric, direction)
        global_rank = rank_row(global_rows, str(node.get("id", "")), metric, direction)
        best_own = best_row(own_rows, metric, direction)
        best_global = best_row(global_rows, metric, direction)
        feedback["metrics"][metric] = {
            "value": float(value),
            "direction": direction,
            "direction_source": direction_sources.get(metric, "unknown"),
            "previous_value": previous,
            "change_from_previous": None if previous is None else float(value) - previous,
            "own_rank": own_rank,
            "global_rank": global_rank,
            "own_best": summarize_metric_row(best_own, metric),
            "global_best": summarize_metric_row(best_global, metric),
            "no_improvement_rounds": no_improvement_rounds(own_rows, metric, direction),
        }
    return feedback


def rows_with_metric(rows: list[dict[str, Any]], metric: str, agent: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if agent is not None and row.get("agent") != agent:
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        value = metrics.get(metric)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(row)
    return out


def previous_metric_value(rows: list[dict[str, Any]], metric: str, agent: str) -> float | None:
    for row in reversed(rows):
        if row.get("agent") != agent:
            continue
        metrics = row.get("metrics")
        if isinstance(metrics, dict):
            value = metrics.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def metric_value(row: dict[str, Any], metric: str) -> float:
    return float(row["metrics"][metric])


def is_better(value: float, reference: float, direction: str) -> bool:
    return value > reference if direction == "higher" else value < reference


def rank_row(rows: list[dict[str, Any]], row_id: str, metric: str, direction: str) -> dict[str, int]:
    current = next((row for row in rows if row.get("id") == row_id), None)
    if current is None:
        return {"rank": 0, "of": len(rows)}
    current_value = metric_value(current, metric)
    better = sum(1 for row in rows if is_better(metric_value(row, metric), current_value, direction))
    return {"rank": better + 1, "of": len(rows)}


def best_row(rows: list[dict[str, Any]], metric: str, direction: str) -> dict[str, Any] | None:
    if not rows:
        return None
    reverse = direction == "higher"
    return sorted(rows, key=lambda row: metric_value(row, metric), reverse=reverse)[0]


def summarize_metric_row(row: dict[str, Any] | None, metric: str) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "version": row.get("id"),
        "agent": row.get("agent"),
        "value": metric_value(row, metric),
    }


def no_improvement_rounds(rows: list[dict[str, Any]], metric: str, direction: str) -> int:
    best: float | None = None
    last_improvement_index = -1
    for idx, row in enumerate(rows):
        value = metric_value(row, metric)
        if best is None or is_better(value, best, direction):
            best = value
            last_improvement_index = idx
    if last_improvement_index < 0:
        return 0
    return len(rows) - 1 - last_improvement_index


def safe_file_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return token.strip("._") or "version"


def unused_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"could not find unused archive path for {path}")


def archive_notebook_for_reflection(agent_dir: Path, version: str, next_plan: str) -> dict[str, Any]:
    archive_dir = agent_dir / "notebooks"
    archive_dir.mkdir(parents=True, exist_ok=True)
    token = safe_file_token(version)

    notebook = agent_dir / "notebook.md"
    archive: dict[str, Any] = {"directory": "notebooks"}
    if notebook.exists():
        previous_path = unused_path(archive_dir / f"{token}_previous_notebook.md")
        notebook.replace(previous_path)
        archive["previous_notebook"] = str(previous_path.relative_to(agent_dir))
    else:
        archive["previous_notebook"] = None

    next_plan_path = unused_path(archive_dir / f"{token}_next_plan.md")
    next_plan_path.write_text(next_plan.rstrip() + "\n", encoding="utf-8")
    archive["next_plan_copy"] = str(next_plan_path.relative_to(agent_dir))
    return archive


def archive_reflection_input_file(agent_dir: Path, version: str, source_path: Path, suffix: str) -> str | None:
    if not source_path.exists() or not source_path.is_file():
        return None
    notebook = agent_dir / "notebook.md"
    archive_dir = agent_dir / "notebooks"
    try:
        if source_path.resolve() == notebook.resolve():
            return None
        if archive_dir.resolve() in source_path.resolve().parents:
            return str(source_path.relative_to(agent_dir))
    except FileNotFoundError:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    token = safe_file_token(version)
    archived_path = unused_path(archive_dir / f"{token}_{suffix}.md")
    source_path.replace(archived_path)
    return str(archived_path.relative_to(agent_dir))


def cmd_reflect(workspace: Path, cwd: Path, args: argparse.Namespace, *, emit: bool = True) -> dict[str, Any]:
    agent_dir = find_agent_dir(workspace, cwd)
    state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
    if state.get("phase") != "reflection_loop":
        raise SystemExit("reflection can run only when loop_state phase is reflection_loop")
    if state.get("last_version") != args.version:
        raise SystemExit(f"reflection version must match current last_version: {state.get('last_version')}")
    summary_path = agent_relative_path(agent_dir, str(getattr(args, "summary_file", "")), "Version knowledge summary")
    note_path = agent_relative_path(agent_dir, args.note_file, "reflection note")
    next_plan_path = agent_relative_path(agent_dir, args.next_plan_file, "next target brief")
    node = read_practice(workspace, args.version)
    if node.get("agent") != agent_dir.name:
        raise SystemExit(f"practice version {args.version} belongs to {node.get('agent')}, not {agent_dir.name}")
    summary = summary_path.read_text(encoding="utf-8").strip()
    summary_words = re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)?", summary)
    if not summary or "\n\n" in summary or not 80 <= len(summary_words) <= 220:
        raise SystemExit("--summary-file must contain one non-empty paragraph of 80–220 English words")
    note = note_path.read_text(encoding="utf-8").strip()
    next_plan = next_plan_path.read_text(encoding="utf-8").strip()
    if not note or not next_plan:
        raise SystemExit("reflection note and next target brief must not be empty")
    node["summary"] = summary
    node["note"] = note
    node["next_plan"] = next_plan
    node["reflected_at"] = now()
    node["knowledge_status"] = "complete"
    provisional = node.get("snapshot") if isinstance(node.get("snapshot"), dict) else {}
    frozen_parent = provisional.get("parent")
    node["snapshot"] = snapshot_code(
        agent_dir,
        args.version,
        str(provisional.get("tree") or ""),
        frozen_parent if isinstance(frozen_parent, str) and frozen_parent else None,
        summary,
    )
    node["notebook_archive"] = archive_notebook_for_reflection(agent_dir, args.version, node["next_plan"])
    notebook = agent_dir / "notebook.md"
    notebook.write_text(node["next_plan"].rstrip() + "\n", encoding="utf-8")
    archived_note = archive_reflection_input_file(agent_dir, args.version, note_path, "reflection_input")
    archived_target = archive_reflection_input_file(agent_dir, args.version, next_plan_path, "target_brief_input")
    if archived_note:
        node["notebook_archive"]["reflection_input"] = archived_note
    if archived_target:
        node["notebook_archive"]["target_brief_input"] = archived_target
    write_practice(workspace, node)
    set_loop_state(agent_dir, "work_loop", last_reflected_version=args.version, eval_status=None, active_eval=None)
    payload = {"reflected": args.version, "knowledge_status": "complete", "notebook": "rotated", "archive": node.get("notebook_archive"), "snapshot": node.get("snapshot")}
    if emit:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main_agent_notices_path(workspace: Path) -> Path:
    return pub(workspace) / "notices.jsonl"


def cmd_notice(workspace: Path, cwd: Path, args: argparse.Namespace, *, emit: bool = True) -> Any:
    if args.notice_cmd == "list":
        payload: Any = format_main_agent_notices(read_jsonl(main_agent_notices_path(workspace)))
        if emit:
            print(payload)
        return payload
    elif args.notice_cmd == "add":
        require_main_workspace(workspace, cwd, "notice add")
        active = maintain_active_team_work(workspace)
        if active:
            raise SystemExit("Problem Team is not stationary:\n" + "\n".join(active))
        notice_id = safe_id(args.id, "notice id")
        notices = read_jsonl(main_agent_notices_path(workspace))
        if any(row.get("id") == notice_id for row in notices):
            raise SystemExit(f"notice id already exists: {notice_id}")
        title = str(args.title).strip()
        body = str(args.body).strip()
        if not title:
            raise SystemExit("notice title must not be empty")
        if not body:
            raise SystemExit("notice body must not be empty")
        notice = {
            "id": notice_id,
            "published_at": now(),
            "version_anchor": maintain_version_anchor(workspace),
            "priority": args.priority,
            "title": title,
            "body": body,
            "tags": [str(tag).strip() for tag in args.tag if str(tag).strip()],
        }
        append_jsonl(main_agent_notices_path(workspace), notice)
        if emit:
            print(format_main_agent_notices([notice]))
        return notice
    elif args.notice_cmd == "delete":
        require_main_workspace(workspace, cwd, "notice delete")
        active = maintain_active_team_work(workspace)
        if active:
            raise SystemExit("Problem Team is not stationary:\n" + "\n".join(active))
        notice_id = safe_id(args.id, "notice id")
        notices = read_jsonl(main_agent_notices_path(workspace))
        deleted = [row for row in notices if row.get("id") == notice_id]
        if not deleted:
            raise SystemExit(f"notice id not found: {notice_id}")
        write_jsonl(main_agent_notices_path(workspace), [row for row in notices if row.get("id") != notice_id])
        payload = {"deleted": notice_id}
        if emit:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return payload


def format_main_agent_notices(notices: list[dict[str, Any]]) -> str:
    lines = ["# Main Agent Notices", ""]
    valid = [notice for notice in notices if isinstance(notice, dict)]
    valid.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
    if not valid:
        lines.append("(none)")
        return "\n".join(lines)
    for notice in valid:
        priority = str(notice.get("priority") or "normal")
        title = str(notice.get("title") or notice.get("id") or "")
        body = str(notice.get("body") or "").strip()
        tags = notice.get("tags")
        tag_text = ", ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        lines.append(f"[{priority}] {title}")
        if body:
            lines.append(body)
        if tag_text:
            lines.append(f"tags: {tag_text}")
        lines.append(f"id: {notice.get('id', '')}")
        published_at = notice.get("published_at") or notice.get("created_at")
        if published_at:
            lines.append(f"published_at: {published_at}")
        anchor = notice.get("version_anchor")
        if isinstance(anchor, dict):
            lines.append("version_anchor: " + json.dumps(anchor, ensure_ascii=False, sort_keys=True))
        lines.append("")
    return "\n".join(lines).rstrip()


def read_practice(workspace: Path, version: str) -> dict[str, Any]:
    version = safe_id(version, "practice version", VERSION_RE)
    versions_dir = knowledge_versions_dir(knowledge_root(workspace))
    path = require_under(versions_dir / f"{version}.json", versions_dir, "practice version path")
    if not path.exists():
        raise SystemExit(f"practice version not found: {version}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_practice(workspace: Path, node: dict[str, Any]) -> None:
    version = safe_id(str(node["id"]), "practice version", VERSION_RE)
    node = dict(node)
    node.setdefault("entity_type", "version")
    node.setdefault("scope", f"problem:{current_problem_id(workspace)}")
    versions_dir = knowledge_versions_dir(knowledge_root(workspace))
    version_path = require_under(versions_dir / f"{version}.json", versions_dir, "practice version path")
    write_json(version_path, node)


def knowledge_root(workspace: Path) -> Path:
    return pub(workspace) / "knowledge"


def knowledge_items_path(root: Path) -> Path:
    return root / "items.json"


def knowledge_topics_path(root: Path) -> Path:
    return root / "topics.json"


def memory_root(root: Path) -> Path:
    return root.parent / "memory"


def main_memory_path(root: Path) -> Path:
    return memory_root(root) / "main.md"


def memory_logs_dir(root: Path) -> Path:
    return memory_root(root) / "logs"


def knowledge_versions_dir(root: Path) -> Path:
    return root / "versions"


def read_entity_map(path: Path) -> dict[str, dict[str, Any]]:
    data = read_json(path, {})
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def load_knowledge(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "items": read_entity_map(knowledge_items_path(root)),
        "topics": read_entity_map(knowledge_topics_path(root)),
    }


def write_knowledge(root: Path, knowledge: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_json(knowledge_items_path(root), knowledge.get("items", {}))
    write_json(knowledge_topics_path(root), knowledge.get("topics", {}))


def read_json_object_file(raw_path: str, label: str) -> dict[str, Any]:
    path = Path(raw_path)
    path = path if path.is_absolute() else Path.cwd() / path
    data = read_json(path, {})
    if not isinstance(data, dict):
        raise SystemExit(f"{label} must be a JSON object: {raw_path}")
    return data


def knowledge_search(workspace: Path, query: str, limit: int = 20, root: Path | None = None) -> list[dict[str, Any]]:
    root = root or knowledge_root(workspace)
    knowledge = load_knowledge(root)
    needle = query.casefold().strip()
    ranked: list[tuple[int, dict[str, Any]]] = []
    entities: list[tuple[str, dict[str, Any]]] = []
    entities.extend(("item", node) for node in knowledge["items"].values())
    entities.extend(("topic", node) for node in knowledge["topics"].values())
    if is_topic_knowledge_root(root):
        entities.extend(("memory", node) for node in read_memory_logs(root))
    else:
        entities.extend(("baseline", {**node, "id": node.get("method")}) for node in load_dashboard_baseline_rows(workspace)[0])
        entities.extend(("version", node) for node in read_versions(root))
    for kind, node in entities:
        if not isinstance(node, dict):
            continue
        haystack = json.dumps(node, ensure_ascii=False).casefold()
        if needle and needle not in haystack:
            continue
        node_id = str(node.get("id") or "").casefold()
        title = str(node.get("title") or "").casefold()
        body = str(node.get("summary") or node.get("text") or node.get("note") or "").casefold()
        score = 1
        if needle:
            if needle == node_id:
                score += 100
            elif needle in node_id:
                score += 60
            if needle in title:
                score += 40
            if needle in body:
                score += 20
        result = {
            "entity_type": kind,
            "id": node.get("id"),
            "title": node.get("title") or node.get("summary") or "",
        }
        if kind == "item":
            result.update({"path": node.get("path"), "summary": node.get("summary")})
        elif kind == "topic":
            result["text"] = node.get("text")
        elif kind == "memory":
            result["text"] = node.get("report")
        elif kind == "baseline":
            result.update({"summary": node.get("summary"), "metrics": node.get("metrics"), "locator": node.get("locator")})
        else:
            result.update({"agent": node.get("agent"), "created_at": node.get("created_at"), "summary": node.get("summary")})
        ranked.append((score, result))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("entity_type") or ""), str(pair[1].get("id") or "")))
    return [row for _, row in ranked[:limit]]


def require_public_knowledge_source(workspace: Path, source: Path) -> None:
    topic = find_topic_root(workspace)
    restricted = [program_root(topic) / "private"]
    restricted.extend(problem_workspace(topic, str(row["id"])) / ROOT_MARKER / "private" for row in registered_problems(topic))
    resolved = source.resolve()
    for private_root in restricted:
        try:
            resolved.relative_to(private_root.resolve())
        except ValueError:
            continue
        raise SystemExit(f"external knowledge source must not come from a private evidence space: {source}")


def knowledge_item_add(workspace: Path, root: Path, args: argparse.Namespace) -> dict[str, Any]:
    item_id = safe_id(str(args.item_id), "knowledge item id")
    source = Path(str(args.source))
    source = source if source.is_absolute() else Path.cwd() / source
    source = source.resolve()
    if not source.exists():
        raise SystemExit(f"external knowledge source not found: {source}")
    require_public_knowledge_source(workspace, source)
    metadata = read_json_object_file(str(args.metadata), "knowledge item metadata")
    title = str(metadata.get("title") or "").strip()
    summary = str(metadata.get("summary") or "").strip()
    if not title or not summary:
        raise SystemExit("knowledge item metadata requires non-empty title and summary")
    knowledge = load_knowledge(root)
    if item_id in knowledge["items"]:
        raise SystemExit(f"knowledge item already exists: {item_id}")
    items_root = root / "items"
    items_root.mkdir(parents=True, exist_ok=True)
    final_dir = items_root / item_id
    temp_dir = items_root / f".{item_id}.import-tmp"
    existing_in_place = source == final_dir.resolve()
    if existing_in_place and not final_dir.is_dir():
        raise SystemExit("an in-place Item source must be an existing directory")
    if (final_dir.exists() and not existing_in_place) or temp_dir.exists():
        raise SystemExit(f"knowledge item directory already exists: {item_id}")
    try:
        if not existing_in_place:
            if source.is_dir():
                shutil.copytree(source, temp_dir)
            else:
                temp_dir.mkdir(parents=True)
                shutil.copy2(source, temp_dir / source.name)
        item = {
            "id": item_id,
            "title": title,
            "summary": summary,
            "path": rel(workspace, final_dir),
        }
        if not existing_in_place:
            temp_dir.replace(final_dir)
        knowledge["items"][item_id] = item
        write_knowledge(root, knowledge)
        return item
    except BaseException:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        if not existing_in_place and final_dir.exists() and item_id not in load_knowledge(root)["items"]:
            shutil.rmtree(final_dir)
        raise


def knowledge_item_delete(root: Path, item_id: str) -> dict[str, str]:
    item_id = safe_id(item_id, "knowledge item id")
    knowledge = load_knowledge(root)
    item = knowledge["items"].pop(item_id, None)
    if not isinstance(item, dict):
        raise SystemExit(f"knowledge item not found: {item_id}")
    for topic in knowledge["topics"].values():
        topic["items"] = [value for value in topic.get("items", []) if value != item_id]
    write_knowledge(root, knowledge)
    item_dir = require_under(root / "items" / item_id, root / "items", "knowledge item path")
    if item_dir.is_dir():
        shutil.rmtree(item_dir)
    return {"deleted": item_id}


def memory_log_add(root: Path, memory_id: str, memory_file: str) -> dict[str, str]:
    memory_id = safe_id(memory_id, "Memory Log id")
    memory_data = read_json_object_file(memory_file, "Memory Log")
    unknown = sorted(set(memory_data) - {"summary", "report"})
    if unknown:
        raise SystemExit("Memory Log supports only summary and report: " + ", ".join(unknown))
    summary = str(memory_data.get("summary") or "").strip()
    report = str(memory_data.get("report") or "").strip()
    if not summary or not report:
        raise SystemExit("Memory Log requires non-empty summary and report")
    logs_dir = memory_logs_dir(root)
    log_path = require_under(logs_dir / f"{memory_id}.json", logs_dir, "Memory Log path")
    if log_path.exists():
        raise SystemExit(f"Memory Log already exists: {memory_id}")
    memory_log = {"id": memory_id, "created_at": now(), "summary": summary, "report": report}
    write_json(log_path, memory_log)
    return memory_log


def maintain_scope(topic: Path, scope: str, problem_id: str) -> tuple[Path, Path]:
    if scope == "topic":
        return topic, program_root(topic) / "knowledge"
    if not problem_id:
        raise SystemExit("Problem scope requires --problem <problem-id>")
    workspace = problem_workspace(topic, safe_id(problem_id, "problem id"))
    return workspace, knowledge_root(workspace)


def maintain_reference_blockers(
    topic: Path,
    kind: str,
    entity_id: str,
    *,
    problem_id: str = "",
    exclude_memory: str = "",
) -> list[str]:
    """Return mutable/immutable entities that resolve a reference to one entity.

    Topic entities are identified by an unqualified reference.  Problem entities
    are identified locally inside their Problem and by a qualified reference in
    Topic knowledge.  This mirrors ``resolve_ref`` instead of treating ids as
    global names.
    """
    blockers: list[str] = []
    scopes: list[tuple[str, Path]] = [("topic", program_root(topic) / "knowledge")]
    scopes.extend((f"problem:{row['id']}", knowledge_root(problem_workspace(topic, str(row["id"])))) for row in registered_problems(topic))
    for scope, root in scopes:
        knowledge = load_knowledge(root)
        entities: list[tuple[str, str, dict[str, Any]]] = []
        entities.extend(("item", str(row.get("id") or ""), row) for row in knowledge["items"].values() if isinstance(row, dict))
        entities.extend(("topic", str(row.get("id") or ""), row) for row in knowledge["topics"].values() if isinstance(row, dict))
        is_topic_scope = is_topic_knowledge_root(root)
        owner_problem_id = "" if is_topic_scope else current_problem_id(problem_workspace(topic, scope.split(":", 1)[1]))
        if is_topic_scope:
            entities.extend(("memory", str(row.get("id") or ""), row) for row in read_memory_logs(root))
        else:
            problem_workspace_path = problem_workspace(topic, scope.split(":", 1)[1])
            entities.extend(("baseline", str(row.get("method") or ""), row) for row in load_dashboard_baseline_rows(problem_workspace_path)[0])
            entities.extend(("version", str(row.get("id") or ""), row) for row in read_versions(root))
        for entity_kind, owner_id, entity in entities:
            is_target_owner = (
                entity_kind == kind
                and owner_id == entity_id
                and ((not problem_id and is_topic_scope) or (problem_id and owner_problem_id == problem_id))
            )
            if is_target_owner:
                continue
            if entity_kind == "memory" and owner_id == exclude_memory:
                continue
            for match in REF_RE.finditer(json.dumps(entity, ensure_ascii=False)):
                ref_kind, ref_problem_id, ref_id, _ = reference_parts(match)
                if reference_targets_entity(
                    owner_is_topic=is_topic_scope,
                    owner_problem_id=owner_problem_id,
                    kind=ref_kind,
                    problem_id=ref_problem_id,
                    entity_id=ref_id,
                    target_kind=kind,
                    target_problem_id=problem_id,
                    target_entity_id=entity_id,
                ):
                    blockers.append(f"{scope}:{entity_kind}:{owner_id}")
                    break
    return sorted(set(blockers))


def maintain_active_team_work(workspace: Path) -> list[str]:
    blockers: list[str] = []
    for campaign in read_headless_campaigns(workspace):
        if campaign.get("status") in {"starting", "running", "paused"}:
            blockers.append(f"headless campaign {campaign.get('id') or campaign.get('agent')}: {campaign.get('status')}")
    for row in read_headless_runs(workspace):
        if row.get("status") in {"starting", "running", "paused"}:
            blockers.append(f"headless {row.get('id') or row.get('agent')}: {row.get('status')}")
    refresh_all_jobs(workspace)
    for row in read_jsonl(job_index(workspace)):
        if str(row.get("agent") or "").startswith("agent") and row.get("status") in {"queued", "starting", "running"}:
            blockers.append(f"job {row.get('id')}: {row.get('status')}")
    for agent_dir in sorted(path for path in workspace.glob("agent*") if path.is_dir()):
        state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
        if isinstance(state, dict) and state.get("eval_status") in {"queued", "running"}:
            blockers.append(f"{agent_dir.name} eval: {state.get('eval_status')}")
    return blockers


def maintain_version_anchor(workspace: Path) -> dict[str, str | None]:
    latest: dict[str, dict[str, Any]] = {}
    for row in read_versions(knowledge_root(workspace)):
        agent = str(row.get("agent") or "")
        if agent:
            latest[agent] = row
    agents = sorted(path.name for path in workspace.glob("agent*") if path.is_dir())
    return {agent: str(latest[agent].get("id")) if agent in latest else None for agent in agents}


def maintain_reference_issues(workspace: Path, root: Path) -> list[dict[str, Any]]:
    knowledge = load_knowledge(root)
    entities: list[tuple[str, dict[str, Any]]] = []
    entities.extend((f"item:{row.get('id')}", row) for row in knowledge["items"].values() if isinstance(row, dict))
    entities.extend((f"topic:{row.get('id')}", row) for row in knowledge["topics"].values() if isinstance(row, dict))
    if is_topic_knowledge_root(root):
        entities.extend((f"memory:{row.get('id')}", row) for row in read_memory_logs(root))
    else:
        entities.extend((f"baseline:{row.get('method')}", row) for row in load_dashboard_baseline_rows(workspace)[0])
        entities.extend((f"version:{row.get('id')}", row) for row in read_versions(root))
    issues: list[dict[str, Any]] = []
    for owner, entity in entities:
        for match in REF_RE.finditer(json.dumps(entity, ensure_ascii=False)):
            ref = match.group(0)
            try:
                resolve_ref(workspace, ref, root=root)
            except SystemExit:
                issues.append({"kind": "unresolved_reference", "owner": owner, "ref": ref})
    return issues


def maintain_check(topic: Path, problem_id: str) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    if problem_id:
        workspaces = [problem_workspace(topic, safe_id(problem_id, "problem id"))]
    else:
        topic_root = program_root(topic) / "knowledge"
        topic_report = unified_knowledge_integrity_report(topic, root=topic_root, versions_workspace=None)
        topic_report["issues"].extend(maintain_reference_issues(topic, topic_root))
        topic_report["ok"] = not topic_report["issues"]
        reports.append({"scope": "topic", **topic_report})
        workspaces = [problem_workspace(topic, str(row["id"])) for row in registered_problems(topic)]
    for workspace in workspaces:
        root = knowledge_root(workspace)
        report = unified_knowledge_integrity_report(workspace, root=root, versions_workspace=workspace)
        report["issues"].extend(maintain_reference_issues(workspace, root))
        notice_issues: list[dict[str, Any]] = []
        agent_names = {path.name for path in workspace.glob("agent*") if path.is_dir()}
        for notice in read_jsonl(main_agent_notices_path(workspace)):
            if not str(notice.get("published_at") or "").strip():
                notice_issues.append({"kind": "notice_missing_published_at", "id": notice.get("id")})
            anchor = notice.get("version_anchor")
            if not isinstance(anchor, dict) or set(anchor) != agent_names:
                notice_issues.append({"kind": "invalid_notice_version_anchor", "id": notice.get("id")})
        report["issues"].extend(notice_issues)
        report["ok"] = not report["issues"]
        reports.append({"scope": f"problem:{current_problem_id(workspace)}", **report})
    payload = {"ok": all(report.get("ok") for report in reports), "reports": reports}
    return payload


def cmd_maintain(topic: Path, current_workspace: Path | None, cwd: Path, args: argparse.Namespace) -> None:
    if current_workspace is not None and in_agent_workspace(current_workspace, cwd) is not None:
        raise SystemExit("maintenance is unavailable inside a Route workspace")
    entity = str(args.maintain_entity)
    action = str(getattr(args, "maintain_action", ""))
    payload: Any
    if entity == "item":
        workspace, root = maintain_scope(topic, str(args.scope), str(args.problem or ""))
        item_id = safe_id(str(args.id), "Item id")
        if action == "add":
            metadata = read_json_object_file(str(args.metadata), "Item metadata")
            unknown = sorted(set(metadata) - {"title", "summary"})
            if unknown:
                raise SystemExit("Item metadata supports only title and summary: " + ", ".join(unknown))
            payload = knowledge_item_add(
                workspace,
                root,
                argparse.Namespace(item_id=item_id, source=args.source, metadata=args.metadata),
            )
        elif action == "delete":
            blockers = maintain_reference_blockers(
                topic,
                "item",
                item_id,
                problem_id=str(args.problem or "") if str(args.scope) == "problem" else "",
            )
            if blockers:
                raise SystemExit("Item is still referenced; rewrite these entities first:\n" + "\n".join(blockers))
            payload = knowledge_item_delete(root, item_id)
        else:
            raise SystemExit(f"unknown Item maintenance action: {action}")
    elif entity == "memory":
        root = program_root(topic) / "knowledge"
        memory_id = safe_id(str(args.id), "Memory Log id")
        if action == "add":
            payload = memory_log_add(root, memory_id, str(args.file))
        else:
            raise SystemExit(f"unknown Memory Log maintenance action: {action}")
    elif entity == "notice":
        workspace = problem_workspace(topic, safe_id(str(args.problem), "problem id"))
        if action == "add":
            notice = read_json_object_file(str(args.file), "Notice")
            unknown = sorted(set(notice) - {"title", "body", "priority", "tags"})
            if unknown:
                raise SystemExit("Notice supports only title, body, priority, and tags: " + ", ".join(unknown))
            tags = notice.get("tags", [])
            if not isinstance(tags, list):
                raise SystemExit("Notice tags must be a list")
            payload = cmd_notice(
                workspace,
                cwd,
                argparse.Namespace(
                    notice_cmd="add",
                    id=args.id,
                    title=notice.get("title", ""),
                    body=notice.get("body", ""),
                    priority=notice.get("priority", "high"),
                    tag=tags,
                ),
                emit=False,
            )
        elif action == "delete":
            payload = cmd_notice(workspace, cwd, argparse.Namespace(notice_cmd="delete", id=args.id), emit=False)
        else:
            raise SystemExit(f"unknown Notice maintenance action: {action}")
    elif entity == "check":
        payload = maintain_check(topic, str(args.problem or ""))
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        if not payload.get("ok"):
            raise SystemExit(1)
        return
    else:
        raise SystemExit(f"unknown maintenance entity: {entity}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def knowledge_integrity_report(workspace: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or knowledge_root(workspace)
    knowledge = load_knowledge(root)
    issues: list[dict[str, Any]] = []
    if not knowledge_items_path(root).exists():
        issues.append({"kind": "missing_items_index", "path": str(knowledge_items_path(root))})
    if not knowledge_topics_path(root).exists():
        issues.append({"kind": "missing_topics_file", "path": str(knowledge_topics_path(root))})
    is_topic_knowledge = is_topic_knowledge_root(root)
    if not is_topic_knowledge and (root / "notes.json").exists():
        issues.append({"kind": "unexpected_problem_notes_file", "path": str(root / "notes.json")})
    item_ids = set(knowledge["items"])
    topic_ids = set(knowledge["topics"])
    for item_id, item in knowledge["items"].items():
        if item.get("id") != item_id or not str(item.get("title") or "").strip() or not str(item.get("summary") or "").strip():
            issues.append({"kind": "incomplete_item", "id": item_id})
        raw_path = str(item.get("path") or "")
        item_path = Path(raw_path)
        if not item_path.is_absolute():
            item_path = workspace / item_path
        if not item_path.exists():
            issues.append({"kind": "missing_item_content", "id": item_id, "path": raw_path})
    for topic_id, topic in knowledge["topics"].items():
        if topic.get("id") != topic_id or not str(topic.get("title") or "").strip():
            issues.append({"kind": "incomplete_topic", "id": topic_id})
        for item_id in topic.get("items", []):
            if item_id not in item_ids:
                issues.append({"kind": "topic_missing_item", "topic": topic_id, "item": item_id})
    content_dirs = {path.name for path in (root / "items").iterdir() if path.is_dir() and not path.name.startswith(".")} if (root / "items").exists() else set()
    orphan_dirs = sorted(content_dirs - item_ids)
    if orphan_dirs:
        issues.append({"kind": "orphan_item_content", "items": orphan_dirs})
    return {
        "ok": not issues,
        "items": len(item_ids),
        "topics": len(topic_ids),
        "issues": issues,
    }


def memory_versions_integrity_report(
    workspace: Path,
    *,
    root: Path,
    versions_workspace: Path | None,
    knowledge_root_path: Path,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    memory_logs = read_memory_logs(root) if versions_workspace is None else []
    memory_ids: set[str] = set()
    for memory_log in memory_logs:
        memory_id = str(memory_log.get("id") or "")
        if not SAFE_ID_RE.fullmatch(memory_id):
            issues.append({"kind": "invalid_memory_log", "id": memory_id})
            continue
        if memory_id in memory_ids:
            issues.append({"kind": "duplicate_memory_log", "id": memory_id})
        memory_ids.add(memory_id)
        if set(memory_log) != {"id", "created_at", "summary", "report"} or not str(memory_log.get("created_at") or "").strip() or not str(memory_log.get("summary") or "").strip() or not str(memory_log.get("report") or "").strip():
            issues.append({"kind": "incomplete_memory_log", "id": memory_id})
        else:
            try:
                datetime.fromisoformat(str(memory_log["created_at"]).replace("Z", "+00:00"))
            except ValueError:
                issues.append({"kind": "invalid_memory_log_created_at", "id": memory_id})
    if versions_workspace is None:
        memory_path = main_memory_path(root)
        try:
            main_memory = memory_path.read_text(encoding="utf-8")
        except OSError:
            main_memory = ""
            issues.append({"kind": "missing_main_memory", "path": str(memory_path)})
        if main_memory:
            lines = main_memory.splitlines()
            required = ["# Main Agent Memory", "## 目标与背景", "## 元认知", "## 当前进展与文件索引"]
            positions = [lines.index(heading) if lines.count(heading) == 1 else -1 for heading in required]
            if len(lines) > 200 or positions[0] != 0 or any(position < 0 for position in positions) or positions != sorted(positions):
                issues.append({"kind": "invalid_main_memory_structure", "path": str(memory_path)})
    versions = read_versions(root) if versions_workspace is not None else []
    for version in versions:
        version_id = str(version.get("id") or "")
        if not VERSION_RE.fullmatch(version_id) or version.get("entity_type") != "version":
            issues.append({"kind": "invalid_version", "id": version_id})
    texts: list[tuple[str, str]] = [("main_memory", main_memory)] if versions_workspace is None else []
    texts.extend((f"memory:{row.get('id')}", str(row.get("report") or "")) for row in memory_logs)
    texts.extend((f"version:{row.get('id')}", str(row.get("note") or "")) for row in versions)
    for owner, body in texts:
        for match in REF_RE.finditer(body):
            ref = match.group(0)
            try:
                resolve_ref(workspace, ref, root=knowledge_root_path)
            except SystemExit:
                issues.append({"kind": "unresolved_reference", "owner": owner, "ref": ref})
    return {
        "ok": not issues,
        "memory_logs": len(memory_logs),
        "versions": len(versions),
        "issues": issues,
    }


def unified_knowledge_integrity_report(
    workspace: Path,
    *,
    root: Path,
    versions_workspace: Path | None,
) -> dict[str, Any]:
    external = knowledge_integrity_report(workspace, root=root)
    internal = memory_versions_integrity_report(
        workspace,
        root=root,
        versions_workspace=versions_workspace,
        knowledge_root_path=root,
    )
    baseline_rows, baseline_errors = load_dashboard_baseline_rows(workspace) if versions_workspace is not None else ([], [])
    baseline_issues = [{"kind": "invalid_baseline", **error} for error in baseline_errors]
    return {
        "ok": bool(external.get("ok")) and bool(internal.get("ok")) and not baseline_issues,
        "items": external.get("items", 0),
        "topics": external.get("topics", 0),
        "memory_logs": internal.get("memory_logs", 0),
        "baselines": len(baseline_rows),
        "versions": internal.get("versions", 0),
        "issues": [
            *[{"entity_group": "items_topics", **issue} for issue in external.get("issues", [])],
            *[{"entity_group": "memory_versions", **issue} for issue in internal.get("issues", [])],
            *[{"entity_group": "baselines", **issue} for issue in baseline_issues],
        ],
    }


REF_TOKEN = r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?"
REF_RE = re.compile(
    rf"@(?P<kind>topic|item|memory|baseline|version):(?:(?P<problem_id>{REF_TOKEN})/)?"
    rf"(?P<entity_id>{REF_TOKEN})(?:#(?P<layer>{REF_TOKEN}))?"
)


def is_topic_knowledge_root(root: Path) -> bool:
    return root.parent.name == TOPIC_MARKER


def reference_parts(match: re.Match[str]) -> tuple[str, str, str, str | None]:
    return (
        str(match.group("kind")),
        str(match.group("problem_id") or ""),
        str(match.group("entity_id")),
        match.group("layer"),
    )


def reference_targets_entity(
    *,
    owner_is_topic: bool,
    owner_problem_id: str,
    kind: str,
    problem_id: str,
    entity_id: str,
    target_kind: str,
    target_problem_id: str,
    target_entity_id: str,
) -> bool:
    if kind != target_kind or entity_id != target_entity_id:
        return False
    if target_problem_id:
        return (owner_is_topic and problem_id == target_problem_id) or (
            not owner_is_topic and owner_problem_id == target_problem_id and not problem_id
        )
    return owner_is_topic and not problem_id


def read_memory_logs(root: Path) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for path in sorted(memory_logs_dir(root).glob("*.json")):
        node = read_json(path, {})
        if isinstance(node, dict):
            logs.append(node)
    logs.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
    return logs


def read_versions(root: Path) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for path in sorted(knowledge_versions_dir(root).glob("version-*.json")):
        node = read_json(path, {})
        if isinstance(node, dict):
            versions.append(node)
    versions.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
    return versions


def resolve_ref(workspace: Path, ref: str, *, root: Path | None = None) -> dict[str, Any]:
    root = root or knowledge_root(workspace)
    match = REF_RE.fullmatch(ref.strip())
    if not match:
        raise SystemExit(
            "ref must look like @item:<id>, @topic:<id>, @memory:<id>, @baseline:<id>, @version:<id>, "
            "or (for Main Topic knowledge) @item:<problem-id>/<id>, "
            "@topic:<problem-id>/<id>, @baseline:<problem-id>/<id>, @version:<problem-id>/<id>"
        )
    kind, problem_id, entity_id, layer = reference_parts(match)
    topic = find_topic_root(workspace)
    is_topic_scope = is_topic_knowledge_root(root)

    if problem_id:
        if not is_topic_scope:
            raise SystemExit(f"Problem knowledge may not use qualified references: {ref}")
        if kind == "memory":
            raise SystemExit(f"Topic Memory Logs cannot be Problem-qualified: {ref}")
        problem_path = problem_workspace(topic, safe_id(problem_id, "problem id"))
        problem_root = knowledge_root(problem_path)
        if kind == "item":
            entity = load_knowledge(problem_root)["items"].get(entity_id)
        elif kind == "topic":
            entity = load_knowledge(problem_root)["topics"].get(entity_id)
        elif kind == "baseline":
            entity = next((row for row in load_dashboard_baseline_rows(problem_path)[0] if row.get("method") == entity_id), None)
            if isinstance(entity, dict):
                entity = {**entity, "id": entity_id}
        else:
            entity = read_version_from_root(problem_root, entity_id)
        if isinstance(entity, dict):
            return maybe_layer(entity, layer)
        raise SystemExit(f"reference not found: {ref}")

    knowledge = load_knowledge(root)
    if kind == "item":
        entity = knowledge["items"].get(entity_id)
    elif kind == "topic":
        entity = knowledge["topics"].get(entity_id)
    elif kind == "memory" and is_topic_scope:
        entity = next((memory_log for memory_log in read_memory_logs(root) if memory_log.get("id") == entity_id), None)
    elif kind == "baseline" and not is_topic_scope:
        entity = next((row for row in load_dashboard_baseline_rows(workspace)[0] if row.get("method") == entity_id), None)
        if isinstance(entity, dict):
            entity = {**entity, "id": entity_id}
    elif kind == "version" and not is_topic_scope:
        entity = read_version_from_root(root, entity_id)
    else:
        entity = None
    if isinstance(entity, dict):
        return maybe_layer(entity, layer)
    raise SystemExit(f"reference not found: {ref}")


def read_version_from_root(root: Path, version: str) -> dict[str, Any] | None:
    version = safe_id(version, "practice version", VERSION_RE)
    versions_dir = knowledge_versions_dir(root)
    path = require_under(versions_dir / f"{version}.json", versions_dir, "practice version path")
    if not path.is_file():
        return None
    node = read_json(path, {})
    return node if isinstance(node, dict) else None


def maybe_layer(data: dict[str, Any], layer: str | None) -> dict[str, Any]:
    if not layer:
        return data
    if layer in data:
        return {"id": data.get("id"), layer: data[layer]}
    return data


THREAD_ENV_VARS = [
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
]


TERMINAL_JOB_STATUSES = {"done", "failed", "cancelled", "stale"}


def resource_config_path(workspace: Path) -> Path:
    return console(workspace) / "resources.json"


def resource_state_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "resource_state.json"


def resource_lock_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "resource.lock"


def validate_resource_config_data(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {"schema_version", "free_run", "queue", "evaluation", "scheduler"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SystemExit("resource config contains unsupported fields: " + ", ".join(unknown))
    if data.get("schema_version") != 1:
        raise SystemExit("resource config schema_version must be 1")
    for key in ("free_run", "queue", "evaluation", "scheduler"):
        if not isinstance(data.get(key), dict):
            raise SystemExit(f"resource config {key} must be an object")

    free_run = data["free_run"]
    if set(free_run) - {"default", "agents"}:
        raise SystemExit("resource config free_run supports only default and agents")
    required_resource_fields = {"cpus", "memory_gb", "gpus"}
    default_raw = free_run.get("default", {})
    if not isinstance(default_raw, dict) or set(default_raw) != required_resource_fields:
        raise SystemExit("resource config free_run.default must contain exactly cpus, memory_gb, and gpus")
    default = normalize_resource_request(default_raw, allow_any_gpu=False)
    agents = free_run.get("agents", {})
    if not isinstance(agents, dict):
        raise SystemExit("resource config free_run.agents must be an object")
    normalized_agents: dict[str, dict[str, Any]] = {}
    for agent, override in agents.items():
        if not isinstance(agent, str) or not AGENT_NAME_RE.fullmatch(agent):
            raise SystemExit(f"invalid free_run agent id: {agent}")
        if not isinstance(override, dict):
            raise SystemExit(f"free_run.agents.{agent} must be an object")
        if set(override) - required_resource_fields:
            raise SystemExit(f"free_run.agents.{agent} supports only cpus, memory_gb, and gpus")
        merged = {**default_raw, **override}
        normalized_agents[agent] = normalize_resource_request(merged, allow_any_gpu=False)
    if default["gpus"]:
        raise SystemExit("free_run.default.gpus must be empty; assign exclusive GPU ids with per-agent overrides")

    queue = data["queue"]
    if set(queue) != {"capacity"}:
        raise SystemExit("resource config queue must contain exactly capacity")
    if not isinstance(queue["capacity"], dict) or set(queue["capacity"]) != required_resource_fields:
        raise SystemExit("resource config queue.capacity must contain exactly cpus, memory_gb, and gpus")
    capacity = normalize_resource_request(queue["capacity"], allow_any_gpu=False)

    evaluation = data["evaluation"]
    if set(evaluation) - {"resources", "timeout_seconds"} or "resources" not in evaluation:
        raise SystemExit("resource config evaluation supports only resources and timeout_seconds")
    if not isinstance(evaluation["resources"], dict) or set(evaluation["resources"]) != required_resource_fields:
        raise SystemExit("resource config evaluation.resources must contain exactly cpus, memory_gb, and gpus")
    eval_resources = normalize_resource_request(
        evaluation["resources"], evaluation.get("timeout_seconds"), allow_any_gpu=False
    )
    if exceeds_limit(eval_resources, capacity):
        raise SystemExit("evaluation.resources must fit within queue.capacity")

    scheduler = data["scheduler"]
    expected_scheduler = {
        "memory_reserve_gb",
        "respect_system_load",
        "respect_external_gpu_processes",
        "attached_wait_timeout_seconds",
    }
    unknown_scheduler = sorted(set(scheduler) - expected_scheduler)
    # Older Problems predate attached waits.  Keep their resource contracts
    # usable, while normalizing them to the documented default.
    required_scheduler = expected_scheduler - {"attached_wait_timeout_seconds"}
    missing_scheduler = sorted(required_scheduler - set(scheduler))
    if unknown_scheduler or missing_scheduler:
        details = []
        if missing_scheduler:
            details.append("missing " + ", ".join(missing_scheduler))
        if unknown_scheduler:
            details.append("unsupported " + ", ".join(unknown_scheduler))
        raise SystemExit("resource config scheduler fields invalid: " + "; ".join(details))
    reserve = scheduler.get("memory_reserve_gb")
    if not isinstance(reserve, (int, float)) or isinstance(reserve, bool) or reserve < 0:
        raise SystemExit("scheduler.memory_reserve_gb must be a non-negative number")
    for key in ("respect_system_load", "respect_external_gpu_processes"):
        if not isinstance(scheduler.get(key), bool):
            raise SystemExit(f"scheduler.{key} must be boolean")
    attached_wait = scheduler.get("attached_wait_timeout_seconds", DEFAULT_ATTACHED_WAIT_TIMEOUT_SECONDS)
    if not isinstance(attached_wait, (int, float)) or isinstance(attached_wait, bool) or attached_wait <= 0:
        raise SystemExit("scheduler.attached_wait_timeout_seconds must be a positive number")

    queue_gpus = set(capacity["gpus"])
    free_gpu_owners: dict[int, str] = {}
    for agent, resources in normalized_agents.items():
        for gpu in resources["gpus"]:
            if gpu in queue_gpus:
                raise SystemExit(f"GPU {gpu} is assigned to both free_run agent {agent} and queue.capacity")
            if gpu in free_gpu_owners:
                raise SystemExit(f"GPU {gpu} is assigned to multiple free_run agents: {free_gpu_owners[gpu]}, {agent}")
            free_gpu_owners[gpu] = agent

    return {
        "schema_version": 1,
        "free_run": {"default": default, "agents": normalized_agents},
        "queue": {"capacity": capacity},
        "evaluation": {
            "resources": {key: eval_resources[key] for key in ("cpus", "memory_gb", "gpus")},
            "timeout_seconds": eval_resources["timeout_seconds"],
        },
        "scheduler": {
            "memory_reserve_gb": float(reserve),
            "respect_system_load": scheduler["respect_system_load"],
            "respect_external_gpu_processes": scheduler["respect_external_gpu_processes"],
            "attached_wait_timeout_seconds": float(attached_wait),
        },
    }


def load_resource_config(workspace: Path) -> dict[str, Any]:
    path = resource_config_path(workspace)
    if not path.exists():
        raise SystemExit(f"Problem resource config is missing: {rel(workspace, path)}")
    data = read_json(path, {})
    if not isinstance(data, dict):
        raise SystemExit(f"resource config must be a JSON object: {path}")
    return validate_resource_config_data(data)


def free_run_resources(config: dict[str, Any], agent: str) -> dict[str, Any]:
    default = config["free_run"]["default"]
    return dict(config["free_run"]["agents"].get(agent, default))


def queue_capacity(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config["queue"]["capacity"])


def evaluation_resources(config: dict[str, Any]) -> dict[str, Any]:
    evaluation = config["evaluation"]
    return normalize_resource_request(evaluation["resources"], evaluation.get("timeout_seconds"), allow_any_gpu=False)


def agent_name_for_cwd(workspace: Path, cwd: Path) -> str:
    agent_dir = in_agent_workspace(workspace, cwd)
    if agent_dir is None:
        raise SystemExit("Route resource commands are unavailable in the Main workspace")
    return agent_dir.name


def agent_resource_policy(config: dict[str, Any], agent: str) -> dict[str, Any]:
    return {
        "free_run": free_run_resources(config, agent),
        "queue_capacity": queue_capacity(config),
        "evaluation": evaluation_resources(config),
    }


def normalize_gpu_request(value: Any, *, allow_any: bool = True) -> list[int] | str:
    if value is None:
        return []
    if allow_any and value == "any":
        return "any"
    if isinstance(value, int) and not isinstance(value, bool):
        return [int(value)]
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool):
                raise SystemExit("resource gpus must be a list of integer GPU ids, an integer GPU id, or \"any\"")
            out.append(int(item))
        return out
    raise SystemExit("resource gpus must be a list of integer GPU ids, an integer GPU id, or \"any\"")


def gpu_count(value: Any) -> int:
    if value is None:
        return 0
    if value == "any":
        return 1
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return len(value)
    return 0


def normalize_resource_request(raw: dict[str, Any], default_timeout: Any = None, *, allow_any_gpu: bool = True) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SystemExit("resource request must be a JSON object")
    unknown = sorted(set(raw) - {"cpus", "memory_gb", "gpus", "timeout_seconds"})
    if unknown:
        raise SystemExit("resource request contains unsupported fields: " + ", ".join(unknown))
    request = dict(raw)
    cpus = request.get("cpus", 1)
    memory_gb = request.get("memory_gb", 1)
    if not isinstance(cpus, int) or isinstance(cpus, bool) or cpus < 1:
        raise SystemExit("resource cpus must be a positive integer")
    if not isinstance(memory_gb, (int, float)) or isinstance(memory_gb, bool) or memory_gb <= 0:
        raise SystemExit("resource memory_gb must be a positive number")
    timeout = request.get("timeout_seconds", default_timeout)
    if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0):
        raise SystemExit("resource timeout_seconds must be null, omitted, or a positive number")
    return {
        "cpus": int(cpus),
        "memory_gb": float(memory_gb),
        "gpus": normalize_gpu_request(request.get("gpus", []), allow_any=allow_any_gpu),
        "timeout_seconds": None if timeout is None else float(timeout),
    }


def default_resource_request(config: dict[str, Any], agent: str, mode: str) -> dict[str, Any]:
    if mode == "queued":
        raise SystemExit("queued Route run requires --resources <request.json>")
    return normalize_resource_request(free_run_resources(config, agent), None, allow_any_gpu=False)


def eval_formal_config(workspace: Path) -> dict[str, Any]:
    return load_resource_config(workspace)["evaluation"]


def eval_formal_resources(workspace: Path, agent: str) -> dict[str, Any]:
    config = load_resource_config(workspace)
    return evaluation_resources(config)


def eval_check_resources(workspace: Path, agent_dir: Path) -> dict[str, Any]:
    config = load_resource_config(workspace)
    return default_resource_request(config, agent_dir.name, "run")


def load_resource_request(workspace: Path, cwd: Path, raw_path: str, agent: str, mode: str) -> dict[str, Any]:
    config = load_resource_config(workspace)
    if not raw_path:
        return default_resource_request(config, agent, mode)
    path = Path(raw_path)
    resolved = path if path.is_absolute() else cwd / path
    if not resolved.exists():
        raise SystemExit(f"resource request not found: {raw_path}")
    data = read_json(resolved, {})
    if not isinstance(data, dict):
        raise SystemExit(f"resource request must be a JSON object: {raw_path}")
    return normalize_resource_request(data)


def scheduler_policy(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("scheduler", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "respect_external_gpu_processes": bool(raw.get("respect_external_gpu_processes", True)),
        "respect_system_load": bool(raw.get("respect_system_load", True)),
        "memory_reserve_gb": float(raw.get("memory_reserve_gb", 2.0)),
        "attached_wait_timeout_seconds": float(raw.get("attached_wait_timeout_seconds", DEFAULT_ATTACHED_WAIT_TIMEOUT_SECONDS)),
    }


def attached_wait_timeout_seconds(workspace: Path) -> float:
    """Return the one configurable Route attached-wait window."""
    try:
        config = load_resource_config(workspace)
    except SystemExit:
        # Production calls have already loaded/validated this file before a
        # Job exists.  The fallback keeps direct unit-level Job construction
        # backwards compatible with historical fixtures.
        return DEFAULT_ATTACHED_WAIT_TIMEOUT_SECONDS
    return float(scheduler_policy(config)["attached_wait_timeout_seconds"])


def read_mem_available_gb() -> float | None:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return float(parts[1]) / (1024.0 * 1024.0)
                except ValueError:
                    return None
    return None


def read_mem_total_gb() -> float | None:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return float(parts[1]) / (1024.0 * 1024.0)
                except ValueError:
                    return None
    return None


def host_resources() -> dict[str, Any]:
    snapshot = nvidia_gpu_snapshot()
    return {
        "cpus": os.cpu_count() or 1,
        "memory_gb": read_mem_total_gb(),
        "gpus": sorted(snapshot.get("gpus", {})) if snapshot.get("available") else [],
        "gpu_detection_available": bool(snapshot.get("available")),
    }


def resource_integrity_report(workspace: Path, *, probe_enforcement: bool = False) -> dict[str, Any]:
    issues: list[str] = []
    try:
        config = load_resource_config(workspace)
    except SystemExit as exc:
        return {"ok": False, "config": None, "host": host_resources(), "enforcement": resource_enforcement_report(refresh=probe_enforcement), "issues": [str(exc)]}
    host = host_resources()
    capacity = queue_capacity(config)
    host_memory = host.get("memory_gb")
    if capacity["cpus"] > host["cpus"]:
        issues.append(f"queue.capacity.cpus={capacity['cpus']} exceeds host cpus={host['cpus']}")
    if isinstance(host_memory, (int, float)) and capacity["memory_gb"] > host_memory:
        issues.append(f"queue.capacity.memory_gb={capacity['memory_gb']} exceeds host memory_gb={host_memory:.3f}")
    configured_gpus = set(capacity["gpus"])
    for resources in config["free_run"]["agents"].values():
        configured_gpus.update(resources["gpus"])
    if configured_gpus and not host["gpu_detection_available"]:
        issues.append("configured GPU ids cannot be verified because nvidia-smi is unavailable")
    else:
        missing_gpus = sorted(configured_gpus - set(host["gpus"]))
        if missing_gpus:
            issues.append("configured GPU ids are absent from the host: " + ", ".join(map(str, missing_gpus)))
    if isinstance(host_memory, (int, float)) and config["scheduler"]["memory_reserve_gb"] >= host_memory:
        issues.append("scheduler.memory_reserve_gb must be smaller than host memory")

    route_names = sorted(path.name for path in workspace.iterdir() if path.is_dir() and AGENT_NAME_RE.fullmatch(path.name))
    if route_names:
        total_free_cpus = sum(free_run_resources(config, agent)["cpus"] for agent in route_names)
        total_free_memory = sum(free_run_resources(config, agent)["memory_gb"] for agent in route_names)
        if total_free_cpus + capacity["cpus"] > host["cpus"]:
            issues.append("sum of active Route free-run CPU allocations and queue.capacity exceeds host CPUs")
        if isinstance(host_memory, (int, float)) and total_free_memory + capacity["memory_gb"] + config["scheduler"]["memory_reserve_gb"] > host_memory:
            issues.append("sum of active Route free-run memory, queue.capacity, and memory reserve exceeds host memory")

    enforcement = resource_enforcement_report(refresh=probe_enforcement)
    if not enforcement.get("available"):
        issues.append("cgroup CPU/memory enforcement is unavailable")
    return {"ok": not issues, "config": config, "host": host, "enforcement": enforcement, "issues": issues}


def run_nvidia_smi(args: list[str]) -> str | None:
    for attempt in range(NVIDIA_SMI_ATTEMPTS):
        try:
            result = subprocess.run(
                ["nvidia-smi", *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            result = None
        if result is not None and result.returncode == 0:
            return result.stdout
        if attempt + 1 < NVIDIA_SMI_ATTEMPTS:
            time.sleep(NVIDIA_SMI_RETRY_DELAY_SECONDS)
    return None


def parse_nvidia_int(value: str) -> int | None:
    match = re.search(r"-?\d+", value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def parse_nvidia_float(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def process_owner(pid: int | None) -> str | None:
    if not isinstance(pid, int):
        return None
    try:
        return pwd.getpwuid(Path(f"/proc/{pid}").stat().st_uid).pw_name
    except (FileNotFoundError, KeyError, OSError):
        return None


def nvidia_gpu_snapshot() -> dict[str, Any]:
    gpu_text = run_nvidia_smi(
        [
            "--query-gpu=index,name,uuid,memory.used,memory.free,memory.total,utilization.gpu,utilization.memory,temperature.gpu,power.draw,power.limit",
            "--format=csv,noheader,nounits",
        ]
    )
    if gpu_text is None:
        return {"available": False, "gpus": {}, "compute_apps": []}
    gpus: dict[int, dict[str, Any]] = {}
    for parts in csv.reader(io.StringIO(gpu_text)):
        parts = [part.strip() for part in parts]
        if len(parts) < 11:
            continue
        index = parse_nvidia_int(parts[0])
        if index is None:
            continue
        used_mb = parse_nvidia_int(parts[3])
        free_mb = parse_nvidia_int(parts[4])
        total_mb = parse_nvidia_int(parts[5])
        gpus[index] = {
            "index": index,
            "name": parts[1],
            "uuid": parts[2],
            "memory_used_gb": None if used_mb is None else used_mb / 1024.0,
            "memory_free_gb": None if free_mb is None else free_mb / 1024.0,
            "memory_total_gb": None if total_mb is None else total_mb / 1024.0,
            "utilization_percent": parse_nvidia_int(parts[6]),
            "memory_utilization_percent": parse_nvidia_int(parts[7]),
            "temperature_c": parse_nvidia_int(parts[8]),
            "power_draw_w": parse_nvidia_float(parts[9]),
            "power_limit_w": parse_nvidia_float(parts[10]),
        }

    apps: list[dict[str, Any]] = []
    app_text = run_nvidia_smi(["--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory", "--format=csv,noheader,nounits"])
    gpu_by_uuid = {gpu.get("uuid"): index for index, gpu in gpus.items()}
    if app_text:
        for parts in csv.reader(io.StringIO(app_text)):
            parts = [part.strip() for part in parts]
            if len(parts) < 4:
                continue
            pid = parse_nvidia_int(parts[1])
            used_mb = parse_nvidia_int(parts[3])
            apps.append(
                {
                    "gpu_uuid": parts[0],
                    "gpu_index": gpu_by_uuid.get(parts[0]),
                    "pid": pid,
                    "user": process_owner(pid),
                    "process_name": parts[2],
                    "used_memory_gb": None if used_mb is None else used_mb / 1024.0,
                }
            )
    return {"available": True, "gpus": gpus, "compute_apps": apps}


def exceeds_limit(request: dict[str, Any], limit: dict[str, Any]) -> bool:
    if request["cpus"] > int(limit.get("cpus", 0)):
        return True
    if request["memory_gb"] > float(limit.get("memory_gb", 0)):
        return True
    limit_gpus = limit.get("gpus", [])
    if isinstance(limit_gpus, list):
        allowed = set(limit_gpus)
        requested = request["gpus"]
        if requested == "any":
            return len(allowed) < 1
        return not set(requested).issubset(allowed)
    return gpu_count(request["gpus"]) > gpu_count(limit_gpus)


def validate_resource_request(workspace: Path, agent: str, request: dict[str, Any], mode: str) -> None:
    config = load_resource_config(workspace)
    if mode == "run" and exceeds_limit(request, free_run_resources(config, agent)):
        raise SystemExit("resource request exceeds this Route's free_run allocation; use ./explore run --queued --resources ...")
    if mode in {"queued", "formal_eval"}:
        if exceeds_limit(request, queue_capacity(config)):
            raise SystemExit("resource request exceeds queue.capacity")


def build_resource_env(request: dict[str, Any], allocated_gpus: list[int] | None = None) -> dict[str, str]:
    env = {name: str(request["cpus"]) for name in THREAD_ENV_VARS}
    gpus = allocated_gpus
    if gpus is None:
        gpus = [] if request["gpus"] == "any" else list(request["gpus"])
    visible_gpus = ",".join(str(gpu) for gpu in gpus)
    env["DISCOVERY_CPUS"] = str(request["cpus"])
    env["DISCOVERY_MEMORY_GB"] = str(request["memory_gb"])
    env["DISCOVERY_GPUS"] = visible_gpus
    env["CUDA_VISIBLE_DEVICES"] = visible_gpus
    return env


_RESOURCE_ENFORCEMENT_REPORT: dict[str, Any] | None = None


def resource_runner_command(command: list[str], request: dict[str, Any]) -> list[str]:
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        raise SystemExit("resource enforcement is unavailable: systemd-run was not found")
    memory_bytes = max(1, int(float(request["memory_gb"]) * 1024**3))
    return [
        systemd_run,
        "--user",
        "--scope",
        "--quiet",
        "--property",
        f"CPUQuota={int(request['cpus']) * 100}%",
        "--property",
        f"MemoryMax={memory_bytes}",
        "--property",
        "MemorySwapMax=0",
        "--",
        *command,
    ]


def resource_enforcement_report(*, refresh: bool = False) -> dict[str, Any]:
    global _RESOURCE_ENFORCEMENT_REPORT
    if _RESOURCE_ENFORCEMENT_REPORT is not None and not refresh:
        return dict(_RESOURCE_ENFORCEMENT_REPORT)
    probe_request = {"cpus": 1, "memory_gb": 0.125, "gpus": [], "timeout_seconds": 5.0}
    try:
        command = resource_runner_command(["/usr/bin/true"], probe_request)
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        detail = (result.stderr or result.stdout or "").strip()
        available = result.returncode == 0
    except (OSError, subprocess.SubprocessError, SystemExit) as exc:
        detail = str(exc)
        available = False
    _RESOURCE_ENFORCEMENT_REPORT = {
        "available": available,
        "backend": "systemd-cgroup-v2",
        "detail": detail,
        "remediation": "Enable a user systemd manager with delegated cgroup v2 CPU and memory control." if not available else "",
    }
    return dict(_RESOURCE_ENFORCEMENT_REPORT)


def resource_failure_reason(returncode: int, log_path: Path | None = None) -> str:
    text = ""
    if log_path is not None and log_path.exists():
        text = tail_text(log_path, 80).lower()
    if returncode in {-9, 137} or any(token in text for token in ("out of memory", "memorymax", "oom-kill", "memory limit")):
        return "resource_exhausted"
    if "401 unauthorized" in text or "incorrect api key provided" in text:
        return "authentication_failed"
    return "nonzero_exit"


def read_resource_state(workspace: Path) -> dict[str, Any]:
    state = read_json(resource_state_path(workspace), {"leases": []})
    if not isinstance(state, dict):
        state = {"leases": []}
    state.setdefault("leases", [])
    return state


def write_resource_state(workspace: Path, state: dict[str, Any]) -> None:
    write_json(resource_state_path(workspace), state)


def with_resource_lock(workspace: Path):
    path = resource_lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def running_leases(workspace: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    leases = []
    for lease in state.get("leases", []):
        if lease.get("pid") is None or process_alive(lease.get("pid")):
            leases.append(lease)
        else:
            lease_workspace_raw = str(lease.get("workspace") or "")
            lease_workspace = Path(lease_workspace_raw) if lease_workspace_raw else workspace
            if lease_workspace.is_dir():
                mark_job_stale_if_running(lease_workspace, str(lease.get("job_id", "")), "lost_worker_or_pid")
    if len(leases) != len(state.get("leases", [])):
        state["leases"] = leases
        write_resource_state(workspace, state)
    return leases


def resource_availability(workspace: Path, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_resource_config(workspace)
    policy = scheduler_policy(config)
    capacity = queue_capacity(config)
    leases = running_leases(workspace, state)
    used_cpus = sum(int(lease.get("resources", {}).get("cpus", 0)) for lease in leases)
    used_memory = sum(float(lease.get("resources", {}).get("memory_gb", 0)) for lease in leases)
    used_gpus: set[int] = set()
    for lease in leases:
        for gpu in lease.get("allocated_gpus", []):
            if isinstance(gpu, int):
                used_gpus.add(gpu)
    lease_available = {
        "cpus": max(0, int(capacity["cpus"]) - used_cpus),
        "memory_gb": max(0.0, float(capacity["memory_gb"]) - used_memory),
        "gpus": [gpu for gpu in capacity["gpus"] if gpu not in used_gpus],
    }
    pressure: dict[str, Any] = {
        "policy": policy,
        "lease_used": {"cpus": used_cpus, "memory_gb": used_memory, "gpus": sorted(used_gpus)},
        "lease_available": lease_available,
        "cpu_load": None,
        "memory_available_gb": None,
        "nvidia_smi_available": None,
        "external_busy_gpus": [],
        "gpu_details": {},
    }

    cpus = int(lease_available["cpus"])
    if policy["respect_system_load"]:
        try:
            load1, load5, load15 = os.getloadavg()
        except OSError:
            load1 = load5 = load15 = None
        pressure["cpu_load"] = {"1m": load1, "5m": load5, "15m": load15}
        if load1 is not None:
            host_cpus = os.cpu_count() or 1
            cpu_load_limit = float(host_cpus)
            load_headroom = max(0, int(cpu_load_limit - float(load1)))
            pressure["cpu_load_limit"] = cpu_load_limit
            pressure["cpu_headroom"] = load_headroom
            cpus = min(cpus, load_headroom)

    memory_gb = float(lease_available["memory_gb"])
    mem_available = read_mem_available_gb()
    pressure["memory_available_gb"] = mem_available
    if mem_available is not None:
        memory_headroom = max(0.0, mem_available - float(policy["memory_reserve_gb"]))
        pressure["memory_headroom_gb"] = memory_headroom
        memory_gb = min(memory_gb, memory_headroom)

    external_busy_gpus: set[int] = set()
    snapshot = nvidia_gpu_snapshot() if capacity["gpus"] else {"available": None, "gpus": {}, "compute_apps": []}
    pressure["nvidia_smi_available"] = snapshot.get("available")
    pressure["gpu_compute_apps"] = snapshot.get("compute_apps", [])
    gpu_by_uuid = {info.get("uuid"): idx for idx, info in snapshot.get("gpus", {}).items() if isinstance(idx, int)}
    for idx, info in snapshot.get("gpus", {}).items():
        pressure["gpu_details"][str(idx)] = info
    if snapshot.get("available") is False:
        external_busy_gpus.update(int(gpu) for gpu in capacity["gpus"] if isinstance(gpu, int) and gpu not in used_gpus)
    if policy["respect_external_gpu_processes"]:
        for app in snapshot.get("compute_apps", []):
            idx = gpu_by_uuid.get(app.get("gpu_uuid"))
            if isinstance(idx, int) and idx not in used_gpus:
                external_busy_gpus.add(idx)
    pressure["external_busy_gpus"] = sorted(external_busy_gpus)

    available = {
        "cpus": max(0, cpus),
        "memory_gb": max(0.0, memory_gb),
        "gpus": [gpu for gpu in lease_available["gpus"] if gpu not in external_busy_gpus],
    }
    pressure["available"] = available
    return available, pressure


def available_resources(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    available, _pressure = resource_availability(workspace, state)
    return available


def allocate_resources_if_available(workspace: Path, job: dict[str, Any]) -> dict[str, Any] | None:
    request = job.get("resources", {})
    state = read_resource_state(workspace)
    available = available_resources(workspace, state)
    if int(request.get("cpus", 0)) > int(available["cpus"]):
        return None
    if float(request.get("memory_gb", 0)) > float(available["memory_gb"]):
        return None
    requested_gpus = request.get("gpus", [])
    allocated_gpus: list[int]
    if requested_gpus == "any":
        if not available["gpus"]:
            return None
        allocated_gpus = [int(available["gpus"][0])]
    else:
        allocated_gpus = [int(gpu) for gpu in requested_gpus]
        if not set(allocated_gpus).issubset(set(available["gpus"])):
            return None
    lease = {
        "job_id": job["id"],
        "problem_id": current_problem_id(workspace),
        "workspace": str(workspace),
        "agent": job.get("agent"),
        "pid": None,
        "pgid": None,
        "resources": request,
        "allocated_gpus": allocated_gpus,
        "leased_at": now(),
    }
    state.setdefault("leases", []).append(lease)
    write_resource_state(workspace, state)
    return lease


def update_lease_pid(workspace: Path, job_id: str, pid: int, pgid: int) -> None:
    with with_resource_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_resource_state(workspace)
        for lease in state.get("leases", []):
            if lease.get("job_id") == job_id:
                lease["pid"] = pid
                lease["pgid"] = pgid
        write_resource_state(workspace, state)


def release_lease(workspace: Path, job_id: str) -> None:
    with with_resource_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_resource_state(workspace)
        state["leases"] = [lease for lease in state.get("leases", []) if lease.get("job_id") != job_id]
        write_resource_state(workspace, state)


def next_job_id(prefix: str = "job") -> str:
    return prefix + "-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")


def shell_exit_code(returncode: Any) -> int:
    if not isinstance(returncode, int):
        return 1
    if returncode == 0:
        return 0
    if returncode < 0 or returncode > 255:
        return 1
    return returncode


def command_display(command: list[str]) -> str:
    return " ".join(command)


def tail_text(path: Path, lines: int = 20) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def summarize_job_log(workspace: Path, job: dict[str, Any], lines: int = 20) -> str:
    raw = str(job.get("log", ""))
    if not raw:
        return ""
    try:
        path = require_under(workspace / raw, pub(workspace) / "log", "job log")
    except SystemExit:
        return ""
    return tail_text(path, lines)


def append_job_error(workspace: Path, job: dict[str, Any], message: str) -> None:
    raw = str(job.get("log", ""))
    if not raw:
        return
    try:
        path = require_under(workspace / raw, pub(workspace) / "log", "job log")
    except SystemExit:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write("\n[discovery] " + message.rstrip() + "\n")


def launch_and_wait_job(workspace: Path, job: dict[str, Any], *, allocated_gpus: list[int] | None = None, stream_output: bool = False) -> dict[str, Any]:
    job_id = str(job["id"])
    log_root = private(workspace) / "eval_submissions" if job.get("_private_log") else pub(workspace) / "log"
    log_path = require_under(workspace / str(job["log"]), log_root, "job log")
    command = list(job["command"])
    cwd = Path(str(job.get("cwd") or workspace))
    request = normalize_resource_request(job.get("resources", {}))
    env = os.environ.copy()
    env.update(build_resource_env(request, allocated_gpus))
    # Python normally block-buffers stdout when it is piped.  Route programs
    # should expose flushed epoch/batch/progress output without each author
    # having to remember `python -u`.
    env.setdefault("PYTHONUNBUFFERED", "1")
    route = str(job.get("agent") or "")
    if job.get("launcher") in {"submit", "run"} and AGENT_NAME_RE.fullmatch(route):
        agent_dir = require_under(workspace / route, workspace, "queued Route workspace")
        cwd = require_under(cwd, agent_dir, "queued Route cwd")
        route_tmp = agent_dir / ".tmp"
        route_tmp.mkdir(exist_ok=True)
        env["TMPDIR"] = str(route_tmp)
        env["DISCOVERY_PROBLEM_ROOT"] = str(workspace.resolve())
        command = [
            "codex",
            "sandbox",
            "-P",
            "discovery_route",
            *route_permission_overrides(workspace, agent_dir),
            "-C",
            str(cwd),
            *command,
        ]
    command = resource_runner_command(command, request)
    timeout = request.get("timeout_seconds")
    started_at = now()
    proc: subprocess.Popen[bytes] | None = None
    try:
        with log_path.open("ab") as log:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                env=env,
                start_new_session=True,
                bufsize=0,
            )
            pgid = os.getpgid(proc.pid)
            update_job(workspace, job_id, {"status": "running", "pid": proc.pid, "pgid": pgid, "started_at": started_at, "reason": None})
            update_lease_pid(workspace, job_id, proc.pid, pgid)
            try:
                deadline = time.time() + float(timeout) if timeout else None
                assert proc.stdout is not None
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                    pipe_open = True
                    while pipe_open or proc.poll() is None:
                        if deadline is not None and time.time() > deadline:
                            raise subprocess.TimeoutExpired(command, timeout)
                        events = selector.select(timeout=0.1) if pipe_open else []
                        for key, _mask in events:
                            chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                pipe_open = False
                                continue
                            log.write(chunk)
                            log.flush()
                            if stream_output:
                                sys.stdout.buffer.write(chunk)
                                sys.stdout.buffer.flush()
                        if not pipe_open and proc.poll() is None:
                            time.sleep(0.05)
                    returncode = proc.wait()
            except subprocess.TimeoutExpired:
                kill_process_group(pgid)
                returncode = proc.wait()
                finished = now()
                update_job(workspace, job_id, {"status": "failed", "reason": "timeout", "returncode": returncode, "finished_at": finished})
                return get_job(workspace, job_id)
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()
    except Exception:
        with log_path.open("ab") as log:
            log.write(traceback.format_exc().encode("utf-8", errors="replace"))
        update_job(workspace, job_id, {"status": "failed", "reason": "launcher_error", "returncode": None, "finished_at": now()})
        return get_job(workspace, job_id)
    finished = now()
    latest = get_job(workspace, job_id)
    if latest.get("status") == "cancelled":
        return latest
    status = "done" if int(returncode) == 0 else "failed"
    reason = "completed" if int(returncode) == 0 else resource_failure_reason(int(returncode), log_path)
    update_job(workspace, job_id, {"status": status, "reason": reason, "returncode": int(returncode), "finished_at": finished})
    return get_job(workspace, job_id)


def kill_process_group(pgid: Any) -> None:
    if not isinstance(pgid, int):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def route_run_lock_path(workspace: Path, agent: str) -> Path:
    return private(workspace) / "broker_locks" / f"{safe_id(agent, 'agent name')}.run.lock"


@contextmanager
def exclusive_route_run(workspace: Path, agent: str):
    """Allow one free-run command per Route without blocking Route reads."""
    path = route_run_lock_path(workspace, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = latest_active_route_run(workspace, agent)
            detail = str(active.get("id")) if active else "unknown"
            raise SystemExit(
                f"Route development run already active: {detail}; inspect it with ./explore context --job {detail}"
            ) from None
        yield


def route_run_jobs(
    workspace: Path,
    agent: str,
    *,
    headless_run_id: str = "",
    campaign_id: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in read_jsonl(job_index(workspace)):
        if raw.get("kind") != "route_run" or raw.get("agent") != agent:
            continue
        if headless_run_id and raw.get("headless_run_id") != headless_run_id:
            continue
        if campaign_id and raw.get("campaign_id") != campaign_id:
            continue
        rows.append(refresh_job(raw))
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows


def route_builder_jobs(
    workspace: Path,
    agent: str,
    *,
    headless_run_id: str = "",
    campaign_id: str = "",
) -> list[dict[str, Any]]:
    """Return foreground and queued development Jobs owned by one Route."""
    rows: list[dict[str, Any]] = []
    for raw in read_jsonl(job_index(workspace)):
        is_development_job = raw.get("kind") == "route_run" or raw.get("launcher") == "submit"
        if not is_development_job or raw.get("agent") != agent:
            continue
        if headless_run_id and raw.get("headless_run_id") != headless_run_id:
            continue
        if campaign_id and raw.get("campaign_id") != campaign_id:
            continue
        rows.append(refresh_job(raw))
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows


def latest_active_queued_route_job(workspace: Path, agent: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in route_builder_jobs(workspace, agent)
        if row.get("launcher") == "submit" and row.get("status") in {"queued", "starting", "running", "paused"}
    ]
    return rows[-1] if rows else None


def latest_active_route_run(workspace: Path, agent: str) -> dict[str, Any] | None:
    rows = [row for row in route_run_jobs(workspace, agent) if row.get("status") in {"starting", "running", "paused"}]
    return rows[-1] if rows else None


def validate_route_run_origin(workspace: Path, agent: str, headless_run_id: str, campaign_id: str) -> None:
    if not headless_run_id and not campaign_id:
        return
    if not headless_run_id:
        raise SystemExit("campaign-linked Route run is missing its Headless run id")
    run = get_headless_run(workspace, headless_run_id)
    if run.get("agent") != agent:
        raise SystemExit("Headless run does not belong to this Route")
    recorded_campaign = str(run.get("campaign_id") or "")
    if campaign_id != recorded_campaign:
        raise SystemExit("Route run campaign does not match its Headless run")


def bounded_job_output(path: Path, max_bytes: int = 512 * 1024) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    raw = path.read_bytes()
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[-max_bytes:]
    return raw.decode("utf-8", errors="replace"), truncated


def completion_mode(value: Any, *, queued: bool) -> str:
    """Normalize the two Route completion modes, preserving old defaults."""
    mode = str(value or ("detach" if queued else "wait"))
    if mode not in {"wait", "detach"}:
        raise SystemExit("completion mode must be wait or detach")
    return mode


def job_result_payload(workspace: Path, job: dict[str, Any], *, completion: str, handoff_required: bool = False) -> dict[str, Any]:
    log_path = require_under(workspace / str(job["log"]), pub(workspace) / "log", "job log")
    output, output_truncated = bounded_job_output(log_path)
    job_status = str(job.get("status") or "unknown")
    detached_active = completion == "detach" and job_status in {"queued", "starting", "running", "paused"}
    return {
        "job": str(job["id"]),
        "status": "still_running" if handoff_required else job_status,
        "job_status": job_status,
        "reason": job.get("reason"),
        "returncode": job.get("returncode"),
        "resources": job.get("resources"),
        "log": str(job.get("log")),
        "output": output,
        "output_truncated": output_truncated,
        "completion_mode": completion,
        "wait_deadline": job.get("wait_deadline"),
        "handoff_required": handoff_required,
        "next_action": "checkpoint_and_end_turn" if handoff_required or detached_active else "continue_current_role",
        "wait_timeout_seconds": attached_wait_timeout_seconds(workspace),
    }


def wait_for_job_completion(workspace: Path, job_id: str, timeout_seconds: float) -> dict[str, Any]:
    """Mechanically await a persisted Job without submitting or polling a model."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        job = refresh_job(get_job(workspace, job_id))
        if str(job.get("status") or "") in TERMINAL_JOB_STATUSES:
            return job_result_payload(workspace, job, completion="wait")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            update_job(workspace, job_id, {"wait_timed_out_at": now()})
            return job_result_payload(workspace, get_job(workspace, job_id), completion="wait", handoff_required=True)
        # This is a Broker/Worker wait, not a model request.  A small bounded
        # sleep lets terminal jobs return promptly without busy-spinning.
        time.sleep(min(0.1, remaining))


def _run_unqueued_route_job(workspace: Path, job: dict[str, Any], allocated_gpus: list[int]) -> None:
    try:
        launch_and_wait_job(workspace, job, allocated_gpus=allocated_gpus)
    except BaseException:
        update_job(
            workspace,
            str(job["id"]),
            {"status": "failed", "reason": "launcher_error", "returncode": 1, "finished_at": now()},
        )
        append_job_error(workspace, job, traceback.format_exc())


def cmd_route_run_local(workspace: Path, cwd: Path, args: argparse.Namespace, *, emit: bool = True) -> dict[str, Any]:
    agent_dir = find_agent_dir(workspace, cwd)
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise SystemExit("missing command")
    resources = load_resource_request(workspace, cwd, args.resources, agent_dir.name, "run")
    validate_resource_request(workspace, agent_dir.name, resources, "run")
    completion = completion_mode(getattr(args, "completion", None), queued=False)
    defer_wait = bool(getattr(args, "defer_wait", False))
    allocated = [] if resources.get("gpus") == "any" else list(resources.get("gpus", []))
    headless_run_id = str(getattr(args, "headless_run_id", "") or "")
    campaign_id = str(getattr(args, "campaign_id", "") or "")
    validate_route_run_origin(workspace, agent_dir.name, headless_run_id, campaign_id)
    job_id = next_job_id("run")
    log_path = pub(workspace) / "log" / f"{job_id}.log"
    job = {
        "id": job_id,
        "kind": "route_run",
        "status": "starting",
        "reason": None,
        "returncode": None,
        "pid": None,
        "pgid": None,
        "supervisor_pid": None,
        "agent": agent_dir.name,
        "command": command,
        "display_command": command_display(command),
        "cwd": str(cwd),
        "resources": resources,
        "allocated_gpus": allocated,
        "log": rel(workspace, log_path),
        "created_at": now(),
        "launcher": "run",
        "execution_mode": "foreground",
        "completion_mode": completion,
        "wait_deadline": None,
        "wait_timed_out_at": None,
        "headless_run_id": headless_run_id or None,
        "campaign_id": campaign_id or None,
        "enforcement": "systemd_cgroup_v2+discovery_route_profile",
    }
    with exclusive_route_run(workspace, agent_dir.name):
        active = latest_active_route_run(workspace, agent_dir.name)
        if active is not None:
            raise SystemExit(
                f"Route development run already active: {active.get('id')}; "
                f"inspect it with ./explore context --job {active.get('id')}"
            )
        upsert_job(workspace, job)
        if defer_wait or completion == "detach":
            command = [sys.executable, str(Path(__file__).resolve()), "_supervise_direct", job_id]
            try:
                supervisor = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    start_new_session=True,
                )
            except OSError as exc:
                update_job(
                    workspace,
                    job_id,
                    {"status": "failed", "reason": "supervisor_launch_failed", "returncode": None, "finished_at": now()},
                )
                append_job_error(workspace, job, f"failed to launch direct Job supervisor: {exc}")
            else:
                update_job(workspace, job_id, {"supervisor_pid": supervisor.pid})
        else:
            runner = threading.Thread(
                target=_run_unqueued_route_job,
                args=(workspace, job, allocated),
                name=f"discovery-route-job-{job_id}",
                daemon=True,
            )
            runner.start()
    if completion == "wait":
        # Store an externally meaningful deadline, while using a monotonic
        # clock internally so wall-clock changes never alter the wait window.
        deadline_at = datetime.fromtimestamp(time.time() + attached_wait_timeout_seconds(workspace), UTC).isoformat()
        update_job(workspace, job_id, {"wait_deadline": deadline_at})
        if defer_wait:
            payload = job_result_payload(workspace, get_job(workspace, job_id), completion="wait")
        else:
            payload = wait_for_job_completion(workspace, job_id, attached_wait_timeout_seconds(workspace))
    else:
        payload = job_result_payload(workspace, get_job(workspace, job_id), completion="detach")
    if emit:
        if payload["output"]:
            sys.stdout.write(payload["output"])
        print(json.dumps({key: value for key, value in payload.items() if key != "output"}, indent=2, sort_keys=True))
    if payload["status"] != "still_running" and payload["returncode"] != 0 and emit:
        raise SystemExit(shell_exit_code(payload["returncode"]))
    return payload


def cmd_submit(workspace: Path, cwd: Path, args: argparse.Namespace, *, emit: bool = True) -> dict[str, Any]:
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise SystemExit("missing command")
    agent = agent_name_for_cwd(workspace, cwd)
    resources = load_resource_request(workspace, cwd, args.resources, agent, "queued")
    validate_resource_request(workspace, agent, resources, "queued")
    completion = completion_mode(getattr(args, "completion", None), queued=True)
    defer_wait = bool(getattr(args, "defer_wait", False))
    headless_run_id = str(getattr(args, "headless_run_id", "") or "")
    campaign_id = str(getattr(args, "campaign_id", "") or "")
    validate_route_run_origin(workspace, agent, headless_run_id, campaign_id)
    job_id = next_job_id()
    log_path = pub(workspace) / "log" / f"{job_id}.log"
    job = {
        "id": job_id,
        "status": "queued",
        "reason": None,
        "returncode": None,
        "pid": None,
        "pgid": None,
        "agent": agent,
        "command": command,
        "cwd": str(cwd),
        "resources": resources,
        "allocated_gpus": None,
        "log": rel(workspace, log_path),
        "created_at": now(),
        "launcher": "submit",
        "execution_mode": "queued",
        "completion_mode": completion,
        "wait_deadline": None,
        "wait_timed_out_at": None,
        "headless_run_id": headless_run_id or None,
        "campaign_id": campaign_id or None,
        "enforcement": "systemd_cgroup_v2+discovery_route_profile",
    }
    upsert_job(workspace, job)
    if completion == "wait":
        deadline_at = datetime.fromtimestamp(time.time() + attached_wait_timeout_seconds(workspace), UTC).isoformat()
        update_job(workspace, job_id, {"wait_deadline": deadline_at})
        if defer_wait:
            payload = job_result_payload(workspace, get_job(workspace, job_id), completion="wait")
        else:
            payload = wait_for_job_completion(workspace, job_id, attached_wait_timeout_seconds(workspace))
    else:
        payload = job_result_payload(workspace, get_job(workspace, job_id), completion="detach")
    if emit:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def cmd_worker(workspace: Path, args: argparse.Namespace) -> None:
    if bool(args.once):
        _run_worker_loop(workspace, args)
        return
    endpoint_path = route_broker_endpoint_path(workspace)
    broker_server: FileRouteBrokerServer | None = None
    broker_thread: threading.Thread | None = None
    try:
        broker_server, broker_thread = start_route_broker_server(workspace)
        write_json(
            endpoint_path,
            {
                "transport": "file",
                "pid": os.getpid(),
                "owner": "worker",
                "started_at": now(),
            },
        )
        for agent_dir in sorted(path for path in workspace.iterdir() if path.is_dir() and AGENT_NAME_RE.fullmatch(path.name)):
            ensure_route_broker_token(agent_dir)
        update_dashboard_worker_for_pid(
            workspace,
            os.getpid(),
            {"broker": {"status": "ready", "transport": "file"}},
        )
        _run_worker_loop(workspace, args)
    finally:
        if broker_server is not None:
            broker_server.shutdown()
            broker_server.server_close()
        if broker_thread is not None:
            broker_thread.join(timeout=2)
        endpoint = read_json(endpoint_path, {})
        if isinstance(endpoint, dict) and endpoint.get("pid") == os.getpid():
            endpoint_path.unlink(missing_ok=True)


def _run_worker_loop(workspace: Path, args: argparse.Namespace) -> None:
    while True:
        worker_pid = os.getpid()
        stopping = managed_worker_stop_requested(workspace, worker_pid)
        refresh_all_jobs(workspace)
        all_jobs = read_jsonl(job_index(workspace))
        active_rows = [job for job in all_jobs if job.get("status") in {"starting", "running"}]
        active_jobs = [str(job.get("id")) for job in active_rows]
        worker_state = read_json(dashboard_worker_state_path(workspace), {})
        previous_runtime = worker_state.get("job_runtime", {}) if isinstance(worker_state, dict) else {}
        job_runtime = {
            str(job.get("id")): collect_job_runtime(
                workspace,
                job,
                previous_runtime.get(str(job.get("id")), {}) if isinstance(previous_runtime, dict) else {},
            )
            for job in active_rows
        }
        update_dashboard_worker_for_pid(
            workspace,
            worker_pid,
            {
                "active_jobs": active_jobs,
                "current_job": active_jobs[0] if active_jobs else None,
                "job_runtime": job_runtime,
                "heartbeat_at": now(),
            },
        )
        if stopping and not active_jobs:
            update_dashboard_worker_for_pid(
                workspace,
                worker_pid,
                {"status": "stopped", "current_job": None, "active_jobs": [], "finished_at": now(), "reason": "stop_requested"},
            )
            return
        launched: list[str] = []
        once_selected: tuple[dict[str, Any], dict[str, Any]] | None = None
        if not stopping:
            with with_resource_lock(workspace) as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for job in read_jsonl(job_index(workspace)):
                    if job.get("status") != "queued":
                        continue
                    lease = allocate_resources_if_available(workspace, job)
                    if lease is None:
                        continue
                    allocated = lease.get("allocated_gpus", [])
                    update_job(
                        workspace,
                        str(job["id"]),
                        {
                            "status": "starting",
                            "allocated_gpus": allocated,
                            "started_at": now(),
                            "worker_pid": worker_pid,
                        },
                    )
                    job_id = str(job["id"])
                    if args.once:
                        once_selected = (get_job(workspace, job_id), lease)
                        break
                    command = [sys.executable, str(Path(__file__).resolve()), "_supervise", job_id]
                    try:
                        proc = subprocess.Popen(
                            command,
                            cwd=workspace,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            text=True,
                            start_new_session=True,
                        )
                    except OSError as exc:
                        state = read_resource_state(workspace)
                        state["leases"] = [row for row in state.get("leases", []) if row.get("job_id") != job_id]
                        write_resource_state(workspace, state)
                        update_job(workspace, job_id, {"status": "failed", "reason": "supervisor_launch_failed", "returncode": None, "finished_at": now()})
                        append_job_error(workspace, job, f"failed to launch job supervisor: {exc}")
                        continue
                    update_job(workspace, job_id, {"supervisor_pid": proc.pid})
                    launched.append(job_id)
        if once_selected is not None:
            job, lease = once_selected
            result = execute_leased_job(workspace, job, lease)
            print(json.dumps({"job": job["id"], "status": result.get("status"), "reason": result.get("reason"), "returncode": result.get("returncode"), "log": result.get("log")}, indent=2, sort_keys=True))
            return
        if launched:
            active_jobs.extend(launched)
            update_dashboard_worker_for_pid(
                workspace,
                worker_pid,
                {"active_jobs": active_jobs, "current_job": active_jobs[0] if active_jobs else None},
            )
        else:
            if args.once:
                print(json.dumps({"status": "idle", "reason": "no_runnable_queued_job"}, indent=2, sort_keys=True))
                return
            time.sleep(max(0.1, float(args.poll_seconds)))


def execute_leased_job(workspace: Path, job: dict[str, Any], lease: dict[str, Any]) -> dict[str, Any]:
    try:
        if job.get("kind") == "formal_eval":
            return run_formal_eval_job(workspace, job, allocated_gpus=lease.get("allocated_gpus", []))
        return launch_and_wait_job(workspace, job, allocated_gpus=lease.get("allocated_gpus", []))
    except (Exception, SystemExit):
        if job.get("kind") != "formal_eval":
            update_job(workspace, str(job["id"]), {"status": "failed", "reason": "launcher_error", "returncode": 1, "finished_at": now()})
            append_job_error(workspace, job, traceback.format_exc())
            return get_job(workspace, str(job["id"]))
        update_job(workspace, str(job["id"]), {"status": "failed", "reason": "formal_eval_setup_failed", "returncode": 1, "finished_at": now()})
        append_job_error(workspace, job, "formal eval setup failed; Human/Main Agent must inspect the registered evaluator and private submission state")
        try:
            agent_dir = require_under(workspace / str(job.get("agent_dir") or job.get("agent")), workspace, "agent directory")
            record_eval_failure(
                agent_dir,
                RuntimeError("Problem evaluator setup failed; ask Human/Main Agent to inspect private diagnostics"),
                stage="formal_eval",
                active_eval={"job": job.get("id"), "log": job.get("log"), "reason": "formal_eval_setup_failed"},
            )
        except (Exception, SystemExit):
            pass
        return get_job(workspace, str(job["id"]))
    finally:
        release_lease(workspace, str(job["id"]))


def reviewer_permission_overrides(workspace: Path, submission_root: Path, review_dir: Path, contract: dict[str, Any]) -> list[str]:
    """Give the Reviewer the narrowest useful filesystem and no network."""
    knowledge = knowledge_root(workspace)
    prompt = require_under(workspace / str(contract["ai_review"]["prompt"]), pub(workspace), "reviewer prompt")
    entries: dict[str, str] = {
        ":minimal": "read",
        str(Path(sys.prefix).resolve()): "read",
        str(submission_root / "candidate"): "read",
        str(submission_root / "submission.json"): "read",
        str(submission_root / "objective_evidence.json"): "read",
        str(review_dir): "write",
        str(workspace / "problem.json"): "read",
        str(pub(workspace) / "README.md"): "read",
        str(pub(workspace) / "evaluation" / "API.md"): "read",
        str(pub(workspace) / "evaluation" / "contract.json"): "read",
        str(prompt): "read",
        str(knowledge / "items"): "read",
        str(knowledge / "items.json"): "read",
        str(knowledge / "topics.json"): "read",
        str(pub(workspace) / "baseline"): "read",
        str(private(workspace)): "deny",
    }
    # The L2/L3 evidence space is private; reveal only that directory, never
    # the rest of private (and especially never test_space/submissions).
    if contract.get("evidence_level") == "L1":
        entries[str(pub(workspace) / "development_space")] = "read"
    else:
        entries[str(private(workspace) / "validation_space")] = "read"
    install_root = codex_install_root()
    if install_root is not None:
        entries[str(install_root)] = "read"
    filesystem = "{" + ", ".join(f"{json.dumps(path)}={json.dumps(access)}" for path, access in entries.items()) + "}"
    return [
        "--config", 'default_permissions="discovery_reviewer"',
        "--config", f"permissions.discovery_reviewer.filesystem={filesystem}",
        "--config", "permissions.discovery_reviewer.network.enabled=false",
        "--config", 'permissions.discovery_reviewer.network.domains={}',
        "--config", "permissions.discovery_reviewer.network.unix_sockets={}",
    ]


def reviewer_objective_evidence(contract: dict[str, Any], metrics: dict[str, float], space: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "contract_digest": evaluation_contract_digest(contract),
        "evidence_space": space,
        "metrics": {},
    }
    for name, value in metrics.items():
        spec = contract["metrics"][name]
        item: dict[str, Any] = {"value": value, "direction": spec["direction"]}
        if spec.get("role") in {"breakthrough", "guardrail"}:
            item["role"] = spec["role"]
        evidence["metrics"][name] = item
    return evidence


def prepare_reviewer_workspace(
    workspace: Path,
    submission_root: Path,
    contract: dict[str, Any],
    metadata: dict[str, Any],
    objective_metrics: dict[str, float] | None,
) -> Path:
    template = topic_root(workspace) / ".discovery" / "reviewer-template"
    if not template.is_dir():
        raise SystemExit("reviewer template is missing")
    review_dir = submission_root / "review"
    if review_dir.exists():
        shutil.rmtree(review_dir)
    shutil.copytree(template, review_dir)
    (review_dir / "work").mkdir(exist_ok=True)
    prompt_path = require_under(workspace / str(contract["ai_review"]["prompt"]), pub(workspace), "reviewer prompt")
    objective_evidence_path: Path | None = None
    if objective_metrics is not None:
        objective_evidence_path = submission_root / "objective_evidence.json"
        write_json(
            objective_evidence_path,
            reviewer_objective_evidence(contract, objective_metrics, evaluation_search_space(contract)),
        )
        objective_evidence_path.chmod(0o444)
    context = {
        "schema_version": 1,
        "candidate": str((submission_root / "candidate").resolve()),
        "submission": str((submission_root / "submission.json").resolve()),
        "problem_readme": str((pub(workspace) / "README.md").resolve()),
        "candidate_api": str((pub(workspace) / "evaluation" / "API.md").resolve()),
        "contract": str(evaluation_contract_path(workspace).resolve()),
        "prompt": str(prompt_path.resolve()),
        "knowledge_root": str(knowledge_root(workspace).resolve()),
        "baseline_root": str((pub(workspace) / "baseline").resolve()),
        "evidence_space": str((pub(workspace) / "development_space" if contract.get("evidence_level") == "L1" else private(workspace) / "validation_space").resolve()),
        "objective_evidence": str(objective_evidence_path.resolve()) if objective_evidence_path is not None else None,
        "result": str((review_dir / "result.json").resolve()),
        "dimensions": contract["ai_review"]["dimensions"],
        "prompt_digest": contract["ai_review"]["prompt_digest"],
        "knowledge_digest": metadata.get("review_knowledge_digest"),
        "baseline_digest": metadata.get("review_baseline_digest"),
    }
    write_json(review_dir / "context.json", context)
    for path in (review_dir / "AGENTS.md", review_dir / "review"):
        if path.exists(): path.chmod(0o555)
    (review_dir / "context.json").chmod(0o444)
    return review_dir


def build_reviewer_codex_command(workspace: Path, submission_root: Path, review_dir: Path, contract: dict[str, Any], reviewer: dict[str, Any]) -> list[str]:
    prompt_path = workspace / str(contract["ai_review"]["prompt"])
    rubric = prompt_path.read_text(encoding="utf-8")
    instruction = (
        "You are the formal evidence reviewer. Follow AGENTS.md and run ./review context. "
        "Before scoring, read the Problem README, Candidate API, Evaluation contract, Candidate, public rubric, relevant evidence space, public Baselines when useful, and objective_evidence when present. "
        "Assess only the declared dimensions. Use ./review knowledge for @item/@topic material if useful, then write the exact JSON schema and run ./review submit --file <json>. "
        "Do not provide recommendations, a total score, pass/fail outcome, or any output outside result.json.\n\n"
        + rubric
    )
    command = ["codex", *reviewer_permission_overrides(workspace, submission_root, review_dir, contract), "--config", 'web_search="disabled"', "--config", "allow_login_shell=false", "--ask-for-approval", "never", "exec", "--json", "--skip-git-repo-check"]
    command.extend(["--model", str(reviewer["model"]), "--config", f"model_reasoning_effort={json.dumps(str(reviewer['reasoning_effort']))}", instruction])
    return command


def run_ai_reviewer(
    workspace: Path,
    job: dict[str, Any],
    submission_root: Path,
    contract: dict[str, Any],
    reviewer: dict[str, Any],
    metadata: dict[str, Any],
    allocated_gpus: list[int] | None,
    objective_metrics: dict[str, float] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt_path = require_under(workspace / str(contract["ai_review"]["prompt"]), pub(workspace), "reviewer prompt")
    if str(metadata.get("review_prompt_digest") or "") != file_digest(prompt_path):
        raise SystemExit("reviewer prompt changed after this Candidate was queued; cancel and resubmit")
    if str(metadata.get("review_knowledge_digest") or "") != evaluation_knowledge_digest(workspace):
        raise SystemExit("reviewer knowledge changed after this Candidate was queued; cancel and resubmit")
    if str(metadata.get("review_baseline_digest") or "") != evaluation_baseline_digest(workspace):
        raise SystemExit("reviewer Baselines changed after this Candidate was queued; cancel and resubmit")
    review_dir = prepare_reviewer_workspace(workspace, submission_root, contract, metadata, objective_metrics)
    command = build_reviewer_codex_command(workspace, submission_root, review_dir, contract, reviewer)
    execution_job = dict(job)
    execution_job.update({"command": command, "cwd": str(review_dir), "log": rel(workspace, review_dir / "events.jsonl"), "_private_log": True})
    result = launch_and_wait_job(workspace, execution_job, allocated_gpus=allocated_gpus)
    result_path = review_dir / "result.json"
    if result.get("status") != "done" or int(result.get("returncode") or 0) != 0:
        raise EvalCommandFailed(int(result.get("returncode") or 1), str(job.get("log")))
    if not result_path.is_file():
        raise SystemExit("AI Reviewer did not submit result.json")
    result_path.chmod(0o600)
    review = validate_ai_review_result(result_path, contract)
    provenance = {
        "schema_version": 1, "reviewer_id": reviewer.get("id"), "backend": "codex",
        "model": reviewer.get("model"), "reasoning_effort": reviewer.get("reasoning_effort"),
        "candidate_digest": metadata.get("candidate_digest"), "contract_digest": metadata.get("contract_digest"),
        "prompt_digest": metadata.get("review_prompt_digest"), "knowledge_digest": metadata.get("review_knowledge_digest"),
        "baseline_digest": metadata.get("review_baseline_digest"), "finished_at": now(),
    }
    write_json(review_dir / "provenance.json", provenance)
    return review, provenance


def run_formal_eval_job(workspace: Path, job: dict[str, Any], *, allocated_gpus: list[int] | None = None) -> dict[str, Any]:
    job_id = str(job["id"])
    agent_dir = require_under(workspace / str(job.get("agent_dir") or job.get("agent")), workspace, "agent directory")
    if not agent_dir.is_dir():
        raise SystemExit(f"formal eval agent directory not found: {agent_dir}")
    metadata = job.get("formal_eval_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    contract = load_evaluation_contract(workspace, require_configured=True)
    registry = load_evaluation_registry(workspace, require_configured=True)
    validate_evaluation_pair(workspace, contract, registry)
    if str(metadata.get("contract_digest") or "") != evaluation_contract_digest(contract):
        raise SystemExit("evaluation contract changed after this Candidate was queued; cancel and resubmit")
    submission_id = safe_id(str(metadata.get("submission_id") or ""), "submission id")
    submission_root = require_under(
        private(workspace) / "eval_submissions" / submission_id,
        private(workspace) / "eval_submissions",
        "submission directory",
    )
    manifest = read_json(submission_root / "submission.json", {})
    if not isinstance(manifest, dict) or manifest.get("submission_id") != submission_id:
        raise SystemExit("formal eval Candidate submission manifest is missing or invalid")
    if manifest.get("agent") != agent_dir.name or manifest.get("digest") != metadata.get("candidate_digest"):
        raise SystemExit("formal eval Candidate submission does not match the queued job")
    candidate_path = require_under(submission_root / str(manifest.get("candidate_entry") or ""), submission_root / "candidate", "submitted Candidate")
    if not candidate_path.exists():
        raise SystemExit("formal eval Candidate snapshot is missing")
    if candidate_digest(candidate_files(candidate_path)) != manifest.get("digest"):
        raise SystemExit("formal eval Candidate snapshot digest changed after queueing")
    space = evaluation_search_space(contract)
    evaluator = registry["evaluators"][space]
    objective_enabled = valid_command_array(evaluator.get("command"))
    reviewer = evaluator.get("ai_reviewer") if isinstance(evaluator.get("ai_reviewer"), dict) else None
    report_path = submission_root / "report.json" if objective_enabled else None
    private_log_path = submission_root / "evaluator.log"
    if report_path is not None:
        report_path.unlink(missing_ok=True)
    active_eval = {
        "job": job_id,
        "id": job_id,
        "submission_id": submission_id,
        "candidate_digest": manifest.get("digest"),
        "tree": manifest.get("tree"),
        "parent": manifest.get("parent"),
        "log": job.get("log"),
        "resources": job.get("resources"),
        "started_at": now(),
    }
    set_loop_state(agent_dir, "work_loop", eval_status="running", active_eval=active_eval, last_error=None)
    result: dict[str, Any] = {"status": "done", "returncode": 0, "reason": None}
    objective_metrics: dict[str, float] | None = None
    review_result: dict[str, Any] | None = None
    review_provenance: dict[str, Any] | None = None
    run_info = {
        "id": job_id,
        "returncode": result.get("returncode"),
        "status": result.get("status"),
        "reason": result.get("reason"),
        "log": job.get("log"),
        "resources": job.get("resources"),
        "allocated_gpus": allocated_gpus or [],
        "submission_id": submission_id,
        "candidate_digest": manifest.get("digest"),
        "tree": manifest.get("tree"),
        "parent": manifest.get("parent"),
        "contract_digest": evaluation_contract_digest(contract),
        "evaluator_id": evaluator.get("id") or (f"{space}-evaluator" if objective_enabled else None),
        "formal_eval_stage": "objective" if objective_enabled else "ai_review",
    }
    try:
        if objective_enabled:
            update_job(workspace, job_id, {"formal_eval_stage": "objective"})
            report_start = time.time()
            command = expand_registered_command(list(evaluator["command"]), {"candidate": str(candidate_path), "report": str(report_path), "workspace": str(workspace), "agent": agent_dir.name, "submission_id": submission_id})
            execution_job = dict(job)
            execution_job.update({"command": command, "cwd": str(registered_cwd(workspace, evaluator.get("cwd"), "formal evaluator cwd")), "log": rel(workspace, private_log_path), "_private_log": True})
            result = launch_and_wait_job(workspace, execution_job, allocated_gpus=allocated_gpus)
            append_job_error(workspace, job, f"Problem evaluator finished with status={result.get('status')} returncode={result.get('returncode')}")
            if result.get("status") != "done" or int(result.get("returncode") or 0) != 0:
                raise EvalCommandFailed(int(result.get("returncode") or 1), str(job.get("log")))
            if not report_path or not report_path.is_file() or report_path.stat().st_mtime < report_start - 1:
                raise SystemExit("registered evaluator did not produce its required report")
            report_path.chmod(0o600)
            objective_metrics = validate_registered_eval_report(report_path, contract)
        if reviewer is not None:
            update_job(workspace, job_id, {"formal_eval_stage": "ai_review"})
            review_result, review_provenance = run_ai_reviewer(
                workspace,
                job,
                submission_root,
                contract,
                reviewer,
                metadata,
                allocated_gpus,
                objective_metrics,
            )
            run_info["reviewer"] = review_provenance
        run_info["formal_eval_stage"] = "finalize"
        payload = finalize_eval_practice(
            workspace,
            agent_dir,
            report_path,
            review_result,
            str(metadata.get("message") or "formal eval"),
            space,
            contract,
            run_info,
            objective_metrics,
        )
        set_loop_state(agent_dir, "reflection_loop", last_version=payload["version"], eval_status="succeeded", active_eval=None, last_error=None)
        update_job(workspace, job_id, {"status": "done", "returncode": 0, "finished_at": now(), "formal_eval_stage": "finalize", "formal_eval_result": payload, "practice_version": payload["version"]})
    except BaseException as exc:
        active_eval["returncode"] = result.get("returncode")
        active_eval["reason"] = result.get("reason")
        active_eval["error"] = "Problem evaluator failed; inspect the public job status or ask Human/Main Agent to inspect private evaluator diagnostics"
        record_eval_failure(
            agent_dir,
            RuntimeError("Problem evaluator failed; ask Human/Main Agent to inspect private diagnostics"),
            stage="formal_eval",
            active_eval=active_eval,
        )
        append_job_error(workspace, job, "formal eval failed; private evaluator diagnostics were not released to the Route")
        update_job(workspace, job_id, {"status": "failed", "reason": "formal_eval_failed", "returncode": (result.get("returncode") if result.get("status") != "done" and result.get("returncode") is not None else 1), "finished_at": now()})
    return get_job(workspace, job_id)


def cmd_supervise(workspace: Path, job_id: str) -> None:
    job = get_job(workspace, job_id)
    if job.get("status") == "cancelled":
        release_lease(workspace, job_id)
        return
    lease = next((row for row in running_leases(workspace, read_resource_state(workspace)) if row.get("job_id") == job_id), None)
    if lease is None:
        update_job(workspace, job_id, {"status": "failed", "reason": "resource_lease_missing", "returncode": 1, "finished_at": now()})
        raise SystemExit(1)
    result = execute_leased_job(workspace, job, lease)
    if result.get("status") == "failed":
        raise SystemExit(int(result.get("returncode") or 1))


def cmd_supervise_direct(workspace: Path, job_id: str) -> None:
    """Own one free-run Job independently of the Broker and Codex Turn."""
    job = get_job(workspace, job_id)
    if job.get("kind") != "route_run" or job.get("launcher") != "run":
        raise SystemExit("direct supervisor accepts only a persisted Route run Job")
    if job.get("status") == "cancelled":
        return
    update_job(workspace, job_id, {"supervisor_pid": os.getpid()})
    allocated = job.get("allocated_gpus", [])
    _run_unqueued_route_job(
        workspace,
        get_job(workspace, job_id),
        [int(value) for value in allocated] if isinstance(allocated, list) else [],
    )
    result = get_job(workspace, job_id)
    if result.get("status") == "failed":
        raise SystemExit(int(result.get("returncode") or 1))


def codex_install_root() -> Path | None:
    executable = shutil.which("codex")
    if not executable:
        return None
    resolved = Path(executable).resolve()
    if resolved.name == "codex.js" and resolved.parent.name == "bin":
        return resolved.parent.parent
    return resolved.parent


def route_permission_overrides(workspace: Path, agent_dir: Path) -> list[str]:
    entries: dict[str, str] = {
        ":minimal": "read",
        str(Path(sys.prefix).resolve()): "read",
        str(agent_dir.resolve()): "write",
        str((agent_dir / ".codex").resolve()): "read",
        str((agent_dir / ".agents").resolve()): "read",
        str((agent_dir / ".discovery").resolve()): "read",
        str((agent_dir / ".git").resolve()): "read",
        str((agent_dir / "AGENTS.md").resolve()): "read",
        str((agent_dir / "goals").resolve()): "read",
        str((agent_dir / "headless_goals").resolve()): "read",
        str((agent_dir / "explore").resolve()): "read",
        str((workspace / "problem.json").resolve()): "read",
        str(pub(workspace).resolve()): "read",
        str(private(workspace).resolve()): "deny",
    }
    install_root = codex_install_root()
    if install_root is not None:
        entries[str(install_root)] = "read"
    filesystem = "{" + ", ".join(f"{json.dumps(path)}={json.dumps(access)}" for path, access in entries.items()) + "}"
    return [
        "--config",
        'default_permissions="discovery_route"',
        "--config",
        f"permissions.discovery_route.filesystem={filesystem}",
        "--config",
        "permissions.discovery_route.network.enabled=true",
        "--config",
        'permissions.discovery_route.network.domains={"*"="allow"}',
    ]


def build_headless_codex_command(run: dict[str, Any], workspace: Path, agent_dir: Path) -> list[str]:
    command = [
        "codex",
        *route_permission_overrides(workspace, agent_dir),
        "--config",
        'web_search="live"',
        "--config",
        "allow_login_shell=false",
        "--ask-for-approval",
        "never",
        "exec",
        "--json",
        "--skip-git-repo-check",
    ]
    model = str(run.get("model") or "")
    reasoning_effort = str(run.get("model_reasoning_effort") or "")
    if model:
        command.extend(["--model", model])
    if reasoning_effort:
        command.extend(["--config", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
    command.append(str(run.get("prompt") or ""))
    return command


def cmd_headless_campaign(workspace: Path, campaign_id: str, *, poll_seconds: float = 2.0) -> None:
    try:
        while True:
            campaign = get_headless_campaign(workspace, campaign_id)
            status = str(campaign.get("status") or "")
            if status in {"done", "failed", "stopped", "blocked"}:
                return
            if status == "paused":
                time.sleep(poll_seconds)
                continue
            if status not in {"starting", "running"}:
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {"status": "failed", "reason": f"invalid_campaign_status:{status}", "finished_at": now(), "updated_at": now()},
                )
                return

            agent = str(campaign.get("agent") or "")
            agent_dir = require_under(workspace / agent, workspace, "campaign Route workspace")
            loop_state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
            if observe_campaign_reflection(campaign, loop_state.get("last_reflected_version")):
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {
                        "current_version": campaign.get("current_version"),
                        "completed_versions": campaign.get("completed_versions", []),
                        "completed_iterations": campaign.get("completed_iterations", 0),
                        "debug_attempts": 0,
                        "updated_at": now(),
                    },
                )
                if int(campaign.get("completed_iterations") or 0) >= int(campaign.get("target_iterations") or 0):
                    update_headless_campaign(
                        workspace,
                        campaign_id,
                        {
                            "status": "done",
                            "current_stage": "complete",
                            "active_run_id": None,
                            "reason": "target_iterations_reflected",
                            "finished_at": now(),
                            "updated_at": now(),
                        },
                    )
                    return
                campaign = get_headless_campaign(workspace, campaign_id)

            waiting_route_jobs = [str(item) for item in campaign.get("waiting_route_jobs", []) if str(item)]
            if waiting_route_jobs:
                observed_jobs: list[dict[str, Any]] = []
                for job_id in waiting_route_jobs:
                    try:
                        observed_jobs.append(refresh_job(get_job(workspace, job_id)))
                    except SystemExit:
                        continue
                active_jobs = [job for job in observed_jobs if job.get("status") in {"queued", "starting", "running", "paused"}]
                if active_jobs:
                    update_headless_campaign(
                        workspace,
                        campaign_id,
                        {"current_stage": "wait_builder_job", "updated_at": now()},
                    )
                    time.sleep(poll_seconds)
                    continue
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {
                        "waiting_route_jobs": [],
                        "last_route_jobs": waiting_route_jobs,
                        "current_stage": "start_builder",
                        "no_progress_attempts": 0,
                        "updated_at": now(),
                    },
                )
                campaign = get_headless_campaign(workspace, campaign_id)

            active_run = latest_active_headless_run(workspace, agent)
            if active_run is not None:
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {"active_run_id": active_run.get("id"), "current_stage": active_run.get("runner_action"), "updated_at": now()},
                )
                time.sleep(poll_seconds)
                continue

            active_run_id = str(campaign.get("active_run_id") or "")
            if active_run_id and active_run_id != str(campaign.get("last_processed_run_id") or ""):
                completed_run = get_headless_run(workspace, active_run_id)
                run_status = str(completed_run.get("status") or "")
                if run_status not in {"done", "failed", "stopped", "stale"}:
                    time.sleep(poll_seconds)
                    continue
                before = completed_run.get("loop_state_before")
                current_state = read_json(agent_dir / ".discovery" / "loop_state.json", {})
                if isinstance(before, dict) and before == current_state:
                    infrastructure_reason = headless_run_infrastructure_reason(workspace, completed_run)
                    if infrastructure_reason:
                        retry_attempts = int(campaign.get("infrastructure_retry_attempts") or 0) + 1
                        max_retries = int(campaign.get("max_infrastructure_retries") or 2)
                        if route_broker_is_available(workspace) and retry_attempts <= max_retries:
                            # A Route may observe a short endpoint/token visibility
                            # race while a freshly restarted file Broker is becoming
                            # visible in its sandbox.  The live endpoint is
                            # authoritative, so retry the same role a bounded number
                            # of times instead of permanently blocking the Campaign.
                            update_headless_campaign(
                                workspace,
                                campaign_id,
                                {
                                    "last_processed_run_id": active_run_id,
                                    "active_run_id": None,
                                    "current_stage": "retry_after_infrastructure",
                                    "infrastructure_retry_attempts": retry_attempts,
                                    "last_infrastructure_reason": infrastructure_reason,
                                    "updated_at": now(),
                                },
                            )
                            time.sleep(poll_seconds)
                            campaign = get_headless_campaign(workspace, campaign_id)
                            continue
                        update_headless_campaign(
                            workspace,
                            campaign_id,
                            {
                                "status": "blocked",
                                "reason": infrastructure_reason,
                                "failed_run_id": active_run_id,
                                "finished_at": now(),
                                "updated_at": now(),
                            },
                        )
                        return
                    associated_jobs = route_builder_jobs(
                        workspace,
                        agent,
                        headless_run_id=active_run_id,
                        campaign_id=campaign_id,
                    )
                    active_associated = [
                        job for job in associated_jobs if job.get("status") in {"queued", "starting", "running", "paused"}
                    ]
                    if active_associated:
                        update_headless_campaign(
                            workspace,
                            campaign_id,
                            {
                                "last_processed_run_id": active_run_id,
                                "active_run_id": None,
                                "waiting_route_jobs": [str(job.get("id")) for job in active_associated],
                                "current_stage": "wait_builder_job",
                                "updated_at": now(),
                            },
                        )
                        time.sleep(poll_seconds)
                        continue
                    # Any development Job can become terminal before Codex
                    # exits while the model still reasons from an earlier
                    # heartbeat. Runtime state is authoritative: re-enter the
                    # Builder once so it receives the terminal fact, inspects
                    # the result, and continues instead of turning a stale
                    # handoff into a no-progress block.
                    terminal_associated = [
                        job
                        for job in associated_jobs
                        if job.get("status") in {"done", "failed", "stopped", "stale"}
                    ]
                    if terminal_associated:
                        update_headless_campaign(
                            workspace,
                            campaign_id,
                            {
                                "last_processed_run_id": active_run_id,
                                "active_run_id": None,
                                "waiting_route_jobs": [str(job.get("id")) for job in terminal_associated],
                                "current_stage": "wait_builder_job",
                                "updated_at": now(),
                            },
                        )
                        time.sleep(poll_seconds)
                        continue
                    if completed_run.get("runner_action") == "start_builder" and run_status == "done":
                        update_headless_campaign(
                            workspace,
                            campaign_id,
                            {
                                "status": "blocked",
                                "reason": "builder_handoff_without_candidate",
                                "last_processed_run_id": active_run_id,
                                "active_run_id": None,
                                "finished_at": now(),
                                "updated_at": now(),
                            },
                        )
                        return
                    previous_attempts = int(campaign.get("no_progress_attempts") or 0)
                    attempts = previous_attempts + 1
                    max_attempts = int(campaign.get("max_no_progress_attempts") or 1)
                    if attempts > max_attempts:
                        stage_reason = str(completed_run.get("reason") or run_status)
                        update_headless_campaign(
                            workspace,
                            campaign_id,
                            {
                                "status": "blocked",
                                "reason": "headless_stage_made_no_state_progress",
                                "last_no_progress_reason": stage_reason,
                                "failed_run_id": active_run_id,
                                "no_progress_attempts": attempts,
                                "finished_at": now(),
                                "updated_at": now(),
                            },
                        )
                        return
                    # A Codex exit is not itself a failed research stage.  It
                    # gets one bounded fresh same-role retry, never a resumed
                    # Thread, so an early clean exit can recover without
                    # Human intervention.
                    update_headless_campaign(
                        workspace,
                        campaign_id,
                        {
                            "last_processed_run_id": active_run_id,
                            "active_run_id": None,
                            "current_stage": "start_builder",
                            "no_progress_attempts": attempts,
                            "last_no_progress_reason": str(completed_run.get("reason") or run_status),
                            "updated_at": now(),
                        },
                    )
                    campaign = get_headless_campaign(workspace, campaign_id)
                    continue
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {
                        "last_processed_run_id": active_run_id,
                        "active_run_id": None,
                        "no_progress_attempts": 0,
                        "infrastructure_retry_attempts": 0,
                        "updated_at": now(),
                    },
                )
                campaign = get_headless_campaign(workspace, campaign_id)

            status_rows = build_dashboard_agent_statuses(workspace, [agent], include_campaigns=False)
            route_status = status_rows[0] if status_rows else {}
            action = str(route_status.get("runner_action") or "")
            if action == "wait_eval":
                update_headless_campaign(workspace, campaign_id, {"current_stage": "wait_eval", "updated_at": now()})
                time.sleep(poll_seconds)
                continue
            if action == "wait_main":
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {
                        "status": "blocked",
                        "reason": "main_review_required",
                        "current_stage": "wait_main",
                        "finished_at": now(),
                        "updated_at": now(),
                    },
                )
                return
            if not route_status.get("should_start_codex"):
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {
                        "status": "failed",
                        "reason": f"route_not_startable:{route_status.get('status_label') or action or 'unknown'}",
                        "finished_at": now(),
                        "updated_at": now(),
                    },
                )
                return
            if action == "start_debug":
                attempts = int(campaign.get("debug_attempts") or 0) + 1
                if attempts > int(campaign.get("max_debug_attempts") or 3):
                    update_headless_campaign(
                        workspace,
                        campaign_id,
                        {
                            "status": "failed",
                            "reason": "debug_attempt_limit_reached",
                            "finished_at": now(),
                            "updated_at": now(),
                        },
                    )
                    return
                update_headless_campaign(workspace, campaign_id, {"debug_attempts": attempts, "updated_at": now()})

            stage_model, stage_effort = headless_campaign_stage_config(campaign, action)
            launched = launch_dashboard_headless_goal(
                workspace,
                agent,
                stage_model,
                stage_effort,
                campaign_id=campaign_id,
            )
            update_headless_campaign(
                workspace,
                campaign_id,
                {"active_run_id": launched.get("run"), "current_stage": action, "updated_at": now()},
            )
            time.sleep(poll_seconds)
    except BaseException as exc:
        try:
            current = get_headless_campaign(workspace, campaign_id)
            if current.get("status") not in {"done", "failed", "stopped"}:
                update_headless_campaign(
                    workspace,
                    campaign_id,
                    {
                        "status": "failed",
                        "reason": "campaign_supervisor_error",
                        "error": str(exc),
                        "finished_at": now(),
                        "updated_at": now(),
                    },
                )
        except BaseException:
            pass
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise SystemExit(1) from None


def cmd_headless_goal(workspace: Path, run_id: str) -> None:
    run = get_headless_run(workspace, run_id)
    agent = str(run.get("agent") or "")
    agent_dir = require_under(workspace / agent, workspace, "agent directory")
    if not agent_dir.is_dir():
        update_headless_run(workspace, run_id, {"status": "failed", "reason": "agent_dir_missing", "finished_at": now(), "returncode": 1})
        raise SystemExit(1)
    prompt = str(run.get("prompt") or "")
    if not prompt:
        update_headless_run(workspace, run_id, {"status": "failed", "reason": "missing_prompt", "finished_at": now(), "returncode": 1})
        raise SystemExit(1)
    command = build_headless_codex_command(run, workspace, agent_dir)
    resources = normalize_resource_request(run.get("resources", free_run_resources(load_resource_config(workspace), agent)), allow_any_gpu=False)
    log_path = require_under(workspace / str(run.get("log", "")), pub(workspace) / "log", "headless log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    update_headless_run(workspace, run_id, {"status": "running", "command": command, "started_at": now()})
    proc: subprocess.Popen[str] | None = None
    try:
        with log_path.open("a", encoding="utf-8") as log:
            route_tmp = agent_dir / ".tmp"
            route_tmp.mkdir(exist_ok=True)
            child_env = dict(os.environ)
            child_env["PATH"] = str(topic_root(workspace) / ".discovery" / "bin") + os.pathsep + child_env.get("PATH", "")
            child_env["TMPDIR"] = str(route_tmp)
            child_env["DISCOVERY_HEADLESS_RUN_ID"] = run_id
            child_env["DISCOVERY_HEADLESS_CAMPAIGN_ID"] = str(run.get("campaign_id") or "")
            child_env["DISCOVERY_PROBLEM_ROOT"] = str(workspace.resolve())
            child_env.update(build_resource_env(resources, list(resources["gpus"])))
            command = resource_runner_command(command, resources)
            proc = subprocess.Popen(command, cwd=agent_dir, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True, bufsize=1)
            pgid = os.getpgid(proc.pid)
            update_headless_run(workspace, run_id, {"pid": proc.pid, "pgid": pgid})
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                log.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    update_headless_run(
                        workspace,
                        run_id,
                        {"last_activity_at": now(), "last_event_type": event.get("type")},
                    )
                if isinstance(event, dict) and event.get("type") == "thread.started":
                    new_thread_id = event.get("thread_id")
                    if isinstance(new_thread_id, str) and new_thread_id:
                        write_headless_thread(workspace, agent, new_thread_id)
                        update_headless_run(workspace, run_id, {"thread_id": new_thread_id})
                if isinstance(event, dict):
                    usage = headless_usage_from_event(event)
                    if usage is not None:
                        update_headless_run(workspace, run_id, {"usage": usage, "usage_recorded_at": now()})
            returncode = proc.wait()
    except Exception as exc:
        if proc is not None and proc.poll() is None:
            kill_process_group(os.getpgid(proc.pid))
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"type": "runner.error", "error": str(exc), "traceback": traceback.format_exc()}, sort_keys=True) + "\n")
        update_headless_run(workspace, run_id, {"status": "failed", "reason": "runner_error", "error": str(exc), "finished_at": now(), "returncode": 1})
        raise SystemExit(1) from None
    status = "done" if returncode == 0 else "failed"
    reason = "completed" if returncode == 0 else resource_failure_reason(int(returncode), log_path)
    update_headless_run(workspace, run_id, {"status": status, "reason": reason, "finished_at": now(), "returncode": int(returncode)})
    raise SystemExit(shell_exit_code(returncode))


def cancel_job(workspace: Path, job_id: str, *, emit: bool = True) -> dict[str, Any]:
    job = get_job(workspace, job_id)
    status = job.get("status")
    if status == "queued":
        update_job(workspace, job_id, {"status": "cancelled", "reason": "cancelled_by_user", "finished_at": now(), "returncode": None})
    elif status in {"running", "starting", "paused"}:
        target = job.get("pgid") if isinstance(job.get("pgid"), int) else job.get("pid")
        if target is None and job.get("kind") != "route_run":
            target = job.get("supervisor_pid")
        kill_process_group(target)
        update_job(workspace, job_id, {"status": "cancelled", "reason": "cancelled_by_user", "finished_at": now(), "returncode": None})
        release_lease(workspace, job_id)
    elif status in TERMINAL_JOB_STATUSES:
        pass
    else:
        raise SystemExit(f"cannot cancel job in status {status}")
    current = get_job(workspace, job_id)
    payload = {"job": job_id, "status": current.get("status"), "reason": current.get("reason")}
    if emit:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def job_index(workspace: Path) -> Path:
    path = pub(workspace) / "log" / "jobs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def job_lock_path(workspace: Path) -> Path:
    return pub(workspace) / "log" / "jobs.lock"


def with_job_lock(workspace: Path):
    path = job_lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def get_job(workspace: Path, job_id: str) -> dict[str, Any]:
    return find_row(read_jsonl(job_index(workspace)), job_id)


def upsert_job(workspace: Path, job: dict[str, Any]) -> None:
    job.setdefault("problem_id", current_problem_id(workspace))
    with with_job_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = job_index(workspace)
        rows = read_jsonl(path)
        out = [job if row.get("id") == job.get("id") else row for row in rows]
        if not any(row.get("id") == job.get("id") for row in rows):
            out.append(job)
        write_jsonl(path, out)


def update_job(workspace: Path, job_id: str, updates: dict[str, Any]) -> None:
    with with_job_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = job_index(workspace)
        rows = read_jsonl(path)
        found = False
        for job in rows:
            if job.get("id") == job_id:
                job.update(updates)
                found = True
                break
        if not found:
            raise SystemExit(f"id not found: {job_id}")
        write_jsonl(path, rows)


def refresh_all_jobs(workspace: Path) -> None:
    with with_job_lock(workspace) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = read_jsonl(job_index(workspace))
        refreshed = [refresh_job(job) for job in rows]
        if refreshed != rows:
            write_jsonl(job_index(workspace), refreshed)


def mark_job_stale_if_running(workspace: Path, job_id: str, reason: str) -> None:
    if not job_id:
        return
    try:
        job = get_job(workspace, job_id)
    except SystemExit:
        return
    if job.get("status") in {"running", "starting"}:
        job.update({"status": "stale", "reason": reason, "finished_at": now()})
        upsert_job(workspace, job)


def refresh_job(job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    if out.get("status") in {"running", "starting"}:
        pid = out.get("pid") or out.get("supervisor_pid")
        if not process_alive(pid):
            # A newly persisted Job has a short, legitimate interval before
            # its independent supervisor records a PID.
            if out.get("status") == "starting" and not pid:
                try:
                    created = datetime.fromisoformat(str(out.get("created_at") or ""))
                    if (datetime.now(UTC) - created).total_seconds() < 30:
                        return out
                except (TypeError, ValueError):
                    pass
            out["status"] = "stale"
            out["reason"] = "lost_worker_or_pid"
            out["finished_at"] = out.get("finished_at") or now()
    return out


def process_alive(pid: Any) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        stat_fields = stat_path.read_text(encoding="utf-8", errors="ignore").split() if stat_path.exists() else []
        if len(stat_fields) > 2 and stat_fields[2] == "Z":
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_cgroup_value(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _job_cgroup_path(pid: Any) -> Path | None:
    if not isinstance(pid, int) or not process_alive(pid):
        return None
    try:
        for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[0] == "0":
                path = (Path("/sys/fs/cgroup") / fields[2].lstrip("/")).resolve()
                if str(path).startswith("/sys/fs/cgroup/") and path.is_dir():
                    return path
    except OSError:
        return None
    return None


def _cgroup_cpu_usage_usec(path: Path) -> int | None:
    try:
        for line in (path / "cpu.stat").read_text(encoding="utf-8").splitlines():
            key, value = line.split(maxsplit=1)
            if key == "usage_usec":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def collect_job_runtime(
    workspace: Path,
    job: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect observable facts; quiet stdout alone never means a stalled Job."""
    previous = previous if isinstance(previous, dict) else {}
    sampled_at = now()
    sampled_monotonic = time.monotonic()
    pid = job.get("pid") if isinstance(job.get("pid"), int) else None
    supervisor_pid = job.get("supervisor_pid") if isinstance(job.get("supervisor_pid"), int) else None
    alive = process_alive(pid) or (pid is None and process_alive(supervisor_pid))
    cgroup = _job_cgroup_path(pid)
    usage_usec = _cgroup_cpu_usage_usec(cgroup) if cgroup else None
    cpu_percent: float | None = None
    prior_usage = previous.get("cpu_usage_usec")
    prior_monotonic = previous.get("sampled_monotonic")
    if isinstance(usage_usec, int) and isinstance(prior_usage, int) and isinstance(prior_monotonic, (int, float)):
        elapsed = sampled_monotonic - float(prior_monotonic)
        if elapsed > 0 and usage_usec >= prior_usage:
            cpu_percent = round((usage_usec - prior_usage) / (elapsed * 1_000_000) * 100, 1)
    memory_current = _read_cgroup_value(cgroup / "memory.current") if cgroup else None
    memory_peak = _read_cgroup_value(cgroup / "memory.peak") if cgroup else None
    process_count = _read_cgroup_value(cgroup / "pids.current") if cgroup else None
    log_size = 0
    log_updated_at: str | None = None
    output_quiet_seconds: float | None = None
    raw_log = str(job.get("log") or "")
    if raw_log:
        try:
            log_path = require_under(workspace / raw_log, pub(workspace) / "log", "job log")
            stat = log_path.stat()
            log_size = stat.st_size
            log_updated_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
            output_quiet_seconds = round(max(0.0, time.time() - stat.st_mtime), 1)
        except (OSError, SystemExit):
            pass
    status = str(job.get("status") or "unknown")
    if status == "queued":
        activity = "waiting_for_resources"
    elif status in TERMINAL_JOB_STATUSES:
        activity = status
    elif alive and cpu_percent is not None and cpu_percent >= 5:
        activity = "computing"
    elif alive:
        activity = "alive_waiting_or_io"
    else:
        activity = "starting"
    return {
        "sampled_at": sampled_at,
        "sampled_monotonic": sampled_monotonic,
        "activity": activity,
        "process_alive": alive,
        "supervisor_alive": process_alive(supervisor_pid),
        "pid": pid,
        "supervisor_pid": supervisor_pid,
        "cpu_percent": cpu_percent,
        "cpu_cores": round(cpu_percent / 100, 2) if cpu_percent is not None else None,
        "cpu_usage_usec": usage_usec,
        "memory_current_gb": round(memory_current / 1024**3, 3) if memory_current is not None else None,
        "memory_peak_gb": round(memory_peak / 1024**3, 3) if memory_peak is not None else None,
        "process_count": process_count,
        "log_size_bytes": log_size,
        "log_updated_at": log_updated_at,
        "output_quiet_seconds": output_quiet_seconds,
    }


def set_loop_state(agent_dir: Path, phase: str, **extra: Any) -> None:
    path = agent_dir / ".discovery" / "loop_state.json"
    state = read_json(
        path,
        {
            "phase": "work_loop",
            "last_version": None,
            "last_reflected_version": None,
            "eval_status": None,
            "active_eval": None,
            "last_error": None,
        },
    )
    state["phase"] = phase
    state["updated_at"] = now()
    state.update(extra)
    write_json(path, state)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def find_row(rows: list[dict[str, Any]], row_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("id") == row_id:
            return row
    raise SystemExit(f"id not found: {row_id}")


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, quiet: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    output = subprocess.DEVNULL if quiet else None
    return subprocess.run(command, cwd=cwd, env=merged, text=True, stdout=output, stderr=output, check=check)


def git_stdout(repo: Path, command: list[str], check: bool = True) -> str:
    proc = subprocess.run(["git", *command], cwd=repo, text=True, capture_output=True)
    if proc.returncode != 0:
        if check:
            raise SystemExit(proc.stderr.strip())
        return ""
    return proc.stdout


if __name__ == "__main__":
    main()
