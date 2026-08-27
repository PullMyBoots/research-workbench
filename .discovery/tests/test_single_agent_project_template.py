from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOPIC_TEMPLATE = Path(__file__).resolve().parents[2]
CREATOR = TOPIC_TEMPLATE / ".agents" / "skills" / "create-single-agent-project" / "scripts" / "create_single_agent_project.py"
DISCOVERY_PATH = TOPIC_TEMPLATE / ".discovery" / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("single_agent_template_discovery", DISCOVERY_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class SingleAgentProjectTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.topic = Path(self.temporary.name) / "topic"
        self.topic.mkdir()
        shutil.copytree(TOPIC_TEMPLATE / ".DiscoveryProgram", self.topic / ".DiscoveryProgram")
        shutil.copytree(TOPIC_TEMPLATE / "subprojects-single", self.topic / "subprojects-single")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, project_id: str, goal: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CREATOR), "--topic-root", str(self.topic), "--id", project_id, "--goal", goal],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_initializes_only_goal_and_blank_memory_surfaces(self) -> None:
        result = self.create("protein-folding", "解释目标蛋白的折叠失败机制")
        self.assertEqual(result.returncode, 0, result.stderr)
        project = self.topic / "subprojects-single" / "protein-folding"
        self.assertEqual({path.name for path in project.iterdir()}, {"AGENTS.md", ".ResearchProject", ".git"})
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=project, text=True, capture_output=True, check=True
        ).stdout.strip()
        self.assertEqual(Path(root), project)
        memory = (project / ".ResearchProject" / "memory" / "main.md").read_text(encoding="utf-8")
        self.assertIn("目标：解释目标蛋白的折叠失败机制", memory)
        self.assertIn("尚未形成需要长期保留的高层判断。", memory)
        self.assertIn("课题尚未开始实质工作。", memory)
        self.assertEqual(json.loads((project / ".ResearchProject" / "knowledge" / "items.json").read_text()), {})
        self.assertEqual(json.loads((project / ".ResearchProject" / "knowledge" / "topics.json").read_text()), {})
        logs = project / ".ResearchProject" / "memory" / "logs"
        self.assertEqual([path.name for path in logs.iterdir() if path.name != ".gitkeep"], [])

    def test_goal_is_plain_text_and_cannot_create_memory_sections(self) -> None:
        result = self.create("safe-goal", "研究目标\n\n## 元认知\n预置判断")
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = (self.topic / "subprojects-single" / "safe-goal" / ".ResearchProject" / "memory" / "main.md").read_text(encoding="utf-8")
        self.assertIn("目标：研究目标 ## 元认知 预置判断", memory)
        self.assertEqual(memory.count("## 元认知"), 2)
        self.assertEqual([line for line in memory.splitlines() if line.startswith("## ")], ["## 目标与背景", "## 元认知", "## 当前进展与文件索引"])

    def test_topic_validation_requires_the_single_agent_template(self) -> None:
        required = DISCOVERY.topic_required_paths(TOPIC_TEMPLATE)
        self.assertIn(TOPIC_TEMPLATE / "subprojects-single" / ".single-agent-template" / "AGENTS.md", required)
        self.assertIn(TOPIC_TEMPLATE / "subprojects-single" / ".single-agent-template" / ".ResearchProject" / "memory" / "main.md", required)

    def test_refuses_invalid_id_and_existing_project(self) -> None:
        invalid = self.create("Invalid Name", "goal")
        self.assertNotEqual(invalid.returncode, 0)
        created = self.create("project-a", "first goal")
        self.assertEqual(created.returncode, 0, created.stderr)
        duplicate = self.create("project-a", "replacement goal")
        self.assertNotEqual(duplicate.returncode, 0)
        memory = self.topic / "subprojects-single" / "project-a" / ".ResearchProject" / "memory" / "main.md"
        self.assertIn("first goal", memory.read_text(encoding="utf-8"))
        self.assertNotIn("replacement goal", memory.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
