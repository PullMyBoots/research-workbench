import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli_registered_eval_tests", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class RegisteredEvalProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.topic = Path(self.temp_dir.name) / "topic"
        self.workspace = self.topic / "subprojects-team" / "problem-a"
        self.agent = self.workspace / "agent1"
        (self.topic / ".DiscoveryProgram" / "log").mkdir(parents=True)
        (self.workspace / ".DiscoveryConsole" / "pub" / "evaluation").mkdir(parents=True)
        (self.workspace / ".DiscoveryConsole" / "pub" / "knowledge" / "versions").mkdir(parents=True)
        (self.workspace / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
        (self.workspace / ".DiscoveryConsole" / "private" / "eval_submissions").mkdir(parents=True)
        (self.agent / ".discovery").mkdir(parents=True)
        DISCOVERY.write_json(
            self.workspace / "problem.json",
            {"schema_version": 1, "problem_id": "problem-a"},
        )
        DISCOVERY.write_json(
            self.topic / ".DiscoveryProgram" / "problem_registry.json",
            {
                "schema_version": 1,
                "default_problem": "problem-a",
                "problems": [{"id": "problem-a", "path": "subprojects-team/problem-a"}],
            },
        )
        DISCOVERY.write_json(
            self.workspace / ".DiscoveryConsole" / "resources.json",
            {
                "schema_version": 1,
                "free_run": {"default": {"cpus": 1, "memory_gb": 1, "gpus": []}, "agents": {}},
                "queue": {"capacity": {"cpus": 1, "memory_gb": 1, "gpus": []}},
                "evaluation": {"resources": {"cpus": 1, "memory_gb": 1, "gpus": []}, "timeout_seconds": 10},
                "scheduler": {"memory_reserve_gb": 0, "respect_system_load": False, "respect_external_gpu_processes": True},
            },
        )
        DISCOVERY.write_json(
            self.agent / ".discovery" / "loop_state.json",
            {"schema_version": 1, "phase": "work_loop", "eval_status": "idle"},
        )
        DISCOVERY.write_json(self.workspace / ".DiscoveryConsole" / "pub" / "knowledge" / "items.json", {})
        DISCOVERY.write_json(self.workspace / ".DiscoveryConsole" / "pub" / "knowledge" / "topics.json", {})

        self.check_script = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation" / "check.py"
        self.check_script.write_text(
            "import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser(); p.add_argument('--candidate', required=True); a=p.parse_args(); assert Path(a.candidate).exists()\n",
            encoding="utf-8",
        )
        self.evaluator_script = self.workspace / ".DiscoveryConsole" / "private" / "evaluate.py"
        self.evaluator_script.write_text(
            "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser(); p.add_argument('--candidate', required=True); p.add_argument('--report', required=True); a=p.parse_args(); text=(Path(a.candidate)/'value.txt').read_text(); Path(a.report).write_text(json.dumps({'schema_version':1,'metrics':{'score':float(text)}}))\n",
            encoding="utf-8",
        )
        self.contract = {
            "schema_version": 1,
            "problem_id": "problem-a",
            "configured": True,
            "evidence_level": "L2",
            "candidate": {
                "kind": "directory",
                "max_files": 10,
                "max_bytes": 1024,
                "reject_symlinks": True,
            },
            "check": {
                "command": ["python3", str(self.check_script), "--candidate", "{candidate}"],
                "cwd": ".",
            },
            "metrics": {
                "score": {
                    "direction": "higher",
                    "role": "breakthrough",
                }
            },
            "feedback": {
                "search_space": "validation",
                "information_budget": {
                    "max_submissions_per_route": 3,
                    "precision_decimals": 4,
                    "released_fields": ["metrics"],
                },
            },
        }
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), self.contract)
        DISCOVERY.write_json(
            DISCOVERY.evaluation_registry_path(self.workspace),
            {
                "schema_version": 1,
                "problem_id": "problem-a",
                "configured": True,
                "public_contract_digest": DISCOVERY.evaluation_contract_digest(self.contract),
                "evaluators": {
                    "validation": {
                        "id": "test-validation-v1",
                        "command": [
                            "python3",
                            str(self.evaluator_script),
                            "--candidate",
                            "{candidate}",
                            "--report",
                            "{report}",
                        ],
                        "cwd": ".",
                    }
                },
            },
        )
        self.candidate = self.agent / "candidate"
        self.candidate.mkdir()
        (self.candidate / "value.txt").write_text("0.75", encoding="utf-8")

    def tearDown(self) -> None:
        for path in self.workspace.glob(".DiscoveryConsole/private/eval_submissions/*/candidate"):
            if path.is_dir():
                path.chmod(0o755)
                for child in path.rglob("*"):
                    child.chmod(0o755 if child.is_dir() else 0o644)
        self.temp_dir.cleanup()

    def submit(self) -> dict:
        output = io.StringIO()
        args = argparse.Namespace(message="candidate v1", candidate="candidate")
        with mock.patch.object(DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command), contextlib.redirect_stdout(output):
            DISCOVERY.cmd_eval(self.workspace, self.agent, args)
        return json.loads(output.getvalue())

    def test_route_submits_only_candidate_and_worker_uses_registered_evaluator(self) -> None:
        queued = self.submit()
        job = DISCOVERY.get_job(self.workspace, queued["job"])
        self.assertEqual(job["command"], ["<problem-registered-formal-evaluator>"])
        self.assertNotIn("eval_command", job)
        self.assertNotIn("report_path", job)
        submission_id = job["formal_eval_metadata"]["submission_id"]
        submission_root = DISCOVERY.private(self.workspace) / "eval_submissions" / submission_id
        self.assertEqual((submission_root / "candidate" / "value.txt").read_text(), "0.75")

        (self.candidate / "value.txt").write_text("9.0", encoding="utf-8")
        with mock.patch.object(DISCOVERY, "snapshot_code", return_value={"type": "test", "commit": "abc"}), mock.patch.object(
            DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command
        ):
            result = DISCOVERY.run_formal_eval_job(self.workspace, job, allocated_gpus=[])

        self.assertEqual(result["status"], "done")
        version = DISCOVERY.read_json(
            DISCOVERY.knowledge_root(self.workspace) / "versions" / f"{result['practice_version']}.json",
            {},
        )
        self.assertEqual(version["metrics"], {"score": 0.75})
        self.assertEqual(version["metric_directions"], {"score": "higher"})
        self.assertEqual(version["metric_roles"], {"score": "breakthrough"})
        self.assertEqual(version["eval_run"]["evaluator_id"], "test-validation-v1")
        self.assertTrue(version["snapshot"]["tree"])
        self.assertEqual(version["snapshot"]["tree"], version["eval_run"]["tree"])
        self.assertTrue((submission_root / "report.json").is_file())
        self.assertNotIn(str(self.evaluator_script), json.dumps(result))

    def test_successful_eval_can_reflect_from_its_frozen_tree(self) -> None:
        queued = self.submit()
        job = DISCOVERY.get_job(self.workspace, queued["job"])
        with mock.patch.object(DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command):
            result = DISCOVERY.run_formal_eval_job(self.workspace, job, allocated_gpus=[])
        version_id = result["practice_version"]
        provisional = DISCOVERY.read_practice(self.workspace, version_id)
        frozen_tree = provisional["snapshot"]["tree"]
        # Simulate Builder edits while the evaluation was running: they must not
        # enter the commit produced by reflection.
        (self.candidate / "value.txt").write_text("changed-after-eval", encoding="utf-8")
        (self.agent / "summary.md").write_text(" ".join(["evidence"] * 80), encoding="utf-8")
        (self.agent / "reflection.md").write_text("Audited formal evidence.", encoding="utf-8")
        (self.agent / "next.md").write_text("Next Builder Target Brief", encoding="utf-8")
        reflected = DISCOVERY.cmd_reflect(
            self.workspace,
            self.agent,
            argparse.Namespace(version=version_id, summary_file="summary.md", note_file="reflection.md", next_plan_file="next.md"),
            emit=False,
        )
        completed = DISCOVERY.read_practice(self.workspace, version_id)
        self.assertEqual(completed["knowledge_status"], "complete")
        self.assertEqual(completed["snapshot"]["tree"], frozen_tree)
        self.assertTrue(completed["snapshot"]["commit"])
        self.assertEqual(completed["snapshot"]["tag"], f"snapshot-{version_id}")
        committed = DISCOVERY.git_stdout(self.agent, ["show", f"{completed['snapshot']['commit']}:candidate/value.txt"]).strip()
        self.assertEqual(committed, "0.75")
        self.assertEqual(reflected["knowledge_status"], "complete")

    def test_candidate_symlink_is_rejected(self) -> None:
        linked = self.agent / "linked-candidate"
        linked.symlink_to("candidate")
        args = argparse.Namespace(message="bad", candidate="linked-candidate")
        with self.assertRaisesRegex(SystemExit, "must not be a symlink"):
            DISCOVERY.cmd_eval(self.workspace, self.agent, args)

    def test_report_metric_mismatch_is_rejected(self) -> None:
        report = self.workspace / ".DiscoveryConsole" / "private" / "bad-report.json"
        DISCOVERY.write_json(report, {"metrics": {"other": 1.0}})
        with self.assertRaisesRegex(SystemExit, "violates the metric contract"):
            DISCOVERY.validate_registered_eval_report(report, self.contract)

    def test_scientific_gate_metadata_is_rejected(self) -> None:
        contract = json.loads(json.dumps(self.contract))
        contract["metrics"]["score"]["floor_gate"] = 0.5
        with self.assertRaisesRegex(SystemExit, "belong in pub/README.md"):
            DISCOVERY.validate_evaluation_contract_data(self.workspace, contract, require_configured=True)

    def test_metric_role_must_be_breakthrough_or_guardrail(self) -> None:
        contract = json.loads(json.dumps(self.contract))
        contract["metrics"]["score"]["role"] = "floor"
        with self.assertRaisesRegex(SystemExit, "role must be breakthrough or guardrail"):
            DISCOVERY.validate_evaluation_contract_data(self.workspace, contract, require_configured=True)

    def test_legacy_contract_without_roles_remains_readable_but_cannot_be_reactivated(self) -> None:
        contract = json.loads(json.dumps(self.contract))
        del contract["metrics"]["score"]["role"]
        DISCOVERY.validate_evaluation_contract_data(self.workspace, contract, require_configured=True)
        with self.assertRaisesRegex(SystemExit, "must declare role breakthrough or guardrail before activation"):
            DISCOVERY.validate_evaluation_contract_data(
                self.workspace,
                contract,
                require_configured=True,
                require_metric_roles=True,
            )

    def test_activation_computes_digest_and_enables_both_contract_sides(self) -> None:
        contract = dict(self.contract)
        contract["configured"] = False
        registry = DISCOVERY.read_json(DISCOVERY.evaluation_registry_path(self.workspace), {})
        registry["configured"] = False
        registry["public_contract_digest"] = ""
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), contract)
        DISCOVERY.write_json(DISCOVERY.evaluation_registry_path(self.workspace), registry)

        output = io.StringIO()
        args = argparse.Namespace(problem_cmd="activate-eval", problem_id="problem-a")
        with contextlib.redirect_stdout(output):
            DISCOVERY.cmd_problem(self.topic, args)

        activated_contract = DISCOVERY.read_json(DISCOVERY.evaluation_contract_path(self.workspace), {})
        activated_registry = DISCOVERY.read_json(DISCOVERY.evaluation_registry_path(self.workspace), {})
        self.assertTrue(activated_contract["configured"])
        self.assertTrue(activated_registry["configured"])
        self.assertEqual(
            activated_registry["public_contract_digest"],
            DISCOVERY.evaluation_contract_digest(activated_contract),
        )

    def test_validation_information_budget_is_enforced_before_check(self) -> None:
        for index in range(3):
            DISCOVERY.upsert_job(
                self.workspace,
                {
                    "id": f"eval-old-{index}",
                    "kind": "formal_eval",
                    "agent": "agent1",
                    "status": "done",
                },
            )
        args = argparse.Namespace(message="over budget", candidate="candidate")
        with self.assertRaisesRegex(SystemExit, "information budget exhausted"):
            DISCOVERY.cmd_eval(self.workspace, self.agent, args)

    def test_ai_only_eval_publishes_scores_without_l2_rationales(self) -> None:
        prompt = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation" / "reviewer_prompt.md"
        prompt.write_text("Score the declared dimension from evidence.", encoding="utf-8")
        contract = json.loads(json.dumps(self.contract))
        contract["metrics"] = {}
        contract["ai_review"] = {
            "prompt": ".DiscoveryConsole/pub/evaluation/reviewer_prompt.md",
            "prompt_digest": DISCOVERY.file_digest(prompt),
            "dimensions": {"clarity": {"label": "Clarity"}},
        }
        contract["feedback"]["information_budget"] = {
            "max_submissions_per_route": 3,
            "precision_decimals": None,
            "released_fields": ["ai_review_scores"],
        }
        registry = DISCOVERY.read_json(DISCOVERY.evaluation_registry_path(self.workspace), {})
        registry["public_contract_digest"] = DISCOVERY.evaluation_contract_digest(contract)
        registry["evaluators"]["validation"] = {
            "ai_reviewer": {"id": "reviewer-v1", "backend": "codex", "model": "fake", "reasoning_effort": "low"}
        }
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), contract)
        DISCOVERY.write_json(DISCOVERY.evaluation_registry_path(self.workspace), registry)
        fake_review = ({"schema_version": 1, "dimensions": {"clarity": {"score": 8, "rationale": "Evidence is clear."}}}, {"reviewer_id": "reviewer-v1"})
        with mock.patch.object(DISCOVERY, "resolve_headless_model_selection", return_value=("fake", "low")), mock.patch.object(
            DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command
        ), mock.patch.object(DISCOVERY, "run_ai_reviewer", return_value=fake_review):
            queued = self.submit()
            result = DISCOVERY.run_formal_eval_job(self.workspace, DISCOVERY.get_job(self.workspace, queued["job"]), allocated_gpus=[])
        self.assertEqual(result["status"], "done")
        version = DISCOVERY.read_practice(self.workspace, result["practice_version"])
        self.assertEqual(version["metrics"], {})
        self.assertEqual(version["ai_review"], {"dimensions": {"clarity": {"score": 8}}})

    def test_hybrid_eval_keeps_metrics_and_review_in_one_version(self) -> None:
        prompt = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation" / "reviewer_prompt.md"
        prompt.write_text("Score clarity.", encoding="utf-8")
        contract = json.loads(json.dumps(self.contract))
        contract["ai_review"] = {
            "prompt": ".DiscoveryConsole/pub/evaluation/reviewer_prompt.md",
            "prompt_digest": DISCOVERY.file_digest(prompt),
            "dimensions": {"clarity": {"label": "Clarity"}},
        }
        contract["feedback"]["information_budget"]["released_fields"] = ["metrics", "ai_review_scores"]
        registry = DISCOVERY.read_json(DISCOVERY.evaluation_registry_path(self.workspace), {})
        registry["public_contract_digest"] = DISCOVERY.evaluation_contract_digest(contract)
        registry["evaluators"]["validation"]["ai_reviewer"] = {"id": "reviewer-v1", "backend": "codex", "model": "fake", "reasoning_effort": "low"}
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), contract)
        DISCOVERY.write_json(DISCOVERY.evaluation_registry_path(self.workspace), registry)
        fake_review = ({"schema_version": 1, "dimensions": {"clarity": {"score": 7, "rationale": "Clear enough."}}}, {"reviewer_id": "reviewer-v1"})
        with mock.patch.object(DISCOVERY, "resolve_headless_model_selection", return_value=("fake", "low")), mock.patch.object(
            DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command
        ), mock.patch.object(DISCOVERY, "run_ai_reviewer", return_value=fake_review) as reviewer_mock:
            queued = self.submit()
            job = DISCOVERY.get_job(self.workspace, queued["job"])
            self.assertEqual(
                job["formal_eval_metadata"]["review_baseline_digest"],
                DISCOVERY.evaluation_baseline_digest(self.workspace),
            )
            result = DISCOVERY.run_formal_eval_job(self.workspace, job, allocated_gpus=[])
        version = DISCOVERY.read_practice(self.workspace, result["practice_version"])
        self.assertEqual(version["metrics"], {"score": 0.75})
        self.assertEqual(version["ai_review"], {"dimensions": {"clarity": {"score": 7}}})
        self.assertEqual(reviewer_mock.call_args.args[-1], {"score": 0.75})

    def test_hybrid_invalid_objective_report_never_reaches_reviewer(self) -> None:
        prompt = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation" / "reviewer_prompt.md"
        prompt.write_text("Score clarity. 1 is unsupported; 10 is decisive.", encoding="utf-8")
        contract = json.loads(json.dumps(self.contract))
        contract["ai_review"] = {
            "prompt": ".DiscoveryConsole/pub/evaluation/reviewer_prompt.md",
            "prompt_digest": DISCOVERY.file_digest(prompt),
            "dimensions": {"clarity": {"label": "Clarity"}},
        }
        contract["feedback"]["information_budget"]["released_fields"] = ["metrics", "ai_review_scores"]
        registry = DISCOVERY.read_json(DISCOVERY.evaluation_registry_path(self.workspace), {})
        registry["public_contract_digest"] = DISCOVERY.evaluation_contract_digest(contract)
        registry["evaluators"]["validation"]["ai_reviewer"] = {
            "id": "reviewer-v1",
            "backend": "codex",
            "model": "fake",
            "reasoning_effort": "low",
        }
        self.evaluator_script.write_text(
            "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser(); p.add_argument('--candidate'); p.add_argument('--report'); a=p.parse_args(); Path(a.report).write_text(json.dumps({'schema_version':1,'metrics':{'wrong':1.0}}))\n",
            encoding="utf-8",
        )
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), contract)
        DISCOVERY.write_json(DISCOVERY.evaluation_registry_path(self.workspace), registry)
        with mock.patch.object(DISCOVERY, "resolve_headless_model_selection", return_value=("fake", "low")), mock.patch.object(
            DISCOVERY, "resource_runner_command", side_effect=lambda command, _request: command
        ), mock.patch.object(DISCOVERY, "run_ai_reviewer") as reviewer_mock:
            queued = self.submit()
            result = DISCOVERY.run_formal_eval_job(
                self.workspace,
                DISCOVERY.get_job(self.workspace, queued["job"]),
                allocated_gpus=[],
            )
        self.assertEqual(result["status"], "failed")
        state = DISCOVERY.read_json(self.agent / ".discovery" / "loop_state.json", {})
        self.assertEqual(state["eval_status"], "main_review")
        reviewer_mock.assert_not_called()

    def test_reviewer_workspace_receives_only_sanitized_objective_evidence(self) -> None:
        template = self.topic / "subprojects-team" / ".team-template" / "reviewer"
        shutil.copytree(MODULE_PATH.parents[2] / "subprojects-team" / ".team-template" / "reviewer", template)
        evaluation = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation"
        prompt = evaluation / "reviewer_prompt.md"
        prompt.write_text("Score clarity from the declared evidence. 1 is unsupported; 10 is decisive.", encoding="utf-8")
        (evaluation / "API.md").write_text("Candidate API", encoding="utf-8")
        (self.workspace / ".DiscoveryConsole" / "pub" / "README.md").write_text("Problem brief", encoding="utf-8")
        contract = json.loads(json.dumps(self.contract))
        contract["ai_review"] = {
            "prompt": ".DiscoveryConsole/pub/evaluation/reviewer_prompt.md",
            "prompt_digest": DISCOVERY.file_digest(prompt),
            "dimensions": {"clarity": {"label": "Clarity"}},
        }
        DISCOVERY.write_json(DISCOVERY.evaluation_contract_path(self.workspace), contract)
        submission_root = self.workspace / ".DiscoveryConsole" / "private" / "eval_submissions" / "review-prep"
        (submission_root / "candidate").mkdir(parents=True)
        DISCOVERY.write_json(submission_root / "submission.json", {"submission_id": "review-prep"})
        review_dir = DISCOVERY.prepare_reviewer_workspace(
            self.workspace,
            submission_root,
            contract,
            {"review_knowledge_digest": "knowledge-digest"},
            {"score": 0.75},
        )
        context = DISCOVERY.read_json(review_dir / "context.json", {})
        self.assertEqual(context["baseline_root"], str((self.workspace / ".DiscoveryConsole" / "pub" / "baseline").resolve()))
        evidence = DISCOVERY.read_json(Path(context["objective_evidence"]), {})
        self.assertEqual(
            evidence["metrics"],
            {"score": {"value": 0.75, "direction": "higher", "role": "breakthrough"}},
        )
        self.assertEqual(evidence["evidence_space"], "validation")
        self.assertEqual(set(evidence), {"schema_version", "contract_digest", "evidence_space", "metrics"})
        self.assertNotIn("evaluator_provenance", evidence)
        command = DISCOVERY.build_reviewer_codex_command(
            self.workspace,
            submission_root,
            review_dir,
            contract,
            {"model": "fake", "reasoning_effort": "low"},
        )
        self.assertIn("Before scoring, read the Problem README", command[-1])
        context_result = subprocess.run(
            [sys.executable, str(review_dir / "review"), "context"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(context_result.returncode, 0, context_result.stderr)
        self.assertEqual(json.loads(context_result.stdout)["objective_evidence"], context["objective_evidence"])
        self.assertEqual(json.loads(context_result.stdout)["baseline_root"], context["baseline_root"])

    def test_reviewer_placeholder_rubric_is_rejected(self) -> None:
        prompt = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation" / "reviewer_prompt.md"
        prompt.write_text("<!-- discovery-reviewer-rubric-placeholder -->\nReplace me.", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "still the template placeholder"):
            DISCOVERY.validate_ai_review_contract(
                self.workspace,
                {
                    "prompt": ".DiscoveryConsole/pub/evaluation/reviewer_prompt.md",
                    "prompt_digest": DISCOVERY.file_digest(prompt),
                    "dimensions": {"clarity": {"label": "Clarity"}},
                },
            )

    def test_reviewer_permissions_include_candidate_api(self) -> None:
        evaluation = self.workspace / ".DiscoveryConsole" / "pub" / "evaluation"
        prompt = evaluation / "reviewer_prompt.md"
        prompt.write_text("Score clarity.", encoding="utf-8")
        (evaluation / "API.md").write_text("Candidate API", encoding="utf-8")
        review_dir = self.workspace / ".DiscoveryConsole" / "private" / "eval_submissions" / "submission-a" / "review"
        submission_root = review_dir.parent
        (submission_root / "candidate").mkdir(parents=True)
        contract = json.loads(json.dumps(self.contract))
        contract["ai_review"] = {
            "prompt": ".DiscoveryConsole/pub/evaluation/reviewer_prompt.md",
            "dimensions": {"clarity": {"label": "Clarity"}},
        }
        overrides = DISCOVERY.reviewer_permission_overrides(self.workspace, submission_root, review_dir, contract)
        joined = " ".join(overrides)
        self.assertIn(str((evaluation / "API.md").resolve()), joined)
        baseline_root = self.workspace / ".DiscoveryConsole" / "pub" / "baseline"
        self.assertIn(f'{json.dumps(str(baseline_root.resolve()))}="read"', joined)
        self.assertNotIn(f'{json.dumps(str(baseline_root.resolve()))}="write"', joined)

    def test_reviewer_baseline_digest_changes_with_visible_material(self) -> None:
        baseline_root = self.workspace / ".DiscoveryConsole" / "pub" / "baseline"
        baseline_root.mkdir()
        (baseline_root / "baselines.json").write_text('{"method": {"metrics": {"score": 0.5}}}', encoding="utf-8")
        before = DISCOVERY.evaluation_baseline_digest(self.workspace)
        (baseline_root / "notes.md").write_text("reviewed comparator", encoding="utf-8")
        after = DISCOVERY.evaluation_baseline_digest(self.workspace)
        self.assertNotEqual(before, after)

    def test_human_dashboard_restores_private_rationale_without_republishing_it(self) -> None:
        submission_id = "submission-human-review"
        review_dir = self.workspace / ".DiscoveryConsole" / "private" / "eval_submissions" / submission_id / "review"
        review_dir.mkdir(parents=True)
        DISCOVERY.write_json(
            review_dir / "result.json",
            {"schema_version": 1, "dimensions": {"clarity": {"score": 8, "rationale": "The evidence is explicit."}}},
        )
        version = {
            "ai_review": {"dimensions": {"clarity": {"score": 8}}},
            "eval_run": {"submission_id": submission_id},
        }
        human_review = DISCOVERY.dashboard_human_ai_review(self.workspace, version)
        self.assertEqual(
            human_review,
            {"dimensions": {"clarity": {"score": 8, "rationale": "The evidence is explicit."}}},
        )
        self.assertEqual(version["ai_review"], {"dimensions": {"clarity": {"score": 8}}})
        self.assertNotIn("Private at this evidence level", DISCOVERY.DASHBOARD_JS)

    def test_reviewer_knowledge_browse_uses_singular_refs(self) -> None:
        review_dir = self.workspace / ".DiscoveryConsole" / "private" / "review-cli-test"
        review_dir.mkdir(parents=True)
        shutil.copy(Path(__file__).parents[2] / "subprojects-team" / ".team-template" / "reviewer" / "review", review_dir / "review")
        knowledge = self.workspace / ".DiscoveryConsole" / "pub" / "knowledge"
        DISCOVERY.write_json(knowledge / "items.json", {"paper": {"summary": "Paper"}})
        DISCOVERY.write_json(knowledge / "topics.json", {"methods": {"summary": "Methods"}})
        DISCOVERY.write_json(review_dir / "context.json", {"knowledge_root": str(knowledge)})
        result = subprocess.run(
            [sys.executable, str(review_dir / "review"), "knowledge", "browse"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        refs = {row["ref"] for row in json.loads(result.stdout)}
        self.assertEqual(refs, {"@item:paper", "@topic:methods"})

    def test_team_creation_requires_public_meaning_for_every_evaluation_channel(self) -> None:
        skill_root = MODULE_PATH.parents[2] / ".agents" / "skills" / "create-exploration-problem"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        protocol = (skill_root / "references" / "evaluation-system-protocol.md").read_text(encoding="utf-8")
        readiness = (skill_root / "references" / "problem-readiness.md").read_text(encoding="utf-8")
        staged = (skill_root / "references" / "staged-specialist-initialization.md").read_text(encoding="utf-8")
        template_root = MODULE_PATH.parents[2] / "subprojects-team" / ".team-template" / "problem" / ".DiscoveryConsole" / "pub"
        template_readme = (template_root / "README.md").read_text(encoding="utf-8")
        template_api = (template_root / "evaluation" / "API.md").read_text(encoding="utf-8")
        template_rubric = (template_root / "evaluation" / "reviewer_prompt.md").read_text(encoding="utf-8")
        reviewer_rules = (MODULE_PATH.parents[2] / "subprojects-team" / ".team-template" / "reviewer" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("what every objective metric and AI-review dimension means", skill)
        self.assertIn("how it bears on the central claim", protocol)
        self.assertIn("For Hybrid Evaluation", protocol)
        self.assertIn("sanitized objective", protocol)
        self.assertIn("session-authenticated Human Dashboard", protocol)
        self.assertIn("read-only public Baselines", protocol)
        self.assertIn("without inventing a total score", readiness)
        self.assertIn("AI rubric", staged)
        self.assertIn("every enabled objective metric and AI-review dimension", template_readme)
        self.assertIn("Reviewer receives only a sanitized", template_api)
        self.assertIn("discovery-reviewer-rubric-placeholder", template_rubric)
        self.assertIn("Public Baselines are read-only", reviewer_rules)


if __name__ == "__main__":
    unittest.main()
