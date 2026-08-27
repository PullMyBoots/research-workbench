import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "cli" / "knowledge_query.py"
SPEC = importlib.util.spec_from_file_location("discovery_knowledge_query_tests", MODULE_PATH)
assert SPEC and SPEC.loader
QUERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUERY)


class KnowledgeQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "knowledge"
        (self.root / "items").mkdir(parents=True)
        (self.root / "versions").mkdir()
        (self.root / "items.json").write_text(json.dumps({"paper": {"id": "paper", "title": "Paper", "summary": "Calibration source"}}), encoding="utf-8")
        (self.root / "topics.json").write_text(json.dumps({"review": {"id": "review", "title": "Review", "text": "Uses @item:paper twice: @item:paper.", "items": ["paper"]}}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_external_graph_is_local_deduplicated_and_limited(self) -> None:
        payload = QUERY.browse(root=self.root, scope_kind="problem", scope_id="p", view="external", sort="cited", limit=1)
        cards = [card for section in payload["sections"] for card in section["cards"]]
        self.assertLessEqual(payload["counts"]["returned"], 1)
        paper = QUERY.show(root=self.root, scope_kind="problem", scope_id="p", ref="@item:paper")
        self.assertEqual(paper["reference_count"], 1)
        self.assertEqual(paper["card"]["locator"]["path"], "items/paper/")
        self.assertTrue(cards)

    def test_problem_rejects_memory_and_qualified_refs(self) -> None:
        with self.assertRaisesRegex(ValueError, "not available"):
            QUERY.show(root=self.root, scope_kind="problem", scope_id="p", ref="@memory:main")
        with self.assertRaisesRegex(ValueError, "unqualified"):
            QUERY.show(root=self.root, scope_kind="problem", scope_id="p", ref="@item:other/paper")

    def test_baseline_is_a_problem_entity_with_metrics_locator_and_backlinks(self) -> None:
        (self.root / "versions" / "version-agent1-0001.json").write_text(
            json.dumps({
                "id": "version-agent1-0001",
                "agent": "agent1",
                "summary": "Adapted the comparator",
                "note": "Mechanism comparison against @baseline:strong-method.",
                "metrics": {"quality": 0.8},
            }),
            encoding="utf-8",
        )
        baseline_rows = [{
            "id": "baseline:strong-method",
            "method": "strong-method",
            "title": "Strong method",
            "summary": "Competitive reference candidate",
            "method_kind": "competitive_baseline",
            "status": "valid",
            "evidence_space": "validation",
            "contract_digest": "digest",
            "metrics": {"quality": 0.75},
            "metric_validity": {"quality": {"status": "valid", "reason": "reviewed comparator output"}},
            "locator": {"path": "baseline/strong-method/", "score_report": "evaluation/baselines.json"},
        }]
        contract = {
            "contract_digest": "new-digest",
            "compatible_contract_digests": ["digest"],
            "evidence_level": "L2",
            "metrics": {"quality": {"direction": "higher"}},
        }
        shown = QUERY.show(
            root=self.root, scope_kind="problem", scope_id="p", ref="@baseline:strong-method",
            contract=contract, baseline_rows=baseline_rows,
        )
        self.assertEqual(shown["card"]["metrics"]["quality"], 0.75)
        self.assertEqual(shown["card"]["metric_cards"]["quality"]["validity"], "valid")
        self.assertEqual(shown["card"]["metric_cards"]["quality"]["competitive_rank"], None)
        self.assertTrue(shown["card"]["cohort"]["comparable"])
        self.assertEqual(shown["card"]["locator"]["path"], "baseline/strong-method/")
        self.assertEqual(shown["referenced_by"], ["@version:version-agent1-0001"])
        payload = QUERY.browse(
            root=self.root, scope_kind="problem", scope_id="p", view="practice",
            contract=contract, baseline_rows=baseline_rows, limit=20,
        )
        baseline_section = next(section for section in payload["sections"] if section["id"] == "baseline_group")
        self.assertEqual([card["ref"] for card in baseline_section["cards"]], ["@baseline:strong-method"])
        self.assertEqual(baseline_section["cards"][0]["metric_cards"]["quality"]["competitive_rank"], {"rank": 1, "of": 1})

    def test_unreviewed_baseline_metric_is_visible_but_not_ranked(self) -> None:
        baseline_rows = [{
            "id": "baseline:suspicious",
            "method": "suspicious",
            "status": "pending_review",
            "evidence_space": "validation",
            "contract_digest": "digest",
            "metrics": {"quality": 999.0},
            "metric_validity": {"quality": {"status": "pending_review", "reason": "unexpected value under investigation"}},
        }]
        contract = {"contract_digest": "digest", "evidence_level": "L2", "metrics": {"quality": {"direction": "higher"}}}
        shown = QUERY.show(
            root=self.root, scope_kind="problem", scope_id="p", ref="@baseline:suspicious",
            contract=contract, baseline_rows=baseline_rows,
        )
        metric = shown["card"]["metric_cards"]["quality"]
        self.assertEqual(metric["reported_value"], 999.0)
        self.assertIsNone(metric["value"])
        self.assertIsNone(metric["competitive_rank"])

    def test_valid_metric_from_incompatible_baseline_is_reported_but_not_competitive(self) -> None:
        baseline_rows = [{
            "id": "baseline:old-space",
            "method": "old-space",
            "status": "valid",
            "evidence_space": "development",
            "contract_digest": "digest",
            "metrics": {"quality": 999.0},
            "metric_validity": {"quality": {"status": "valid", "reason": "reviewed historical value"}},
        }]
        contract = {"contract_digest": "digest", "evidence_level": "L2", "metrics": {"quality": {"direction": "higher"}}}
        shown = QUERY.show(
            root=self.root, scope_kind="problem", scope_id="p", ref="@baseline:old-space",
            contract=contract, baseline_rows=baseline_rows,
        )
        metric = shown["card"]["metric_cards"]["quality"]
        self.assertEqual(metric["reported_value"], 999.0)
        self.assertIsNone(metric["value"])
        self.assertFalse(shown["card"]["cohort"]["comparable"])

    def test_version_metric_cards_are_nested_by_metric_name(self) -> None:
        for index, value in enumerate((0.7, 0.8), start=1):
            (self.root / "versions" / f"version-agent1-000{index}.json").write_text(
                json.dumps({
                    "id": f"version-agent1-000{index}",
                    "agent": "agent1",
                    "summary": "Version evidence",
                    "metrics": {"quality": value},
                    "metric_directions": {"quality": "higher"},
                    "contract_digest": "digest",
                    "evidence_space": "validation",
                }),
                encoding="utf-8",
            )
        contract = {"contract_digest": "digest", "evidence_level": "L2", "metrics": {"quality": {"direction": "higher"}}}
        shown = QUERY.show(root=self.root, scope_kind="problem", scope_id="p", ref="@version:version-agent1-0002", contract=contract)
        metric = shown["card"]["metric_cards"]["quality"]
        self.assertEqual(metric["value"], 0.8)
        self.assertAlmostEqual(metric["raw_delta"], 0.1)
        self.assertNotIn("raw_delta", shown["card"]["metric_cards"])


if __name__ == "__main__":
    unittest.main()
