# Main Agent Skills

This directory is for the main Codex agent that works with the human.

Main-agent skills:

- `browse-discovery-knowledge`: read one Topic or Problem knowledge scope and
  inspect its externally sourced or evaluated evidence.
- `create-exploration-problem`: after Human/Main choose Team search for one
  bounded problem, prepare, validate, bootstrap, and hand off its search space.
- `create-single-agent-project`: initialize one named long-running single-Agent
  project with only its Human-provided goal and local research-memory skeleton.
- `maintain-discovery`: maintain Main-owned external Items and syntheses,
  Topic Main Memory/Memory Logs, and version-anchored Problem Notices.

Main handles direct work in blank, freely structured `subprojects-main/<id>/`
folders. A long-running independent Project Agent uses
`create-single-agent-project` and `subprojects-single/<id>/`. Do not invoke
`create-exploration-problem` merely because either kind of project exists.

Main Agent owns external Items and Knowledge Topics at both Research Topic and
Problem scope, and writes Topic-only Memory Logs from local and evaluated
evidence after Human approval. Routes contribute practice only through formal
Versions. The shared
Wiki uses exactly `@item`, `@topic`, `@memory`, and `@version` references: Route
contexts resolve only their local Problem entities, while Main cites Problem
entities from Topic knowledge with `<problem-id>/<id>` qualification.

Search-route shared rules live in `.discovery/agents-template/AGENTS.md`.
Builder and Auditor role contracts live in `.discovery/agents-template/goals/`.
The Route-only CLI Skill remains at
`.discovery/agents-template/.agents/skills/explore-cli`; it is distinct from
Main Agent maintenance and exposes only `context`, `run`, `eval`, and `reflect`.
