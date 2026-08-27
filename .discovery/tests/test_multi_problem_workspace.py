import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import sys
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
        (self.topic / ".agents").mkdir()
        shutil.copytree(
            MODULE_PATH.parents[2] / "subprojects-team" / ".team-template",
            self.topic / "subprojects-team" / ".team-template",
        )
        DISCOVERY.write_json(
            self.topic / ".DiscoveryProgram" / "problem_registry.json",
            {"schema_version": 1, "default_problem": None, "problems": []},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_problem(self, problem_id: str, title: str = "Test Problem") -> Path:
        args = argparse.Namespace(problem_cmd="create", problem_id=problem_id, title=title, status="scoping")
        with mock.patch.object(DISCOVERY, "codex_install_root", return_value=Path(sys.prefix)), contextlib.redirect_stdout(io.StringIO()):
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
        self.assertEqual([path.name for path in sorted(workspace.glob("agent*"))], ["agent1", "agent2", "agent3"])
        for route_name in DISCOVERY.INITIAL_ROUTE_NAMES:
            route = workspace / route_name
            self.assertTrue((route / ".git").is_dir())
            self.assertEqual((route / "pub").resolve(), (workspace / ".DiscoveryConsole" / "pub").resolve())

    def test_topic_validation_uses_team_template_outside_discovery_runtime(self) -> None:
        required = {path.relative_to(self.topic).as_posix() for path in DISCOVERY.topic_required_paths(self.topic)}
        self.assertIn("subprojects-team/.team-template/problem/problem.json", required)
        self.assertIn("subprojects-team/.team-template/route/AGENTS.md", required)
        self.assertIn("subprojects-team/.team-template/reviewer/AGENTS.md", required)
        self.assertFalse(
            any(
                path.startswith((".discovery/problem-template", ".discovery/agents-template", ".discovery/reviewer-template"))
                for path in required
            )
        )

    def test_registry_rejects_noncanonical_problem_path(self) -> None:
        self.create_problem("problem-a")
        registry = DISCOVERY.read_problem_registry(self.topic)
        registry["problems"][0]["path"] = "problems/problem-a"
        DISCOVERY.write_json(self.topic / ".DiscoveryProgram" / "problem_registry.json", registry)
        with self.assertRaisesRegex(SystemExit, "must use canonical workspace subprojects-team/problem-a"):
            DISCOVERY.problem_workspace(self.topic, "problem-a")

    def test_unregistered_subprojects_team_directory_is_not_a_problem(self) -> None:
        (self.topic / "subprojects-team" / "problem-a" / ".DiscoveryConsole").mkdir(parents=True)
        with self.assertRaisesRegex(SystemExit, "unknown Problem"):
            DISCOVERY.problem_workspace(self.topic, "problem-a")

    def test_runtime_rejects_old_problems_directory_even_for_registered_id(self) -> None:
        self.create_problem("problem-a")
        old = self.topic / "problems" / "problem-a"
        old.mkdir(parents=True)
        DISCOVERY.write_json(old / "problem.json", {"problem_id": "problem-a"})
        with self.assertRaisesRegex(SystemExit, "must run from canonical workspace subprojects-team/problem-a"):
            DISCOVERY.resolve_problem_workspace(self.topic, old, "")

    def test_agent_creation_is_blocked_before_evaluator_is_configured(self) -> None:
        workspace = self.create_problem("problem-a")
        args = argparse.Namespace(agent_cmd="create", name="agent4", force=False)
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
