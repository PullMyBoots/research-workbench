# Candidate and Evaluation API

This file is a required placeholder. Human and Main Agent must replace it with
the Problem-specific developer contract before setting `configured: true`.

Document exactly:

- what one Candidate file or directory contains;
- its entry point, inputs, outputs, schema, dependency and error behavior;
- the public lightweight Check and how to run it;
- every returned objective metric, direction and `breakthrough`/`guardrail`
  role, or each AI-review dimension and its public rubric, with a link to README
  for its meaning and current priority;
- which failures mean the Candidate/report could not be validly evaluated;
- whether formal search feedback is Development (L1) or Validation (L2/L3);
- submission limits and the feedback/information budget.

Routes submit only the Candidate:

```bash
./explore eval -m "candidate change brief" --candidate <file-or-directory>
```

They do not provide a scoring command, report, metric definitions, evaluation
space or formal resources. Discovery snapshots the Candidate and invokes the
registered objective evaluator, AI Reviewer, or both in one formal job. AI
review returns only per-dimension 1–10 scores and rationales; it creates no
total score, ranking, automatic gate, or recommendation. L1 publishes scores
and rationales to Routes; L2/L3 publish scores only to Routes, while the
session-authenticated Human Dashboard always shows both. In Hybrid Evaluation, the
objective report is validated first and the Reviewer receives only a sanitized
file of registered values, directions and roles, never the raw private report.

Do not encode research judgment as `floor_gate`, `floor_passed`, fixed metric
weights or an automatic acceptance score. The Problem README, after Evaluation
calibration, explains current objective and AI-review priorities, meanings and
tradeoffs. Poor measured values are
valid evidence; only Candidate, execution, safety or report-validity failures
make an Evaluation fail.
