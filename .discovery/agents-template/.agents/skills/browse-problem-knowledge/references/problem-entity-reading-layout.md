# Problem entity reading layout

Public Problem knowledge is `pub/knowledge/{items.json,topics.json,versions/,items/}` plus `pub/baseline/baselines.json`. Browse provides compact Item, Topic, Baseline, and Version cards. `show @topic` returns synthesis prose; `show @item` points to the source bundle; `show @baseline` returns the scored comparator card and its `pub/baseline/` evidence locator; `show @version` returns metrics, Reflection locator, notebook archive, and frozen Git tag/commit. Open locators for complete original material rather than printing it through the CLI.
