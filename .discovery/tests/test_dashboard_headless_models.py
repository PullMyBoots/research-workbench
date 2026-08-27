import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class HeadlessModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "workspace"
        self.codex_home = Path(self.temp_dir.name) / "codex-home"
        self.workspace.mkdir()
        self.codex_home.mkdir()
        (self.codex_home / "config.toml").write_text(
            'model = "model-a"\nmodel_reasoning_effort = "high"\n',
            encoding="utf-8",
        )
        self.catalog = {
            "models": [
                {
                    "slug": "model-a",
                    "display_name": "Model A",
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Fast"},
                        {"effort": "high", "description": "Deep"},
                    ],
                },
                {
                    "slug": "model-b",
                    "display_name": "Model B",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "medium", "description": "Balanced"},
                    ],
                },
            ]
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def mocked_catalog(self):
        return mock.patch.object(
            DISCOVERY.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["codex", "debug", "models"],
                0,
                stdout=json.dumps(self.catalog),
                stderr="",
            ),
        )

    def test_configured_model_and_reasoning_are_dashboard_defaults(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), self.mocked_catalog():
            config = DISCOVERY.build_headless_model_config(self.workspace)
        self.assertEqual(config["default_model"], "model-a")
        self.assertEqual(config["default_reasoning_effort"], "high")
        self.assertEqual([model["id"] for model in config["models"]], ["model-a", "model-b"])

    def test_selection_rejects_effort_not_supported_by_model(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), self.mocked_catalog():
            with self.assertRaisesRegex(SystemExit, "unsupported reasoning effort"):
                DISCOVERY.resolve_headless_model_selection(self.workspace, "model-b", "high")

    def test_stage_selections_are_validated_and_frozen_independently(self) -> None:
        requested = {
            "auditor": {"model": "model-a", "reasoning_effort": "high"},
            "builder": {"model": "model-a", "reasoning_effort": "low"},
            "debug_eval": {"model": "model-b", "reasoning_effort": "medium"},
        }
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), self.mocked_catalog():
            resolved = DISCOVERY.resolve_headless_stage_configs(self.workspace, requested)
        self.assertEqual(resolved, requested)
        campaign = {"stage_configs": resolved, "model": "legacy", "model_reasoning_effort": "low"}
        self.assertEqual(DISCOVERY.headless_campaign_stage_config(campaign, "start_auditor"), ("model-a", "high"))
        self.assertEqual(DISCOVERY.headless_campaign_stage_config(campaign, "start_builder"), ("model-a", "low"))
        self.assertEqual(DISCOVERY.headless_campaign_stage_config(campaign, "start_debug"), ("model-b", "medium"))

    def test_legacy_single_selection_expands_to_all_stages(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), self.mocked_catalog():
            resolved = DISCOVERY.resolve_headless_stage_configs(
                self.workspace,
                model="model-a",
                reasoning_effort="high",
            )
        self.assertEqual(set(resolved), {"auditor", "builder", "debug_eval"})
        self.assertTrue(all(value == {"model": "model-a", "reasoning_effort": "high"} for value in resolved.values()))

    def test_headless_command_contains_explicit_selection(self) -> None:
        problem = self.workspace / "subprojects-team" / "problem-a"
        agent = problem / "agent1"
        (self.workspace / ".DiscoveryProgram").mkdir()
        (problem / ".DiscoveryConsole" / "pub").mkdir(parents=True)
        (problem / ".DiscoveryConsole" / "private").mkdir()
        agent.mkdir()
        command = DISCOVERY.build_headless_codex_command(
            {
                "prompt": "Follow the goal",
                "model": "model-a",
                "model_reasoning_effort": "high",
            },
            problem,
            agent,
        )
        self.assertIn('default_permissions="discovery_route"', command)
        self.assertTrue(any("permissions.discovery_route.filesystem=" in part for part in command))
        self.assertIn('permissions.discovery_route.network.domains={"*"="allow"}', command)
        self.assertIn('web_search="live"', command)
        self.assertIn("allow_login_shell=false", command)
        self.assertIn("never", command)
        self.assertNotIn("--ignore-user-config", command)
        self.assertNotIn("danger-full-access", command)
        self.assertNotIn("workspace-write", command)
        self.assertEqual(command[-5:], ["--model", "model-a", "--config", 'model_reasoning_effort="high"', "Follow the goal"])

    def test_dashboard_distinguishes_next_launch_from_run_history(self) -> None:
        self.assertIn("Next Headless Launch", DISCOVERY.DASHBOARD_JS)
        self.assertIn("Settings are frozen when a Campaign starts.", DISCOVERY.DASHBOARD_JS)
        self.assertIn("Use one setup for all stages", DISCOVERY.DASHBOARD_JS)
        self.assertIn('headlessStageRowHtml("auditor", "Auditor"', DISCOVERY.DASHBOARD_JS)
        self.assertIn('headlessStageRowHtml("builder", "Builder"', DISCOVERY.DASHBOARD_JS)
        self.assertIn('headlessStageRowHtml("debug_eval", "Debug Eval"', DISCOVERY.DASHBOARD_JS)
        self.assertIn("Last run:", DISCOVERY.DASHBOARD_JS)
        self.assertIn('? "Paused" : "Running"', DISCOVERY.DASHBOARD_JS)
        self.assertIn("Start${iterations > 1 ? ` ×${iterations}` : \"\"}", DISCOVERY.DASHBOARD_JS)
        self.assertIn('id="headlessIterationsInput"', DISCOVERY.DASHBOARD_JS)
        self.assertIn("iterations: state.headlessIterations", DISCOVERY.DASHBOARD_JS)
        self.assertIn("stage_configs: state.headlessStageConfigs", DISCOVERY.DASHBOARD_JS)
        self.assertIn("function scheduleCampaignRefresh()", DISCOVERY.DASHBOARD_JS)
        self.assertIn("Boolean(status.headless_campaign)", DISCOVERY.DASHBOARD_JS)
        self.assertIn("Version goal", DISCOVERY.DASHBOARD_JS)
        self.assertIn("campaign-stage-row", DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('role="progressbar"', DISCOVERY.DASHBOARD_JS)

    def test_headless_controls_live_only_inside_control_view(self) -> None:
        self.assertNotIn('id="agentStatusPanel"', DISCOVERY.DASHBOARD_HTML)
        self.assertIn('<h2>Headless Routes</h2>', DISCOVERY.DASHBOARD_JS)
        self.assertIn('id="agentStatusPanel"', DISCOVERY.DASHBOARD_JS)
        self.assertIn("renderAgentStatusPanel();", DISCOVERY.DASHBOARD_JS)

    def test_dashboard_uses_contract_driven_breakthrough_and_guardrail_shortcuts(self) -> None:
        self.assertFalse(hasattr(DISCOVERY, "METRIC_GROUPS"))
        self.assertFalse(hasattr(DISCOVERY, "DASHBOARD_METRIC_SHORTCUTS"))
        self.assertIn('data-metric-action="breakthrough"', DISCOVERY.DASHBOARD_JS)
        self.assertIn('data-metric-action="guardrail"', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('data-metric-action="floor"', DISCOVERY.DASHBOARD_JS)
        self.assertIn("new Set(metricShortcut(action))", DISCOVERY.DASHBOARD_JS)

    def test_campaign_iteration_count_is_bounded(self) -> None:
        self.assertEqual(DISCOVERY.validate_campaign_iterations(3), 3)
        for invalid in (0, 21, True, "not-a-number"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(SystemExit, "1 to 20"):
                DISCOVERY.validate_campaign_iterations(invalid)

    def test_best_per_metric_uses_only_reviewed_valid_baseline_values(self) -> None:
        rows = [
            {
                "id": "baseline:trusted",
                "method": "trusted",
                "row_type": "baseline",
                "metrics": {"score": 0.8},
                "metric_validity": {"score": {"status": "valid", "reason": "reviewed"}},
            },
            {
                "id": "baseline:suspicious",
                "method": "suspicious",
                "row_type": "baseline",
                "metrics": {"score": 99.0},
                "metric_validity": {"score": {"status": "pending_review", "reason": "unexpected"}},
            },
        ]
        best = DISCOVERY.build_baseline_best_metric_row(self.workspace, rows, ["score"], {"score": "higher"})
        self.assertEqual(best["metrics"], {"score": 0.8})
        self.assertEqual(best["metric_sources"]["score"]["method"], "trusted")
        self.assertEqual(best["metric_sources"]["score"]["methods"], ["trusted"])

    def test_best_per_metric_ignores_incompatible_baseline_cohort(self) -> None:
        rows = [
            {
                "id": "baseline:current",
                "method": "current",
                "row_type": "baseline",
                "comparison_eligible": True,
                "metrics": {"score": 0.8},
                "metric_validity": {"score": {"status": "valid", "reason": "reviewed"}},
            },
            {
                "id": "baseline:old-space",
                "method": "old-space",
                "row_type": "baseline",
                "comparison_eligible": False,
                "metrics": {"score": 99.0},
                "metric_validity": {"score": {"status": "valid", "reason": "reviewed historical value"}},
            },
        ]
        best = DISCOVERY.build_baseline_best_metric_row(self.workspace, rows, ["score"], {"score": "higher"})
        self.assertEqual(best["metrics"], {"score": 0.8})
        self.assertEqual(best["metric_sources"]["score"]["method"], "current")

    def test_best_per_metric_preserves_all_tied_sources(self) -> None:
        rows = [
            {
                "id": f"baseline:{method}",
                "method": method,
                "row_type": "baseline",
                "metrics": {"schema": 1.0},
                "metric_validity": {"schema": {"status": "valid", "reason": "reviewed"}},
            }
            for method in ("b", "a", "c")
        ]
        best = DISCOVERY.build_baseline_best_metric_row(self.workspace, rows, ["schema"], {"schema": "higher"})
        self.assertEqual(best["metric_sources"]["schema"]["methods"], ["a", "b", "c"])
        self.assertIn("a, b, c", best["metric_validity"]["schema"]["reason"])

    def test_campaign_counts_each_new_reflected_version_once(self) -> None:
        campaign = {"current_version": "version-agent1-0001", "completed_versions": [], "completed_iterations": 0}
        self.assertFalse(DISCOVERY.observe_campaign_reflection(campaign, "version-agent1-0001"))
        self.assertTrue(DISCOVERY.observe_campaign_reflection(campaign, "version-agent1-0002"))
        self.assertFalse(DISCOVERY.observe_campaign_reflection(campaign, "version-agent1-0002"))
        self.assertTrue(DISCOVERY.observe_campaign_reflection(campaign, "version-agent1-0003"))
        self.assertEqual(campaign["completed_iterations"], 2)
        self.assertEqual(campaign["completed_versions"], ["version-agent1-0002", "version-agent1-0003"])

    def test_campaign_progress_reports_version_goal_and_current_stage(self) -> None:
        campaign = {"status": "running", "target_iterations": 2, "completed_iterations": 0, "current_stage": "start_builder"}
        progress = DISCOVERY.headless_campaign_progress(campaign)
        self.assertEqual(progress["completed_versions"], 0)
        self.assertEqual(progress["target_iterations"], 2)
        self.assertEqual(progress["stage_label"], "Builder")
        self.assertEqual([stage["state"] for stage in progress["stages"]], ["done", "active", "pending"])
        campaign.update({"completed_iterations": 1, "current_stage": "start_debug"})
        progress = DISCOVERY.headless_campaign_progress(campaign)
        self.assertEqual(progress["completed_versions"], 1)
        self.assertEqual(progress["stage_label"], "Debug Eval")
        self.assertEqual([stage["label"] for stage in progress["stages"]], ["Auditor", "Builder", "Debug Eval", "Evaluation"])

    def test_completed_campaign_progress_marks_all_current_stages_done(self) -> None:
        progress = DISCOVERY.headless_campaign_progress(
            {"status": "done", "target_iterations": 3, "completed_iterations": 3, "current_stage": "complete"}
        )
        self.assertEqual(progress["completed_versions"], 3)
        self.assertTrue(all(stage["state"] == "done" for stage in progress["stages"]))

    def test_campaign_can_pause_resume_and_stop_without_active_codex(self) -> None:
        campaign = {
            "id": "campaign-a",
            "agent": "agent1",
            "status": "running",
            "supervisor_pid": os.getpid(),
            "created_at": "now",
        }
        DISCOVERY.upsert_headless_campaign(self.workspace, campaign)
        paused = DISCOVERY.control_dashboard_headless_goal(self.workspace, "agent1", "pause")
        self.assertEqual(paused["status"], "paused")
        resumed = DISCOVERY.control_dashboard_headless_goal(self.workspace, "agent1", "resume")
        self.assertEqual(resumed["status"], "running")
        stopped = DISCOVERY.control_dashboard_headless_goal(self.workspace, "agent1", "stop")
        self.assertEqual(stopped["status"], "stopped")

    def test_reconcile_restarts_a_lost_campaign_supervisor(self) -> None:
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {
                "id": "campaign-a",
                "agent": "agent1",
                "status": "running",
                "supervisor_pid": 99999,
                "created_at": "now",
            },
        )
        fake_process = mock.Mock(pid=43210)
        with mock.patch.object(DISCOVERY, "process_alive", return_value=False), mock.patch.object(
            DISCOVERY.subprocess, "Popen", return_value=fake_process
        ) as popen:
            rows = DISCOVERY.reconcile_headless_campaigns(self.workspace)
        self.assertEqual(rows[0]["status"], "running")
        self.assertEqual(rows[0]["supervisor_pid"], 43210)
        self.assertIn("_headless_campaign", popen.call_args.args[0])

    def test_headless_authentication_failure_is_classified(self) -> None:
        log_path = self.workspace / "auth-failure.jsonl"
        log_path.write_text('{"type":"error","message":"401 Unauthorized: Incorrect API key provided"}\n', encoding="utf-8")
        self.assertEqual(DISCOVERY.resource_failure_reason(1, log_path), "authentication_failed")

    def test_headless_usage_uses_only_reported_event_fields(self) -> None:
        self.assertEqual(
            DISCOVERY.headless_usage_from_event(
                {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 12, "reasoning_tokens": 4}}
            ),
            {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 12, "reasoning_tokens": 4},
        )
        self.assertEqual(DISCOVERY.headless_usage_from_event({"item": {"usage": {"input_tokens": 3}}}), {"input_tokens": 3})
        self.assertIsNone(DISCOVERY.headless_usage_from_event({"type": "item.completed"}))
        self.assertIn("headlessUsageLabel", DISCOVERY.DASHBOARD_JS)
        self.assertIn("cached_input_tokens", DISCOVERY.DASHBOARD_JS)


class DashboardWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "workspace"
        (self.workspace / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_start_records_managed_worker_and_stop_requests_drain(self) -> None:
        fake_process = mock.Mock(pid=43210)
        with mock.patch.object(DISCOVERY.subprocess, "Popen", return_value=fake_process):
            payload = DISCOVERY.launch_dashboard_worker(self.workspace)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["worker"]["pid"], 43210)
        self.assertIn("-u", payload["worker"]["command"])

        with mock.patch.object(DISCOVERY, "process_alive", return_value=True):
            stopped = DISCOVERY.control_dashboard_worker(self.workspace, "stop")
        self.assertEqual(stopped["worker"]["status"], "draining")
        self.assertTrue(DISCOVERY.managed_worker_stop_requested(self.workspace, 43210))

    def test_missing_managed_worker_process_is_reported_failed(self) -> None:
        DISCOVERY.write_json(
            DISCOVERY.dashboard_worker_state_path(self.workspace),
            {"status": "running", "pid": 98765, "started_at": "now", "stop_requested_at": None},
        )
        with mock.patch.object(DISCOVERY, "process_alive", return_value=False):
            state = DISCOVERY.read_dashboard_worker_state(self.workspace)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["reason"], "worker_exited")


class DashboardActivityStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "problem-a"
        self.agent = self.workspace / "agent1"
        (self.workspace / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
        (self.agent / ".discovery").mkdir(parents=True)
        DISCOVERY.write_json(self.workspace / "problem.json", {"problem_id": "problem-a"})
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), {"configured": True})
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {"phase": "work_loop", "last_version": None, "last_reflected_version": None, "eval_status": None, "active_eval": None, "last_error": None},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def status(self) -> dict:
        with mock.patch.object(DISCOVERY, "route_sandbox_report", return_value={"available": True}), mock.patch.object(
            DISCOVERY, "route_cli_protocol_ready", return_value=True
        ), mock.patch.object(
            DISCOVERY, "route_prompt_bundle_issues", return_value=[]
        ):
            return DISCOVERY.build_dashboard_agent_statuses(self.workspace, ["agent1"])[0]

    def test_real_job_state_maps_to_waiting_background_and_attached_labels(self) -> None:
        DISCOVERY.upsert_job(
            self.workspace,
            {"id": "job-queued", "launcher": "submit", "agent": "agent1", "status": "queued", "log": ".DiscoveryConsole/pub/log/job-queued.log"},
        )
        self.assertEqual(self.status()["status_label"], "Waiting for resources")
        DISCOVERY.update_job(self.workspace, "job-queued", {"status": "running", "pid": os.getpid(), "completion_mode": "detach"})
        self.assertEqual(self.status()["status_label"], "Background compute")

        DISCOVERY.upsert_headless_run(
            self.workspace,
            {"id": "headless-a", "agent": "agent1", "status": "running", "supervisor_pid": os.getpid(), "created_at": "now"},
        )
        DISCOVERY.update_job(self.workspace, "job-queued", {"headless_run_id": "headless-a", "completion_mode": "wait"})
        self.assertEqual(self.status()["status_label"], "Running code")

    def test_formal_eval_and_historical_blocked_campaign_have_distinct_human_states(self) -> None:
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {"phase": "work_loop", "eval_status": "running", "active_eval": {"job": "eval-a"}, "last_error": None},
        )
        self.assertEqual(self.status()["status_label"], "Formal evaluation")
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {"phase": "work_loop", "eval_status": None, "active_eval": None, "last_error": None},
        )
        DISCOVERY.upsert_headless_campaign(
            self.workspace,
            {"id": "campaign-a", "agent": "agent1", "status": "blocked", "reason": "headless_stage_made_no_state_progress", "created_at": "now"},
        )
        state = self.status()
        self.assertEqual(state["status_label"], "Builder ready")
        self.assertTrue(state["should_start_codex"])
        self.assertIn("Previous Campaign stopped", state["status_detail"])
        self.assertIn("headless_stage_made_no_state_progress", state["status_detail"])

    def test_private_formal_eval_failure_waits_for_main_instead_of_debug(self) -> None:
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {"phase": "work_loop", "eval_status": "main_review", "active_eval": {"job": "eval-a"}, "last_error": {"stage": "formal_eval"}},
        )
        state = self.status()
        self.assertEqual(state["runner_action"], "wait_main")
        self.assertEqual(state["status_label"], "Main review required")
        self.assertFalse(state["should_start_codex"])

    def test_public_check_failure_preserves_debug_job_locator(self) -> None:
        DISCOVERY.record_eval_failure(
            self.agent,
            DISCOVERY.EvalCommandFailed(2, ".DiscoveryConsole/pub/log/eval-check-123.log"),
            stage="check",
        )
        state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        self.assertEqual(state["eval_status"], "check_failed")
        self.assertEqual(state["active_eval"]["job"], "eval-check-123")

    def test_version_slider_does_not_render_variable_length_summary(self) -> None:
        self.assertIn('data-agent-version=', DISCOVERY.DASHBOARD_JS)
        self.assertIn('refreshPolygonPanel();', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('version-slider-summary', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('.version-slider-summary', DISCOVERY.DASHBOARD_CSS)

    def test_dashboard_uses_current_loop_progress_and_preserves_scroll(self) -> None:
        self.assertIn('const campaign = status.headless_campaign;', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('status.headless_campaign || status.last_headless_campaign', DISCOVERY.DASHBOARD_JS)
        self.assertIn('function currentRouteProgress(status)', DISCOVERY.DASHBOARD_JS)
        self.assertIn('{label: "Evaluation", state: "done"}', DISCOVERY.DASHBOARD_JS)
        for key in ('polygon-metrics', 'polygon-objects', 'runtime-job-queue', 'runtime-log:'):
            self.assertIn(f'data-scroll-key="{key}', DISCOVERY.DASHBOARD_JS)
        self.assertIn('captureScrollPositions();', DISCOVERY.DASHBOARD_JS)
        self.assertIn('restoreScrollPositions();', DISCOVERY.DASHBOARD_JS)
        self.assertIn('position.atBottom ? element.scrollHeight', DISCOVERY.DASHBOARD_JS)

    def test_dashboard_does_not_replace_an_active_metric_select_during_refresh(self) -> None:
        self.assertIn('function mainSelectIsActive()', DISCOVERY.DASHBOARD_JS)
        self.assertIn('keepSelection && mainSelectIsActive()', DISCOVERY.DASHBOARD_JS)
        self.assertIn('deferredMainRender = true;', DISCOVERY.DASHBOARD_JS)
        self.assertIn('mainPanel.addEventListener("focusout"', DISCOVERY.DASHBOARD_JS)

    def test_dashboard_header_shows_problem_and_server_time_only(self) -> None:
        self.assertNotIn('>Workspace<', DISCOVERY.DASHBOARD_HTML)
        self.assertNotIn('>Latest Eval<', DISCOVERY.DASHBOARD_HTML)
        self.assertNotIn('id="workspace"', DISCOVERY.DASHBOARD_HTML)
        self.assertNotIn('id="latestEval"', DISCOVERY.DASHBOARD_HTML)
        self.assertIn('>Problem<', DISCOVERY.DASHBOARD_HTML)
        self.assertIn('>Server Time (UTC)<', DISCOVERY.DASHBOARD_HTML)
        self.assertIn('id="serverTime"', DISCOVERY.DASHBOARD_HTML)


class GpuSnapshotTests(unittest.TestCase):
    def test_nvidia_smi_retries_a_timed_out_query_with_a_longer_timeout(self) -> None:
        completed = subprocess.CompletedProcess(["nvidia-smi"], 0, stdout="0, GPU\n", stderr="")
        with mock.patch.object(
            DISCOVERY.subprocess,
            "run",
            side_effect=[subprocess.TimeoutExpired(["nvidia-smi"], 2.0), completed],
        ) as run, mock.patch.object(DISCOVERY.time, "sleep") as sleep:
            output = DISCOVERY.run_nvidia_smi(["--query-gpu=index", "--format=csv,noheader"])

        self.assertEqual(output, "0, GPU\n")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.kwargs["timeout"], DISCOVERY.NVIDIA_SMI_TIMEOUT_SECONDS)
        sleep.assert_called_once_with(DISCOVERY.NVIDIA_SMI_RETRY_DELAY_SECONDS)

    def test_gpu_inventory_includes_process_owner_and_runtime_fields(self) -> None:
        gpu_csv = "1, NVIDIA L40S, GPU-test, 1024, 45000, 46024, 25, 3, 44, 96.5, 350.0\n"
        app_csv = "GPU-test, 1234, /opt/python, 768\n"
        with mock.patch.object(DISCOVERY, "run_nvidia_smi", side_effect=[gpu_csv, app_csv]), mock.patch.object(
            DISCOVERY, "process_owner", return_value="researcher"
        ):
            snapshot = DISCOVERY.nvidia_gpu_snapshot()
        self.assertEqual(snapshot["gpus"][1]["name"], "NVIDIA L40S")
        self.assertEqual(snapshot["gpus"][1]["utilization_percent"], 25)
        self.assertEqual(snapshot["gpus"][1]["power_draw_w"], 96.5)
        self.assertEqual(snapshot["compute_apps"][0]["gpu_index"], 1)
        self.assertEqual(snapshot["compute_apps"][0]["user"], "researcher")


if __name__ == "__main__":
    unittest.main()
