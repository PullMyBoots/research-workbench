---
name: maintain-discovery
description: Maintain Main-owned Discovery state. Use when Main Agent must initialize, add, or remove Topic/Problem external Items and their syntheses; maintain Topic Main Memory or add a Memory Log; add or remove version-anchored Problem Notices; or verify knowledge integrity after those changes.
---

# Maintain Discovery

Maintain only state owned by Human/Main Agent. Read the root `AGENTS.md` first.
Do not modify Route notebooks, Versions, Evaluation results, Reflections, loop
state, jobs, or evaluator state.

## Knowledge model

```text
Topic: .DiscoveryProgram/knowledge/
  items/<item-id>/   complete external source bundles
  items.json         Item id, title, path, and about-200-word summary
  topics.json        Main-written review-style syntheses of external Items
  ../memory/main.md  Main Agent's cross-session briefing
  ../memory/logs/    immutable Topic Memory Log entities

Problem: subprojects-team/<id>/.DiscoveryConsole/pub/
  knowledge/items/<item-id>/   Problem-specific external source bundles
  knowledge/items.json         Problem Item index and summaries
  knowledge/topics.json        Problem-specific review-style syntheses
  baseline/baselines.json      scored Baseline entities and evidence locators
  baseline/                    Baseline implementations, adapters, and reports
  knowledge/versions/*.json    immutable formal Route practice
  notices.jsonl                later review findings and change notifications
```

Use `@item:<id>`, `@topic:<id>`, `@memory:<id>`, `@baseline:<id>`, and `@version:<id>` as stable
Wiki references. In a Problem, only local Item, Knowledge Topic, Baseline, and Version
references are valid; never use `@memory` or rely on Topic/other-Problem
fallback. In Topic/Main knowledge, unqualified references resolve only Topic
entities. Cite a Problem entity only as `@item:<problem-id>/<id>`,
`@topic:<problem-id>/<id>`, `@baseline:<problem-id>/<id>`, or `@version:<problem-id>/<id>`; `@memory:<id>` is
Topic-only and never qualified. Items and Knowledge Topics are external
support. A formal Version is practice evidence. A Memory Log records material
Main progress with references to supporting evidence.

Baseline entities are not added or deleted through `discovery maintain`.
Problem construction/Evaluator governance registers one real scored comparator
per entry in `pub/baseline/baselines.json`; the entry points to its existing
method material and score report instead of copying either into `knowledge/`.
Knowledge maintenance only validates and resolves those read-only entities.

## Minimal maintenance CLI

Use the public Main-maintenance surface below. It only makes atomic filesystem
and JSON/JSONL changes; it does not write summaries or syntheses:

```bash
./discovery maintain item add --scope topic --id <id> \
  --source <bundle> --metadata <metadata.json>
./discovery maintain item add --scope problem --problem <problem-id> \
  --id <id> --source <bundle> --metadata <metadata.json>
./discovery maintain item delete --scope topic --id <id>
./discovery maintain item delete --scope problem --problem <problem-id> --id <id>

./discovery maintain memory add --id <id> --file <memory-log.json>

./discovery maintain notice add --problem <problem-id> --id <id> --file <notice.json>
./discovery maintain notice delete --problem <problem-id> --id <id>

./discovery maintain check
./discovery maintain check --problem <problem-id>
```

Run maintenance from the Topic root. Add operations never overwrite an existing
id. Delete and rewrite explicitly when Human/Main intend to replace current
Main-owned knowledge.

After a maintenance change, use `$browse-discovery-knowledge` to confirm the intended reading surface without duplicating the query workflow here.

## External Items and syntheses

Choose the scope before importing:

- Put support for the overall research Topic in Topic Knowledge.
- Put support that directly serves one Problem in that Problem's Knowledge.

For an initial knowledge build, read the complete source set before deciding
the synthesis structure. For an incremental addition, read the existing
`topics.json` before deciding where the new Item belongs.

To add an Item:

1. Inspect the complete source bundle and exclude secrets, private evaluator
   material, hidden Validation/Test content, and irrelevant files.
2. Assign a stable id. Write metadata JSON containing non-empty `title` and an
   evidence-bearing summary of about 200 words: source identity, relevant
   claims or methods, applicability, and limitations.
3. Run `item add` with the source bundle and its metadata.
4. Read and directly edit the selected `topics.json`. Integrate the Item into
   one or more existing syntheses, or create a genuinely new synthesis. Write
   connected review prose explaining technical relationships, evidence,
   disagreements, failure modes, and implications. Never substitute a list of
   `@item` references for synthesis.
5. Run `check`.

To delete an Item:

1. Find every mutable and immutable reference before deleting.
2. Rewrite affected Knowledge Topics and Main Memory/Logs as coherent prose; remove
   only claims that lose support and preserve independently supported content.
3. Do not rewrite historical Versions. The CLI refuses deletion while a local
   Problem reference or a Main qualified reference to that Item remains.
4. Run `item delete`, which removes the registry entry and Item directory
   together, then run `check`.

Edit `topics.json` directly because synthesis is a language-and-judgment task,
not a JSON mutation task. The CLI validates the result.

## Topic Main Memory and Memory Logs

Before every new session, read `.DiscoveryProgram/memory/main.md` completely.
It is a Main-edited Markdown briefing with exactly the ordered headings
`目标与背景`, `元认知`, and `当前进展与文件索引`, and may not exceed 200
lines. Update “目标与背景” for project creation, material direction changes,
or durable Human constraints. When progress changes substantially, first add an
immutable Memory Log and then update “当前进展与文件索引”. Review “元认知”
after every new Log, but change it only when practice and experience support a
high-level judgment that will affect future decisions; retain its scope,
evidence, failure boundary, and uncertainty. Before each change, review the
Human request and explain the intended update.

Use `memory add` only with a JSON object containing exactly non-empty `summary`
and `report`. The CLI adds `id` and UTC `created_at`, atomically creates
`memory/logs/<id>.json`, and never overwrites or deletes a log. Do not record
routine commands, small fixes, one-off requests, speculation, or secrets. Use
`summary` for the concise progress change; use `report` for key data, evidence
locations, extracted experience, applicability, and limitations.

## Problem Notices

Add or delete a Notice only while all Routes in that Problem are stopped and no
Route Evaluation or queued/running Route job remains. Write JSON containing
`title`, `body`, and optional `priority` (`high` or `normal`) and `tags`.
`notice add` records `published_at` and the latest evaluated Version of every
Route as `version_anchor`; do not hand-write those fields.

Use Notice to tell every Route about a problem found during later review or a
new adjustment: what was found, what changed, why it changed, which evidence or
Versions motivated it, and what the Route should now notice. Routes read all
current Notices when starting a task.

README and Notice have different jobs. README is the complete description of
the subproblem; Notice records a later finding or change. When an adjustment
changes the complete task or metric description, edit README and publish the
Notice as two parts of the same update. Do not describe either one as overriding,
ranking above, or replacing the other. They must be mutually consistent.

A Notice may record an Evaluation change, invalid historical score, leakage
incident, new metric interpretation or changed research emphasis. Delete a
temporary Notice when it no longer helps. Never correct a Route by editing its
Notebook, Version or Reflection, and never turn a textual scientific judgment
into evaluator pass/fail code.

## Delegation

When a source bundle is large or synthesis-heavy, the Primary Agent may start
one clean Subagent and explicitly require `$maintain-discovery`. Give it one
bounded Item or one coherent initial-build batch. Do not delegate again when
already running as a Subagent. Only one Agent may write shared knowledge JSON
at a time; parallel readers may return drafts, but one writer performs the
integration and final `check`.
