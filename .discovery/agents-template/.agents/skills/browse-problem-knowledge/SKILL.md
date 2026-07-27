---
name: browse-problem-knowledge
description: Browse the current Discovery Problem's public Items, Knowledge Topics, Baselines, and Versions; inspect metric rankings, gains, citations, Route history, original public materials, Baseline evidence, and frozen Git snapshots without accessing Topic or other-Problem knowledge.
---

<!-- knowledge-query-protocol: 1 -->

# Browse Problem Knowledge

The current Problem is the only scope. Begin external reading with Topics and practice reading with a declared metric or a Route history.

```bash
./explore knowledge browse --view external
./explore knowledge browse --view practice --metric <metric> --sort gain
./explore knowledge show @baseline:<id>
./explore knowledge show @version:<id>
```

Use cards for selection, then open `pub/knowledge/`, `pub/baseline/`, Version JSON, notebook archives, or the Git locator for complete evidence. Scores, gains, and citation counts are reading aids, not conclusions. Never request Topic, another Problem, or qualified references. Read `references/problem-entity-reading-layout.md` for stable fields and locators.
