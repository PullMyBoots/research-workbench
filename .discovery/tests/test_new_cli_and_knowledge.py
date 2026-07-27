import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli_new_surface_tests", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class KnowledgeLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.topic = Path(self.temp_dir.name) / "topic"
        self.workspace = self.topic / "subprojects-team" / "problem-a"
        self.agent = self.workspace / "agent1"
        self.root = self.workspace / ".DiscoveryConsole" / "pub" / "knowledge"
        self.topic_root = self.topic / ".DiscoveryProgram" / "knowledge"
        self.practice_root = self.root
        self.agent.mkdir(parents=True)
        (self.topic / ".DiscoveryProgram").mkdir()
        self.topic_root.mkdir()
        (self.root / "items").mkdir(parents=True)
        (self.root / "versions").mkdir()
        DISCOVERY.write_json(
            self.topic / ".DiscoveryProgram" / "problem_registry.json",
            {
                "schema_version": 1,
                "topic_id": "topic-a",
                "default_problem": "problem-a",
                "problems": [{"id": "problem-a", "path": "subprojects-team/problem-a"}],
            },
        )
        DISCOVERY.write_json(self.workspace / "problem.json", {"problem_id": "problem-a"})
        DISCOVERY.write_json(self.root / "items.json", {})
        DISCOVERY.write_json(self.root / "topics.json", {})
        DISCOVERY.main_memory_path(self.topic_root).parent.mkdir(parents=True)
        DISCOVERY.main_memory_path(self.topic_root).write_text("# Main Agent Memory\n\n## 当前项目\n\nfixture\n\n## 项目进度\n\nfixture\n\n## 用户偏好\n\nfixture\n", encoding="utf-8")
        self.source = self.agent / "paper.txt"
        self.source.write_text("stable research source", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_item(self, item_id: str = "paper-a", source: Path | None = None) -> dict:
        metadata_path = self.workspace / f"{item_id}-metadata.json"
        DISCOVERY.write_json(
            metadata_path,
            {
                "title": "Accepted paper",
                "summary": "Evidence-bearing summary.",
            },
        )
        args = argparse.Namespace(
            item_id=item_id,
            source=str(source or self.source),
            metadata=str(metadata_path),
        )
        return DISCOVERY.knowledge_item_add(self.workspace, self.root, args)

    def add_memory(self, memory_id: str, summary: str, report: str) -> dict:
        patch = self.topic / f"{memory_id}.json"
        DISCOVERY.write_json(patch, {"summary": summary, "report": report})
        return DISCOVERY.memory_log_add(self.topic_root, memory_id, str(patch))

    def test_main_owned_item_topic_lifecycle_is_self_contained(self) -> None:
        item = self.add_item()
        self.assertTrue(self.source.exists())
        item_dir = self.root / "items" / "paper-a"
        self.assertEqual((item_dir / "paper.txt").read_text(encoding="utf-8"), "stable research source")
        self.assertEqual(DISCOVERY.read_json(self.root / "items.json", {})["paper-a"]["id"], "paper-a")
        self.assertEqual(DISCOVERY.read_json(self.root / "topics.json", {}), {})
        self.assertEqual(item, DISCOVERY.read_json(self.root / "items.json", {})["paper-a"])
        DISCOVERY.write_json(
            self.root / "topics.json",
            {"methods": {"id": "methods", "title": "Methods", "text": "Synthesis with @item:paper-a.", "items": ["paper-a"]}},
        )
        report = DISCOVERY.knowledge_integrity_report(self.workspace, root=self.root)
        self.assertTrue(report["ok"], report)
        self.assertEqual(DISCOVERY.knowledge_search(self.workspace, "paper-a", root=self.root)[0]["id"], "paper-a")
        DISCOVERY.write_json(self.root / "topics.json", {})
        DISCOVERY.knowledge_item_delete(self.root, "paper-a")
        self.assertFalse(item_dir.exists())

    def test_directory_bundle_and_private_source_boundary(self) -> None:
        source_dir = self.agent / "handoff-bundle"
        (source_dir / "workspace").mkdir(parents=True)
        (source_dir / "README.md").write_text("original question", encoding="utf-8")
        (source_dir / "workspace" / "result.md").write_text("external result", encoding="utf-8")
        self.add_item("handoff-a", source_dir)
        self.assertTrue((self.root / "items" / "handoff-a" / "workspace" / "result.md").exists())

        private_source = self.workspace / ".DiscoveryConsole" / "private" / "secret.txt"
        private_source.parent.mkdir(parents=True)
        private_source.write_text("hidden", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "private evidence space"):
            self.add_item("private-a", private_source)

    def test_topic_external_item_directly_serves_research_topic(self) -> None:
        topic_root = self.topic_root
        (topic_root / "items").mkdir(parents=True)
        DISCOVERY.write_json(topic_root / "items.json", {})
        DISCOVERY.write_json(topic_root / "topics.json", {})
        metadata_path = self.topic / "topic-item.json"
        DISCOVERY.write_json(
            metadata_path,
            {
                "title": "Topic framing source",
                "summary": "External evidence for the overall research Topic.",
            },
        )
        item = DISCOVERY.knowledge_item_add(
            self.topic,
            topic_root,
            argparse.Namespace(item_id="topic-source", source=str(self.source), metadata=str(metadata_path)),
        )
        self.assertEqual(item["id"], "topic-source")
        self.assertTrue((topic_root / "items" / "topic-source" / "paper.txt").exists())
        DISCOVERY.write_practice(
            self.workspace,
            {"id": "version-agent1-0001", "problem_id": "problem-a", "agent": "agent1", "summary": "formal practice", "note": "", "metrics": {}},
        )
        topic_practice = topic_root
        memory_patch = self.topic / "topic-memory.json"
        DISCOVERY.write_json(
            memory_patch,
            {
                "summary": "Topic conclusion from external and practice evidence.",
                "report": "Combine @item:topic-source with @version:problem-a/version-agent1-0001.",
            },
        )
        DISCOVERY.memory_log_add(topic_practice, "topic-conclusion", str(memory_patch))
        report = DISCOVERY.memory_versions_integrity_report(
            self.topic,
            root=topic_practice,
            versions_workspace=None,
            knowledge_root_path=topic_root,
        )
        self.assertTrue(report["ok"], report)

    def test_five_wiki_entities_resolve_and_practice_is_structurally_valid(self) -> None:
        self.add_item()
        DISCOVERY.write_json(
            self.root / "topics.json",
            {"methods": {"id": "methods", "title": "Methods", "text": "Synthesis with @item:paper-a.", "items": ["paper-a"]}},
        )
        version = {
            "id": "version-agent1-0001",
            "problem_id": "problem-a",
            "agent": "agent1",
            "summary": "formal practice",
            "note": "Tested the mechanism from @item:paper-a and @topic:methods.",
            "metrics": {},
        }
        DISCOVERY.write_practice(self.workspace, version)
        baseline_dir = self.workspace / ".DiscoveryConsole" / "pub" / "baseline"
        baseline_dir.mkdir()
        DISCOVERY.write_json(
            baseline_dir / "baselines.json",
            {"strong": {"id": "strong", "title": "Strong baseline", "summary": "Competitive comparator.", "status": "valid", "evidence_space": "validation", "metrics": {"score": 1.0}, "metric_validity": {"score": {"status": "valid", "reason": "reviewed test fixture"}}, "locator": {"path": "baseline/"}}},
        )
        memory_patch = self.workspace / "memory-patch.json"
        DISCOVERY.write_json(
            memory_patch,
            {
                "summary": "Integrated practice conclusion for the Topic.",
                "report": "Main synthesis of @version:problem-a/version-agent1-0001.",
            },
        )
        DISCOVERY.memory_log_add(self.topic_root, "integrated-result", str(memory_patch))
        for ref in ("@item:paper-a", "@topic:methods", "@baseline:strong", "@version:version-agent1-0001"):
            self.assertEqual(DISCOVERY.resolve_ref(self.workspace, ref, root=self.root)["id"], ref.split(":", 1)[1])
        with self.assertRaisesRegex(SystemExit, "reference not found"):
            DISCOVERY.resolve_ref(self.workspace, "@memory:integrated-result", root=self.root)
        self.assertFalse((self.root / "notes.json").exists())
        topic_report = DISCOVERY.memory_versions_integrity_report(
            self.topic,
            root=self.topic_root,
            versions_workspace=None,
            knowledge_root_path=self.topic_root,
        )
        self.assertTrue(topic_report["ok"], topic_report)
        report = DISCOVERY.memory_versions_integrity_report(
            self.workspace,
            root=self.practice_root,
            versions_workspace=self.workspace,
            knowledge_root_path=self.root,
        )
        self.assertTrue(report["ok"], report)

    def test_topic_qualified_references_are_exact_and_problem_references_are_local(self) -> None:
        (self.topic_root / "items").mkdir(parents=True)
        DISCOVERY.write_json(self.topic_root / "items.json", {})
        DISCOVERY.write_json(self.topic_root / "topics.json", {})
        topic_metadata = self.topic / "topic-metadata.json"
        DISCOVERY.write_json(topic_metadata, {"title": "Topic item", "summary": "Topic-scoped evidence."})
        DISCOVERY.knowledge_item_add(
            self.topic,
            self.topic_root,
            argparse.Namespace(item_id="shared", source=str(self.source), metadata=str(topic_metadata)),
        )
        DISCOVERY.write_json(
            self.topic_root / "topics.json",
            {"shared-topic": {"id": "shared-topic", "title": "Topic synthesis", "text": "Uses @item:shared.", "items": ["shared"]}},
        )
        memory_file = self.topic / "topic-memory.json"
        DISCOVERY.write_json(
            memory_file,
            {"summary": "Topic conclusion referencing a Problem Version.", "report": "Uses @version:problem-a/version-agent1-0001."},
        )
        DISCOVERY.memory_log_add(self.topic_root, "topic-memory", str(memory_file))

        second = self.topic / "subprojects-team" / "problem-b"
        second_root = second / ".DiscoveryConsole" / "pub" / "knowledge"
        (second_root / "items").mkdir(parents=True)
        (second_root / "versions").mkdir()
        DISCOVERY.write_json(second_root / "items.json", {})
        DISCOVERY.write_json(second_root / "topics.json", {})
        DISCOVERY.write_json(second / "problem.json", {"problem_id": "problem-b"})
        registry = DISCOVERY.read_json(self.topic / ".DiscoveryProgram" / "problem_registry.json", {})
        registry["problems"].append({"id": "problem-b", "path": "subprojects-team/problem-b"})
        DISCOVERY.write_json(self.topic / ".DiscoveryProgram" / "problem_registry.json", registry)

        for workspace, root, title in ((self.workspace, self.root, "Problem A item"), (second, second_root, "Problem B item")):
            metadata = self.topic / f"{workspace.name}-metadata.json"
            DISCOVERY.write_json(metadata, {"title": title, "summary": "Problem-scoped evidence."})
            DISCOVERY.knowledge_item_add(
                workspace,
                root,
                argparse.Namespace(item_id="shared", source=str(self.source), metadata=str(metadata)),
            )
            DISCOVERY.write_practice(
                workspace,
                {
                    "id": "version-agent1-0001",
                    "entity_type": "version",
                    "problem_id": workspace.name,
                    "agent": "agent1",
                    "summary": title,
                    "note": "",
                    "metrics": {},
                },
            )

        self.assertEqual(DISCOVERY.resolve_ref(self.topic, "@item:shared", root=self.topic_root)["title"], "Topic item")
        self.assertEqual(
            DISCOVERY.resolve_ref(self.topic, "@item:problem-a/shared", root=self.topic_root)["title"],
            "Problem A item",
        )
        self.assertEqual(
            DISCOVERY.resolve_ref(self.topic, "@version:problem-b/version-agent1-0001", root=self.topic_root)["problem_id"],
            "problem-b",
        )
        with self.assertRaisesRegex(SystemExit, "reference not found"):
            DISCOVERY.resolve_ref(self.topic, "@version:version-agent1-0001", root=self.topic_root)
        with self.assertRaisesRegex(SystemExit, "may not use qualified"):
            DISCOVERY.resolve_ref(self.workspace, "@item:problem-b/shared", root=self.root)
        with self.assertRaisesRegex(SystemExit, "reference not found"):
            DISCOVERY.resolve_ref(self.workspace, "@memory:topic-memory", root=self.root)

    def test_problem_item_delete_is_blocked_by_local_and_topic_qualified_references(self) -> None:
        self.add_item("paper-a")
        DISCOVERY.write_json(
            self.root / "topics.json",
            {"methods": {"id": "methods", "title": "Methods", "text": "Uses @item:paper-a.", "items": ["paper-a"]}},
        )
        (self.topic_root / "items").mkdir(parents=True)
        DISCOVERY.write_json(self.topic_root / "items.json", {})
        DISCOVERY.write_json(self.topic_root / "topics.json", {})
        memory_file = self.topic / "topic-memory.json"
        DISCOVERY.write_json(memory_file, {"summary": "Topic conclusion about Problem evidence.", "report": "Uses @item:problem-a/paper-a."})
        DISCOVERY.memory_log_add(self.topic_root, "topic-memory", str(memory_file))
        blockers = DISCOVERY.maintain_reference_blockers(
            self.topic,
            "item",
            "paper-a",
            problem_id="problem-a",
        )
        self.assertEqual(blockers, ["problem:problem-a:topic:methods", "topic:memory:topic-memory"])

    def test_route_context_and_search_exclude_topic_memory(self) -> None:
        self.add_item("paper-a")
        DISCOVERY.write_practice(
            self.workspace,
            {
                "id": "version-agent1-0001",
                "entity_type": "version",
                "problem_id": "problem-a",
                "agent": "agent1",
                "summary": "Local practice",
                "note": "",
                "metrics": {"score": 0.8},
                "metric_roles": {"score": "breakthrough"},
                "ai_review": {"dimensions": {"clarity": {"score": 8}}},
            },
        )
        evaluation = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation"
        evaluation.mkdir()
        DISCOVERY.write_json(
            evaluation / "contract.json",
            {
                "configured": True,
                "metrics": {"score": {"direction": "higher", "role": "breakthrough"}},
                "ai_review": {"dimensions": {"clarity": {"label": "Clarity"}}},
            },
        )
        self.add_memory("topic-memory", "Topic-only memory for Main Agent use.", "Do not expose this to Routes.")
        result_types = {row["entity_type"] for row in DISCOVERY.knowledge_search(self.workspace, "", root=self.root)}
        self.assertEqual(result_types, {"item", "version"})
        args = argparse.Namespace(limit=20, query="", job="")
        with mock.patch.object(DISCOVERY, "load_resource_config", return_value={}), mock.patch.object(
            DISCOVERY, "agent_resource_policy", return_value={}
        ):
            context = DISCOVERY.build_route_context(self.workspace, self.agent, args)
        self.assertNotIn("main_memory", context)
        self.assertNotIn("memory_logs", context)
        self.assertEqual(context["problem"]["evaluation_mode"], "Hybrid")
        self.assertEqual(context["practice"][0]["metric_roles"], {"score": "breakthrough"})
        self.assertEqual(context["practice"][0]["ai_review"]["dimensions"]["clarity"]["score"], 8)



class MaintainCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.topic = Path(self.temp_dir.name) / "topic"
        self.problem = self.topic / "subprojects-team" / "problem-a"
        self.topic_knowledge = self.topic / ".DiscoveryProgram" / "knowledge"
        self.problem_knowledge = self.problem / ".DiscoveryConsole" / "pub" / "knowledge"
        (self.topic_knowledge / "items").mkdir(parents=True)
        (self.problem_knowledge / "items").mkdir(parents=True)
        (self.problem_knowledge / "versions").mkdir()
        (self.problem / ".DiscoveryConsole" / "pub" / "log").mkdir()
        (self.problem / "agent1" / ".discovery").mkdir(parents=True)
        for root in (self.topic_knowledge, self.problem_knowledge):
            DISCOVERY.write_json(root / "items.json", {})
            DISCOVERY.write_json(root / "topics.json", {})
        memory_path = DISCOVERY.main_memory_path(self.topic_knowledge)
        memory_path.parent.mkdir(parents=True)
        memory_path.write_text("# Main Agent Memory\n\n## 当前项目\n\nfixture\n\n## 项目进度\n\nfixture\n\n## 用户偏好\n\nfixture\n", encoding="utf-8")
        DISCOVERY.write_json(
            self.topic / ".DiscoveryProgram" / "problem_registry.json",
            {"schema_version": 1, "default_problem": "problem-a", "problems": [{"id": "problem-a", "path": "subprojects-team/problem-a"}]},
        )
        DISCOVERY.write_json(self.problem / "problem.json", {"problem_id": "problem-a"})
        DISCOVERY.write_json(self.problem / "agent1" / ".discovery" / "loop_state.json", {"phase": "work_loop", "eval_status": None})
        (self.problem / ".DiscoveryConsole" / "pub" / "notices.jsonl").write_text("", encoding="utf-8")
        self.source = self.topic / "source"
        self.source.mkdir()
        (self.source / "paper.md").write_text("external evidence", encoding="utf-8")
        self.metadata = self.topic / "item.json"
        DISCOVERY.write_json(self.metadata, {"title": "External source", "summary": "Evidence-bearing external source summary."})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(MODULE_PATH), *args], cwd=self.topic, text=True, capture_output=True, check=False)

    def test_item_add_and_reference_safe_delete_are_end_to_end(self) -> None:
        result = self.run_cli("maintain", "item", "add", "--scope", "topic", "--id", "paper-a", "--source", str(self.source), "--metadata", str(self.metadata))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.topic_knowledge / "items" / "paper-a" / "paper.md").is_file())
        DISCOVERY.write_json(self.topic_knowledge / "topics.json", {"methods": {"id": "methods", "title": "Methods", "text": "Synthesis of @item:paper-a.", "items": ["paper-a"]}})
        blocked = self.run_cli("maintain", "item", "delete", "--scope", "topic", "--id", "paper-a")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("still referenced", blocked.stderr)
        DISCOVERY.write_json(self.topic_knowledge / "topics.json", {})
        deleted = self.run_cli("maintain", "item", "delete", "--scope", "topic", "--id", "paper-a")
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertFalse((self.topic_knowledge / "items" / "paper-a").exists())

    def test_pending_handoff_is_registered_only_after_human_result(self) -> None:
        item_dir = self.topic_knowledge / "items" / "handoff-a"
        item_dir.mkdir()
        DISCOVERY.write_json(item_dir / DISCOVERY.PENDING_HANDOFF_FILE, {"recipient": "Deep Research"})
        (item_dir / "question.md").write_text("research question", encoding="utf-8")
        blocked = self.run_cli("maintain", "item", "add", "--scope", "topic", "--id", "handoff-a", "--source", str(item_dir), "--metadata", str(self.metadata))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("still pending", blocked.stderr)
        (item_dir / DISCOVERY.PENDING_HANDOFF_FILE).unlink()
        (item_dir / "result.md").write_text("complete web result", encoding="utf-8")
        completed = self.run_cli("maintain", "item", "add", "--scope", "topic", "--id", "handoff-a", "--source", str(item_dir), "--metadata", str(self.metadata))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("handoff-a", DISCOVERY.read_json(self.topic_knowledge / "items.json", {}))

    def test_memory_log_and_version_anchored_notice_are_separate(self) -> None:
        memory_file = self.topic / "memory.json"
        DISCOVERY.write_json(memory_file, {"summary": "Integrated Main conclusion after review.", "report": "Integrated conclusion."})
        memory = self.run_cli("maintain", "memory", "add", "--id", "main-conclusion", "--file", str(memory_file))
        self.assertEqual(memory.returncode, 0, memory.stderr)
        self.assertTrue((self.topic / ".DiscoveryProgram" / "memory" / "logs" / "main-conclusion.json").is_file())
        created = DISCOVERY.read_json(self.topic / ".DiscoveryProgram" / "memory" / "logs" / "main-conclusion.json", {})
        self.assertEqual(set(created), {"id", "created_at", "summary", "report"})
        self.assertTrue(created["created_at"].endswith("+00:00"))
        duplicate = self.run_cli("maintain", "memory", "add", "--id", "main-conclusion", "--file", str(memory_file))
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("already exists", duplicate.stderr)
        invalid_file = self.topic / "invalid-memory.json"
        DISCOVERY.write_json(invalid_file, {"summary": "Valid summary", "report": "Valid report", "created_at": "forbidden"})
        invalid = self.run_cli("maintain", "memory", "add", "--id", "invalid", "--file", str(invalid_file))
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("supports only summary and report", invalid.stderr)
        DISCOVERY.write_practice(self.problem, {"id": "version-agent1-0001", "entity_type": "version", "problem_id": "problem-a", "agent": "agent1", "created_at": "2026-01-01T00:00:00+00:00", "summary": "formal", "note": "", "metrics": {}})
        notice_file = self.topic / "notice.json"
        DISCOVERY.write_json(notice_file, {"title": "Metric correction", "body": "Prior scores require caution.", "priority": "high", "tags": ["evaluation"]})
        notice = self.run_cli("maintain", "notice", "add", "--problem", "problem-a", "--id", "metric-correction", "--file", str(notice_file))
        self.assertEqual(notice.returncode, 0, notice.stderr)
        row = DISCOVERY.read_jsonl(self.problem / ".DiscoveryConsole" / "pub" / "notices.jsonl")[0]
        self.assertTrue(row["published_at"])
        self.assertEqual(row["version_anchor"], {"agent1": "version-agent1-0001"})
        DISCOVERY.append_jsonl(self.problem / ".DiscoveryConsole" / "pub" / "log" / "headless_runs.jsonl", {"id": "run-a", "agent": "agent1", "status": "running"})
        blocked = self.run_cli("maintain", "notice", "add", "--problem", "problem-a", "--id", "blocked", "--file", str(notice_file))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("not stationary", blocked.stderr)
        blocked_delete = self.run_cli("maintain", "notice", "delete", "--problem", "problem-a", "--id", "metric-correction")
        self.assertNotEqual(blocked_delete.returncode, 0)
        self.assertIn("not stationary", blocked_delete.stderr)

    def test_check_accepts_marked_pending_topic_handoff(self) -> None:
        item_dir = self.topic_knowledge / "items" / "pending-a"
        item_dir.mkdir()
        DISCOVERY.write_json(item_dir / DISCOVERY.PENDING_HANDOFF_FILE, {"recipient": "Pro"})
        checked = self.run_cli("maintain", "check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["ok"])

    def test_check_rejects_notice_without_publication_anchor(self) -> None:
        DISCOVERY.append_jsonl(
            self.problem / ".DiscoveryConsole" / "pub" / "notices.jsonl",
            {"id": "legacy", "title": "Legacy", "body": "Missing anchor", "priority": "high", "tags": []},
        )
        checked = self.run_cli("maintain", "check", "--problem", "problem-a")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("notice_missing_published_at", checked.stdout)
        self.assertIn("invalid_notice_version_anchor", checked.stdout)

    def test_check_rejects_problem_notes_file(self) -> None:
        DISCOVERY.write_json(self.problem_knowledge / "notes.json", {})
        checked = self.run_cli("maintain", "check", "--problem", "problem-a")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("unexpected_problem_notes_file", checked.stdout)

    def test_check_rejects_invalid_main_memory_and_memory_reference(self) -> None:
        memory_path = DISCOVERY.main_memory_path(self.topic_knowledge)
        memory_path.write_text("# Main Agent Memory\n\n## 当前项目\n\nfixture\n\n## 用户偏好\n\nfixture\n", encoding="utf-8")
        checked = self.run_cli("maintain", "check")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("invalid_main_memory_structure", checked.stdout)
        memory_path.write_text("# Main Agent Memory\n\n## 当前项目\n\nfixture\n\n## 项目进度\n\n@memory:missing\n\n## 用户偏好\n\nfixture\n", encoding="utf-8")
        checked = self.run_cli("maintain", "check")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("unresolved_reference", checked.stdout)


class NewCliSurfaceTests(unittest.TestCase):
    def test_active_builder_prompts_require_new_signal_and_forbid_incumbent_resubmission(self) -> None:
        topic = MODULE_PATH.parents[2]
        template = topic / ".discovery" / "agents-template"
        builder_files = [
            template / "AGENTS.md",
            template / "goals" / "route_builder.md",
        ]
        headless_files = [template / "headless_goals" / "route_builder.md"]
        forbidden = (
            "well-evidenced plateau",
            "headless_stop_reason",
            "Stopping without queueing eval is allowed",
            "fallback headless stop",
            "choose the strongest defensible relative candidate",
            "select the strongest defensible relative candidate",
            "A negative or non-improving Version is valid",
            "If the final result is negative or does not improve the frontier",
        )
        for path in builder_files:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"premature Builder exit in {path}: {token}")
            normalized = " ".join(text.split()).lower()
            self.assertIn("materially different", normalized, f"missing substantive exploration gate in {path}")
            self.assertIn("evidence-bounded", normalized, f"missing honest no-Candidate handoff in {path}")
            self.assertIn("effective incumbent", normalized, f"missing duplicate-Candidate prohibition in {path}")
            self.assertIn("metadata-only", normalized, f"missing semantic-change gate in {path}")
            self.assertIn("latest own version", normalized, f"missing previous-Version comparison in {path}")

        for path in headless_files:
            normalized = " ".join(path.read_text(encoding="utf-8").split()).lower()
            self.assertIn("goals/route_builder.md", normalized)
            self.assertIn("adds only", normalized)
            self.assertIn("evidence-bounded no-candidate handoff", normalized)

    def test_active_prompt_sources_use_five_entity_knowledge_protocol(self) -> None:
        topic = MODULE_PATH.parents[2]
        prompt_files = [
            topic / "AGENTS.md",
            topic / ".agents" / "skills" / "maintain-discovery" / "SKILL.md",
            topic / ".agents" / "skills" / "chatgpt-handoff" / "SKILL.md",
            topic / ".agents" / "skills" / "create-exploration-problem" / "SKILL.md",
            topic / ".discovery" / "agents-template" / "AGENTS.md",
            topic / ".discovery" / "agents-template" / "notebook.md",
            topic / ".discovery" / "agents-template" / "goals" / "route_builder.md",
            topic / ".discovery" / "agents-template" / "goals" / "route_auditor.md",
            topic / ".discovery" / "agents-template" / "headless_goals" / "route_builder.md",
            topic / ".discovery" / "agents-template" / "headless_goals" / "route_auditor.md",
            topic / ".discovery" / "agents-template" / ".agents" / "skills" / "explore-cli" / "SKILL.md",
        ]
        forbidden = (
            "knowledge-request",
            "request-add",
            "ingest_requests",
            "notes/items",
            "notes/main_agent",
            "main_notes.md",
            "catalog.json",
            "item.json",
            ".catalog.lock",
            "pub/practice",
            "practice/notes",
            "practice/versions",
        )
        for path in prompt_files:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"obsolete knowledge protocol in {path}: {token}")
        root_prompt = (topic / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("$browse-discovery-knowledge", root_prompt)
        self.assertIn("$maintain-discovery", root_prompt)
        maintain_prompt = (topic / ".agents" / "skills" / "maintain-discovery" / "SKILL.md").read_text(encoding="utf-8")
        for token in ("@item:<id>", "@topic:<id>", "@memory:<id>", "@baseline:<id>", "@version:<id>"):
            self.assertIn(token, maintain_prompt)
        self.assertIn("@baseline:<problem-id>/<id>", maintain_prompt)
        self.assertIn("@version:<problem-id>/<id>", maintain_prompt)
        route_prompt = (topic / ".discovery" / "agents-template" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("exactly four citeable entities", route_prompt)
        self.assertIn("@baseline:<id>", route_prompt)
        self.assertIn("does not enter Route Context", route_prompt)
        self.assertNotIn("Main `@memory:<id>`", route_prompt)

    def test_main_parser_exposes_only_human_main_surface(self) -> None:
        parser = DISCOVERY.build_cli_parser()
        help_text = parser.format_help()
        for command in ("start", "doctor", "maintain"):
            self.assertIn(command, help_text)
        self.assertNotIn("Main-Agent control API", help_text)
        subparser_action = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        self.assertEqual(set(subparser_action.choices), {"start", "doctor", "maintain", "knowledge"})

    def test_route_explore_client_has_a_separate_help_surface_in_a_clean_environment(self) -> None:
        topic = MODULE_PATH.parents[2]
        route_client = topic / ".discovery" / "agents-template" / "explore"
        result = subprocess.run(
            [sys.executable, str(route_client), "--help"],
            cwd=topic,
            env={"PATH": "/usr/bin:/bin"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{context,knowledge,run,eval,reflect}", result.stdout)
        self.assertNotIn("maintain", result.stdout)

    def test_route_knowledge_filter_cannot_override_authenticated_identity(self) -> None:
        topic = MODULE_PATH.parents[2]
        template = (topic / ".discovery" / "agents-template" / "explore").read_text(encoding="utf-8")
        self.assertIn('"route_filter": args.route', template)
        payload_index = template.index("**payload,")
        identity_index = template.index('"route": route_id', payload_index)
        self.assertLess(payload_index, identity_index)
        self.assertIn("Thin Route-only client", template)

    def test_private_control_is_rejected_inside_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            topic = Path(tmp) / "topic"
            workspace = topic / "subprojects-team" / "problem-a"
            agent = workspace / "agent1"
            (topic / ".DiscoveryProgram").mkdir(parents=True)
            agent.mkdir(parents=True)
            DISCOVERY.write_json(workspace / "problem.json", {"problem_id": "problem-a"})
            with self.assertRaisesRegex(SystemExit, "unavailable inside a Route"):
                DISCOVERY.cmd_main(topic, workspace, agent, ["status"])

    def test_start_runs_only_the_selected_problem_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            topic = Path(tmp) / "topic"
            first = topic / "subprojects-team" / "a"
            second = topic / "subprojects-team" / "b"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            route_skill = topic / ".discovery" / "agents-template" / ".agents" / "skills" / "explore-cli" / "SKILL.md"
            route_skill.parent.mkdir(parents=True)
            route_skill.write_text("<!-- explore-cli-protocol: 8 -->", encoding="utf-8")
            template_query_skill = topic / ".discovery" / "agents-template" / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
            template_query_skill.parent.mkdir(parents=True)
            template_query_skill.write_text("<!-- knowledge-query-protocol: 1 -->", encoding="utf-8")
            DISCOVERY.write_json(
                topic / ".DiscoveryProgram" / "problem_registry.json",
                {
                    "schema_version": 1,
                    "default_problem": "a",
                    "problems": [{"id": "a", "path": "subprojects-team/a"}, {"id": "b", "path": "subprojects-team/b"}],
                },
            )
            topic_knowledge = topic / ".DiscoveryProgram" / "knowledge"
            for owner in (topic_knowledge, DISCOVERY.knowledge_root(first), DISCOVERY.knowledge_root(second)):
                (owner / "items").mkdir(parents=True)
                DISCOVERY.write_json(owner / "items.json", {})
                DISCOVERY.write_json(owner / "topics.json", {})
            memory_path = DISCOVERY.main_memory_path(topic_knowledge)
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text("# Main Agent Memory\n\n## 当前项目\n\nfixture\n\n## 项目进度\n\nfixture\n\n## 用户偏好\n\nfixture\n", encoding="utf-8")
            args = argparse.Namespace(problem="", host="127.0.0.1", port=8765, no_browser=True)
            with mock.patch.object(DISCOVERY, "required_paths", return_value=[]), mock.patch.object(
                DISCOVERY, "topic_required_paths", return_value=[]
            ), mock.patch.object(
                DISCOVERY, "resource_integrity_report", return_value={"ok": True, "issues": []}
            ), mock.patch.object(
                DISCOVERY, "assert_problem_runtime_exclusive"
            ), mock.patch.object(
                DISCOVERY,
                "read_dashboard_worker_state",
                return_value={"status": "running", "pid": 1},
            ), mock.patch.object(
                DISCOVERY,
                "route_broker_is_available",
                return_value=True,
            ), mock.patch.object(
                DISCOVERY,
                "launch_dashboard_worker",
                return_value={"worker": {"status": "running", "pid": 2}},
            ) as launch, mock.patch.object(DISCOVERY, "serve_dashboard") as serve, contextlib.redirect_stdout(io.StringIO()):
                DISCOVERY.cmd_start(topic, None, topic, args)
            launch.assert_not_called()
        serve.assert_called_once_with(first, "127.0.0.1", 8765, 2.0, True)

    def test_start_refuses_non_loopback_dashboard_binding(self) -> None:
        args = argparse.Namespace(problem="", host="0.0.0.0", port=8765, no_browser=True)
        with tempfile.TemporaryDirectory() as tmp:
            topic = Path(tmp)
            with self.assertRaisesRegex(SystemExit, "only to loopback"):
                DISCOVERY.cmd_start(topic, None, topic, args)

    def test_dashboard_javascript_and_control_surfaces_are_valid(self) -> None:
        self.assertIn('data-view="control"', DISCOVERY.DASHBOARD_HTML)
        self.assertIn('data-view="knowledge"', DISCOVERY.DASHBOARD_HTML)
        self.assertIn('/api/main-action', DISCOVERY.DASHBOARD_JS)
        self.assertIn('/api/knowledge.json', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('data-knowledge-link', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('data-knowledge-add-item', DISCOVERY.DASHBOARD_JS)
        self.assertNotIn('data-knowledge-create-note', DISCOVERY.DASHBOARD_JS)
        self.assertIn('id="knowledgeScope"', DISCOVERY.DASHBOARD_JS)
        self.assertIn('bindKnowledgeNavigation', DISCOVERY.DASHBOARD_JS)
        self.assertIn('data-job-cancel', DISCOVERY.DASHBOARD_JS)
        self.assertIn("Managed automatically by Discovery", DISCOVERY.DASHBOARD_JS)
        self.assertNotIn(">Start worker<", DISCOVERY.DASHBOARD_JS)
        self.assertNotIn(">Stop worker<", DISCOVERY.DASHBOARD_JS)
        self.assertIn("runtime.cpu_percent", DISCOVERY.DASHBOARD_JS)
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "dashboard.js"
            script.write_text(DISCOVERY.DASHBOARD_JS, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_route_cli_protocol_gate_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "problem"
            client = workspace / "agent1" / "explore"
            skill = workspace / "agent1" / ".agents" / "skills" / "explore-cli" / "SKILL.md"
            query_skill = workspace / "agent1" / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("legacy commands", encoding="utf-8")
            self.assertFalse(DISCOVERY.route_cli_protocol_ready(workspace, "agent1"))
            client.write_text("#!/bin/sh\n", encoding="utf-8")
            skill.write_text("<!-- explore-cli-protocol: 8 -->", encoding="utf-8")
            query_skill.parent.mkdir(parents=True)
            query_skill.write_text("<!-- knowledge-query-protocol: 1 -->", encoding="utf-8")
            self.assertTrue(DISCOVERY.route_cli_protocol_ready(workspace, "agent1"))

    def test_stale_route_prompt_bundle_blocks_new_headless_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "problem"
            template = workspace / ".discovery" / "agents-template"
            agent = workspace / "agent1"
            for relative in DISCOVERY.ROUTE_PROMPT_BUNDLE:
                (template / relative).parent.mkdir(parents=True, exist_ok=True)
                (agent / relative).parent.mkdir(parents=True, exist_ok=True)
                (template / relative).write_text(f"template:{relative}\n", encoding="utf-8")
                (agent / relative).write_text(f"template:{relative}\n", encoding="utf-8")
            (agent / ".discovery").mkdir(exist_ok=True)
            DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(workspace), {"configured": True})
            DISCOVERY.write_json(agent / ".discovery" / "loop_state.json", {"phase": "work_loop"})
            with mock.patch.object(DISCOVERY, "route_sandbox_report", return_value={"available": True}), mock.patch.object(
                DISCOVERY, "route_cli_protocol_ready", return_value=True
            ):
                ready = DISCOVERY.build_dashboard_agent_statuses(workspace, ["agent1"])[0]
                self.assertTrue(ready["prompt_bundle_ready"])
                (agent / "goals" / "route_builder.md").write_text("stale\n", encoding="utf-8")
                blocked = DISCOVERY.build_dashboard_agent_statuses(workspace, ["agent1"])[0]
            self.assertEqual(blocked["runner_action"], "blocked_prompt_bundle")
            self.assertFalse(blocked["should_start_codex"])
            self.assertEqual(blocked["prompt_bundle_issues"][0]["issue"], "stale_route_prompt_file")

    def test_route_status_is_blocked_when_bubblewrap_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "problem"
            agent = workspace / "agent1"
            (agent / ".discovery").mkdir(parents=True)
            (agent / "explore").write_text("#!/bin/sh\n", encoding="utf-8")
            skill = agent / ".agents" / "skills" / "explore-cli" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("<!-- explore-cli-protocol: 8 -->", encoding="utf-8")
            query_skill = agent / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
            query_skill.parent.mkdir(parents=True)
            query_skill.write_text("<!-- knowledge-query-protocol: 1 -->", encoding="utf-8")
            DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(workspace), {"configured": True})
            DISCOVERY.write_json(agent / ".discovery" / "loop_state.json", {"phase": "work_loop"})
            with mock.patch.object(DISCOVERY, "route_sandbox_report", return_value={"available": False, "detail": "user namespaces blocked"}):
                status = DISCOVERY.build_dashboard_agent_statuses(workspace, ["agent1"])[0]
            self.assertEqual(status["runner_action"], "blocked_sandbox")
            self.assertFalse(status["should_start_codex"])

    def test_dashboard_marks_only_active_jobs_cancellable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            queued = DISCOVERY.dashboard_job_summary(workspace, {"id": "job-a", "status": "queued", "command": []})
            done = DISCOVERY.dashboard_job_summary(workspace, {"id": "job-b", "status": "done", "command": []})
            paused = DISCOVERY.dashboard_job_summary(workspace, {"id": "run-a", "status": "paused", "command": []})
            self.assertTrue(queued["can_cancel"])
            self.assertTrue(paused["can_cancel"])
            self.assertFalse(done["can_cancel"])

    def test_route_broker_authenticates_and_dispatches_queued_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "problem"
            agent = workspace / "agent1"
            (workspace / ".DiscoveryConsole" / "private").mkdir(parents=True)
            (agent / ".discovery").mkdir(parents=True)
            DISCOVERY.write_json(workspace / "problem.json", {"problem_id": "problem-a"})
            token = DISCOVERY.ensure_route_broker_token(agent)
            data = {
                "route": "agent1",
                "action": "run.queued",
                "resources": "large.json",
                "command": ["/bin/true"],
                "headless_run_id": "headless-a",
                "campaign_id": "campaign-a",
            }
            with self.assertRaisesRegex(SystemExit, "invalid Route broker credential"):
                DISCOVERY.route_broker_action(workspace, data, "wrong")
            with mock.patch.object(DISCOVERY, "cmd_submit", return_value={"job": "job-a", "status": "queued"}) as submit:
                result = DISCOVERY.route_broker_action(workspace, data, token)
            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["job"], "job-a")
            self.assertFalse(submit.call_args.kwargs["emit"])
            queued_args = submit.call_args.args[2]
            self.assertEqual(queued_args.headless_run_id, "headless-a")
            self.assertEqual(queued_args.campaign_id, "campaign-a")

    def test_route_broker_dispatches_read_only_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "problem"
            agent = workspace / "agent1"
            (workspace / ".DiscoveryConsole" / "private").mkdir(parents=True)
            (agent / ".discovery").mkdir(parents=True)
            DISCOVERY.write_json(workspace / "problem.json", {"problem_id": "problem-a"})
            token = DISCOVERY.ensure_route_broker_token(agent)
            data = {"route": "agent1", "action": "context", "query": "calibration", "job": "", "limit": 3}
            with mock.patch.object(DISCOVERY, "build_route_context", return_value={"schema_version": 1}) as context:
                result = DISCOVERY.route_broker_action(workspace, data, token)
            self.assertTrue(result["ok"])
            self.assertEqual(result["result"], {"schema_version": 1})
            self.assertEqual(context.call_args.args[1], agent)
            self.assertFalse(hasattr(context.call_args.args[2], "query"))

    def test_route_broker_keeps_authenticated_route_separate_from_knowledge_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "problem"
            agent = workspace / "agent1"
            (workspace / ".DiscoveryConsole" / "private").mkdir(parents=True)
            (workspace / ".DiscoveryConsole" / "pub" / "knowledge" / "versions").mkdir(parents=True)
            (agent / ".discovery").mkdir(parents=True)
            DISCOVERY.write_json(workspace / "problem.json", {"problem_id": "problem-a"})
            token = DISCOVERY.ensure_route_broker_token(agent)
            data = {
                "route": "agent1",
                "route_filter": "agent2",
                "action": "knowledge.browse",
                "view": "practice",
                "query": "",
                "metric": "score",
                "sort": "best",
                "limit": 5,
            }
            expected = {"scope": "problem", "view": "practice", "cards": []}
            with mock.patch.object(DISCOVERY, "query_contract", return_value={"metrics": {"score": {}}}), mock.patch.object(
                DISCOVERY, "load_dashboard_baseline_rows", return_value=([], [])
            ), mock.patch.object(DISCOVERY.knowledge_query, "browse", return_value=expected) as browse:
                result = DISCOVERY.route_broker_action(workspace, data, token)
            self.assertEqual(result["route"], "agent1")
            self.assertEqual(result["result"], expected)
            self.assertEqual(browse.call_args.kwargs["route"], "agent2")

    def test_route_permission_profile_allows_web_and_broker_but_denies_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            topic = Path(tmp) / "topic"
            workspace = topic / "subprojects-team" / "problem-a"
            agent = workspace / "agent1"
            (topic / ".DiscoveryProgram").mkdir(parents=True)
            (workspace / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
            (workspace / ".DiscoveryConsole" / "private").mkdir()
            agent.mkdir(parents=True)
            overrides = DISCOVERY.route_permission_overrides(workspace, agent)
            joined = "\n".join(overrides)
            self.assertIn("network.unix_sockets", joined)
            self.assertIn('network.domains={"*"="allow"}', joined)
            self.assertIn(str(DISCOVERY.private(workspace).resolve()), joined)
            self.assertIn(str((agent / ".codex").resolve()), joined)
            self.assertIn(str((agent / ".discovery").resolve()), joined)
            self.assertIn(str((agent / ".git").resolve()), joined)

    def test_dashboard_payload_includes_evaluation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            topic = Path(tmp) / "topic"
            workspace = topic / "subprojects-team" / "problem-a"
            agent = workspace / "agent1"
            (topic / ".DiscoveryProgram").mkdir(parents=True)
            knowledge_root = workspace / ".DiscoveryConsole" / "pub" / "knowledge"
            (knowledge_root / "versions").mkdir(parents=True)
            (workspace / ".DiscoveryConsole" / "pub" / "evaluation").mkdir(parents=True)
            (agent / ".discovery").mkdir(parents=True)
            (agent / "explore").write_text("#!/bin/sh\n", encoding="utf-8")
            skill = agent / ".agents" / "skills" / "explore-cli" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("<!-- explore-cli-protocol: 8 -->", encoding="utf-8")
            query_skill = agent / ".agents" / "skills" / "browse-problem-knowledge" / "SKILL.md"
            query_skill.parent.mkdir(parents=True)
            query_skill.write_text("<!-- knowledge-query-protocol: 1 -->", encoding="utf-8")
            DISCOVERY.write_json(workspace / "problem.json", {"problem_id": "problem-a", "title": "Problem A"})
            DISCOVERY.write_json(
                topic / ".DiscoveryProgram" / "problem_registry.json",
                {"schema_version": 1, "default_problem": "problem-a", "problems": [{"id": "problem-a", "path": "subprojects-team/problem-a"}]},
            )
            contract = {
                "schema_version": 1,
                "problem_id": "problem-a",
                "configured": False,
                "metrics": {
                    "quality": {"direction": "higher", "role": "breakthrough"},
                    "runtime": {"direction": "lower", "role": "guardrail"},
                },
            }
            DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(workspace), contract)
            DISCOVERY.write_json(agent / ".discovery" / "loop_state.json", {"phase": "work_loop"})
            DISCOVERY.write_json(knowledge_root / "items.json", {})
            DISCOVERY.write_json(knowledge_root / "topics.json", {})
            with mock.patch.object(DISCOVERY, "build_headless_model_config", return_value={"models": [], "default_model": "", "default_reasoning_effort": "", "error": None}):
                payload = DISCOVERY.build_dashboard_payload(workspace)
            self.assertEqual(payload["evaluation_contract"], contract)
            self.assertEqual(payload["metric_shortcuts"], {"breakthrough": ["quality"], "guardrail": ["runtime"]})
            self.assertEqual(
                {metric["name"]: metric["role"] for metric in payload["metrics"]},
                {"quality": "breakthrough", "runtime": "guardrail"},
            )
            self.assertTrue(payload["agent_statuses"][0]["cli_protocol_ready"])

    def test_dashboard_session_cookie_is_required(self) -> None:
        token = "human-dashboard-token"
        self.assertTrue(DISCOVERY.dashboard_cookie_authorized(f"other=x; discovery_session={token}", token))
        self.assertFalse(DISCOVERY.dashboard_cookie_authorized("discovery_session=wrong", token))
        self.assertFalse(DISCOVERY.dashboard_cookie_authorized("", token))


if __name__ == "__main__":
    unittest.main()
