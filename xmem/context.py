from __future__ import annotations

import json
from typing import Any, Dict, List

from .toon import compact
from .source_check import source_freshness
from .util import list_after_key, normalize_text


REGISTRY_TYPES = {"identity", "wiki.service", "wiki.repo", "wiki.domain", "wiki.project"}
RULE_TYPES = {"invariant", "rule", "guard"}
METHOD_TYPES = {"method", "playbook", "howto"}
EVIDENCE_TYPES = {"evidence.issue"}
ALIAS_TYPES = {"alias", "canonical-alias"}
RELATION_TYPES = {"relation", "link"}
CORRECTION_TYPES = {"correction", "alias-correction"}
MEMORY_TYPES = {"hook.memory", "memory"}
STATUS_RANK = {"verified": 60, "partial": 40, "inferred": 30, "stale": 20, "unknown": 10, "disputed": 0}
SOURCE_RANK = {
    "local-card": 100,
    "card-file": 95,
    "issue-bug-patterns": 90,
    "issue-tracking-export": 82,
    "project-wiki-export": 78,
    "project-wiki": 70,
    "issue-tracking": 60,
}


def build_context(query: str, current: Dict[str, Any] | None, cards: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    cards = fuse_related_cards(cards)
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
    correction_guidance_items = correction_guidance(query, corrections)
    suggested_queries = canonical_queries_from_corrections(query, corrections)

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
    if correction_guidance_items:
        warnings.append("query matched a correction/dispute overlay; prefer canonical aliases and verify before editing")
    if any(c.get("status") in {"inferred", "partial", "stale", "unknown", "disputed"} for c in cards[:5]):
        warnings.append("some top cards are not verified; use as hints only")
    freshness = source_freshness()
    if freshness.get("status") != "fresh":
        warnings.append("source exports are newer than registry or registry is missing; run xmem sync before relying on this packet")

    next_reads = unique_paths(registry[:4] + rules[:3] + methods[:3] + relations[:3] + memories[:3] + evidence[:3])
    packet = {
        "schema": "xmem.context.v1",
        "truth_policy": "files/code/runtime are truth; sqlite is generated index/cache",
        "query": query,
        "resolution": {
            "status": resolution,
            "do_not_assume_single_project": resolution in {"ambiguous", "guided_by_alias_card", "guided_by_correction"},
            "reason": resolution_reason(resolution, strong_registry, cards, correction_guidance_items),
        },
        "current": current_brief(current),
        "suggested_queries": suggested_queries,
        "correction_guidance": correction_guidance_items,
        "corrections": [card_brief(c, i + 1) for i, c in enumerate(corrections[:5])],
        "alias_guidance": [card_brief(c, i + 1) for i, c in enumerate(alias_cards[:5])],
        "registry_candidates": [card_brief(c, i + 1) for i, c in enumerate(registry[:8])],
        "rules": [card_brief(c, i + 1) for i, c in enumerate(rules[:5])],
        "methods": [card_brief(c, i + 1) for i, c in enumerate(methods[:5])],
        "memories": [card_brief(c, i + 1) for i, c in enumerate(memories[:5])],
        "relations": [card_brief(c, i + 1) for i, c in enumerate(relations[:5])],
        "evidence": [card_brief(c, i + 1) for i, c in enumerate(evidence[:6])],
        "source_freshness": freshness_brief(freshness),
        "warnings": warnings,
        "next_reads": next_reads[:10],
        "latest_events": event_briefs(events),
    }
    return packet


def canonical_queries_from_corrections(query: str, corrections: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    out: List[str] = []
    for item in correction_guidance(query, corrections):
        for alias in item.get("canonical_aliases") or []:
            alias = str(alias or "").strip()
            if alias and alias not in out:
                out.append(alias)
            if len(out) >= limit:
                return out
    return out


def correction_guidance(query: str, corrections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    qnorm = normalize_text(query)
    out: List[Dict[str, Any]] = []
    for card in corrections:
        body = str(card.get("body") or "")
        wrong_aliases = list_after_key(body, "wrong_aliases")
        canonical_aliases = list_after_key(body, "canonical_aliases")
        if not wrong_aliases and not canonical_aliases:
            continue
        matched_wrong = [
            alias for alias in wrong_aliases
            if alias and (normalize_text(alias) in qnorm or qnorm in normalize_text(alias))
        ]
        if not matched_wrong and card.get("status") != "disputed":
            continue
        out.append({
            "id": card.get("card_id", ""),
            "truth": card.get("status", ""),
            "wrong_aliases": wrong_aliases[:6],
            "canonical_aliases": canonical_aliases[:6],
            "matched_wrong_aliases": matched_wrong[:6],
            "source_path": card.get("path") or card.get("source_ref", ""),
        })
    return out[:5]


def resolution_reason(
    resolution: str,
    strong_registry: List[Dict[str, Any]],
    cards: List[Dict[str, Any]],
    guidance: List[Dict[str, Any]] | None = None,
) -> str:
    if resolution == "missing":
        return "no indexed card matched query"
    if resolution == "guided_by_correction":
        if guidance:
            aliases = []
            for item in guidance:
                aliases.extend(item.get("canonical_aliases") or [])
            if aliases:
                return "correction/dispute matched; prefer canonical aliases: " + ", ".join(dict.fromkeys(aliases[:5]))
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
    brief = {
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
    supporting = card.get("_supporting_cards") or []
    if supporting:
        brief["supporting_cards"] = [supporting_card_brief(c) for c in supporting[:4]]
        brief["supporting_count"] = len(supporting)
    return brief


def supporting_card_brief(card: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": card.get("card_id", ""),
        "type": card.get("type", ""),
        "truth": card.get("status", ""),
        "confidence": float(card.get("confidence") or 0),
        "score": float(card.get("score") or 0),
        "source": card.get("source", ""),
        "source_ref": card.get("source_ref", ""),
        "source_path": card.get("path", ""),
        "title": compact(card.get("title", ""), 96),
    }


def freshness_brief(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": data.get("status", ""),
        "stale_exports": data.get("stale_exports", 0),
        "registry": data.get("registry", ""),
        "stale": [
            {"kind": item.get("kind", ""), "path": item.get("path", "")}
            for item in (data.get("stale") or [])[:5]
        ],
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
        for item in [card, *(card.get("_supporting_cards") or [])]:
            path = item.get("path") or item.get("source_ref")
            if path and path not in out:
                out.append(path)
    return out


def fuse_related_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    singles: List[Dict[str, Any]] = []
    for card in cards:
        key = fusion_key(card)
        if not key:
            singles.append(card)
            continue
        groups.setdefault(key, []).append(card)

    fused: List[Dict[str, Any]] = [dict(card) for card in singles]
    for members in groups.values():
        if len(members) == 1:
            fused.append(dict(members[0]))
            continue
        ordered = sorted(members, key=fusion_sort_key, reverse=True)
        primary = dict(ordered[0])
        max_score = max(float(item.get("score") or 0) for item in ordered)
        if max_score > float(primary.get("score") or 0):
            primary["score"] = max_score
        primary["_supporting_cards"] = [dict(item) for item in ordered[1:]]
        fused.append(primary)
    fused.sort(key=lambda item: (float(item.get("score") or 0), fusion_sort_key(item)), reverse=True)
    return fused


def fusion_key(card: Dict[str, Any]) -> str:
    title = normalize_text(card.get("title", ""), loose=False)
    if len(title) < 4:
        return ""
    family = fusion_family(str(card.get("type") or ""))
    if not family:
        return ""
    return f"{family}:{title}"


def fusion_family(card_type: str) -> str:
    if card_type in RULE_TYPES:
        return "rule"
    if card_type in METHOD_TYPES:
        return "method"
    if card_type in RELATION_TYPES:
        return "relation"
    if card_type in EVIDENCE_TYPES:
        return "evidence"
    if card_type in REGISTRY_TYPES or card_type.startswith("wiki."):
        return "registry"
    if card_type in ALIAS_TYPES:
        return "alias"
    if card_type in CORRECTION_TYPES:
        return "correction"
    if card_type in MEMORY_TYPES:
        return "memory"
    return ""


def fusion_sort_key(card: Dict[str, Any]) -> tuple[float, int, int, float]:
    return (
        float(card.get("score") or 0),
        STATUS_RANK.get(str(card.get("status") or "unknown"), 10),
        SOURCE_RANK.get(str(card.get("source") or ""), 50),
        float(card.get("confidence") or 0),
    )


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
