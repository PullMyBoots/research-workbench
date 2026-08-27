# Exploration Team Readiness Contract

Use this reference while creating or auditing one Discovery Problem.

## 1. Intended Scope

A Problem is the bounded task contract for one optional exploration Team. It
owns one candidate interface, one evaluation-and-acceptance design, an optional
baseline frontier, one Practice namespace, and one multi-Route search space.
It is created because Human/Main chose Team search for this question, not
because every Topic must pass through a Problem stage.

Do not create a separate Problem merely because work has multiple metrics, modules, datasets, or implementation steps. Create one only when independent scoping improves evaluation fidelity, search focus, evidence interpretation, or integration ownership.

## 2. Target Workspace Shape

Adapt names to the installed Topic schema, but preserve these boundaries:

```text
subprojects-team/<problem-id>/
  problem.json                       # identity, status, dependencies, contract pointers
  .DiscoveryConsole/
    resources.json                   # Problem resource and scheduler policy
    pub/
      README.md                      # canonical Problem brief
      development_space/             # serious open laboratory
      evaluation/                    # Candidate API, contract, public Check
      baseline/                      # strong baseline artifacts and frontier
      knowledge/                     # Problem Items, Topics, and formal Route Versions
      log/                           # public operational logs
    private/
      validation_space/              # hidden iterative evaluation when selected
      test_space/                    # sealed final evidence when selected
      main_review/                   # Problem-private review material when needed
      evaluation_registry.json       # registered Problem-owned evaluator
      eval_submissions/              # immutable Candidate snapshots/reports
  agent1/ agent2/ agent3/             # initially blank Route workspaces
  agentN/                              # optional later Route when justified
```

Keep shared CLI code and templates, Topic external knowledge, Topic Main Memory,
and Program integration state outside an individual Problem. Keep each
Problem's resource policy, queue, leases, jobs, and Worker state inside that
Problem. Place each external Item and Knowledge Topic in the scope it directly
serves.

## 3. Access Matrix

| Material | Human/Main | Evaluator | Owning Routes | Other Problems' Routes |
|---|---:|---:|---:|---:|
| Problem public brief/development/baseline/knowledge | yes | yes | yes | only when explicitly published |
| Route-local candidate and notebook | review as authorized | submitted snapshot only | owning Route | no by default |
| Hidden validation | yes under policy | yes | no | no |
| Sealed final test | numeric observation and final publication evidence | yes | no | no |
| Topic/Main private notes | yes | no by default | no | no |

Verify access using effective filesystem permissions or sandbox mounts. A symlink is not an access-control mechanism. Do not place sensitive targets under a Route-readable root.

## 4. Problem Specification

Record at least:

- `problem_id`, title, status, and creation rationale;
- parent Topic and dependencies;
- real bottleneck, objective, central claim, and non-goals;
- expected Team answer and how Main will use it;
- Candidate package/interface;
- promised output artifact and integration interface;
- selected L1/L2/L3 design;
- public/private data boundaries and feedback policy;
- objective metric directions/roles and AI-review dimensions/rubric when
  enabled, plus an ordinary-language explanation of what each measures, how it
  relates to the central claim, which evidence matters more or equally and why,
  and which costs, ranges, rubric anchors, limitations or counterevidence
  deserve attention after Evaluation calibration;
- for Hybrid Evaluation, the distinct question answered by each channel and how
  conflicts are interpreted without a fixed total or weights;
- resource assumptions and formal-eval policy;
- whether Baselines are used and, when present, the set and frontier pointer;
- brief, knowledge, practice, and evaluator pointers;
- success, freeze, stop, and failure conditions.

Treat machine-readable metadata as canonical where values may change. Link to it from prose rather than copying mutable values.

## 5. Evaluation Readiness

At least one calibrated Evaluation channel is required. A Baseline is optional
unless the intended claim is comparative; without one, calibrate with a public
rubric, diagnostic Candidate, representative examples, or Human inspection.

Confirm that:

- exactly one Candidate file/directory is accepted per evaluation;
- the Candidate interface is runnable and documented publicly;
- every Route submits only `--candidate`; no Route chooses a scorer or report;
- the Problem-owned evaluator alone writes the private numeric Score Report;
- report metric keys exactly match the active public contract;
- every objective metric has a direction and a `breakthrough` or `guardrail`
  role in the machine contract and a natural-language meaning in README;
- every AI-review dimension has a public rubric and a README explanation of its
  research meaning, central-claim relationship, rubric anchors, current
  importance, and qualifying limitations or counterevidence;
- the Reviewer starts by reading README, Candidate API, contract, Candidate,
  rubric and relevant evidence space; in Hybrid Evaluation it also receives a
  sanitized objective evidence file only after the objective report validates;
- the Reviewer can read but not modify public Baselines, and Baseline changes
  after Candidate queueing invalidate that review run;
- the evaluator has no scientific threshold, Floor pass/fail, fixed weight or
  automatic acceptance gate;
- machine failure is limited to invalid Candidate, execution, safety or report
  production, while poor measured values remain valid evidence;
- the README identifies which objective metrics and/or AI-review dimensions best
  express the central claim rather than leaving Routes to infer priority from a
  convenient proxy; for Hybrid Evaluation it keeps both channels distinct and
  explains how to interpret conflicts;
- the selected calibration evidence challenges the data, metrics and Evaluator;
  when Baselines are used, representative strong Baselines have been run and
  anomalies investigated until the valid matrix is explainable to Human/Main;
- the Problem-owned public Check proves packaging/schema/interface behavior;
- full resources come from worker/system policy, not Route flags;
- one representative full evaluation has been benchmarked under that exact
  allocation; throughput, useful CPU/GPU utilization, peak memory and the main
  bottleneck are recorded, and materially idle allocation is reduced or
  justified;
- progress and best-effort ETA/heartbeat output exist for long evaluations;
- hidden feedback contains only approved aggregate information;
- the session-authenticated Human Dashboard shows every AI-review score and
  rationale at L1/L2/L3, while L2/L3 Routes continue to receive scores only;
- adaptive submissions are recorded;
- sealed-final resources are unreachable to Routes, and Test results or their
  implications cannot enter search feedback or selection.

## 6. Baseline Readiness When Used

When Human/Main choose a Baseline Group, confirm that it:

- includes established or otherwise strong locally runnable methods;
- is not chosen merely for ease of defeat;
- uses the same candidate and evaluation contract as Routes;
- records code/model/data versions and runnable provenance;
- reports all applicable metrics;
- records a per-metric `pending_review`, `valid`, `invalid` or
  `not_applicable` label with a reason for every reported value;
- has no `pending_review` value being used in a comparison or Frontier;
- establishes the current metric frontier and important uncovered gaps;
- exposes evaluator defects before team search begins.

## 7. Public Brief Readiness

The brief must let a new Route answer, without private access:

- What is the real Problem and central claim?
- What answer or decision is the Team expected to return to Main?
- What is explicitly out of scope?
- What artifact must I produce and how is it run?
- Which evaluation design and data boundaries apply?
- What does each objective metric and AI-review dimension mean, how does it
  relate to the central claim, which evidence currently matters more or equally
  and why, and which costs, ranges, rubric anchors, limitations or
  counterevidence deserve review?
- For Hybrid Evaluation, what question does each channel answer and how should
  conflicts be interpreted without inventing a total score?
- What calibration evidence supports the Evaluation, and what strong Baselines
  define the frontier when a Baseline Group is used?
- What public development, knowledge, and Practice evidence is available?
- What feedback will formal evaluation return?
- What resource constraints apply?
- What makes a candidate worth formal submission?
- What constitutes success, freeze, or stop?

Require human inspection before assigning or launching a Route.

## 8. Starter Portfolio Readiness

For every starter, record:

- the frontier gap that justified creation;
- its mechanism hypothesis and material distinction;
- primary attack set and important Guardrail tradeoffs to monitor;
- public evidence and initial development checks;
- shared Candidate contract and exact submission path;
- lightweight-check result;
- bootstrap Version and formal metrics;
- demonstrated specialty, failure modes, and remaining frontier gap.

Develop only `agent1` first for the central contradiction. Leave the other
precreated workspaces blank until the baseline-plus-team frontier justifies a
materially different Starter; the three directories are capacity, not a fixed
research roster. Require human inspection before full parallel launch.

## 9. Completion Gates

### Gate A: Problem Ready

- Problem identity and dependency metadata exist.
- Workspace namespace and access boundaries are enforced.
- Public/private spaces match the selected design.
- Candidate and evaluation contracts pass cheap validation.
- The selected Evaluation calibration is credible; when Baselines are used,
  their strong representatives have completed formal evaluation.
- The public brief is current and human-inspected.

### Gate B: Parallel Search Ready

- Every planned starter has a valid workspace and notebook.
- Every starter passes the lightweight check.
- Every starter has one controlled bootstrap Version.
- The portfolio covers the central attack set and major gaps.
- Important Guardrail tradeoffs have been reviewed and mechanisms are
  meaningfully diverse.
- Formal resources, jobs, ids, rankings, Practice, and logs remain scoped to the Problem.

If Gate A fails, do not create Routes. If Gate B fails, keep initialization sequential and do not claim that full parallel search is ready.
