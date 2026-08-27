# Staged Specialist Initialization

Use this protocol before creating `.DiscoveryConsole/pub/README.md` or any
search-route workspace.

This protocol initializes one already-authorized Team search space. It does not
define the surrounding Main research process and does not imply that every
Main subproject needs Routes.

## Public Research Brief

Create `.DiscoveryConsole/pub/README.md` as the complete public introduction to
the subproblem. Keep it concise and link to machine contracts. Include:

- the research objective, real bottleneck, central claim, and non-goals;
- candidate inputs, outputs, and runnable public entry points;
- the selected L1/L2/L3 design and public/validation/final boundaries;
- what every enabled objective metric and AI-review dimension means for the
  Problem and central claim; include objective roles/directions, AI rubric
  anchors, current relative priorities, and the costs, ranges, limitations or
  counterevidence that qualify a high value;
- for Hybrid Evaluation, which research question each channel answers and how
  conflicting evidence is interpreted without a fixed total or weights;
- the Evaluation calibration evidence and, when used, strong Baselines and the
  current Baseline Frontier;
- validation feedback rules, query discipline, freeze, and acceptance criteria;
- links to `pub/knowledge`, public development, baselines, and
  machine-readable eval contracts;
- the staged specialist initialization policy and route startup expectations.

Require human inspection before Route assignment or launch. Require every Route to read the
current brief and all current Notices before starting each task. Human/Main edit
README directly when the complete subproblem description changes. When later
review produces a problem finding or adjustment, publish a Notice describing
that change; if the complete description also changed, update README in the
same adjustment. These files have different functions and must agree. Keep
route-local tactics in route notebooks.

## Sequential Starter Loop

Build the initial portfolio sequentially:

1. After Human/Main have calibrated and accepted the Evaluation, use its
   objective metric roles/directions, AI-review dimensions/rubric when enabled,
   uncertainty, Provenance and the README's natural-language explanation as the
   initial evidence surface. When a Baseline Group is used, include its valid
   metric matrix and Frontier.
2. Assign the precreated `agent1` workspace as the central-contradiction specialist. Choose a credible
   mechanism aimed at a clear lead on the most important uncovered
   Breakthrough capability or a coherent primary attack set. Monitor and report
   the README's Guardrail concerns without turning them into automatic gates.
3. Give the starter a Candidate package and notebook, pass the Problem-owned
   public Check, submit through `./explore eval --candidate`, and record one
   controlled Bootstrap Practice Version from the registered evaluator.
4. Compare the evaluated starter against the current Team evidence and every
   strong Baseline when present. Diagnose meaningful gaps using the objective
   roles, AI-review dimensions, priorities and reasons described in README;
   ignore tiny noisy movements and interpret Guardrail costs in context.
5. Select the largest important gap not covered by any current route. Assign
   the next blank workspace around a materially different solution system
   specialized for that gap, then repeat the check/eval/comparison loop. Add
   another workspace only after the initial three are assigned and the same
   evidence justifies expansion.
6. Stop adding starters when the portfolio covers the central claim and its
   major uncovered gaps with credible specialists, important Guardrail
   tradeoffs have been reviewed, and the routes contain genuinely different
   mechanisms. Have the human inspect the portfolio, then start full parallel
   Builder search.

Use the full Team evidence surface, including the Baseline Frontier when
present, rather than only `agentN-1` when choosing the next weakness. A new
Route must have a plausible path to change the answer Main receives, not merely
improve one predecessor locally.

## Mechanism-Diversity Gate

Count a starter as a different solution system only when it changes one or more
substantive foundations such as signal source, representation, objective,
inductive bias, inference mechanism, or expected failure mode. Ask:

> If the core assumption of the existing route fails, can this starter still
> plausibly succeed?

Reject a new-route proposal when its distinction is mainly:

- a threshold, scalar, seed, parameter grid, or calibration constant;
- a wrapper, output rename, runtime cleanup, or packaging change;
- a small feature addition or local patch to the same core mechanism;
- a weaker clone whose only purpose is filling an agent slot.

Material hybridization can qualify when it changes the causal mechanism and has
public evidence. Starter diversity is an initialization policy, not permanent
method ownership: after parallel search begins, routes may pivot, replace, or
combine mechanisms when evidence supports the move.

## Controlled Validation Feedback

For L2/L3 initialization, use hidden validation only through the approved eval
interface. Return predeclared aggregate metrics and generic rank/trend feedback;
never inspect or expose hidden data, labels, paths, task rows, slices, or case
diagnostics. Record every bootstrap submission and the evidence that justified
it.

Treat aggregate validation metrics as adaptive selection feedback: they reduce
direct leakage but can still be overfit through repeated queries. Require a
public-development mechanism hypothesis and meaningful effect before reacting
to validation. Do not infer thresholds or hidden composition from metric
movements. For L3, never use sealed-final results to create, tune, select, or
redirect starters; run the sealed final only after candidate freeze.

## Portfolio Record

Maintain a compact initialization matrix containing:

- route and bootstrap practice version;
- solution-system hypothesis and material distinction;
- primary attack set and important Guardrail tradeoffs;
- current evidence gaps before creation, including Baseline gaps when present;
- public evidence, bootstrap objective/AI-review feedback, and validation query
  count;
- demonstrated specialty, failure modes, and the next uncovered gap.

Do not claim that a starter is leading without evidence. “Clearly leading” is a
pressure target; a failed attempt remains negative evidence for the next route.
