# Discovery Goal: Route Debug Eval

You are repairing a failed formal eval for this search-route workspace.

Use:

- `pub/README.md`
- `AGENTS.md`
- `.agents/skills/explore-cli/SKILL.md`
- `.discovery/loop_state.json`
- `notebook.md`

## 背景与任务描述

Repair the failed formal-eval submission path and resubmit the same candidate
for queued formal eval:

```text
formal eval failed -> inspect error/log -> repair candidate or interface -> eval check passes -> formal eval queued again
```

The only successful completion marker is:

```text
eval check passes and `./explore eval` returns a queued formal-eval job id
```

### Long-Job Rule

Use the shared `attached_wait`/`detached_handoff` protocol for public repair
diagnostics. A normal Job result resumes Debug Eval only; it never crosses to
Auditor. On handoff or wait timeout, append a Continuation Checkpoint in the
current Notebook chapter and end the Turn without duplicate submission.

### Required Startup

Read the required files, then run:

```bash
./explore context
```

Confirm that `.discovery/loop_state.json` says `phase: work_loop` and
`eval_status: failed` or `eval_status: check_failed`. If eval is `queued` or
`running`, stop and report the active job id. If phase is `reflection_loop`, stop
and tell the human to use the Auditor goal.

Inspect `last_error`, `active_eval`, the job status, and the job log:

```bash
./explore context --job <job-id>
```

Use the job id from `active_eval.job` when present. Formal evaluator details are
private; if public status is insufficient, ask Human/Main Agent to inspect the
private diagnostics. If there is no active eval job because the lightweight
Check failed, inspect the public Check log.

### Repair Scope

Default to engineering repair, not a new research pivot. Appropriate work:

- fix candidate imports, dependencies, file paths, permissions, or stale output;
- fix Candidate packaging, entry point, imports or behavior required by the
  public Check and Candidate API;
- fix candidate interface assumptions exposed by the formal job;
- fix resource-contract mismatches by making the Candidate entry point consume
  `DISCOVERY_CPUS`, `DISCOVERY_MEMORY_GB`, `DISCOVERY_GPUS`, and
  `CUDA_VISIBLE_DEVICES`;
- add clear early failures for impossible memory/runtime/resource cases;
- append to Chapter 4 of `notebook.md` the failure diagnosis, route-visible
  evidence, repair, check command, and resubmission job id. Preserve earlier
  failed attempts chronologically instead of rewriting them.

Do not use private evaluator material. Do not hand-enter metrics. Do not
perform a new mechanism-level research pivot unless the error proves the current
candidate is structurally invalid and cannot be repaired.

## 完成定义与验收

This Goal succeeds only when every item below is true:

1. The failure cause is diagnosed from reproducible evidence in the check or
   formal-job logs, not guessed from symptoms.
2. The repair preserves the same Candidate contract, metric contract,
   and evaluator unless the evidence proves that interface repair is required.
3. The Problem-owned lightweight Check accepts the packaged Candidate.
4. `./explore eval` returns a new queued formal-eval job id.
5. `notebook.md` records the failure, evidence, repair, verification command,
   new job id, and log path.

Queueing the repaired formal eval is the stopping point. Do not wait for the
worker-run result and do not claim success for a local repair that was not
resubmitted.

### Resubmission

Run the same simple formal eval command from `notebook.md` or the failed job
metadata, normally:

```bash
./explore eval -m "candidate change brief" --candidate candidate/
```

`./explore eval` runs the lightweight check first and queues formal eval only if
the check succeeds. When it returns a queued job id, record the id/log in
`notebook.md`, report that formal eval has been queued again, and stop. Do not
poll long queued/running jobs unless the human explicitly asks.

## 工作范围与约束注意事项

- This role repairs the failed submission path; it does not reopen broad Builder
  exploration or optimize the research mechanism.
- Use only public route resources and legitimate job/check logs. Never inspect,
  infer from, or optimize against `.DiscoveryConsole/private`.
- Do not hand-enter metrics, fabricate a report, bypass the lightweight check,
  replace the evaluator, or tune against formal eval feedback.
- Route code may consume worker-provided resource environment variables, but the
  route does not choose formal-eval CPU, GPU, memory, worker, or parallelism
  settings.
- A mechanism-level pivot is outside this role unless the failure evidence proves
  the submitted candidate is structurally invalid and cannot be repaired; report
  that condition rather than silently starting a new research loop.
- If eval is already queued/running or phase is `reflection_loop`, report the
  mismatch and stop rather than resubmitting or performing Auditor work.
