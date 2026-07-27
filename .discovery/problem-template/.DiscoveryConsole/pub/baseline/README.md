# Baseline Group

This optional directory is the public locator when Human/Main choose a Baseline
Group. Otherwise leave `baselines.json` empty and calibrate Evaluation with the
evidence named in the Problem brief. When used, `baselines.json` registers each
scored comparator as a Problem-local knowledge entity:

```json
{
  "method-id": {
    "id": "method-id",
    "title": "Readable method name",
    "summary": "What the comparator is and why it matters",
    "method_kind": "competitive_baseline",
    "status": "valid",
    "evidence_space": "validation",
    "contract_digest": "the active evaluator digest",
    "metrics_source": {
      "path": "evaluation/results/baselines.json",
      "keys": ["methods", "method-id", "metrics"]
    },
    "metric_validity": {
      "metric-id": {
        "status": "valid",
        "reason": "Main review confirmed task, adapter, metric and run evidence",
        "evidence": "baseline/method-id/review.md"
      }
    },
    "locator": {"path": "baseline/method-id/"}
  }
}
```

`metrics_source.path` is relative to `.DiscoveryConsole/pub/`; `keys` walks
through that JSON to the metric object. Metrics may instead be embedded as a
`metrics` object when no separate score report exists.

`status` summarizes the Baseline method record. `metric_validity` is the
decision-bearing label for each reported value and uses `pending_review`,
`valid`, `invalid` or `not_applicable`. Each entry includes a reason and may
include an evidence locator. A missing label is treated as `pending_review`.
Only explicit `valid` values enter rankings and the best-per-metric Frontier.
Do not register a
synthetic best-per-metric row as a Baseline entity: one entity represents one
real Candidate/method evaluated under a declared contract.
