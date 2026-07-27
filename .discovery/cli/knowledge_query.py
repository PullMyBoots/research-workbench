"""Read-only, scope-local Discovery knowledge queries.

This module deliberately has no workspace mutations or caches.  Callers provide
one knowledge root and (for Problems) the active evaluation contract/baselines.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REF_TOKEN = r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?"
REF_RE = re.compile(rf"@(?P<kind>item|topic|memory|baseline|version):(?:(?P<problem>{REF_TOKEN})/)?(?P<id>{REF_TOKEN})")


def _read(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _map(path: Path) -> dict[str, dict[str, Any]]:
    data = _read(path, {})
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def load(root: Path, kind: str) -> dict[str, dict[str, Any]]:
    if kind == "item":
        return _map(root / "items.json")
    if kind == "topic":
        return _map(root / "topics.json")
    if kind == "memory":
        answer: dict[str, dict[str, Any]] = {}
        for path in sorted((root.parent / "memory" / "logs").glob("*.json")):
            node = _read(path, {})
            if isinstance(node, dict) and node.get("id"):
                answer[str(node["id"])] = dict(node)
        return answer
    if kind == "version":
        answer: dict[str, dict[str, Any]] = {}
        for path in sorted((root / "versions").glob("version-*.json")):
            node = _read(path, {})
            if isinstance(node, dict):
                node = dict(node)
                node.setdefault("id", path.stem)
                answer[str(node["id"])] = node
        return answer
    return {}


def _scope_root(root: Path, kind: str, scope_id: str) -> dict[str, Any]:
    return {"kind": kind, "id": scope_id, "root": str(root)}


def _compact(text: Any, limit: int = 380) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _entity_text(kind: str, node: dict[str, Any]) -> str:
    if kind == "topic":
        return str(node.get("text") or "")
    if kind == "memory":
        return str(node.get("report") or "")
    if kind in {"baseline", "version"}:
        return "\n".join(str(node.get(key) or "") for key in ("summary", "note", "next_plan"))
    return str(node.get("summary") or "")


def _baseline_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    answer: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("baseline_id") or row.get("method") or row.get("id") or "")
        if entity_id.startswith("baseline:"):
            entity_id = entity_id.split(":", 1)[1]
        if not entity_id:
            continue
        node = dict(row)
        node["id"] = entity_id
        answer[entity_id] = node
    return answer


def graph(root: Path, scope_kind: str, baseline_rows: list[dict[str, Any]] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], list[str]]:
    kinds = ("item", "topic", "memory") if scope_kind == "topic" else ("item", "topic", "baseline", "version")
    baselines = _baseline_map(baseline_rows or [])
    entities = {f"{kind}:{entity_id}": node for kind in kinds for entity_id, node in load(root, kind).items()}
    if scope_kind == "problem":
        entities.update({f"baseline:{entity_id}": node for entity_id, node in baselines.items()})
    incoming = {key: set() for key in entities}
    warnings: list[str] = []
    for owner, node in entities.items():
        owner_kind = owner.split(":", 1)[0]
        raw = json.dumps(node, ensure_ascii=False)
        seen: set[str] = set()
        for match in REF_RE.finditer(raw):
            if match.group("problem"):
                # Qualified Topic references name another local knowledge graph;
                # do not leak them into this graph's centrality.
                continue
            target = f"{match.group('kind')}:{match.group('id')}"
            if target == owner or target in seen:
                continue
            seen.add(target)
            if target in incoming:
                incoming[target].add(owner)
            elif match.group("kind") in kinds:
                warnings.append(f"unresolved reference {match.group(0)} in @{owner_kind}:{node.get('id', '')}")
    return entities, incoming, sorted(set(warnings))


def _card(kind: str, node: dict[str, Any], root: Path, incoming: dict[str, set[str]]) -> dict[str, Any]:
    entity_id = str(node.get("id") or "")
    key = f"{kind}:{entity_id}"
    title = str(node.get("title") or node.get("summary") or entity_id)
    card: dict[str, Any] = {
        "entity_type": kind,
        "ref": f"@{kind}:{entity_id}",
        "title": title,
        "summary": _compact(node.get("summary") or _entity_text(kind, node), 900 if kind == "memory" else 380),
        "reference_count": len(incoming.get(key, set())),
        "locator": {},
    }
    if kind == "item":
        card["locator"] = {"path": f"items/{entity_id}/"}
        card["topic_refs"] = []
    elif kind == "topic":
        text = str(node.get("text") or "")
        item_refs = sorted(set(node.get("items", []))) if isinstance(node.get("items"), list) else []
        card.update({"lead": _compact(text, 380), "item_count": len(item_refs), "item_refs": [f"@item:{x}" for x in item_refs]})
        card["locator"] = {"path": "topics.json"}
    elif kind == "memory":
        card.update({"id": entity_id, "created_at": node.get("created_at")})
        card["locator"] = {"path": f"memory/logs/{entity_id}.json"}
    elif kind == "baseline":
        locator = node.get("locator") if isinstance(node.get("locator"), dict) else {}
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        card.update({
            "id": entity_id,
            "method_kind": node.get("method_kind") or node.get("kind") or "baseline",
            "status": node.get("status") or "unknown",
            "created_at": node.get("created_at"),
            "evidence_space": node.get("evidence_space") or node.get("space"),
            "metrics": metrics,
            "locator": {"path": "baseline/", **locator},
        })
    else:
        snapshot = node.get("snapshot") if isinstance(node.get("snapshot"), dict) else {}
        card.update({
            "id": entity_id,
            "route": node.get("agent"),
            "created_at": node.get("created_at"),
            "knowledge_status": "complete" if node.get("reflected_at") and node.get("summary") else ("awaiting_reflection" if node.get("knowledge_status") == "awaiting_reflection" else "legacy"),
            "locator": {"path": f"versions/{entity_id}.json", "notebook_archive": node.get("notebook_archive"), "eval_run": node.get("eval_run")},
            "snapshot": {key: snapshot.get(key) for key in ("repo", "commit", "tag", "tree", "parent") if snapshot.get(key) is not None},
        })
        if isinstance(node.get("ai_review"), dict):
            card["ai_review"] = node["ai_review"]
    return card


def _compatible_contract_digests(contract: dict[str, Any]) -> set[str]:
    current = str(contract.get("contract_digest") or "")
    declared = contract.get("compatible_contract_digests")
    compatible = {str(value) for value in declared} if isinstance(declared, list) else set()
    if current:
        compatible.add(current)
    return compatible


def _baseline_metric_review(node: dict[str, Any], metric: str) -> dict[str, Any]:
    reviews = node.get("metric_validity")
    review = reviews.get(metric) if isinstance(reviews, dict) else None
    if isinstance(review, dict):
        return review
    return {"status": "pending_review", "reason": "Main Agent review has not been recorded"}


def _baseline_cards(rows: list[dict[str, Any]], contract: dict[str, Any], incoming: dict[str, set[str]]) -> list[dict[str, Any]]:
    specs = contract.get("metrics") if isinstance(contract.get("metrics"), dict) else {}
    compatible_digests = _compatible_contract_digests(contract)
    current_space = "development" if contract.get("evidence_level") == "L1" else "validation"
    cards: list[dict[str, Any]] = []
    for node in _baseline_map(rows).values():
        card = _card("baseline", node, Path(), incoming)
        digest = str(node.get("contract_digest") or "")
        space = str(node.get("evidence_space") or node.get("space") or "")
        card["cohort"] = {
            "contract_digest": digest or None,
            "evidence_space": space or None,
            "comparable": bool(digest in compatible_digests and space == current_space),
        }
        card["metric_validity"] = node.get("metric_validity", {})
        for metric, value in card.get("metrics", {}).items():
            if not isinstance(value, (int, float)):
                continue
            spec = specs.get(metric) if isinstance(specs.get(metric), dict) else {}
            review = _baseline_metric_review(node, metric)
            eligible = review.get("status") == "valid" and card["cohort"]["comparable"]
            card.setdefault("metric_cards", {})[metric] = {
                "value": float(value) if eligible else None,
                "reported_value": float(value),
                "direction": spec.get("direction"),
                "validity": review.get("status"),
                "validity_reason": review.get("reason"),
                "validity_evidence": review.get("evidence"),
                "team_rank": None,
                "competitive_rank": None,
                "raw_delta": None,
                "gain": None,
            }
        cards.append(card)
    cards.sort(key=lambda card: card["ref"])
    return cards


def _version_cards(root: Path, scope_id: str, contract: dict[str, Any], baseline_rows: list[dict[str, Any]], incoming: dict[str, set[str]]) -> tuple[list[dict[str, Any]], list[str]]:
    current_digest = str(contract.get("contract_digest") or "")
    compatible_digests = _compatible_contract_digests(contract)
    current_space = "development" if contract.get("evidence_level") == "L1" else "validation"
    specs = contract.get("metrics") if isinstance(contract.get("metrics"), dict) else {}
    cards: list[dict[str, Any]] = []
    warnings: list[str] = []
    nodes = list(load(root, "version").values())
    for node in nodes:
        card = _card("version", node, root, incoming)
        digest, space = str(node.get("contract_digest") or ""), str(node.get("evidence_space") or node.get("space") or "")
        legacy = not digest
        card["knowledge_status"] = "legacy" if legacy and card["knowledge_status"] != "awaiting_reflection" else card["knowledge_status"]
        card["cohort"] = {"contract_digest": digest or None, "evidence_space": space or None, "comparable": bool(digest in compatible_digests and space == current_space)}
        cards.append(card)
    def sort_key(card: dict[str, Any]) -> tuple[str, str]:
        return (str(card.get("created_at") or ""), str(card.get("id") or card["ref"]))
    cards.sort(key=sort_key)
    by_route: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        by_route.setdefault(str(card.get("route") or ""), []).append(card)
    for metric, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        direction = str(spec.get("direction") or "higher")
        cohort = [card for card in cards if card["cohort"]["comparable"] and isinstance(load(root, "version").get(str(card.get("id")), {}).get("metrics", {}).get(metric), (int, float))]
        values = {str(card.get("id")): float(load(root, "version")[str(card.get("id"))]["metrics"][metric]) for card in cohort}
        ranks = {ident: 1 + sum((other > value if direction == "higher" else other < value) for other in values.values()) for ident, value in values.items()}
        comparable_baselines = [
            row for row in baseline_rows
            if str(row.get("contract_digest") or "") in compatible_digests
            and str(row.get("evidence_space") or row.get("space") or "") == current_space
            and isinstance(row.get("metrics"), dict)
            and isinstance(row["metrics"].get(metric), (int, float))
            and _baseline_metric_review(row, metric).get("status") == "valid"
        ]
        competitive_values = [*values.values(), *(float(row["metrics"][metric]) for row in comparable_baselines)]
        for card in cards:
            node = load(root, "version").get(str(card.get("id")), {})
            value = node.get("metrics", {}).get(metric) if isinstance(node.get("metrics"), dict) else None
            if not isinstance(value, (int, float)):
                continue
            metric_card = card.setdefault("metric_cards", {})
            previous = None
            if card["cohort"]["comparable"]:
                route_cards = [r for r in by_route.get(str(card.get("route") or ""), []) if r["cohort"]["comparable"] and sort_key(r) < sort_key(card)]
                for prior in reversed(route_cards):
                    prior_node = load(root, "version").get(str(prior.get("id")), {})
                    candidate = prior_node.get("metrics", {}).get(metric) if isinstance(prior_node.get("metrics"), dict) else None
                    if isinstance(candidate, (int, float)):
                        previous = float(candidate); break
            raw_delta = None if previous is None else float(value) - previous
            competitive_rank = 1 + sum((other > float(value) if direction == "higher" else other < float(value)) for other in competitive_values)
            metric_card.update({"value": float(value), "direction": direction, "team_rank": {"rank": ranks.get(str(card.get("id")), 0), "of": len(values)}, "competitive_rank": {"rank": competitive_rank, "of": len(competitive_values)}, "raw_delta": raw_delta, "gain": None if raw_delta is None else (raw_delta if direction == "higher" else -raw_delta)})
    if not current_digest:
        warnings.append("current contract has no digest; metric rankings are unavailable")
    return cards, warnings


def _attach_practice_ranks(cards: list[dict[str, Any]], contract: dict[str, Any]) -> None:
    specs = contract.get("metrics") if isinstance(contract.get("metrics"), dict) else {}
    for metric, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        direction = str(spec.get("direction") or "higher")
        comparable = [
            card for card in cards
            if card.get("cohort", {}).get("comparable")
            and isinstance(card.get("metric_cards", {}).get(metric, {}).get("value"), (int, float))
        ]
        values = [float(card["metric_cards"][metric]["value"]) for card in comparable]
        for card in comparable:
            value = float(card["metric_cards"][metric]["value"])
            rank = 1 + sum((other > value if direction == "higher" else other < value) for other in values)
            card["metric_cards"][metric]["competitive_rank"] = {"rank": rank, "of": len(values)}


def browse(*, root: Path, scope_kind: str, scope_id: str, view: str, query: str = "", metric: str | None = None, sort: str | None = None, route: str | None = None, limit: int = 20, contract: dict[str, Any] | None = None, baseline_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if view not in {"external", "practice"}:
        raise ValueError("view must be external or practice")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be from 1 to 100")
    entities, incoming, warnings = graph(root, scope_kind, baseline_rows)
    criteria = {"query": query, "metric": metric, "sort": sort, "route": route, "limit": limit}
    needle = query.casefold().strip()
    cards: list[dict[str, Any]]
    if view == "external":
        topics = [_card("topic", node, root, incoming) for node in load(root, "topic").values()]
        items = [_card("item", node, root, incoming) for node in load(root, "item").values()]
        for item in items:
            item["topic_refs"] = [topic["ref"] for topic in topics if item["ref"] in topic.get("item_refs", [])]
        source = topics + items
        cards = [card for card in source if not needle or needle in json.dumps(card, ensure_ascii=False).casefold() or needle in _entity_text(card["entity_type"], entities[card["entity_type"] + ":" + card["ref"].split(":", 1)[1]]).casefold()]
        cards.sort(key=lambda card: (-int(card["reference_count"]), card["ref"]) if sort == "cited" else (card["ref"],))
        sections = [{"id": "topics", "cards": [card for card in cards if card["entity_type"] == "topic"]}, {"id": "popular_items", "cards": [card for card in cards if card["entity_type"] == "item"]}]
        mode = "filtered" if needle else "home"
    elif scope_kind == "topic":
        cards = [_card("memory", node, root, incoming) for node in load(root, "memory").values()]
        cards = [card for card in cards if not needle or needle in json.dumps(card, ensure_ascii=False).casefold() or needle in _entity_text("memory", entities["memory:" + card["ref"].split(":", 1)[1]]).casefold()]
        cards.sort(key=lambda card: (-int(card["reference_count"]), card["ref"]) if sort == "cited" else (card["ref"],))
        sections = [{"id": "memory_logs", "cards": cards}]; mode = "filtered" if needle else "home"
    else:
        baseline_cards = _baseline_cards(baseline_rows or [], contract or {}, incoming)
        version_cards, metric_warnings = _version_cards(root, scope_id, contract or {}, baseline_rows or [], incoming); warnings.extend(metric_warnings)
        cards = baseline_cards + version_cards
        _attach_practice_ranks(cards, contract or {})
        if metric:
            cards = [card for card in cards if metric in card.get("metric_cards", {})]
        if route:
            cards = [card for card in cards if card.get("route") == route]
        if needle:
            cards = [card for card in cards if needle in json.dumps(card, ensure_ascii=False).casefold() or needle in _entity_text(card["entity_type"], entities[card["entity_type"] + ":" + str(card.get("id"))]).casefold()]
        if sort == "cited": cards.sort(key=lambda c: (-int(c["reference_count"]), c["ref"]))
        elif metric and sort in {"best", "gain"}:
            direction = str((contract or {}).get("metrics", {}).get(metric, {}).get("direction") or "higher")
            key = "value" if sort == "best" else "gain"
            cards.sort(key=lambda c: (not c.get("cohort", {}).get("comparable", False), c.get("metric_cards", {}).get(metric, {}).get(key) is None, -(c.get("metric_cards", {}).get(metric, {}).get(key) or 0) if (sort == "gain" or direction == "higher") else c.get("metric_cards", {}).get(metric, {}).get(key) or 0, c["ref"]))
        else: cards.sort(key=lambda c: (str(c.get("created_at") or ""), c["ref"]), reverse=True)
        if route: sections = [{"id": "results", "cards": cards}]; mode = "route"
        elif metric and sort == "best": sections = [{"id": "results", "cards": cards}]; mode = "metric"
        elif metric and sort == "gain": sections = [{"id": "results", "cards": cards}]; mode = "metric"
        elif sort == "cited": sections = [{"id": "popular_practice", "cards": cards}]; mode = "filtered"
        else:
            latest: dict[str, dict[str, Any]] = {}
            versions = [card for card in cards if card["entity_type"] == "version"]
            baselines = [card for card in cards if card["entity_type"] == "baseline"]
            for card in versions:
                if card.get("route") not in latest: latest[str(card.get("route"))] = card
            sections = [{"id": "baseline_group", "cards": baselines}, {"id": "metric_frontier", "cards": cards[:]}, {"id": "latest_by_route", "cards": list(latest.values())}, {"id": "popular_versions", "cards": sorted(versions, key=lambda c: (-int(c["reference_count"]), c["ref"]))}]
            mode = "filtered" if needle else "home"
    flat = [card for section in sections for card in section["cards"]]
    # Keep the envelope's limit global while retaining each home section as a
    # useful entry point.  Allocation is deterministic and does not persist.
    remaining = limit
    remaining_sections = len(sections)
    for section in sections:
        cap = (remaining + remaining_sections - 1) // remaining_sections
        section["cards"] = section["cards"][:cap]
        remaining -= len(section["cards"])
        remaining_sections -= 1
    returned = sum(len(section["cards"]) for section in sections)
    return {"schema_version": 1, "scope": _scope_root(root, scope_kind, scope_id), "view": view, "mode": mode, "criteria": criteria, "sections": sections, "counts": {"total": len(flat), "returned": returned, "truncated": len(flat) > returned}, "warnings": warnings}


def show(*, root: Path, scope_kind: str, scope_id: str, ref: str, contract: dict[str, Any] | None = None, baseline_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    match = REF_RE.fullmatch(ref.strip())
    if not match or match.group("problem"):
        raise ValueError("show requires an unqualified local @item, @topic, @memory, @baseline, or @version reference")
    kind, entity_id = match.group("kind"), match.group("id")
    permitted = {"item", "topic", "memory"} if scope_kind == "topic" else {"item", "topic", "baseline", "version"}
    if kind not in permitted:
        raise ValueError(f"{kind} is not available in this knowledge scope")
    entities, incoming, warnings = graph(root, scope_kind, baseline_rows)
    node = _baseline_map(baseline_rows or []).get(entity_id) if kind == "baseline" else load(root, kind).get(entity_id)
    if node is None:
        raise ValueError(f"reference not found: {ref}")
    card = _card(kind, node, root, incoming)
    if kind == "topic":
        card["text"] = str(node.get("text") or "")
        card["items"] = [_card("item", load(root, "item").get(item, {"id": item}), root, incoming) for item in node.get("items", []) if isinstance(item, str)]
    elif kind == "memory": card["report"] = str(node.get("report") or "")
    elif kind == "baseline":
        card = next((row for row in _baseline_cards(baseline_rows or [], contract or {}, incoming) if row.get("id") == entity_id), card)
    elif kind == "version":
        cards, extra = _version_cards(root, scope_id, contract or {}, baseline_rows or [], incoming); warnings.extend(extra)
        card = next((row for row in cards if row.get("id") == entity_id), card)
    key = f"{kind}:{entity_id}"
    refs = sorted({m.group(0) for m in REF_RE.finditer(json.dumps(node, ensure_ascii=False))})
    return {"schema_version": 1, "scope": _scope_root(root, scope_kind, scope_id), "ref": f"@{kind}:{entity_id}", "card": card, "references": refs, "referenced_by": ["@" + owner for owner in sorted(incoming.get(key, set()))], "reference_count": len(incoming.get(key, set())), "warnings": warnings}
