---
name: explore-cli
description: Use inside one Discovery Route workspace to read public Route context, run or queue development experiments, submit a Candidate, and record an evaluated Version reflection through the Route-only Broker client.
---

<!-- explore-cli-protocol: 9 -->

# Explore CLI — Route Client

Use only this Route-local executable:

```text
./explore context
./explore run
./explore eval
./explore reflect
```

`./discovery` is reserved for Human/Main control. Worker, evaluator setup,
knowledge/Notice mutation, and other-Route operations are unavailable here.

The Route is writable, shared Problem `pub` is read-only, and Problem `private`
is denied. Control-plane requests use the authenticated worker-owned file Broker.
If the Broker is unavailable, ask Human/Main to run `./discovery start --problem
<id>` from the Topic root; never request a broader sandbox.

## Start With Context

```bash
./explore context
./explore context --job <own-job-id>
```

The response contains the public Problem/Evaluation contract, Route loop state,
Notices, resource policy, own jobs, and recent own Versions. `--job` accepts
only a job owned by this Route. Use `$browse-problem-knowledge` for research
knowledge; Topic Main Notes are not available to Routes.

## Development Compute

```bash
./explore run -- python experiment.py
./explore run --resources small.json -- python experiment.py
./explore run --completion wait -- python experiment.py
./explore run --queued --completion wait --resources large.json -- python full_experiment.py
./explore run --queued --completion detach --resources large.json -- python full_experiment.py
```

Queued runs require an explicit resource JSON. Resource scheduling and
completion are orthogonal: an unspecified foreground run defaults to `wait`,
and an unspecified queued run defaults to `detach`. The Broker always creates a
stable persistent Job first.

Before non-trivial compute, reason like a researcher managing a shared machine.
Consider the workload shape, likely runtime, memory, I/O, and whether more CPU
workers or a GPU would actually shorten wall time. Use a cheap pilot, existing
progress, or prior runs when that would improve the decision. Small or poorly
scaling work belongs in the Route's free-run allocation; work that needs more
memory/GPU or can gain materially from additional parallel resources belongs in
the queued scheduler. Request a sensible amount and make the program's worker
count agree with it; neither request the maximum by habit nor leave an obviously
scalable long run on personal resources merely because it fits.

Choose the execution resources separately from how the Codex Turn waits. A
local run may still be detached when it is long, and a queued run may still be
waited for when it is predictably short. Use judgment from the actual program,
available capacity, observed progress, and the value of preserving the current
session.

During `wait`, the client streams real program output and emits a deterministic
runtime heartbeat with persisted status, activity, CPU, memory, process state,
supervisor state, and stdout quiet time. Treat these fields as the source of
truth. Quiet stdout is normal for many compute workloads and never proves a
stall. Do not declare a Job blocked while its persisted state is active and its
process/runtime heartbeat remains alive; use only a terminal Job state or an
explicit Runtime failure reason for that conclusion.

For scripts likely to take more than a few minutes, add or reuse truthful
progress information when feasible. Prefer phase, completed/total, elapsed time,
and an ETA based on observed work. If the operation is genuinely opaque, expose
useful stage changes and rely on the Runtime heartbeat rather than inventing a
percentage. Use this evidence to decide between `wait` and `detach`.

Prefer `wait` when queueing and execution should reliably finish within roughly
the useful session/cache window (normally about 25 minutes). Make one long
blocking wait; do not query status, logs, or
the terminal in a model polling loop. A terminal result continues the current
role. `next_action: continue_current_role` and `handoff_required: false` are
authoritative: the Job has returned, so inspect its result and keep working even
if an older Notebook checkpoint says it is active. Only
`next_action: checkpoint_and_end_turn` authorizes a handoff. If a wait window
returns that action with `handoff_required: true`, the same Job is still
running: write a Continuation Checkpoint in the current Notebook chapter and
end the current Turn without resubmitting it.

Prefer `detach` for longer or uncertain work, or when keeping the current Turn
alive has little value. Record the Job id, command,
resources, log/result locator, code state, expected output, post-completion
verification, and non-repeat boundary in that same Notebook checkpoint before
ending the Turn. TUI returns control to Human; Headless Campaign waits
mechanically and launches a fresh same-role Thread after all detached Jobs
finish, whether they succeed or fail. Job completion alone is not a role
completion marker. Never busy-poll and never create a duplicate Job after a
wait timeout or connection interruption.

Notebook text is a handoff record, not live Job state. On continuation, compare
every referenced Job with `./explore context --job <job-id>` and follow the CLI
status. A terminal status always supersedes stale words such as "running".

## Candidate Submission And Reflection

```bash
./explore eval -m "candidate change brief" --candidate candidate/
./explore reflect --version <version> --summary-file summary.md --note reflection.md --next-brief next.md
```

The eval message describes the submitted Candidate change, not the final
knowledge summary. The Auditor supplies the final summary during reflection.
Evaluation is Problem-owned: the Route cannot select metrics, evidence space,
hidden parameters, or formal resources. L3 Test never runs through normal Route
submission. Reflection is only for the current evaluated Version.

## Boundaries

- Use only this Route and the Problem public surface.
- Never read `.DiscoveryConsole/private` or another Route workspace.
- Never hand-edit state, jobs, Versions, external knowledge, evaluator registry,
  reports, metrics, or feedback.
