import argparse
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli_notice_tests", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class NoticeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "workspace"
        self.agent_dir = self.workspace / "agent1"
        self.agent_dir.mkdir(parents=True)
        self.notice_path = DISCOVERY.main_agent_notices_path(self.workspace)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_args(self, notice_id: str) -> argparse.Namespace:
        return argparse.Namespace(
            notice_cmd="add",
            id=notice_id,
            priority="high",
            title="Test notice",
            body="Act on this notice.",
            tag=["test"],
        )

    def delete_args(self, notice_id: str) -> argparse.Namespace:
        return argparse.Namespace(notice_cmd="delete", id=notice_id)

    def run_notice(self, cwd: Path, args: argparse.Namespace) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            DISCOVERY.cmd_notice(self.workspace, cwd, args)
        return output.getvalue()

    def test_main_workspace_can_add_and_delete_notice(self) -> None:
        self.run_notice(self.workspace, self.add_args("notice-test"))
        self.assertEqual([row["id"] for row in DISCOVERY.read_jsonl(self.notice_path)], ["notice-test"])

        output = self.run_notice(self.workspace, self.delete_args("notice-test"))
        self.assertIn('"deleted": "notice-test"', output)
        self.assertEqual(DISCOVERY.read_jsonl(self.notice_path), [])

    def test_search_agent_cannot_add_or_delete_notice(self) -> None:
        with self.assertRaisesRegex(SystemExit, "must be run by the main workspace"):
            self.run_notice(self.agent_dir, self.add_args("notice-agent-add"))

        DISCOVERY.write_jsonl(self.notice_path, [{"id": "notice-existing"}])
        with self.assertRaisesRegex(SystemExit, "must be run by the main workspace"):
            self.run_notice(self.agent_dir, self.delete_args("notice-existing"))
        self.assertEqual(DISCOVERY.read_jsonl(self.notice_path), [{"id": "notice-existing"}])

    def test_delete_missing_notice_fails_without_changing_file(self) -> None:
        notices = [{"id": "notice-existing", "title": "Keep me"}]
        DISCOVERY.write_jsonl(self.notice_path, notices)
        with self.assertRaisesRegex(SystemExit, "notice id not found"):
            self.run_notice(self.workspace, self.delete_args("notice-missing"))
        self.assertEqual(DISCOVERY.read_jsonl(self.notice_path), notices)


class GoalStartupContractTests(unittest.TestCase):
    def test_every_manual_and_headless_goal_reads_public_brief_and_loads_route_context(self) -> None:
        template = Path(__file__).parents[1] / "agents-template"
        for goal_name in ("route_builder.md", "route_auditor.md", "route_debug_eval.md"):
            with self.subTest(goal_dir="goals", goal_name=goal_name):
                text = (template / "goals" / goal_name).read_text(encoding="utf-8")
                controlling_files = text.split("## 背景与任务描述", 1)[0]
                self.assertIn("pub/README.md", controlling_files)
                startup = text.split("### Required Startup", 1)[1].split("###", 1)[0]
                self.assertIn("./explore context", startup)
            with self.subTest(goal_dir="headless_goals", goal_name=goal_name):
                wrapper = (template / "headless_goals" / goal_name).read_text(encoding="utf-8")
                self.assertIn(f"goals/{goal_name}", wrapper)
                self.assertIn("./explore context", wrapper)


if __name__ == "__main__":
    unittest.main()
