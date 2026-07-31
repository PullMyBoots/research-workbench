# Agent Notebook

This notebook is the Route's short-term memory for one complete iteration. The
Auditor creates it after reflecting on the previous Version; Builder and Debug
Eval extend it in place; the next Auditor archives it through `./explore
reflect` before opening the following iteration.

Keep the four chapters below, but choose the internal structure and writing
style that best explains the work. This is a complete experimental log, not a
brief status note or form-filling exercise. Low-schema means freedom of
presentation, not less information. Record enough commands, configurations,
code and result paths, observations, failed attempts, comparisons, decisions,
uncertainty, and reasoning for the Auditor to reconstruct the iteration and
write durable Version knowledge. Cite local knowledge as `@item:<id>`,
`@topic:<id>` and `@version:<id>` whenever it materially informs a claim or
decision.

## 1. Auditor Target Brief

Written by the Auditor, or by Main during cold start. State the evidence anchor,
current competitive position, diagnosed bottleneck, one next objective and its
ambitious pressure target, important Guardrail concerns, required evidence, failure or
reconsideration signals, and claims or shortcuts to avoid. Identify the
Candidate, public Check and formal submission interface. Diagnose what must be
achieved without prescribing the mechanism or first experiment; Builder owns
how to search.

## 2. Broad Exploration Log

Written by Builder in two parts.

### Process Notes

Record the materially different directions explored, where they came from,
public experiments and result paths, discoveries, decisions, reflection,
negative evidence, comparisons, and why one direction appears most valuable.
The trail must let an Auditor understand rejected alternatives and distinguish
a real mechanism from a lucky score, parameter sweep or wrapper change.

### Broad Exploration Confirmation

Explicitly confirm whether broad exploration has found the most valuable
breakthrough direction, and explain the reasons fully from the process notes and
evidence. If it cannot yet be confirmed, say why and continue broad exploration.
Enter Chapter 3 only after this confirmation and its rationale are written.

## 3. Convergence, Build, And Submission Log

Written by Builder in two parts before final Check and submission.

### Process Notes

Record how the promoted direction was implemented, mechanized, maximized and
hardened; the discoveries, decisions and reflection along the way; decisive
comparisons, ablations, sensitivity or boundary checks; priority-metric
behavior, Guardrail costs, tradeoffs and remaining uncertainty; and
reproducibility and review artifacts.

### Maximization Confirmation

Explicitly confirm whether the selected direction has been polished and pushed
to its plausible limit, and explain the reasons fully from the process notes and
evidence. If it cannot yet be confirmed, say why and continue maximizing and
hardening the direction. Run the final Candidate Check and formal submission
only after this confirmation and its rationale are written. Then record their
identities with enough code, command and result locators to reconstruct the
submitted work.

Do not begin this chapter merely because broad exploration is lengthy. Until a
materially changed direction earns promotion with meaningful public evidence,
continue Chapter 2. Negative exploration is valuable evidence but does not
justify repackaging or re-evaluating the incumbent.

## 4. Evaluation Failure And Debug Log

Written only when Check or formal Evaluation fails. Record the job or Check,
route-visible error and log locator, diagnosis, repair, verification, and every
resubmission identity. Append repeated failures chronologically. Do not expose
or infer private evaluator details. If Evaluation succeeds without recovery,
state briefly that no Debug Eval was required.
