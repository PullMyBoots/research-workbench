# Headless Route Debug Eval

Follow every repair, evidence, completion, and boundary instruction in
`goals/route_debug_eval.md`. That file is authoritative; this wrapper adds only
the rules needed for unattended execution.

- Run without interactive nudges and treat `./explore context` plus the Runtime-
  injected job facts as authoritative current state.
- For a detached handoff or wait timeout, write the required Notebook
  Continuation Checkpoint and end this Turn. Runtime starts a fresh Debug Eval
  Turn only after the associated Job is terminal.
- Never resubmit an existing Job, reopen broad research, mix roles, or poll while
  Runtime owns waiting.
- End after the repaired formal Evaluation is queued, a legitimate continuation
  handoff is recorded, or the regular goal requires a stop. Do not invent
  another completion condition.
