import argparse
import contextlib
import importlib.util
import io
import json
import os
import runpy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli_route_run_tests", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class RouteRunLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "problem-a"
        self.agent = self.workspace / "agent1"
        (self.workspace / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
        (self.workspace / ".DiscoveryConsole" / "pub" / "knowledge" / "versions").mkdir(parents=True)
        (self.workspace / ".DiscoveryConsole" / "private").mkdir(parents=True)
        (self.agent / ".discovery").mkdir(parents=True)
        DISCOVERY.write_json(self.workspace / "problem.json", {"problem_id": "problem-a"})
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {
                "phase": "work_loop",
                "last_version": "version-agent1-0001",
                "last_reflected_version": "version-agent1-0001",
                "eval_status": None,
                "active_eval": None,
                "last_error": None,
            },
        )
        (self.workspace / ".DiscoveryConsole" / "pub" / "notices.jsonl").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_foreground_run_does_not_block_context(self) -> None:
        token = DISCOVERY.ensure_route_broker_token(self.agent)
        started = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def blocking_run(*_args, **_kwargs):
            started.set()
            release.wait(2)
            return {"job": "run-a", "status": "done", "returncode": 0}

        def invoke_run() -> None:
            try:
                DISCOVERY.route_broker_action(
                    self.workspace,
                    {"route": "agent1", "action": "run.local", "resources": "", "command": ["true"]},
                    token,
                )
            except BaseException as exc:  # pragma: no cover - assertion reports it
                errors.append(exc)

        with mock.patch.object(DISCOVERY, "cmd_route_run_local", side_effect=blocking_run), mock.patch.object(
            DISCOVERY, "build_route_context", return_value={"schema_version": 1}
        ):
            thread = threading.Thread(target=invoke_run)
            thread.start()
            self.assertTrue(started.wait(1))
            before = time.monotonic()
            context = DISCOVERY.route_broker_action(
                self.workspace,
                {"route": "agent1", "action": "context", "job": "", "limit": 3},
                token,
            )
            elapsed = time.monotonic() - before
            release.set()
            thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(context["result"], {"schema_version": 1})

    def test_foreground_run_is_registered_with_log_and_stable_job_id(self) -> None:
        resources = {"cpus": 2, "memory_gb": 3.0, "gpus": [], "timeout_seconds": None}

        def finish_job(workspace, job, **_kwargs):
            persisted = DISCOVERY.get_job(workspace, str(job["id"]))
            self.assertEqual(persisted["kind"], "route_run")
            self.assertEqual(persisted["execution_mode"], "foreground")
            log_path = workspace / str(job["log"])
            log_path.write_text("tracked output\n", encoding="utf-8")
            DISCOVERY.update_job(
                workspace,
                str(job["id"]),
                {"status": "done", "reason": "completed", "returncode": 0, "finished_at": DISCOVERY.now()},
            )
            return DISCOVERY.get_job(workspace, str(job["id"]))

        args = argparse.Namespace(resources="", command=["python", "probe.py"], headless_run_id="", campaign_id="")
        with mock.patch.object(DISCOVERY, "load_resource_request", return_value=resources), mock.patch.object(
            DISCOVERY, "validate_resource_request"
        ), mock.patch.object(DISCOVERY, "launch_and_wait_job", side_effect=finish_job):
            result = DISCOVERY.cmd_route_run_local(self.workspace, self.agent, args, emit=False)

        self.assertTrue(str(result["job"]).startswith("run-"))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["output"], "tracked output\n")
        self.assertEqual(result["log"], f".DiscoveryConsole/pub/log/{result['job']}.log")

    def test_broker_deferred_wait_uses_an_independent_direct_supervisor(self) -> None:
        resources = {"cpus": 2, "memory_gb": 3.0, "gpus": [], "timeout_seconds": None}
        args = argparse.Namespace(
            resources="",
            command=["python", "probe.py"],
            completion="wait",
            defer_wait=True,
            headless_run_id="",
            campaign_id="",
        )
        fake_process = mock.Mock(pid=43210)
        with mock.patch.object(DISCOVERY, "load_resource_request", return_value=resources), mock.patch.object(
            DISCOVERY, "validate_resource_request"
        ), mock.patch.object(DISCOVERY.subprocess, "Popen", return_value=fake_process) as launch, mock.patch.object(
            DISCOVERY, "wait_for_job_completion"
        ) as wait:
            result = DISCOVERY.cmd_route_run_local(self.workspace, self.agent, args, emit=False)

        wait.assert_not_called()
        launch.assert_called_once()
        self.assertIn("_supervise_direct", launch.call_args.args[0])
        self.assertEqual(result["completion_mode"], "wait")
        job = DISCOVERY.get_job(self.workspace, result["job"])
        self.assertEqual(job["supervisor_pid"], 43210)
        self.assertEqual(job["status"], "starting")

    def test_unread_file_response_does_not_kill_broker(self) -> None:
        token = DISCOVERY.ensure_route_broker_token(self.agent)
        server, thread = DISCOVERY.start_route_broker_server(self.workspace)
        broker_root = DISCOVERY.route_broker_file_root(self.agent)
        first_started = threading.Event()
        release_first = threading.Event()

        def action(_workspace, data, _token):
            if data.get("action") == "slow":
                first_started.set()
                release_first.wait(1)
            return {"ok": True, "result": {"value": data.get("action")}}

        try:
            with mock.patch.object(DISCOVERY, "route_broker_action", side_effect=action):
                DISCOVERY.write_json(
                    broker_root / "requests" / ("1" * 32 + ".json"),
                    {"problem": "problem-a", "route": "agent1", "token": token, "action": "slow"},
                )
                self.assertTrue(first_started.wait(1))
                release_first.set()
                time.sleep(0.05)
                second_id = "2" * 32
                DISCOVERY.write_json(
                    broker_root / "requests" / f"{second_id}.json",
                    {"problem": "problem-a", "route": "agent1", "token": token, "action": "fast"},
                )
                response_path = broker_root / "responses" / f"{second_id}.json"
                deadline = time.monotonic() + 1
                while not response_path.is_file() and time.monotonic() < deadline:
                    time.sleep(0.01)
                response = DISCOVERY.read_json(response_path, {})
                self.assertTrue(response["ok"])
                self.assertEqual(response["result"], {"value": "fast"})
                self.assertTrue(thread.is_alive())
        finally:
            release_first.set()
            server.shutdown()
            server.server_close()
            thread.join(1)

    def test_route_client_round_trips_through_file_broker(self) -> None:
        DISCOVERY.ensure_route_broker_token(self.agent)
        server, thread = DISCOVERY.start_route_broker_server(self.workspace)
        DISCOVERY.write_json(
            DISCOVERY.route_broker_endpoint_path(self.workspace),
            {"transport": "file", "pid": os.getpid(), "owner": "test"},
        )
        client = runpy.run_path(str(MODULE_PATH.parents[2] / "subprojects-team" / ".team-template" / "route" / "explore"))
        try:
            with mock.patch.object(
                DISCOVERY,
                "route_broker_action",
                return_value={"ok": True, "result": {"schema_version": 1}},
            ):
                result = client["request"](self.agent, self.workspace, "agent1", "context", {"job": "", "limit": 3})
            self.assertEqual(result, {"ok": True, "result": {"schema_version": 1}})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)

    def test_runtime_heartbeat_reports_compute_and_memory_without_log_output(self) -> None:
        cgroup = Path(self.temp_dir.name) / "cgroup"
        cgroup.mkdir()
        (cgroup / "cpu.stat").write_text("usage_usec 2500000\n", encoding="utf-8")
        (cgroup / "memory.current").write_text(str(2 * 1024**3), encoding="utf-8")
        (cgroup / "memory.peak").write_text(str(3 * 1024**3), encoding="utf-8")
        (cgroup / "pids.current").write_text("9\n", encoding="utf-8")
        job = {
            "id": "run-live",
            "status": "running",
            "pid": os.getpid(),
            "supervisor_pid": os.getpid(),
            "log": ".DiscoveryConsole/pub/log/run-live.log",
        }
        previous = {"cpu_usage_usec": 500000, "sampled_monotonic": 8.0}
        with mock.patch.object(DISCOVERY, "_job_cgroup_path", return_value=cgroup), mock.patch.object(
            DISCOVERY.time, "monotonic", return_value=10.0
        ):
            runtime = DISCOVERY.collect_job_runtime(self.workspace, job, previous)
        self.assertEqual(runtime["activity"], "computing")
        self.assertEqual(runtime["cpu_percent"], 100.0)
        self.assertEqual(runtime["memory_current_gb"], 2.0)
        self.assertEqual(runtime["memory_peak_gb"], 3.0)
        self.assertEqual(runtime["process_count"], 9)
        self.assertTrue(runtime["process_alive"])

    def test_job_capture_persists_carriage_return_progress_before_newline(self) -> None:
        job_id = "job-progress"
        log_path = self.workspace / ".DiscoveryConsole" / "pub" / "log" / f"{job_id}.log"
        job = {
            "id": job_id,
            "status": "starting",
            "agent": "",
            "launcher": "test",
            "command": [
                os.sys.executable,
                "-c",
                "import os,time; os.write(1,b'Epoch 1/3 ETA 2s\\r'); time.sleep(.4); "
                "os.write(1,b'Epoch 2/3 ETA 1s\\r'); time.sleep(.2); os.write(1,b'Epoch 3/3 ETA 0s\\n')",
            ],
            "cwd": str(self.workspace),
            "resources": {"cpus": 1, "memory_gb": 1.0, "gpus": [], "timeout_seconds": 5.0},
            "log": f".DiscoveryConsole/pub/log/{job_id}.log",
        }
        DISCOVERY.upsert_job(self.workspace, job)
        result: dict = {}

        def run() -> None:
            result.update(DISCOVERY.launch_and_wait_job(self.workspace, job))

        with mock.patch.object(DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command), mock.patch.object(
            DISCOVERY, "update_lease_pid"
        ):
            thread = threading.Thread(target=run)
            thread.start()
            deadline = time.time() + 1
            while time.time() < deadline and (not log_path.exists() or b"Epoch 1/3" not in log_path.read_bytes()):
                time.sleep(0.01)
            self.assertTrue(thread.is_alive(), "first progress frame should be persisted while the process is still running")
            self.assertIn(b"Epoch 1/3 ETA 2s\r", log_path.read_bytes())
            thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "done")
        self.assertIn(b"Epoch 2/3 ETA 1s\r", log_path.read_bytes())

    def test_route_client_coalesces_high_frequency_overwrite_progress(self) -> None:
        client = runpy.run_path(str(Path(__file__).parents[2] / "subprojects-team" / ".team-template" / "route" / "explore"))
        path = Path(self.temp_dir.name) / "progress.log"
        raw = b"Epoch 1/3 ETA 2s\rEpoch 2/3 ETA 1s\rEpoch 3/3 ETA 0s\r"
        path.write_bytes(raw)
        state = {"next_progress_emit": 0.0}
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            offset = client["stream_new_log"](path, 0, state)
        visible = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(offset, len(raw))
        self.assertIn("Epoch 3/3 ETA 0s", visible)
        self.assertNotIn("Epoch 1/3", visible)
        self.assertNotIn("Epoch 2/3", visible)
        self.assertEqual(path.read_bytes(), raw, "the complete raw progress stream remains in the Job log")

    def test_completion_defaults_and_wait_timeout_keep_the_original_job(self) -> None:
        self.assertEqual(DISCOVERY.completion_mode(None, queued=False), "wait")
        self.assertEqual(DISCOVERY.completion_mode(None, queued=True), "detach")
        with self.assertRaisesRegex(SystemExit, "completion mode"):
            DISCOVERY.completion_mode("resume", queued=False)

        job_id = "run-timeout"
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": job_id,
                "kind": "route_run",
                "agent": "agent1",
                "status": "running",
                "pid": os.getpid(),
                "log": f".DiscoveryConsole/pub/log/{job_id}.log",
                "resources": {"cpus": 1, "memory_gb": 1, "gpus": [], "timeout_seconds": None},
            },
        )
        result = DISCOVERY.wait_for_job_completion(self.workspace, job_id, 0)
        self.assertEqual(result["job"], job_id)
        self.assertEqual(result["status"], "still_running")
        self.assertEqual(result["job_status"], "running")
        self.assertTrue(result["handoff_required"])
        self.assertEqual(result["next_action"], "checkpoint_and_end_turn")
        persisted = DISCOVERY.get_job(self.workspace, job_id)
        self.assertIn("wait_timed_out_at", persisted)
        self.assertEqual(len(DISCOVERY.read_jsonl(DISCOVERY.job_index(self.workspace))), 1)

    def test_short_attached_wait_returns_the_terminal_original_job(self) -> None:
        job_id = "run-attached"
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": job_id,
                "kind": "route_run",
                "agent": "agent1",
                "status": "running",
                "pid": os.getpid(),
                "log": f".DiscoveryConsole/pub/log/{job_id}.log",
                "resources": {"cpus": 1, "memory_gb": 1, "gpus": [], "timeout_seconds": None},
                "completion_mode": "wait",
            },
        )

        def complete() -> None:
            time.sleep(0.03)
            DISCOVERY.update_job(
                self.workspace,
                job_id,
                {"status": "done", "reason": "completed", "returncode": 0, "finished_at": DISCOVERY.now()},
            )

        thread = threading.Thread(target=complete)
        thread.start()
        result = DISCOVERY.wait_for_job_completion(self.workspace, job_id, 1)
        thread.join(1)
        self.assertEqual(result["job"], job_id)
        self.assertEqual(result["status"], "done")
        self.assertFalse(result["handoff_required"])
        self.assertEqual(result["next_action"], "continue_current_role")
        self.assertEqual(len(DISCOVERY.read_jsonl(DISCOVERY.job_index(self.workspace))), 1)

    def test_queued_wait_uses_the_same_persistent_job(self) -> None:
        resources = {"cpus": 2, "memory_gb": 3.0, "gpus": [], "timeout_seconds": None}
        args = argparse.Namespace(
            resources="large.json",
            command=["python", "large.py"],
            completion="wait",
            headless_run_id="",
            campaign_id="",
        )
        with mock.patch.object(DISCOVERY, "load_resource_request", return_value=resources), mock.patch.object(
            DISCOVERY, "validate_resource_request"
        ), mock.patch.object(DISCOVERY, "attached_wait_timeout_seconds", return_value=2), mock.patch.object(
            DISCOVERY, "wait_for_job_completion", return_value={"status": "done", "job": "placeholder"}
        ) as wait:
            DISCOVERY.cmd_submit(self.workspace, self.agent, args, emit=False)
        submitted = DISCOVERY.read_jsonl(DISCOVERY.job_index(self.workspace))[-1]
        self.assertEqual(submitted["completion_mode"], "wait")
        self.assertIsNotNone(submitted["wait_deadline"])
        self.assertEqual(wait.call_args.args[1], submitted["id"])

    def test_queued_run_keeps_headless_campaign_provenance(self) -> None:
        campaign_id = "campaign-a"
        headless_id = "headless-a"
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": headless_id,
                "agent": "agent1",
                "status": "running",
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        resources = {"cpus": 4, "memory_gb": 8.0, "gpus": [], "timeout_seconds": None}
        args = argparse.Namespace(
            resources="large.json",
            command=["python", "large.py"],
            headless_run_id=headless_id,
            campaign_id=campaign_id,
        )
        with mock.patch.object(DISCOVERY, "load_resource_request", return_value=resources), mock.patch.object(
            DISCOVERY, "validate_resource_request"
        ):
            result = DISCOVERY.cmd_submit(self.workspace, self.agent, args, emit=False)

        job = DISCOVERY.get_job(self.workspace, result["job"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["execution_mode"], "queued")
        self.assertEqual(job["headless_run_id"], headless_id)
        self.assertEqual(job["campaign_id"], campaign_id)

    def test_campaign_waits_for_active_builder_run_instead_of_failing(self) -> None:
        campaign_id = "campaign-a"
        headless_id = "headless-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": headless_id,
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "runner_action": "start_builder",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "run-a",
                "kind": "route_run",
                "agent": "agent1",
                "status": "running",
                "pid": os.getpid(),
                "headless_run_id": headless_id,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:01+00:00",
                "log": ".DiscoveryConsole/pub/log/run-a.log",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": headless_id,
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        observed: dict = {}

        def stop_after_observation(_seconds: float) -> None:
            observed.update(DISCOVERY.get_headless_campaign(self.workspace, campaign_id))
            DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        with mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_observation):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)

        self.assertEqual(observed["status"], "running")
        self.assertEqual(observed["current_stage"], "wait_builder_job")
        self.assertEqual(observed["waiting_route_jobs"], ["run-a"])
        self.assertEqual(observed["last_processed_run_id"], headless_id)
        self.assertNotEqual(observed.get("reason"), "headless_stage_made_no_state_progress")

    def test_campaign_waits_for_queued_builder_job_instead_of_failing(self) -> None:
        campaign_id = "campaign-a"
        headless_id = "headless-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": headless_id,
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "runner_action": "start_builder",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "job-a",
                "launcher": "submit",
                "execution_mode": "queued",
                "agent": "agent1",
                "status": "queued",
                "headless_run_id": headless_id,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:01+00:00",
                "log": ".DiscoveryConsole/pub/log/job-a.log",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": headless_id,
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        observed: dict = {}

        def stop_after_observation(_seconds: float) -> None:
            observed.update(DISCOVERY.get_headless_campaign(self.workspace, campaign_id))
            DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        with mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_observation):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)

        self.assertEqual(observed["status"], "running")
        self.assertEqual(observed["current_stage"], "wait_builder_job")
        self.assertEqual(observed["waiting_route_jobs"], ["job-a"])
        self.assertEqual(observed["last_processed_run_id"], headless_id)
        self.assertNotEqual(observed.get("reason"), "headless_stage_made_no_state_progress")

    def test_campaign_reenters_builder_for_already_completed_queued_job(self) -> None:
        campaign_id = "campaign-a"
        headless_id = "headless-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": headless_id,
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "runner_action": "start_builder",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "job-a",
                "launcher": "submit",
                "execution_mode": "queued",
                "agent": "agent1",
                "status": "done",
                "returncode": 0,
                "reason": "completed",
                "headless_run_id": headless_id,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:01+00:00",
                "log": ".DiscoveryConsole/pub/log/job-a.log",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": headless_id,
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        sleeps = 0

        def stop_after_launch(_seconds: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        route_status = {
            "runner_action": "start_builder",
            "should_start_codex": True,
            "goal_file": "headless_goals/route_builder.md",
        }
        with mock.patch.object(DISCOVERY, "build_dashboard_agent_statuses", return_value=[route_status]), mock.patch.object(
            DISCOVERY, "launch_dashboard_headless_goal", return_value={"run": "headless-b"}
        ) as launch, mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_launch):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)

        launch.assert_called_once()
        self.assertEqual(launch.call_args.kwargs["campaign_id"], campaign_id)
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["last_route_jobs"], ["job-a"])
        self.assertNotEqual(campaign.get("reason"), "headless_stage_made_no_state_progress")

    def test_campaign_reenters_builder_when_waited_route_run_finishes_before_codex_exit(self) -> None:
        campaign_id = "campaign-a"
        headless_id = "headless-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": headless_id,
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "runner_action": "start_builder",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "run-a",
                "kind": "route_run",
                "launcher": "run",
                "execution_mode": "foreground",
                "completion_mode": "wait",
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "returncode": 0,
                "headless_run_id": headless_id,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:01+00:00",
                "log": ".DiscoveryConsole/pub/log/run-a.log",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": headless_id,
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                # Reproduce the dangerous boundary: without recognizing the
                # terminal run, one more no-progress decision would block.
                "no_progress_attempts": 1,
                "max_no_progress_attempts": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        sleeps = 0

        def stop_after_launch(_seconds: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        route_status = {
            "runner_action": "start_builder",
            "should_start_codex": True,
            "goal_file": "headless_goals/route_builder.md",
        }
        with mock.patch.object(DISCOVERY, "build_dashboard_agent_statuses", return_value=[route_status]), mock.patch.object(
            DISCOVERY, "launch_dashboard_headless_goal", return_value={"run": "headless-b"}
        ) as launch, mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_launch):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)

        launch.assert_called_once()
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["last_route_jobs"], ["run-a"])
        self.assertEqual(campaign["waiting_route_jobs"], [])
        self.assertEqual(campaign["no_progress_attempts"], 0)
        self.assertNotEqual(campaign.get("reason"), "headless_stage_made_no_state_progress")

    def test_dashboard_blocks_new_builder_while_queued_job_is_active(self) -> None:
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "job-a",
                "launcher": "submit",
                "execution_mode": "queued",
                "agent": "agent1",
                "status": "queued",
                "created_at": "2026-01-01T00:00:01+00:00",
                "log": ".DiscoveryConsole/pub/log/job-a.log",
            },
        )
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), {"configured": True})
        with mock.patch.object(DISCOVERY, "route_sandbox_report", return_value={"available": True}), mock.patch.object(
            DISCOVERY, "route_cli_protocol_ready", return_value=True
        ):
            status = DISCOVERY.build_dashboard_agent_statuses(self.workspace, ["agent1"])[0]
        self.assertEqual(status["runner_action"], "wait_builder_job")
        self.assertEqual(status["status_label"], "Waiting for resources")
        self.assertFalse(status["should_start_codex"])

    def test_campaign_reenters_builder_after_waited_run_finishes(self) -> None:
        campaign_id = "campaign-a"
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "run-a",
                "kind": "route_run",
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "returncode": 0,
                "created_at": "2026-01-01T00:00:01+00:00",
                "log": ".DiscoveryConsole/pub/log/run-a.log",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": None,
                "last_processed_run_id": "headless-a",
                "waiting_route_jobs": ["run-a"],
                "stage_configs": {
                    "auditor": {"model": "audit-model", "reasoning_effort": "high"},
                    "builder": {"model": "build-model", "reasoning_effort": "xhigh"},
                    "debug_eval": {"model": "debug-model", "reasoning_effort": "medium"},
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )

        def stop_after_launch(_seconds: float) -> None:
            DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        route_status = {
            "runner_action": "start_builder",
            "should_start_codex": True,
            "goal_file": "headless_goals/route_builder.md",
        }
        with mock.patch.object(DISCOVERY, "build_dashboard_agent_statuses", return_value=[route_status]), mock.patch.object(
            DISCOVERY, "launch_dashboard_headless_goal", return_value={"run": "headless-b"}
        ) as launch, mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_launch):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)

        launch.assert_called_once()
        self.assertEqual(launch.call_args.kwargs["campaign_id"], campaign_id)
        self.assertEqual(launch.call_args.args[2:4], ("build-model", "xhigh"))
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["last_route_jobs"], ["run-a"])
        self.assertEqual(campaign["waiting_route_jobs"], [])

    def test_clean_builder_exit_without_state_change_becomes_main_handoff(self) -> None:
        campaign_id = "campaign-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": "headless-a",
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "runner_action": "start_builder",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": "headless-a",
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "max_no_progress_attempts": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["status"], "blocked")
        self.assertEqual(campaign["reason"], "builder_handoff_without_candidate")

    def test_campaign_counts_iteration_only_after_final_reflection(self) -> None:
        campaign_id = "campaign-reflection"
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {
                "phase": "reflection_loop",
                "last_version": "version-agent1-0002",
                "last_reflected_version": "version-agent1-0001",
                "eval_status": "succeeded",
                "active_eval": None,
                "last_error": None,
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": None,
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )

        def stop_after_launch(_seconds: float) -> None:
            DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        route_status = {"runner_action": "start_auditor", "should_start_codex": True, "goal_file": "headless_goals/route_auditor.md"}
        with mock.patch.object(DISCOVERY, "build_dashboard_agent_statuses", return_value=[route_status]), mock.patch.object(
            DISCOVERY, "launch_dashboard_headless_goal", return_value={"run": "headless-auditor"}
        ) as launch, mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_launch):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)
        launch.assert_called_once()
        self.assertEqual(DISCOVERY.get_headless_campaign(self.workspace, campaign_id)["completed_iterations"], 0)

        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {
                "phase": "work_loop",
                "last_version": "version-agent1-0002",
                "last_reflected_version": "version-agent1-0002",
                "eval_status": None,
                "active_eval": None,
                "last_error": None,
            },
        )
        DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "running", "active_run_id": None})
        DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["status"], "done")
        self.assertEqual(campaign["completed_iterations"], 1)
        self.assertEqual(campaign["reason"], "target_iterations_reflected")

    def test_campaign_retries_transient_broker_failure_while_endpoint_is_live(self) -> None:
        campaign_id = "campaign-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": "headless-a",
                "agent": "agent1",
                "status": "done",
                "reason": "completed",
                "runner_action": "start_auditor",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": "headless-a",
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        route_status = {
            "runner_action": "start_auditor",
            "should_start_codex": True,
            "goal_file": "headless_goals/route_auditor.md",
        }
        sleeps = 0

        def stop_after_retry(_seconds: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        with mock.patch.object(
            DISCOVERY, "headless_run_infrastructure_reason", return_value="headless_route_broker_unavailable"
        ), mock.patch.object(
            DISCOVERY, "route_broker_is_available", return_value=True
        ), mock.patch.object(
            DISCOVERY, "build_dashboard_agent_statuses", return_value=[route_status]
        ), mock.patch.object(
            DISCOVERY, "launch_dashboard_headless_goal", return_value={"run": "headless-b"}
        ) as launch, mock.patch.object(
            DISCOVERY.time, "sleep", side_effect=stop_after_retry
        ):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)

        launch.assert_called_once()
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["infrastructure_retry_attempts"], 1)
        self.assertNotEqual(campaign.get("reason"), "headless_route_broker_unavailable")

    def test_campaign_blocks_after_its_bounded_no_progress_retry(self) -> None:
        campaign_id = "campaign-a"
        loop_state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        DISCOVERY.upsert_headless_run(
            self.workspace,
            {
                "id": "headless-a",
                "agent": "agent1",
                "status": "failed",
                "reason": "runner_error",
                "runner_action": "start_builder",
                "loop_state_before": loop_state,
                "campaign_id": campaign_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": "headless-a",
                "last_processed_run_id": None,
                "waiting_route_jobs": [],
                "no_progress_attempts": 1,
                "max_no_progress_attempts": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["status"], "blocked")
        self.assertEqual(campaign["reason"], "headless_stage_made_no_state_progress")
        self.assertEqual(campaign["last_no_progress_reason"], "runner_error")

    def test_campaign_reenters_once_after_all_failed_or_successful_detached_jobs(self) -> None:
        campaign_id = "campaign-a"
        for job_id, status in (("job-success", "done"), ("job-failure", "failed")):
            DISCOVERY.upsert_job(
                self.workspace,
                {
                    "id": job_id,
                    "launcher": "submit",
                    "execution_mode": "queued",
                    "completion_mode": "detach",
                    "agent": "agent1",
                    "status": status,
                    "returncode": 0 if status == "done" else 1,
                    "campaign_id": campaign_id,
                    "created_at": "2026-01-01T00:00:01+00:00",
                    "log": f".DiscoveryConsole/pub/log/{job_id}.log",
                },
            )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": campaign_id,
                "agent": "agent1",
                "status": "running",
                "target_iterations": 1,
                "completed_iterations": 0,
                "completed_versions": [],
                "current_version": "version-agent1-0001",
                "active_run_id": None,
                "last_processed_run_id": "headless-a",
                "waiting_route_jobs": ["job-success", "job-failure"],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        route_status = {"runner_action": "start_builder", "should_start_codex": True, "goal_file": "headless_goals/route_builder.md"}

        def stop_after_launch(_seconds: float) -> None:
            DISCOVERY.update_headless_campaign(self.workspace, campaign_id, {"status": "stopped"})

        with mock.patch.object(DISCOVERY, "build_dashboard_agent_statuses", return_value=[route_status]), mock.patch.object(
            DISCOVERY, "launch_dashboard_headless_goal", return_value={"run": "headless-b"}
        ) as launch, mock.patch.object(DISCOVERY.time, "sleep", side_effect=stop_after_launch):
            DISCOVERY.cmd_headless_campaign(self.workspace, campaign_id, poll_seconds=0.01)
        launch.assert_called_once()
        campaign = DISCOVERY.get_headless_campaign(self.workspace, campaign_id)
        self.assertEqual(campaign["last_route_jobs"], ["job-success", "job-failure"])

    def test_stopping_campaign_cancels_its_active_builder_jobs(self) -> None:
        campaign_id = "campaign-a"
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {"id": campaign_id, "agent": "agent1", "status": "running", "created_at": "now"},
        )
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "run-a",
                "kind": "route_run",
                "agent": "agent1",
                "status": "running",
                "pid": os.getpid(),
                "campaign_id": campaign_id,
                "created_at": "now",
                "log": ".DiscoveryConsole/pub/log/run-a.log",
            },
        )
        DISCOVERY.upsert_job(
            self.workspace,
            {
                "id": "job-b",
                "launcher": "submit",
                "execution_mode": "queued",
                "agent": "agent1",
                "status": "queued",
                "campaign_id": campaign_id,
                "created_at": "now-2",
                "log": ".DiscoveryConsole/pub/log/job-b.log",
            },
        )
        with mock.patch.object(DISCOVERY, "cancel_job", return_value={"status": "cancelled"}) as cancel:
            result = DISCOVERY.control_dashboard_headless_goal(self.workspace, "agent1", "stop")
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(
            [call.args[1] for call in cancel.call_args_list],
            ["run-a", "job-b"],
        )


if __name__ == "__main__":
    unittest.main()
