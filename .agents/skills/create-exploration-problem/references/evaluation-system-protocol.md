# Registered Evaluation System Protocol

## Optional AI Reviewer

The registered evaluator may be objective-only, AI-review-only, or hybrid. All
three use the same Candidate snapshot, formal-evaluation Job, resource lease,
and Version. An AI Reviewer is a measurement channel: each public contract
dimension returns only an integer score from 1–10 and a non-empty rationale; it
must not emit a total score, weights, recommendation, or acceptance conclusion.
L1 releases score and rationale to Routes. L2/L3 release only scores to Routes
and keep rationales private from them. The session-authenticated Human Dashboard
shows both score and rationale at every level. Reviewer access is limited to the current Candidate,
the selected Development/Validation space, this Problem's `@item`/`@topic`
knowledge, and read-only public Baselines; it never reads Versions, memory,
another Problem, Test, or the network. Visible knowledge and Baselines are
digest-anchored when the Candidate is queued.
In Hybrid Evaluation, objective evaluation runs first. Only after its report
passes the metric contract does the Reviewer receive a sanitized objective
evidence file containing registered values, directions and roles; the raw
private report and diagnostics remain inaccessible.

This is the implementation contract for a Discovery Problem. Research choices
come from Human + Main Agent; this document specifies how to encode and prove
those choices.

## 1. Runtime Invariant

```text
one Problem
  -> one public Candidate contract
  -> one registered search-feedback evaluator
  -> one exact metric contract
  -> many Route Candidate submissions and optional Baseline submissions
```

The Route-facing command is always:

```bash
./explore eval -m "<candidate change brief>" --candidate <file-or-directory>
```

The Route cannot pass an eval command, report path, metric values,
metric definitions/directions, Development/Validation/Test choice, hidden parameters or
formal resources. If any task needs those flags, the Problem has not yet been
adapted to this protocol.

### Evaluation Calibration And Optional Baselines

Calibrate every Evaluation against evidence appropriate to the task. Use a
public rubric, diagnostic Candidates, representative examples, Human inspection
or a Baseline Group selected by Human/Main. When Baselines are used, construct
them and the Evaluation together: run representative strong methods and
diagnostic controls through the same path and interpret the resulting matrix.
Use anomalies to distinguish genuine method weakness from Baseline/Adapter
failure, unfaithful data, misleading metrics or Evaluator defects. Return
scientific choices to Human/Main, revise the responsible part and rerun affected
evidence until the Evaluation credibly measures progress and, when applicable,
the valid Baselines credibly describe current capability.

When Baselines are used, preserve surprising valid results and known
limitations instead of forcing familiar methods into an expected order. Only an
explainable, Human/Main-inspected calibration may be activated for Route search;
later contract changes are versioned and trigger the affected calibration runs.

## 2. Evidence-Space Mapping

| Level | Search feedback evaluator | Final reported evidence | Test callable by Route? |
|---|---|---|---|
| L1 | Development | Development | No separate Test exists |
| L2 | Validation | Validation | No |
| L3 | Validation | Sealed Test | No |

`./explore eval` uses Development for L1 and Validation for L2/L3. It never
executes L3 Test. Human/Main may run a current Candidate on L3 Test through a
separate controlled path and inspect objective numeric results. Those results
are publication evidence only: they must not be exposed to Routes or used for
method, Version, Candidate, parameter, mechanism or next-step selection.

## 3. Public Candidate Contract

Store the machine contract at:

```text
.DiscoveryConsole/pub/evaluation/contract.json
```

Required shape:

```json
{
  "schema_version": 1,
  "problem_id": "example-problem",
  "configured": true,
  "evidence_level": "L2",
  "candidate": {
    "kind": "directory",
    "max_files": 10000,
    "max_bytes": 1073741824,
    "reject_symlinks": true
  },
  "check": {
    "command": [
      "python3",
      ".DiscoveryConsole/pub/evaluation/check_candidate.py",
      "--candidate",
      "{candidate}"
    ],
    "cwd": "."
  },
  "metrics": {
    "quality": {
      "direction": "higher",
      "role": "breakthrough"
    },
    "runtime_seconds": {
      "direction": "lower",
      "role": "guardrail"
    }
  },
  "feedback": {
    "search_space": "validation",
    "information_budget": {
      "max_submissions_per_route": 20,
      "precision_decimals": 4,
      "released_fields": ["metrics"]
    }
  }
}
```

Rules:

- `problem_id` equals `problem.json.problem_id`.
- `evidence_level` is exactly `L1`, `L2` or `L3`.
- `candidate.kind` is `file`, `directory` or `file_or_directory`.
- Candidate packages contain no symlinks. Limits are positive integers.
- `check.command` is an argv array, never a shell string.
- The Check must contain `{candidate}` and be cheap, deterministic and public.
- Metric names are stable safe ids. Every objective metric has one direction
  and one semantic role: `breakthrough` measures progress on the intended
  capability; `guardrail` measures cost, reliability, retained capability or
  risk. Roles carry no weights and create no automatic pass/fail behavior.
  Metric meaning and current priority are explained naturally in the Problem
  README.
- Every AI-review dimension has a stable id and a public rubric. The Problem
  README explains what the dimension judges, how it bears on the central claim,
  what its rubric anchors mean, its current importance relative to the other
  evidence, and which limitations or counterevidence qualify a high score.
- Runtime may read an already-active legacy contract without roles so existing
  evidence remains usable. Every new or reactivated objective contract declares
  roles before activation.
- Do not add scientific `floor_gate`, `floor_passed`, `floor_status`, fixed
  metric weights or automatic acceptance logic to the evaluator contract.
- `feedback.search_space` is `development` for L1 and `validation` for L2/L3.
- `configured` stays false while any placeholder, unproven metric or evaluator
  remains.

The human-facing `API.md` must explain Candidate layout, entry point, inputs,
outputs, dependencies, schema, error behavior, public Check, objective metric
ids/directions/roles, AI-review dimension ids and rubric location, resource
assumptions, limits, feedback fields and examples, and link to README for their
research meaning. It must reveal
enough to implement a correct Candidate without revealing hidden cases or
private scoring logic.

After the Evaluator has been calibrated, the Problem's public README explains
what every enabled objective metric and AI-review dimension means for the
research question, how it relates to the central claim, which evidence currently
matters more or equally and why, and which costs, ranges, rubric anchors,
limitations or counterevidence qualify a high value. For Hybrid Evaluation it
also explains which question each channel answers and how conflicts are
interpreted without creating a fixed total, weights or automatic verdict.
Human/Main edit it directly as evidence changes.
When a later review finds a problem or creates an adjustment, Notice records
that change; if the complete description changes, README is updated at the same
time. Do not encode these judgments as machine pass/fail, universal weights or
a fixed priority template.

## 4. Public Lightweight Check

`check_candidate.py` is Problem-owned. It should verify, as cheaply as possible:

- required files and safe relative layout;
- declared entry point and dependency/import availability;
- input/output schema on public smoke fixtures;
- deterministic invocation and error semantics;
- resource-shape assumptions that can be checked cheaply;
- absence of forbidden embedded outputs, hidden-case keys or path escapes.

It must not emit a substitute formal Score Report. A passing Check proves only
that the Candidate can enter the scoring system.

## 5. Private Evaluator Registry

Store the registry at:

```text
.DiscoveryConsole/private/evaluation_registry.json
```

Required shape:

```json
{
  "schema_version": 1,
  "problem_id": "example-problem",
  "configured": true,
  "public_contract_digest": "<sha256-of-canonical-contract-json>",
  "evaluators": {
    "validation": {
      "id": "example-validation-v1",
      "command": [
        "python3",
        ".DiscoveryConsole/private/validation_space/evaluate.py",
        "--candidate",
        "{candidate}",
        "--report",
        "{report}"
      ],
      "cwd": "."
    }
  }
}
```

For L1 register `development`; for L2 register `validation`; for L3 register
`validation` and separately prepare `test`. Search uses only the first of those
roles. Evaluator argv must contain both `{candidate}` and `{report}`.

Supported substitutions are:

- `{candidate}`: private immutable Candidate snapshot;
- `{report}`: private evaluator-owned JSON output path;
- `{workspace}`: Problem root;
- `{agent}`: submitting Route name;
- `{submission_id}`: immutable submission id.

Commands are argv arrays so Route text never enters a shell. `cwd` is Problem-
relative or an absolute path inside the Problem workspace. Formal resources
remain in the owning Problem's `.DiscoveryConsole/resources.json`, not in Route
submissions.

Whenever the public contract changes, recompute its canonical SHA-256, update
the registry digest, version the evaluator/contract, rerun affected Baselines,
and decide how old Versions remain comparable. Discovery rejects a stale
nonempty digest.

## 6. Evaluator-Owned Report

The evaluator alone writes `{report}` as one JSON object:

```json
{
  "schema_version": 1,
  "metrics": {
    "quality": 0.82,
    "runtime_seconds": 41.7
  },
  "evaluator_provenance": {
    "evaluator_id": "example-validation-v1"
  }
}
```

The `metrics` keys must exactly equal the active public contract. Missing,
unknown and nonnumeric metrics fail the evaluation. Directions and
Breakthrough/Guardrail roles come from the contract; current research priority
and tradeoff interpretation come from the public README, never from the
Candidate or report. A correctly measured but scientifically poor value remains
valid evidence. Extra private diagnostic
fields may exist in the private report but must not be released unless the
feedback contract explicitly allows them.

The evaluator should provide deterministic seeds where meaningful, explicit
timeouts/errors, schema checks, resource accounting, progress/heartbeat for long
runs, repeated-run handling where required, and sufficient private Provenance
for Human/Main audit.

Before activation, run a representative full evaluation with the exact formal
allocation. Record throughput, useful CPU/GPU utilization, peak memory and the
main bottleneck. Use bounded parallelism when evaluation units are independent,
and reduce or justify materially idle allocation. CPU/GPU capacity should serve
useful work; memory is a measured limit with safety headroom, not a target to
artificially fill.

## 7. Submission And Job Lifecycle

Discovery performs this sequence:

1. Resolve Candidate inside the submitting Route.
2. Reject symlinks, unsupported objects and size/count limit violations.
3. Run the Problem-owned public Check on the Route Candidate.
4. Copy it to `.DiscoveryConsole/private/eval_submissions/<submission-id>/`.
5. Record file count, bytes and content digest; make the snapshot read-only.
6. Queue a sanitized public job containing no evaluator argv or private path.
7. Worker reloads the current registered evaluator and verifies the queued
   contract digest before execution.
8. The objective Evaluator, when configured, writes a private report/log and
   Discovery validates its exact metrics.
9. The AI Reviewer, when configured, reads the required project surfaces and,
   for Hybrid Evaluation, the sanitized objective evidence; it never reads the
   raw objective report. It may read public Baselines but cannot modify them.
10. Discovery publishes only contract-authorized aggregate Practice feedback
    with submission/candidate/contract Provenance.
11. Route code snapshot and Reflection proceed normally.

Queued submissions must be rejected if their contract digest no longer matches
the active contract. Candidate source edits after queueing never change the
private snapshot.

## 8. Baseline Equality When Used

When Human/Main choose a Baseline Group, every Competitive Baseline must be
packaged through the same Candidate adapter
and run by the same registered evaluator, dataset/space, resource contract and
report validator. Diagnostic controls may be labeled separately but do not
define the Competitive Frontier.

For every Baseline method and every metric value, record one review object with
`status` and `reason`. Use `pending_review` before Main has checked reference
value, `valid` for trustworthy measured evidence, `invalid` for an adapter,
execution, data or scoring failure, and `not_applicable` only when the metric
does not apply. Retain `valid but weak` and surprising valid values. Investigate
both implausibly high and implausibly low values before deciding.

Only `valid` values enter rankings and per-metric Baseline Frontier
calculations. For each metric select the best valid value in its declared
direction and preserve its source method. If mature Baselines behave
implausibly, return to calibration: identify whether the cause is the method,
adapter, data, metric or evaluator, apply only the evidence-supported repair or
version, and rerun before Route search. If several mature methods cannot form an
interpretable comparison on one metric, inspect the metric, data and Evaluator
before blaming every method.

For L3 publication claims, run the reported Candidate and claim-supporting key
Baselines through the same sealed Test contract. Human/Main may also inspect
Test numbers during exploration, but must not use them to select or modify a
Candidate, Baseline, Route, parameter, mechanism or exploration plan, and must
not pass them or their implications back to Routes.

## 9. Activation Checklist

Keep both public contract and private registry disabled until all are true:

- Candidate API and Check are documented and tested;
- evidence-level space mapping is correct;
- hidden resources are actually isolated;
- evaluator consumes Candidate snapshot and alone creates report;
- exact metric schema, directions and Breakthrough/Guardrail roles are tested;
- Hybrid execution validates objective metrics before exposing only sanitized
  registered values, directions and roles to the AI Reviewer;
- the Reviewer can read but not modify public Baselines, and queued review
  evidence is protected against later Baseline changes;
- evaluator failure is limited to Candidate, execution, safety and report
  validity failures, not scientific metric thresholds;
- feedback budget and released fields are enforced;
- the session-authenticated Human Dashboard exposes every AI-review score and
  rationale without changing the narrower L2/L3 Route feedback;
- resource policy and failure semantics are tested;
- representative full-load resource behavior is measured and the formal
  allocation is right-sized or explicitly justified;
- the selected calibration evidence is credible; when Baselines are used, they
  use the same path and their valid matrix and known anomalies can be reconciled
  with the central claim and the intended meaning of the metrics;
- public jobs/logs do not leak evaluator argv, paths, cases or private output;
- contract digest matches registry;
- Human has inspected the public brief and contracts, including objective
  metric roles, AI-review dimension meaning/rubric when enabled, current
  priorities, Guardrail interpretation and any Hybrid channel relationship.

Then switch `configured` flags together, rerun a minimal end-to-end Candidate,
and record the activation version/digest. Directory existence or a successful
toy Check is not activation evidence.
