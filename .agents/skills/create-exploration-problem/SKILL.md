---
name: create-exploration-problem
description: "Build, calibrate, register, verify, and hand off one optional exploration-team search space after Human/Main decide that a bounded, high-uncertainty, multi-route problem merits Team search. Use to create or audit the Problem workspace, Playground, knowledge, Candidate/Evaluation contract, optional Baselines, resources, initial Routes, isolation, and readiness; or migrate an existing Team to the registered Candidate-to-Evaluator protocol."
---

# Create Exploration Problem

Convert one Human/Main-agreed search assignment into an isolated, executable
exploration-team workspace. This Skill owns Team initialization; the root
`AGENTS.md` does not duplicate the construction sequence. Evaluation and
Baseline evidence may expose a design question that must return to Human/Main
for revision, so iterate or backtrack as needed instead of forcing an
incoherent first draft through the gates.

## Invocation Boundary And Main Ownership

An exploration Team is an optional research tool, not a mandatory stage of a
Topic. Use it only when the delegated question is important, has multiple
materially different plausible routes, admits a credible shared feedback
interface, and benefits enough from parallel search or independent audit to
justify setup and coordination costs.

Do not invoke this Skill for a clear implementation, routine repair, standard
reproduction, one-off diagnostic, or strongly sequential task that Main can
handle directly in `subprojects-main/<id>/`. That directory has no required
template or lifecycle. When one independent Agent needs a long-running local
practice and memory loop but parallel Team search is not justified, use
`create-single-agent-project` and `subprojects-single/<id>/` instead.

Human/Main retain responsibility for:

- choosing the real question and deciding that Team search is warranted;
- defining the intended answer, evidence standard, non-goals and resource
  boundary;
- preparing and approving the initial Team search environment through this
  Skill;
- reviewing Team progress, formal Versions, tradeoffs, failures and integrity;
- deciding whether to continue, redirect, expand, stop or freeze the Team;
- integrating the Team's evidence and candidates into the broader research
  outcome.

The Skill may implement and verify those decisions, but must not invent the
research objective, decide that a Team is needed, or replace Main's continuing
scientific review.

The finished runtime boundary is:

```text
Route Candidate -> ./explore eval -> Problem-owned Evaluator -> Score Report
```

Every Route submits only a Candidate. It must never choose the evaluator,
report, metrics, evidence space, hidden parameters or formal resources.

## Read Before Acting

1. Read the workspace root `AGENTS.md` completely.
2. Read [problem-readiness.md](references/problem-readiness.md) completely.
3. Read [evaluation-system-protocol.md](references/evaluation-system-protocol.md)
   completely before creating or changing evaluation files.
4. Before creating Routes, read
   [staged-specialist-initialization.md](references/staged-specialist-initialization.md)
   completely.
5. Use the private `./discovery _control` setup API only for the deterministic
   lifecycle operations named below. It is not a Human/Main daily CLI.

If the Human/Main design is incomplete, stop at the corresponding gate and
report the missing decision. Do not silently select a dataset, evidence level,
metric, feedback budget, public/private boundary, toolkit, Starter method or
Route count. Human/Main decide whether the task uses a Baseline Group. When it
does, they also select its methods. Breakthrough and Guardrail interpretation
may remain provisional until the Evaluation and its calibration evidence have
been examined in practice.

## Gate 0: Confirm The Team Assignment

Before creating files, record the jointly framed assignment and its evidence
source:

- why this question needs Team search instead of direct Main work;
- Problem id, objective, real bottleneck, central claim and non-goals;
- the answer or decision Main expects back from the Team;
- one Candidate package/interface and promised output artifact;
- L1, L2 or L3 evidence level and its actual space boundaries;
- Development data, hidden Validation data and any sealed Test data;
- initial objective metrics, directions and roles and/or AI-review dimensions
  and rubric anchors, plus the research questions each channel is intended to
  illuminate;
- feedback fields and Validation Information Budget;
- whether to use a Baseline Group and, when used, its strong Competitive
  Baselines and separate diagnostic controls;
- public, controlled and private Toolkit capabilities;
- resources, reproducibility, success, freeze, stop and acceptance conditions;
- knowledge scope and staged Starter-Route strategy.

Create one Problem for one Team assignment. If one Candidate and one Evaluation
cannot faithfully measure the delegated question, return to Human/Main to
revise or split the assignment. This Skill does not decide that research
decomposition itself.

Do not encode scientific acceptability as `floor_gate`, `floor_passed`, fixed
weights or evaluator-side pass/fail. Machine checks may reject only Candidates
or reports that cannot be safely and correctly evaluated. Scientific priority,
cost and risk remain Human/Main judgments expressed in the public README.

## Gate 1: Create The Problem Namespace

Inspect the Topic registry and existing files first. Preserve non-blank work and
Provenance. Then use the installed CLI:

```bash
./discovery _control problem create <problem-id> --title "<title>" --status scoping
```

Confirm the new Problem owns its `problem.json`,
`.DiscoveryConsole/resources.json`, `.DiscoveryConsole/pub`,
`.DiscoveryConsole/private`, unified Knowledge, logs, Baselines, evaluation
contract and submission storage. The command also renders three blank,
unassigned Route workspaces (`agent1`–`agent3`) from the single shared Route
template. Replace the template's safe resource values
with the already-agreed Problem policy, then require `discovery doctor
--problem <problem-id>` to validate the host, GPU ids, Eval-within-queue rule,
free/queue GPU separation, and cgroup enforcement. Confirm each Route will
receive only the Problem `pub` symlink. A symlink is not a security boundary;
verify actual sandbox, mount or permission isolation before storing hidden
resources. Route creation installs its broker credential mechanically; do not
hand-create or publish credentials.

Do not set `configured: true`, mark Problem Ready, assign a Starter, or launch a
Route merely because the blank workspaces exist.

The following modules are the Team-space construction process. They describe
initialization dependencies, not a mandatory lifecycle for the surrounding
research. Revisit an earlier module whenever evidence invalidates an assumption.

## Module A: Materialize Knowledge And Development Space

Implement only the agreed knowledge plan:

1. Main Agent builds the Problem's external knowledge from Items that directly
   serve this Problem. Put each complete source bundle directly under
   `knowledge/items/<item-id>/` and record its id, title, path, and summary
   once in `knowledge/items.json`.
2. Build all Problem Knowledge Topics in `knowledge/topics.json`; use stable
   `@item:<id>` references to synthesize multiple Items.
3. Do not create Problem Main Memory or a Problem `notes.json`. Let each formal
   Evaluation create one `knowledge/versions/<version-id>.json`; Main Agent
   records durable Topic conclusions in `.DiscoveryProgram/memory/`. In the
   Problem, cite only local `@item:<id>`, `@topic:<id>`, `@baseline:<id>`, and `@version:<id>`;
   do not inject Topic Main Memory or permit Topic/other-Problem fallback. The
   system-wide citeable Wiki entities are exactly `@item`, `@topic`, `@memory`,
   `@baseline`, and `@version`; Main cites this Problem from Topic knowledge with
   `@item:<problem-id>/<id>`, `@topic:<problem-id>/<id>`, or
   `@baseline:<problem-id>/<id>` or `@version:<problem-id>/<id>`.
4. Populate the public Development Space with real task data, documentation,
   schemas, examples and reproducible utilities.
5. Label smoke fixtures as smoke fixtures; never present them as research
   evidence.
6. Expose only approved public material. Audit symlinks and traversal paths.

Knowledge volume is not a readiness condition. Required decision-relevant
coverage and traceability are.

## Module B: Build The Registered Evaluation System

Follow [evaluation-system-protocol.md](references/evaluation-system-protocol.md)
exactly.

Implement these artifacts:

```text
.DiscoveryConsole/pub/evaluation/API.md
.DiscoveryConsole/pub/evaluation/contract.json
.DiscoveryConsole/pub/evaluation/check_candidate.py
.DiscoveryConsole/private/evaluation_registry.json
.DiscoveryConsole/private/eval_submissions/
```

Also implement the chosen evaluator code/resources in Development Space for L1,
Validation Space for L2/L3, and Test Space only for L3 final evidence.

Use the following calibration and proof process. Mechanical implementation may
proceed in order, but calibration evidence can send the work back to an earlier
research decision; do not activate an incoherent first draft merely because its
files and tests exist.

1. Freeze one public Candidate interface.
2. Make the cheap public Check reject malformed Candidates and accept a minimal
   valid Candidate without generating formal scores.
3. Implement one Problem-owned formal evaluator for the search feedback space.
4. Make the evaluator alone write the formal Score Report.
5. Declare every objective metric and/or AI-review dimension once in the public
   contract; do not accept Route overrides. Give every objective metric one
   semantic role: `breakthrough` for progress on the intended capability or
   `guardrail` for cost, reliability, retained capability or risk. Roles guide
   interpretation and never create weights, totals or automatic gates. Treat
   the initial objective metrics and AI-review dimensions as claims to be
   checked by calibration evidence, not as correct merely because they are
   encoded.
6. Register evaluator argv, cwd and public-contract digest privately.
7. Test Candidate snapshot immutability, metric-schema rejection, failure
   behavior, logging boundaries and resource enforcement. For Hybrid
   Evaluation, prove that objective evaluation succeeds and its report is
   validated before the Reviewer receives only registered metrics,
   directions and roles. Verify that the Reviewer can read but not modify
   public Baselines.
8. Benchmark a representative full evaluation under the exact formal resource
   allocation. Record throughput, useful CPU/GPU utilization, peak memory and
   the main bottleneck; use bounded parallelism for independent work, and
   reduce or justify materially idle allocation. Treat memory as a limit with
   headroom, not a utilization target.
9. Calibrate the Evaluation against evidence appropriate to the task. When a
   Baseline Group is used, run every Competitive Baseline through the same
   Candidate adapter, evaluator, resources and report schema intended for
   Routes, then audit every Adapter and metric value. Without a Baseline Group,
   use the agreed public rubric, diagnostic Candidates, representative examples
   or Human inspection.
10. Ask whether the data, Breakthrough/Guardrail metric roles, AI-review
    dimensions/rubric when enabled, and Evaluator together distinguish
    meaningful capability. When Baselines are used, also require the valid
    Baseline matrix to credibly represent current capability.
11. If the evidence exposes a scientific design choice, return it to
    Human/Main. Apply the agreed change to the relevant Baseline/Adapter, data,
    metric or Evaluator and rerun every affected result. Retain surprising but
    valid weak results; do not tune the contract merely to manufacture an
    expected ordering.
12. When Baselines are used, build the valid per-metric Baseline Frontier.
    Activate the public contract/private registry atomically with matching
    digest only after the selected calibration is explainable, all proofs pass
    and Human/Main review is complete:

    ```bash
    ./discovery _control problem eval-status <problem-id>
    ./discovery _control problem activate-eval <problem-id>
    ```

For an old workspace, preserve historical Route-local wrappers and results as
legacy evidence. Disable new formal submissions until one common Candidate
contract and evaluator are proven. Never relabel multiple Route-owned scorers as
one registered evaluator.

## Human Inspection

Publish the canonical Problem brief and link, rather than duplicate, the active
machine contracts. The Human must be able to inspect:

- objective, claim, Candidate and acceptance boundary;
- evidence level and public/private spaces;
- what every objective metric and AI-review dimension means for this Problem,
  how it relates to the central claim, which currently matter more or equally
  and why, and what costs, ranges, rubric anchors, limitations or counterevidence
  qualify a high value; for Hybrid Evaluation, which question each channel
  answers and how conflicts are interpreted without a fixed total or weights;
- the feedback budget and which objective or AI-review fields and rationales are
  released to Routes; the session-authenticated Human Dashboard always shows
  every AI-review score and rationale at L1, L2 and L3;
- the Evaluation calibration evidence;
- when Baselines are used, the Baseline Group, per-metric validity and Frontier,
  with one `pub/baseline/baselines.json` entry per real scored Baseline carrying
  a stable `@baseline:<id>`, concise summary, evidence space/contract, score
  source and locator into existing `pub/baseline/` material; never register a
  synthetic best-per-metric row;
- Toolkit surface, resources, knowledge and known limitations;
- initial Route strategy and stop/freeze conditions;
- the expected Team answer and how Main will review or integrate it.

Human/Main review and authorization happen in their ordinary research
conversation. Do not create a duplicate approval field, button or lifecycle
state. Continue only when the current instruction authorizes implementation;
that authorization does not repair missing evaluator, Baseline, isolation or
readiness evidence.

## Module C: Stage Initial Routes

Follow [staged-specialist-initialization.md](references/staged-specialist-initialization.md).

1. After evaluator activation, assign and develop only the precreated `agent1`
   workspace first; leave `agent2` and `agent3` blank.
2. Develop its Starter entirely inside ordinary Route-visible boundaries.
3. Package a minimal valid Candidate and submit it with:

   ```bash
   ./explore eval -m "bootstrap: agent1" --candidate <candidate>
   ```

4. Let the registered evaluator create the Bootstrap Version; compare it with
   the current Team evidence and, when present, all valid strong Baselines.
5. Assign the next blank workspace only for an important uncovered Frontier
   gap, using a materially different mechanism and expected specialty. Create
   `agentN` only when all three initial workspaces are assigned and another
   Route is justified.
6. Repeat Candidate Check and Bootstrap Eval for every Starter.
7. Stop adding Routes when the portfolio has useful Breakthrough coverage,
   reviewed Guardrail tradeoffs, mechanism diversity and proven submission
   paths.

Do not give a Route a local scoring wrapper. Do not inspect private evaluator
code while developing a Starter. Do not manufacture specialties by selecting
favorable metrics or hiding costs and regressions.

## Completion Audit

Run `./discovery _control validate`, public Checks, evaluator protocol tests,
applicable Baseline reproduction checks and access-boundary checks. Verify that a Route can write
its own workspace, read but not write `pub`, cannot read `private` or another
Route, and can complete one brokered mutation without gaining shared-state
write access. Verify separately that the AI Reviewer can read but not modify
public Baselines. Report two independent gates:

1. **Problem Ready**: knowledge/development space, Candidate contract,
   calibrated registered evaluator, resources, brief and isolation are
   implemented, tested and Human-inspected. When Baselines are used, their
   valid matrix and Frontier are also credible.
2. **Parallel Search Ready**: every selected Starter has a valid Candidate,
   Bootstrap Version, supported specialty and useful mechanism diversity.

Report paths, contract digest, evidence level, search feedback space, Baseline
status, Routes/Versions, tests, unresolved risks and both gate results. Never
claim readiness from directory existence, placeholder contracts, legacy
wrappers or unverified scores.

## Hand Off To Main Review

After initialization, return a compact handoff containing the public brief,
Candidate/Evaluation contract, resource policy, calibration evidence, any
Baseline Frontier, created Routes and Bootstrap Versions, Dashboard/knowledge
reading surfaces, unresolved risks, and both readiness results. Do not create
another mandatory Team-answer schema or duplicate lifecycle state.

Main then reviews the Team through its public state and formal evidence. The
Skill does not edit Route notebooks, Versions, Evaluation results or
Reflections on Main's behalf, and it does not keep adding Routes simply because
the template supports them. Re-enter this Skill only when Main authorizes a
construction-level change such as a new Route, revised search space, evaluator
migration or readiness re-audit.
