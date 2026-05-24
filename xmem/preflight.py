from __future__ import annotations

import json
from typing import Any, Dict, List

from .context import (
    METHOD_TYPES,
    REGISTRY_TYPES,
    RULE_TYPES,
    SPEC_TYPES,
    build_context,
    card_brief,
    event_briefs,
    fuse_related_cards,
    unique_paths,
)
from .toon import compact
from .util import field_from_text, list_after_key


ISSUE_PATTERN_SOURCES = {"issue-bug-patterns", "issue-tracking-export"}
ACTIONABLE_WHY_PREFIXES = ("exact_alias:", "alias_match:", "metadata_match:", "alias_term:", "metadata_term:")
MIN_BODY_MATCH_SCORE = 1.0
ISSUE_PATTERN_KEYS = ("symptom", "root_cause", "fix_pattern", "verification", "regression_guard")
DEPLOY_TASK_KEYWORDS = (
    "deploy", "deployment", "pipeline", "safe-access", "safe access", "live verify",
    "live-check", "traffic switch", "traffic-switch", "binding", "bind-check", "approval",
    "purge", "切流", "发版", "部署", "域名绑定", "审批",
)
COMPACT_OUTPUT_KEYWORDS = (
    "scmp", "feishu", "lark", "rg", "grep", "issue-tracking", "issue tracking", "issue",
    "json", "pod", "safe-access", "safe access", "raw", "log", "logs", "read-back",
    "skill.md", "large output", "compact", "summary", "toon", "输出", "日志", "飞书",
)
RUNTIME_BLOCKER_PATTERNS = (
    ("deploy_payload_path_mismatch", ("stale path", "payload/path", "payload path", "path mismatch", "脏 path", "脏path")),
    ("pod_not_converged", ("pod not converged", "not converged", "unconverged", "old pod", "旧 pod", "pod 未收敛", "未收敛")),
    ("safe_access_failed", ("safe-access failed", "safe access failed", "live verification failed", "live verify failed", "live-check failed", "slot 0/", "验证失败")),
    ("domain_binding_missing", ("domain binding mismatch", "binding mismatch", "missing domain binding", "domains not switched", "not switched", "绑定不一致", "域名未切", "没切流")),
)


TARGET_FIELD_KEYS = {"domain", "service", "repo", "project", "entity"}


def build_preflight(
    query: str,
    current: Dict[str, Any] | None,
    cards: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    *,
    structured_fields: Dict[str, str] | None = None,
    raw_query: str = "",
) -> Dict[str, Any]:
    context = build_context(query, current, cards, events)
    fused = fuse_related_cards(cards)
    registry = [c for c in fused if c.get("type") in REGISTRY_TYPES or str(c.get("type", "")).startswith("wiki.")]
    actionable = [c for c in fused if is_preflight_actionable(c)]
    rules = [c for c in actionable if c.get("type") in RULE_TYPES]
    methods = [c for c in actionable if c.get("type") in METHOD_TYPES]
    specs = [c for c in actionable if c.get("type") in SPEC_TYPES]
    issue_patterns = [c for c in rules if is_issue_pattern(c)]
    invariants = [c for c in rules if not is_issue_pattern(c)]

    must_keep = instruction_items(rules + methods, ["regression_guard", "must_include", "warn_if_removed"], 12)
    avoid = instruction_items(rules + issue_patterns, ["forbid", "warn_if_added"], 10)
    known_failure_modes = instruction_items(issue_patterns, ["symptom", "root_cause"], 10)
    required_checks = instruction_items(rules + methods + issue_patterns, ["verification", "checks"], 10)
    guard_cards = issue_patterns + invariants
    freshness = context.get("source_freshness") or {}
    resolution = context.get("resolution") or {}
    readiness = preflight_readiness(freshness, resolution, guard_cards, fused)
    deploy_task = looks_like_deploy_task(query, fused)
    compact_guard = needs_compact_output_guard(query, actionable)
    query_quality = preflight_query_quality(query, structured_fields or {}, fused)
    gate = build_gate(
        query=query,
        freshness=freshness,
        resolution=resolution,
        guard_cards=guard_cards,
        matched_cards=fused,
        deploy_task=deploy_task,
        compact_guard=compact_guard,
        query_quality=query_quality,
    )
    if query_quality.get("status") == "needs_clarification":
        guard_cards = []
        issue_patterns = []
        invariants = []
        methods = []
        specs = []
        must_keep = []
        avoid = []
        known_failure_modes = []
        required_checks = []
        registry = []
        readiness = "needs_clarification"

    warnings = list(context.get("warnings") or [])
    warnings.extend(query_quality.get("warnings") or [])
    if guard_cards:
        warnings.append("preflight matched historical guards; preserve must_keep and run required_checks before final response")
    if not guard_cards and fused:
        warnings.append("no historical guard matched this task; capture durable findings after implementation")
    if deploy_task:
        warnings.append("deploy/traffic task detected; do live domain/service/pod/safe-access verification before declaring done")
    if compact_guard:
        warnings.append("compact-output guard active; avoid raw JSON/log dumps and store bulky output as evidence files")

    return {
        "schema": "xmem.preflight.v1",
        "truth_policy": context.get("truth_policy", ""),
        "query": query,
        "query_hash": context.get("query_hash", ""),
        "query_input": {
            "raw_query": raw_query or query,
            "search_query": query,
            "structured_fields": structured_fields or {},
            "quality": query_quality,
        },
        "intent": "development_preflight",
        "readiness": readiness,
        "severity": gate["severity"],
        "can_proceed": gate["can_proceed"],
        "risk_level": preflight_risk(issue_patterns, invariants, methods, gate["blockers"]),
        "action": preflight_action(readiness),
        "symbolic_memory": context.get("symbolic_memory") or {},
        "blockers": gate["blockers"],
        "required_before_edit": gate["required_before_edit"],
        "required_before_deploy": gate["required_before_deploy"],
        "source_freshness": freshness,
        "local_source_health": context.get("local_source_health") or {},
        "resolution": resolution,
        "current": context.get("current") or {},
        "matched_projects": [card_brief(c, i + 1) for i, c in enumerate(registry[:4])],
        "known_bug_patterns": [card_brief(c, i + 1) for i, c in enumerate(issue_patterns[:6])],
        "invariants": [card_brief(c, i + 1) for i, c in enumerate(invariants[:6])],
        "methods": [card_brief(c, i + 1) for i, c in enumerate(methods[:4])],
        "specs": [card_brief(c, i + 1) for i, c in enumerate(specs[:4])],
        "must_keep": must_keep,
        "avoid": avoid,
        "known_failure_modes": known_failure_modes,
        "required_checks": required_checks,
        "source_refs": unique_paths(guard_cards[:6] + methods[:4] + specs[:4] + registry[:3])[:12],
        "warnings": unique_text_items(warnings),
        "next_reads": unique_paths(guard_cards[:6] + methods[:4] + specs[:4] + registry[:3])[:10],
        "latest_events": event_briefs(events),
    }


def is_preflight_actionable(card: Dict[str, Any]) -> bool:
    """Keep dev-start guardrails focused; body-only generic terms are not enough."""
    if card.get("suppressed_for_query"):
        return False
    why = str(card.get("why") or "")
    if any(part.strip().startswith(ACTIONABLE_WHY_PREFIXES) for part in why.split(";")):
        return True
    return float(card.get("score") or 0) >= MIN_BODY_MATCH_SCORE


def is_issue_pattern(card: Dict[str, Any]) -> bool:
    source = str(card.get("source") or "")
    return source in ISSUE_PATTERN_SOURCES or any(has_structured_field(card, key) for key in ISSUE_PATTERN_KEYS)


def has_structured_field(card: Dict[str, Any], key: str) -> bool:
    body = str(card.get("body") or "")
    payload = json_payload(body)
    if flatten_values(payload_value(payload, key)):
        return True
    return bool(list_after_key(body, key) or field_from_text(body, key))


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


def preflight_risk(
    issue_patterns: List[Dict[str, Any]],
    invariants: List[Dict[str, Any]],
    methods: List[Dict[str, Any]],
    blockers: List[Dict[str, Any]] | None = None,
) -> str:
    if blockers:
        return "high"
    if issue_patterns:
        return "high"
    if invariants:
        return "medium"
    if methods:
        return "low"
    return "unknown"


def build_gate(
    *,
    query: str,
    freshness: Dict[str, Any],
    resolution: Dict[str, Any],
    guard_cards: List[Dict[str, Any]],
    matched_cards: List[Dict[str, Any]],
    deploy_task: bool,
    compact_guard: bool,
    query_quality: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    blockers: List[Dict[str, Any]] = []
    required_before_edit: List[str] = []
    required_before_deploy: List[str] = []

    if freshness.get("status") != "fresh":
        blockers.append(blocker("source_stale", "source exports are stale or registry is missing; run xmem sync before relying on this packet"))
        required_before_edit.append("Run xmem sync and re-run preflight before editing.")
    if resolution.get("do_not_assume_single_project"):
        blockers.append(blocker("ambiguous_target", "project/domain/service identity is ambiguous; resolve the target before editing or deploy"))
        required_before_edit.append("Resolve project/domain/service identity from verified Project Wiki/card evidence before editing.")
    if (query_quality or {}).get("status") == "needs_clarification":
        blockers.append(blocker("low_confidence_preflight_query", "structured preflight target is not anchored by verified memory; rebuild fields/query before using guardrails"))
        required_before_edit.append("Re-run preflight with explicit domain/service/repo/task fields or resolve the target with xmem context first.")

    for code, patterns in RUNTIME_BLOCKER_PATTERNS:
        if any(pattern in query.lower() for pattern in patterns):
            blockers.append(blocker(code, runtime_blocker_text(code)))

    if guard_cards:
        required_before_edit.append("Read source_refs, preserve must_keep, avoid known failures, and run required_checks before closeout.")
    elif matched_cards:
        required_before_edit.append("Use matched cards as routing hints, then verify in source/code/runtime before changing behavior.")
    else:
        required_before_edit.append("No prior memory matched; inspect project sources and capture durable learning if a reusable rule appears.")

    if compact_guard:
        required_before_edit.extend([
            "Prefer compact JSON/TOON summaries for agent-facing structured output.",
            "Store bulky raw JSON/logs/read-backs as evidence files and cite the path instead of pasting them.",
            "Check current issue/progress, xmem context, Project Wiki index, and directed repo reads before broad rg/grep.",
        ])

    if deploy_task:
        required_before_deploy.extend([
            "Live-verify target domain-to-service binding before deploy, approval, or traffic switch.",
            "Ensure SCMP deploy payload/path has no stale deploy path or unintended service target.",
            "Wait for pipeline success and pod convergence; do not treat run Succeeded alone as complete.",
            "Run safe-access/live verification and store compact evidence path before closeout.",
            "Write Issue Record evidence and Project Wiki pending/accepted update only after verification.",
        ])
    if compact_guard and deploy_task:
        required_before_deploy.append("Use compact pod/safe-access/Feishu summaries; keep raw responses in evidence files.")

    blockers = dedupe_blockers(blockers)
    severity = "block" if blockers else ("warn" if guard_cards or deploy_task or compact_guard else "hint")
    return {
        "severity": severity,
        "can_proceed": not blockers,
        "blockers": blockers,
        "required_before_edit": unique_strings(required_before_edit),
        "required_before_deploy": unique_strings(required_before_deploy),
    }


def preflight_query_quality(query: str, fields: Dict[str, str], cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    clean_fields = {k: v for k, v in fields.items() if str(v or "").strip()}
    if not clean_fields:
        return {"status": "plain_query", "confidence": "medium", "warnings": []}
    target_fields = {k: v for k, v in clean_fields.items() if k in TARGET_FIELD_KEYS}
    top_score = max([float(card.get("score") or 0) for card in cards] or [0.0])
    anchored = any(
        (
            card.get("type") in REGISTRY_TYPES
            or str(card.get("type", "")).startswith("wiki.")
            or card.get("type") in {"traffic.switch", "traffic-switch", "scmp.traffic-switch", "relation", "link"}
        )
        and card.get("status") == "verified"
        and float(card.get("score") or 0) >= 8
        for card in cards[:10]
    )
    if target_fields and not anchored:
        return {
            "status": "needs_clarification",
            "confidence": "low",
            "top_score": round(top_score, 3),
            "reason": "domain/service/repo fields did not resolve to a verified target anchor",
            "warnings": ["structured preflight target is low confidence; no guardrail packet emitted until target is clarified"],
        }
    if top_score < 1:
        return {
            "status": "needs_clarification",
            "confidence": "low",
            "top_score": round(top_score, 3),
            "reason": "structured preflight found no relevant cards",
            "warnings": ["structured preflight matched no relevant memory; rebuild fields/query before relying on it"],
        }
    return {
        "status": "structured",
        "confidence": "high" if anchored else "medium",
        "top_score": round(top_score, 3),
        "target_anchor": anchored,
        "warnings": [],
    }


def blocker(code: str, text: str) -> Dict[str, str]:
    return {"code": code, "text": text, "severity": "block"}


def runtime_blocker_text(code: str) -> str:
    messages = {
        "deploy_payload_path_mismatch": "deploy payload/path mismatch detected; clean payload/path before deploy",
        "pod_not_converged": "pod convergence is not proven; wait for old pods to terminate and new pods to run cleanly",
        "safe_access_failed": "safe-access/live verification failed; classify failure and fix before closeout",
        "domain_binding_missing": "domain binding does not point to the intended service; resolve binding before traffic switch/live verification",
    }
    return messages.get(code, code)


def looks_like_deploy_task(query: str, cards: List[Dict[str, Any]]) -> bool:
    text = str(query or "").lower()
    if any(keyword in text for keyword in DEPLOY_TASK_KEYWORDS):
        return True
    if any(pattern in text for _, patterns in RUNTIME_BLOCKER_PATTERNS for pattern in patterns):
        return True
    return any(
        card.get("type") in {"traffic.switch", "traffic-switch", "scmp.traffic-switch"}
        and float(card.get("score") or 0) >= 8
        for card in cards[:8]
    )


def needs_compact_output_guard(query: str, cards: List[Dict[str, Any]]) -> bool:
    text = searchable_text(query, cards[:8])
    if "xmem.policy.agent-output-compactness" in text or "compact output policy" in text:
        return True
    return any(keyword in text for keyword in COMPACT_OUTPUT_KEYWORDS)


def searchable_text(query: str, cards: List[Dict[str, Any]]) -> str:
    parts: List[str] = [query]
    for card in cards:
        parts.extend([
            str(card.get("card_id", "")),
            str(card.get("title", "")),
            str(card.get("type", "")),
            str(card.get("source", "")),
            str(card.get("why", "")),
        ])
        try:
            parts.extend(json.loads(card.get("aliases_json") or "[]"))
        except Exception:
            pass
    return "\n".join(str(part).lower() for part in parts)


def dedupe_blockers(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        code = str(item.get("code") or item.get("text") or "").lower()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(item)
    return out


def unique_strings(values: List[str]) -> List[str]:
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


def preflight_action(readiness: str) -> str:
    if readiness == "blocked_source_stale":
        return "run xmem sync before relying on this preflight"
    if readiness == "needs_clarification":
        return "rebuild structured fields/query or resolve target with xmem context before editing"
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
