# {problem_title}

Problem id: `{problem_id}`

Status: `{problem_status}`. This placeholder is not an approved research brief.

Before creating Routes, replace this file with the human-inspected objective,
real bottleneck, Candidate interface, evaluation design, optional-Baseline
frontier, feedback policy, resource contract, and freeze/stop condition. For
every enabled objective metric and AI-review dimension, explain what it
measures, how it relates to the central claim, its current importance, and the
costs, ranges, rubric anchors, limitations or counterevidence that qualify a
high value. For Hybrid Evaluation, explain which question each channel answers
and how conflicts are interpreted without a fixed total or weights. Use the
contract's `breakthrough`/`guardrail` roles without evaluator pass/fail gates.
Edit the description directly when practice changes the shared judgment. Complete
`evaluation/API.md`, prove and register the common evaluator, then activate it
through the Problem-construction Skill:

```bash
./discovery _control problem activate-eval {problem_id}
```
