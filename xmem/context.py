from __future__ import annotations

import json
from typing import Any, Dict, List

from .toon import compact


REGISTRY_TYPES = {"identity", "wiki.service", "wiki.repo", "wiki.domain", "wiki.project"}
RULE_TYPES = {"invariant", "rule", "guard"}
METHOD_TYPES = {"method", "playbook", "howto"}
EVIDENCE_TYPES = {"evidence.issue"}
ALIAS_TYPES = {"alias", "canonical-alias"}
RELATION_TYPES = {"relation", "link"}
CORRECTION_TYPES = {"correction", "alias-correction"}
MEMORY_TYPES = {"hook.memory", "memory"}


def build_context(query: str, current: Dict[str, Any] | None, cards: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    alias_cards = [c for c in cards if c.get("type") in ALIAS_TYPES]
    corrections = [c for c in cards if c.get("type") in CORRECTION_TYPES]
    registry = [c for c in cards if c.get("type") in REGISTRY_TYPES or str(c.get("type", "")).startswith("wiki.")]
    rules = [c for c in cards if c.get("type") in RULE_TYPES]
    methods = [c for c in cards if c.get("type") in METHOD_TYPES]
    relations = [c for c in cards if c.get("type") in RELATION_TYPES]
    evidence = [c for c in cards if c.get("type") in EVIDENCE_TYPES]
    memories = [c for c in cards if c.get("type") in MEMORY_TYPES]
    strong_alias = [c for c in alias_cards if c.get("status") == "verified" and float(c.get("score") or 0) >= 8]
    strong_correction = [c for c in corrections if c.get("status") in {"verified", "disputed"} and float(c.get("score") or 0) >= 8]
    strong_registry = [c for c in registry if c.get("status") == "verified" and float(c.get("score") or 0) >= 8]

    if not cards:
        resolution = "missing"
    elif strong_correction:
        resolution = "guided_by_correction"
    elif strong_alias:
        resolution = "guided_by_alias_card"
    elif len(strong_registry) == 1:
        resolution = "resolved"
    elif len(strong_registry) > 1:
        resolution = "ambiguous"
    else:
        resolution = "partial"

    warnings: List[str] = []
    if resolution in {"ambiguous", "guided_by_alias_card", "guided_by_correction"}:
        warnings.append("multiple verified registry candidates matched; do not assume a single project")
    if any(c.get("status") in {"inferred", "partial", "stale", "unknown", "disputed"} for c in cards[:5]):
        warnings.append("some top cards are not verified; use as hints only")

    next_reads = unique_paths(registry[:4] + rules[:3] + methods[:3] + memories[:3] + evidence[:3])
    packet = {
        "schema": "xmem.context.v1",
        "truth_policy": "files/code/runtime are truth; sqlite is generated index/cache",
        "query": query,
        "resolution": {
            "status": resolution,
            "do_not_assume_single_project": resolution in {"ambiguous", "guided_by_alias_card", "guided_by_correction"},
            "reason": resolution_reason(resolution, strong_registry, cards),
        },
        "current": current_brief(current),
        "corrections": [card_brief(c, i + 1) for i, c in enumerate(corrections[:5])],
        "alias_guidance": [card_brief(c, i + 1) for i, c in enumerate(alias_cards[:5])],
        "registry_candidates": [card_brief(c, i + 1) for i, c in enumerate(registry[:8])],
        "rules": [card_brief(c, i + 1) for i, c in enumerate(rules[:5])],
        "methods": [card_brief(c, i + 1) for i, c in enumerate(methods[:5])],
        "memories": [card_brief(c, i + 1) for i, c in enumerate(memories[:5])],
        "relations": [card_brief(c, i + 1) for i, c in enumerate(relations[:5])],
        "evidence": [card_brief(c, i + 1) for i, c in enumerate(evidence[:6])],
        "warnings": warnings,
        "next_reads": next_reads[:10],
        "latest_events": event_briefs(events),
    }
    return packet


def resolution_reason(resolution: str, strong_registry: List[Dict[str, Any]], cards: List[Dict[str, Any]]) -> str:
    if resolution == "missing":
        return "no indexed card matched query"
    if resolution == "guided_by_correction":
        return f"correction/dispute card matched: {strong_registry_label(strong_registry)}"
    if resolution == "guided_by_alias_card":
        return f"canonical alias guidance matched: {strong_registry_label(strong_registry)}"
    if resolution == "resolved":
        return f"one verified registry candidate matched strongly: {strong_registry[0].get('card_id')}"
    if resolution == "ambiguous":
        return f"{len(strong_registry)} verified registry candidates matched strongly"
    return "matched cards exist, but no single verified registry candidate is strong enough"


def strong_registry_label(strong_registry: List[Dict[str, Any]]) -> str:
    if not strong_registry:
        return "no strong registry candidate"
    return f"{len(strong_registry)} verified registry candidates still require disambiguation"


def current_brief(current: Dict[str, Any] | None) -> Dict[str, Any]:
    if not current:
        return {}
    return {
        "project_id": current.get("project_id", ""),
        "root": current.get("root", ""),
        "branch": current.get("branch", ""),
        "git_sha": current.get("git_sha", ""),
        "tech_stack": current.get("tech_stack", ""),
    }


def card_brief(card: Dict[str, Any], rank: int) -> Dict[str, Any]:
    aliases: List[str] = []
    try:
        aliases = json.loads(card.get("aliases_json") or "[]")
    except Exception:
        aliases = []
    return {
        "rank": rank,
        "id": card.get("card_id", ""),
        "project_id": card.get("project_id", ""),
        "type": card.get("type", ""),
        "truth": card.get("status", ""),
        "confidence": float(card.get("confidence") or 0),
        "score": float(card.get("score") or 0),
        "source": card.get("source", ""),
        "source_ref": card.get("source_ref", ""),
        "source_path": card.get("path", ""),
        "title": compact(card.get("title", ""), 96),
        "why": card.get("why", ""),
        "hints": card_hints(card.get("body", "")),
        "aliases": aliases[:8],
    }


def card_hints(body: str) -> List[str]:
    hints: List[str] = []
    if not body:
        return hints
    keys = ("summary:", "do_not_assume:", "resolution:", "must_include:", "forbid:", "wrong_aliases:", "canonical_aliases:", "effect:")
    capture = False
    for line in body.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(key) for key in keys):
            capture = True
            hints.append(stripped)
            continue
        if capture and (stripped.startswith("- ") or line.startswith("  ")):
            hints.append(stripped)
            if len(hints) >= 10:
                break
            continue
        if capture and stripped and not line.startswith(" "):
            capture = False
    return hints[:10]


def unique_paths(cards: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for card in cards:
        path = card.get("path") or card.get("source_ref")
        if path and path not in out:
            out.append(path)
    return out


def event_briefs(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in events:
        out.append({
            "ts": event.get("ts", ""),
            "event": event.get("event", ""),
            "project_id": event.get("project_id", ""),
            "card_id": event.get("card_id", ""),
        })
    return out
