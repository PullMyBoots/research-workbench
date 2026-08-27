# Problem DiscoveryConsole

This directory contains one Problem's public search surface and private evaluator/control surface.

- `resources.json` is the only resource-policy source for this Problem.
- `pub/` is visible to this Problem's Routes.
- `private/` is restricted to the Human, Main Agent, and authorized evaluator.
- `pub/knowledge/items/<id>/` contains one complete external source bundle.
- `pub/knowledge/items.json` and `topics.json` contain the Item summaries and
  Main-written syntheses. Main Notes exist only in Topic Knowledge.
- `pub/knowledge/versions/<id>.json` contains formally evaluated Route practice.
