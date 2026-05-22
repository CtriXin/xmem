from __future__ import annotations

import json
from typing import Any, Dict, List

from .context import (
    EVIDENCE_TYPES,
    METHOD_TYPES,
    REGISTRY_TYPES,
    RULE_TYPES,
    build_context,
    card_brief,
    event_briefs,
    fuse_related_cards,
    unique_paths,
)
from .toon import compact
from .util import field_from_text, list_after_key


ISSUE_PATTERN_SOURCES = {"issue-bug-patterns", "issue-tracking-export"}


def build_preflight(query: str, current: Dict[str, Any] | None, cards: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    context = build_context(query, current, cards, events)
    fused = fuse_related_cards(cards)
    registry = [c for c in fused if c.get("type") in REGISTRY_TYPES or str(c.get("type", "")).startswith("wiki.")]
    rules = [c for c in fused if c.get("type") in RULE_TYPES]
    methods = [c for c in fused if c.get("type") in METHOD_TYPES]
    evidence = [c for c in fused if c.get("type") in EVIDENCE_TYPES]
    issue_patterns = [c for c in rules if is_issue_pattern(c)]
    invariants = [c for c in rules if not is_issue_pattern(c)]

    must_keep = instruction_items(rules + methods, ["regression_guard", "must_include", "warn_if_removed"], 12)
    avoid = instruction_items(rules + issue_patterns, ["forbid", "warn_if_added"], 10)
    known_failure_modes = instruction_items(issue_patterns + evidence, ["symptom", "root_cause"], 10)
    required_checks = instruction_items(rules + methods + issue_patterns, ["verification", "checks"], 10)
    guard_cards = issue_patterns + invariants
    freshness = context.get("source_freshness") or {}
    resolution = context.get("resolution") or {}
    readiness = preflight_readiness(freshness, resolution, guard_cards, fused)

    warnings = list(context.get("warnings") or [])
    if guard_cards:
        warnings.append("preflight matched historical guards; preserve must_keep and run required_checks before final response")
    if not guard_cards and fused:
        warnings.append("no historical guard matched this task; capture durable findings after implementation")

    return {
        "schema": "xmem.preflight.v1",
        "truth_policy": context.get("truth_policy", ""),
        "query": query,
        "intent": "development_preflight",
        "readiness": readiness,
        "risk_level": preflight_risk(issue_patterns, invariants, methods),
        "action": preflight_action(readiness),
        "source_freshness": freshness,
        "resolution": resolution,
        "current": context.get("current") or {},
        "matched_projects": [card_brief(c, i + 1) for i, c in enumerate(registry[:4])],
        "known_bug_patterns": [card_brief(c, i + 1) for i, c in enumerate(issue_patterns[:6])],
        "invariants": [card_brief(c, i + 1) for i, c in enumerate(invariants[:6])],
        "methods": [card_brief(c, i + 1) for i, c in enumerate(methods[:4])],
        "must_keep": must_keep,
        "avoid": avoid,
        "known_failure_modes": known_failure_modes,
        "required_checks": required_checks,
        "source_refs": unique_paths(guard_cards[:6] + methods[:4] + evidence[:4] + registry[:3])[:12],
        "warnings": unique_text_items(warnings),
        "next_reads": context.get("next_reads") or [],
        "latest_events": event_briefs(events),
    }


def is_issue_pattern(card: Dict[str, Any]) -> bool:
    source = str(card.get("source") or "")
    body = str(card.get("body") or "")
    return source in ISSUE_PATTERN_SOURCES or any(key in body for key in ("regression_guard", "root_cause", "fix_pattern", "symptom"))


def preflight_readiness(
    freshness: Dict[str, Any],
    resolution: Dict[str, Any],
    guard_cards: List[Dict[str, Any]],
    cards: List[Dict[str, Any]],
) -> str:
    if freshness.get("status") != "fresh":
        return "blocked_source_stale"
    if resolution.get("do_not_assume_single_project"):
        return "needs_disambiguation"
    if guard_cards:
        return "ready_with_guards"
    if cards:
        return "ready_no_known_guards"
    return "no_prior_memory"


def preflight_risk(issue_patterns: List[Dict[str, Any]], invariants: List[Dict[str, Any]], methods: List[Dict[str, Any]]) -> str:
    if issue_patterns:
        return "high"
    if invariants:
        return "medium"
    if methods:
        return "low"
    return "unknown"


def preflight_action(readiness: str) -> str:
    if readiness == "blocked_source_stale":
        return "run xmem sync before relying on this preflight"
    if readiness == "needs_disambiguation":
        return "disambiguate project/entity before editing"
    if readiness == "ready_with_guards":
        return "read source_refs, preserve must_keep, avoid known failures, run required_checks"
    if readiness == "ready_no_known_guards":
        return "develop normally, then capture durable findings if new risks appear"
    return "no prior memory found; inspect project sources and capture reusable learning"


def instruction_items(cards: List[Dict[str, Any]], keys: List[str], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        for key in keys:
            for value in values_for_key(card, key):
                text = compact(clean_instruction_text(value), 220)
                dedupe = text.lower()
                if not text or dedupe in seen:
                    continue
                seen.add(dedupe)
                out.append({
                    "text": text,
                    "field": key,
                    "card_id": card.get("card_id", ""),
                    "truth": card.get("status", ""),
                    "source": card.get("source", ""),
                    "source_ref": card.get("source_ref") or card.get("path", ""),
                })
                if len(out) >= limit:
                    return out
    return out


def values_for_key(card: Dict[str, Any], key: str) -> List[str]:
    body = str(card.get("body") or "")
    payload = json_payload(body)
    values: List[str] = []
    if payload:
        values.extend(flatten_values(payload_value(payload, key)))
    values.extend(list_after_key(body, key))
    scalar = field_from_text(body, key)
    if scalar:
        values.append(scalar)
    return unique_text_items(values)


def clean_instruction_text(value: Any) -> str:
    text = str(value or "").strip().strip('"')
    while text.startswith("- "):
        text = text[2:].strip()
    return text


def json_payload(body: str) -> Dict[str, Any]:
    try:
        payload = json.loads(body)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def payload_value(payload: Dict[str, Any], key: str) -> Any:
    current: Any = payload
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def flatten_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(flatten_values(item))
        return out
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    return [str(value)]


def unique_text_items(values: List[Any]) -> List[Any]:
    out: List[Any] = []
    seen: set[str] = set()
    for value in values:
        key = str(value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
