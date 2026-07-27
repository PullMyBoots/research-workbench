---
name: browse-discovery-knowledge
description: Browse or query Discovery Topic or one Problem knowledge; inspect Item, Knowledge Topic, Memory Log, Baseline, or Version cards; rank Problem practice by a declared metric, gain, citations, or Route history; or locate original Item material, Baseline evidence, and Git snapshots.
---

# Browse Discovery Knowledge

Choose exactly one scope. Never combine Topic and Problem cards, rankings, or hotness. Start external research from Knowledge Topics, then open their Items.

```bash
./discovery knowledge browse --scope topic --view external
./discovery knowledge browse --scope problem --problem <id> --view practice --metric <metric> --sort best
./discovery knowledge show @baseline:<problem-id>/<baseline-id>
./discovery knowledge show @version:<problem-id>/<version-id>
```

Browse Topic practice as Memory Logs. Problem practice contains scored Baselines and formal Route Versions; browse it by declared metric, gain, citation count, or Route before showing one entity. Cards are fast reading surfaces; use their locators to open Item bundles, Baseline reports, JSON, notebooks, or Git snapshots for detailed evidence. Citation count is local graph centrality, and a score or gain is not a quality verdict. Read `references/entity-reading-layout.md` when locating detailed evidence or interpreting the JSON envelope.
