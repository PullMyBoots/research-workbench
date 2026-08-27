# Discovery Runtime

`.discovery/` is the reusable Topic-level runtime. Team construction templates
live beside their instances in `subprojects-team/.team-template/`. A newly
cloned workbench has zero registered Problems; it becomes a Team runtime only
after Human/Main create and configure one.

```text
../discovery
cli/discovery.py
state/
tests/
../subprojects-team/.team-template/{problem,route,reviewer}/
```

## Public command surfaces

Human and Main Agent use:

```bash
./discovery start
./discovery doctor
./discovery maintain ...
```

- `start --problem <id>` validates and starts only the selected Problem Worker,
  starts the Dashboard and authenticated Route Broker, and refuses to run while
  another Problem still has a Worker, Route goal, queued job, or formal Eval.
- `doctor` diagnoses Topic/Problem structure, evaluator registration,
  knowledge integrity, Worker state, and Route sandbox availability.
- `maintain` performs deterministic Main-owned Item, Topic Memory Log, Problem
  Notice, and integrity operations. Main Memory prose remains a Main-Agent
  judgment task rather than a CLI-generated artifact.

Problem and Route construction are intentionally not daily public commands.
`create-exploration-problem` uses the private `_control` surface after Human and
Main Agent have completed the research design and readiness review.

`./discovery _control validate` is safe to run in the initial empty workbench.
`start` requires a registered, structurally valid Problem and intentionally
refuses to start before one exists.

Inside one Route workspace the visible CLI is exactly:

```bash
./explore context
./explore run [--queued] [--completion wait|detach] [--resources <request.json>] -- <command...>
./explore eval -m "<summary>" --candidate <candidate>
./explore reflect --version <id> --note <reflection.md> \
  --next-brief <next-plan.md>
```

Route mutations and queued work pass through the authenticated Unix-socket
Broker. Formal Evaluation is Worker-owned. A queued/running Eval is a waiting
state, not an active Codex goal.

## State and evidence

- Topic control state lives in `.DiscoveryProgram/`.
- Each Problem owns `problem.json` and `.DiscoveryConsole/{pub,private}`.
- Each Route owns its writable research workspace and read-only control files.
- `./explore eval` freezes the Candidate, queues the registered evaluator, and
  creates a formal Version only after a valid result.
- `./explore reflect` attaches supported practice to that Version, archives the
  previous Notebook, installs the next target brief, and returns to
  `work_loop`.

## Resource harness

Each Problem owns one policy at `.DiscoveryConsole/resources.json`. It defines
the default and optional per-Route free-run allocation, one queue capacity, one
fixed formal-Eval allocation, and the small scheduler policy. Small Route
commands use `./explore run`; large commands use
`./explore run --queued --resources ...`. Formal Eval resources cannot be
chosen by a Route.

Free runs, Dashboard Route goals, queued jobs, and formal Eval all use the same
Resource Runner. It derives thread variables from allocated CPUs, exposes only
allocated GPU IDs, and enforces CPU and memory with delegated cgroup v2 through
`systemd-run`. The Problem Worker starts every queued job that fits the
remaining capacity and keeps filling freed capacity. `doctor` fails closed when
the policy, host inventory, GPU IDs, or cgroup enforcement are invalid.

The Dashboard `Workers & Queue` view is the Human/Main interface for Worker
state, jobs, leases, host pressure, GPU state, cancellation, and log tails.

Development Jobs use only two completion modes. `wait` keeps the current Codex
Turn in one blocking wait (the foreground default); `detach` persists the Job
and returns its locator for a Notebook checkpoint and later fresh same-role
Headless re-entry (the queued default). A timed-out wait hands off the original
Job without submitting it again.
