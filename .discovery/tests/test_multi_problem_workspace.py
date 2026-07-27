import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli_multi_problem_tests", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class MultiProblemWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.topic = Path(self.temp_dir.name) / "topic"
        (self.topic / ".DiscoveryProgram" / "log").mkdir(parents=True)
        (self.topic / ".discovery" / "problem-template" / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
        (self.topic / ".agents").mkdir()
        (self.topic / "subprojects-team").mkdir()
        (self.topic / ".discovery" / "problem-template" / "problem.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "problem_id": "{problem_id}",
                    "title": "{problem_title}",
                    "status": "{problem_status}",
                }
            ),
            encoding="utf-8",
        )
        DISCOVERY.write_json(
            self.topic / ".discovery" / "problem-template" / ".DiscoveryConsole" / "resources.json",
            {
                "schema_version": 1,
                "free_run": {"default": {"cpus": 1, "memory_gb": 1, "gpus": []}, "agents": {}},
                "queue": {"capacity": {"cpus": 2, "memory_gb": 2, "gpus": []}},
                "evaluation": {"resources": {"cpus": 1, "memory_gb": 1, "gpus": []}, "timeout_seconds": None},
                "scheduler": {"memory_reserve_gb": 0, "respect_system_load": False, "respect_external_gpu_processes": True},
            },
        )
        DISCOVERY.write_json(
            self.topic / ".DiscoveryProgram" / "problem_registry.json",
            {"schema_version": 1, "default_problem": None, "problems": []},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_problem(self, problem_id: str, title: str = "Test Problem") -> Path:
        args = argparse.Namespace(problem_cmd="create", problem_id=problem_id, title=title, status="scoping")
        with contextlib.redirect_stdout(io.StringIO()):
            DISCOVERY.cmd_problem(self.topic, args)
        return self.topic / "subprojects-team" / problem_id

    def test_stale_topic_console_is_not_a_problem(self) -> None:
        (self.topic / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
        self.assertIsNone(DISCOVERY.find_workspace(self.topic, required=False))

        workspace = self.create_problem("problem-a")
        self.assertEqual(DISCOVERY.find_workspace(workspace), workspace.resolve())

    def test_problem_create_registers_and_namespaces_workspace(self) -> None:
        workspace = self.create_problem("problem-a", "Problem A")
        metadata = DISCOVERY.read_json(workspace / "problem.json", {})
        registry = DISCOVERY.read_problem_registry(self.topic)

        self.assertEqual(metadata["problem_id"], "problem-a")
        self.assertEqual(metadata["title"], "Problem A")
        self.assertNotIn("readiness", metadata)
        self.assertEqual(registry["default_problem"], "problem-a")
        self.assertEqual(registry["problems"][0]["path"], "subprojects-team/problem-a")
        self.assertNotIn("human_approved", registry["problems"][0])
        self.assertEqual((workspace / ".discovery").resolve(), (self.topic / ".discovery").resolve())
        self.assertEqual((workspace / ".agents").resolve(), (self.topic / ".agents").resolve())

    def test_agent_creation_is_blocked_before_evaluator_is_configured(self) -> None:
        workspace = self.create_problem("problem-a")
        args = argparse.Namespace(agent_cmd="create", name="agent1", force=False)
        with self.assertRaisesRegex(SystemExit, "evaluation contract"):
            DISCOVERY.cmd_agent(workspace, args)

    def test_resource_state_locks_and_job_indexes_are_problem_local(self) -> None:
        first = self.create_problem("problem-a")
        second = self.create_problem("problem-b")

        self.assertNotEqual(DISCOVERY.resource_lock_path(first), DISCOVERY.resource_lock_path(second))
        self.assertNotEqual(DISCOVERY.resource_state_path(first), DISCOVERY.resource_state_path(second))
        self.assertNotEqual(DISCOVERY.job_index(first), DISCOVERY.job_index(second))
        self.assertIn("problem-a", str(DISCOVERY.job_index(first)))
        self.assertIn("problem-b", str(DISCOVERY.job_index(second)))

    def test_problem_runtime_refuses_cross_problem_activity(self) -> None:
        first = self.create_problem("problem-a")
        second = self.create_problem("problem-b")

        def activity(workspace: Path) -> dict:
            if workspace == first:
                return {"worker": True, "worker_status": "running", "jobs": ["job-a"], "headless": []}
            return {"worker": False, "worker_status": "stopped", "jobs": [], "headless": []}

        with mock.patch.object(DISCOVERY, "problem_runtime_activity", side_effect=activity):
            with self.assertRaisesRegex(SystemExit, "another Problem still has active runtime state"):
                DISCOVERY.assert_problem_runtime_exclusive(second)

    def test_dashboard_exposes_problem_selector_and_scoped_requests(self) -> None:
        self.assertIn('id="problemSelect"', DISCOVERY.DASHBOARD_HTML)
        self.assertIn("problem: state.problemId", DISCOVERY.DASHBOARD_JS)
        self.assertIn("problem=${encodeURIComponent(state.problemId)}", DISCOVERY.DASHBOARD_JS)

    def test_dashboard_does_not_expose_problem_or_team_mutations(self) -> None:
        self.assertNotIn("Problem and Team", DISCOVERY.DASHBOARD_JS)
        self.assertNotIn("Approve current Problem", DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('action: "problem.create"', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('action: "problem.activate-eval"', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('action: "route.create"', DISCOVERY.DASHBOARD_JS)


if __name__ == "__main__":
    unittest.main()
