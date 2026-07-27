# Discovery entity reading layout

Topic knowledge is `.DiscoveryProgram/knowledge/{items.json,topics.json,items/}` plus `.DiscoveryProgram/memory/{main.md,logs/}`. A Problem uses `.DiscoveryConsole/pub/knowledge/{items.json,topics.json,versions/,items/}` plus `.DiscoveryConsole/pub/baseline/baselines.json`. Item cards lead to `items/<id>/`; Topic and Memory Log `show` include prose; Baseline cards expose metrics and point into `pub/baseline/` and their score report; Version cards lead to `versions/<id>.json` and expose notebook/eval/Git locators.

Use `browse` for compact cards and deterministic rankings, then `show @ref` for one entity. Read Item files, Baseline evidence, and Version JSON directly when complete source, evaluation, Reflection, or snapshot provenance is needed. Main resolves Problem entities as `@type:<problem-id>/<id>`; unqualified references resolve in Topic knowledge only.
