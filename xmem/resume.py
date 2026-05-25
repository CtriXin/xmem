from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .context import build_context
from .preflight import build_preflight
from .toon import compact
from .util import query_hash


def build_resume(
    query: str,
    current: Dict[str, Any] | None,
    cards: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    *,
    structured_fields: Dict[str, str] | None = None,
    raw_query: str = "",
) -> Dict[str, Any]:
    """Build a compact handoff/resume packet from context + preflight memory."""
    context = build_context(query, current, cards, events)
    preflight = build_preflight(
        query,
        current,
        cards,
        events,
        structured_fields=structured_fields,
        raw_query=raw_query,
    )
    evidence_refs = merged_evidence_refs(cards, context, preflight)
    packet = {
        "schema": "xmem.resume.v1",
        "truth_policy": context.get("truth_policy", ""),
        "intent": "task_resume",
        "query": query,
        "query_hash": query_hash(query),
        "query_input": preflight.get("query_input") or {
            "raw_query": raw_query or query,
            "search_query": query,
            "structured_fields": structured_fields or {},
        },
        "resume_summary": resume_summary(context, preflight),
        "identity": resume_identity(context),
        "current_gate": {
            "readiness": preflight.get("readiness", ""),
            "severity": preflight.get("severity", ""),
            "can_proceed": preflight.get("can_proceed", False),
            "risk_level": preflight.get("risk_level", ""),
            "action": preflight.get("action", ""),
            "completion_basis": "xmem reports historical gates and evidence refs; live/issue gate completion must be verified in owner sources",
            "blockers": preflight.get("blockers") or [],
            "required_before_edit": preflight.get("required_before_edit") or [],
            "required_before_deploy": preflight.get("required_before_deploy") or [],
        },
        "historical_pitfalls": preflight.get("known_bug_patterns") or [],
        "invariants": preflight.get("invariants") or [],
        "methods": preflight.get("methods") or [],
        "must_keep": preflight.get("must_keep") or [],
        "avoid": preflight.get("avoid") or [],
        "required_checks": preflight.get("required_checks") or [],
        "token_savers": token_savers(context, preflight),
        "recent_evidence": evidence_refs[:10],
        "source_freshness": preflight.get("source_freshness") or context.get("source_freshness") or {},
        "local_source_health": preflight.get("local_source_health") or context.get("local_source_health") or {},
        "warnings": unique_strings((context.get("warnings") or []) + (preflight.get("warnings") or [])),
        "next_reads": unique_strings((preflight.get("next_reads") or []) + (context.get("next_reads") or []) + evidence_refs)[:12],
        "next_action": resume_next_action(context, preflight),
    }
    return packet


def resume_summary(context: Dict[str, Any], preflight: Dict[str, Any]) -> str:
    identity = resume_identity(context)
    parts: List[str] = []
    traffic = identity.get("traffic_switch") or {}
    if traffic:
        project = traffic.get("project") or traffic.get("template") or traffic.get("id")
        prod = traffic.get("prod_service")
        validation = traffic.get("validation_service")
        parts.append(f"identity={project}")
        if prod:
            parts.append(f"prod={prod}")
        if validation:
            parts.append(f"validation={validation}")
    elif identity.get("registry_candidates"):
        first = identity["registry_candidates"][0]
        parts.append(f"identity={first.get('title') or first.get('id')}")
    else:
        parts.append(f"resolution={identity.get('resolution_status') or 'unknown'}")
    parts.append(f"gate={preflight.get('readiness')}/{preflight.get('severity')}")
    if preflight.get("known_bug_patterns"):
        parts.append(f"bugs={len(preflight.get('known_bug_patterns') or [])}")
    if preflight.get("must_keep"):
        parts.append(f"must_keep={len(preflight.get('must_keep') or [])}")
    return "; ".join(parts)


def resume_identity(context: Dict[str, Any]) -> Dict[str, Any]:
    traffic = context.get("traffic_switch") or []
    registry = context.get("registry_candidates") or []
    out = {
        "resolution_status": (context.get("resolution") or {}).get("status", ""),
        "do_not_assume_single_project": (context.get("resolution") or {}).get("do_not_assume_single_project", False),
        "reason": (context.get("resolution") or {}).get("reason", ""),
        "current": context.get("current") or {},
        "traffic_switch": traffic[0] if traffic else {},
        "registry_candidates": registry[:4],
    }
    return out


def token_savers(context: Dict[str, Any], preflight: Dict[str, Any]) -> List[str]:
    hints: List[str] = []
    hints.extend(str(item) for item in context.get("gain_hints") or [])
    for item in context.get("traffic_switch") or []:
        hints.extend(str(x) for x in item.get("can_skip") or [])
    for item in preflight.get("avoid") or []:
        text = str(item.get("text") or "")
        lower = text.lower()
        if any(key in lower for key in ("raw", "json", "rg", "grep", "skill.md", "notice", "issue-tracking", "feishu", "lark", "log")):
            hints.append(text)
    for warning in preflight.get("warnings") or []:
        if "compact-output" in str(warning) or "raw JSON" in str(warning) or "broad" in str(warning):
            hints.append(str(warning))
    return unique_strings(hints)[:10]


def resume_next_action(context: Dict[str, Any], preflight: Dict[str, Any]) -> str:
    freshness = preflight.get("source_freshness") or {}
    resolution = context.get("resolution") or {}
    if freshness.get("status") != "fresh":
        return "run xmem sync, then rerun xmem resume before relying on this packet"
    if resolution.get("do_not_assume_single_project"):
        return "resolve project identity first; do not edit or deploy from an ambiguous resume packet"
    if not preflight.get("can_proceed", True):
        return "clear blockers and required_before_edit before changing code"
    if preflight.get("risk_level") in {"high", "medium"}:
        return "start from identity/current_gate, read recent_evidence/next_reads only as needed, preserve must_keep, avoid known failures, run required_checks"
    if preflight.get("readiness") == "no_prior_memory":
        return "no prior xmem memory found; inspect owner sources and capture reusable findings after closeout"
    return "use identity and token_savers to skip broad handoff/source scans; verify live/runtime facts in owner systems before closeout"


def merged_evidence_refs(cards: List[Dict[str, Any]], context: Dict[str, Any], preflight: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for section in ("evidence", "traffic_switch", "registry_candidates", "relations", "rules", "methods", "memories", "specs"):
        for item in context.get(section) or []:
            refs.extend(item_refs(item))
    for section in ("known_bug_patterns", "invariants", "methods", "specs"):
        for item in preflight.get(section) or []:
            refs.extend(item_refs(item))
    refs.extend(preflight.get("source_refs") or [])
    refs.extend(context.get("next_reads") or [])
    for card in cards:
        refs.extend(card_raw_refs(card))
    return unique_strings(refs)


def item_refs(item: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for key in ("evidence_ref", "source_ref", "source_path"):
        value = str(item.get(key) or "").strip()
        if value:
            refs.append(value)
    return refs


def card_raw_refs(card: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for key in ("path", "source_ref"):
        value = str(card.get(key) or "").strip()
        if value:
            refs.append(value)
    body = str(card.get("body") or "")
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}
    refs.extend(json_refs(payload))
    refs.extend(re.findall(r"^\s*path:\s*(.+?)\s*$", body, flags=re.MULTILINE))
    refs.extend(re.findall(r"^\s*ref:\s*(.+?)\s*$", body, flags=re.MULTILINE))
    return [clean_ref(ref) for ref in refs if clean_ref(ref)]


def json_refs(value: Any) -> List[str]:
    refs: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"path", "ref", "source_ref", "sourcePath"} and isinstance(item, str):
                refs.append(item)
            refs.extend(json_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(json_refs(item))
    return refs


def clean_ref(value: Any) -> str:
    return compact(str(value or "").strip().strip('"').strip("'"), 240)


def unique_strings(values: List[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
