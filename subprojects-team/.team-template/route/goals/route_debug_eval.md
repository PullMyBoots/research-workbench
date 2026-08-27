# Discovery Goal: Route Debug Eval

You are repairing a Candidate path rejected by the public lightweight Check.

Use:

- `pub/README.md`
- `AGENTS.md`
- `.agents/skills/explore-cli/SKILL.md`
- `.discovery/loop_state.json`
- `notebook.md`

## 背景与任务描述

Repair the failed submission path and resubmit the same Candidate:

```text
check failed -> inspect public log -> repair candidate or interface -> check passes -> formal eval queued
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
`eval_status: check_failed`. If eval is queued/running, in `main_review`, or
phase is `reflection_loop`, report the mismatch and stop.

Inspect `last_error` and its public Check job/log:

```bash
./explore context --job <job-id>
```

Use the Check job id/log from `active_eval` and `last_error`. Formal evaluator
failures and private diagnostics are Human/Main-owned and are not a Debug Eval
trigger.

### Repair Scope

Default to engineering repair, not a new research pivot. Appropriate work:

- fix candidate imports, dependencies, file paths, permissions, or stale output;
- fix Candidate packaging, entry point, imports or behavior required by the
  public Check and Candidate API;
- fix candidate interface assumptions exposed by the public Check;
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

1. The failure cause is diagnosed from reproducible public Check evidence, not
   guessed from symptoms.
2. The repair preserves the Candidate, metric, and evaluator contracts. If a
   contract or evaluator change is required, stop and hand it to Human/Main.
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
