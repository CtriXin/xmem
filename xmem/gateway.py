from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .context import canonical_queries_from_corrections
from .preflight import build_preflight
from .project import detect_project
from .resume import build_resume
from .search import latest_events, search_cards
from .util import append_jsonl, git_root, home_dir, query_hash, utc_now


FIELD_ORDER = ("issue", "domain", "service", "repo", "project", "task", "mode")
TARGET_FIELD_KEYS = {"issue", "domain", "service", "repo", "project", "task"}
IDENTITY_CATEGORIES = {"domain", "service", "issue", "deploy", "cos", "copy_domain", "history", "cross_project"}
PREFLIGHT_CATEGORIES = {"bugfix", "edit", "deploy", "cos", "copy_domain", "tool_failure", "compact_output"}

SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(--token(?:=|\s+))[^\s,;]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(cookie\s*:\s*)[^\n]+"), r"\1<redacted>"),
    (re.compile(r"(?i)((?:password|passwd|pwd|secret|secretid|secretkey|api[_-]?key|access[_-]?key|token)\s*[:=]\s*)[^\n,;]+"), r"\1<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted:aws_access_key>"),
    (re.compile(r"https://(?:open\.)?feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9._~/-]+"), "<redacted:feishu_webhook>"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9._~/-]+"), "<redacted:slack_webhook>"),
]

CATEGORY_PATTERNS = {
    "domain": [
        r"\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\b",
    ],
    "service": [
        r"\bservice\b",
        r"\bsvc\b",
        r"\bptc[-_][a-z0-9][a-z0-9_-]+\b",
        r"\b[a-z0-9][a-z0-9-]+-(?:test|prod|online|pre|gray)\b",
        r"服务",
    ],
    "issue": [
        r"\bt\d{4,}\b",
        r"\bissue[-_/]?[a-z0-9][a-z0-9._-]+\b",
        r"\b[a-z][a-z0-9-]+-\d{8}\b",
        r"工单",
        r"任务单",
    ],
    "deploy": [
        r"\bdeploy(?:ment)?\b",
        r"\brelease\b",
        r"\bpipeline\b",
        r"\bpod\b",
        r"\bsafe-access\b",
        r"\bbinding\b",
        r"\brf\b",
        r"\blookup\b",
        r"\bhealth[_ -]?check\b",
        r"发版",
        r"部署",
        r"切流",
        r"绑定",
        r"解析",
        r"上线",
    ],
    "cos": [
        r"\bCOS\b",
        r"\bcoscli\b",
        r"\bbucket\b",
        r"\bsecretID is missing\b",
        r"\bisolated HOME\b",
        r"\bREAL_PATH\b",
        r"对象存储",
    ],
    "copy_domain": [
        r"复制域名",
        r"新建域名",
        r"\bcopy[-_ ]?domain\b",
        r"\btemplate[-_ ]?clone\b",
        r"\bsibling domain\b",
        r"\blookup miss\b",
        r"模版",
        r"模板",
    ],
    "history": [
        r"以前",
        r"历史",
        r"类似",
        r"之前",
        r"踩坑",
        r"复用",
        r"\bprior\b",
        r"\bhistorical\b",
        r"\bsimilar\b",
        r"\bmemory\b",
    ],
    "bugfix": [
        r"\bbug(?:fix)?\b",
        r"\bregression\b",
        r"\bfix\b",
        r"\berror\b",
        r"\bfail(?:ed|ure)?\b",
        r"\b404\b",
        r"修复",
        r"报错",
        r"失败",
        r"不显示",
        r"异常",
        r"回归",
    ],
    "edit": [
        r"\bchange\b",
        r"\bmodify\b",
        r"\bimplement\b",
        r"\badd\b",
        r"\bremove\b",
        r"修改",
        r"实现",
        r"新增",
        r"删除",
    ],
    "cross_project": [
        r"跨项目",
        r"项目名",
        r"口语",
        r"哪个项目",
        r"\brepo\b",
        r"\bbranch\b",
        r"\bproject\b",
    ],
    "compact_output": [
        r"\braw JSON\b",
        r"\bbroad rg\b",
        r"\bgrep\b",
        r"大 JSON",
        r"长日志",
        r"刷屏",
    ],
}

TOOL_FAILURE_PATTERNS = [
    r"\bsecretID is missing\b",
    r"\blookup miss\b",
    r"\bbinding mismatch\b",
    r"\bhealth[_ -]?check\b.*(?:404|fail|missing)",
    r"\bpod\b.*(?:not ready|not converged|restart)",
    r"\btoken\b.*(?:missing|invalid|expired)",
]


def run_gateway(
    raw_query: str,
    *,
    fields: Dict[str, str] | None = None,
    cwd: Path | None = None,
    event: str = "pre-task",
    limit: int = 8,
    budget: int = 700,
    dry_run: bool = False,
) -> Dict[str, Any]:
    clean_fields = redact_fields(fields or {})
    clean_query = redact_text(raw_query or "")
    current = detect_current_project(cwd)
    query = gateway_search_query(clean_query, clean_fields)
    cards: List[Dict[str, Any]] = []
    prelim_categories = matched_categories(" ".join([query, " ".join(clean_fields.values()), event]))
    if event == "tool-error":
        prelim_categories.extend(match_tool_failures(query))
    has_target_fields = any(clean_fields.get(key) for key in TARGET_FIELD_KEYS)
    should_search = bool(query.strip()) and (bool(prelim_categories) or has_target_fields)
    filter_warnings: List[str] = []
    if should_search:
        cards = search_cards(query, max(limit * 4, 20), record_gain=False)
        for expanded_query in canonical_queries_from_corrections(query, cards):
            cards = merge_cards(cards, search_cards(expanded_query, max(limit * 2, 10), record_gain=False))
        cards, filter_warnings = filter_gateway_cards(query, cards)
    events = latest_events(3)
    profile = classify_gateway(query, clean_fields, event, cards, current)
    packet = build_gateway_packet(
        query=query,
        raw_query=clean_query,
        fields=clean_fields,
        cwd=cwd,
        current=current,
        cards=cards,
        events=events,
        profile=profile,
        budget=budget,
        dry_run=dry_run,
        filter_warnings=filter_warnings,
    )
    record_gateway_event(packet, cards)
    return packet


def detect_current_project(cwd: Path | None) -> Dict[str, Any] | None:
    base = cwd or Path.cwd()
    try:
        root = git_root(base)
        return detect_project(root)
    except Exception:
        return None


def gateway_search_query(raw_query: str, fields: Dict[str, str]) -> str:
    parts: list[str] = []
    for key in FIELD_ORDER:
        value = fields.get(key)
        if value:
            parts.append(value)
    if raw_query and not fields.get("task"):
        parts.append(raw_query)
    return " ".join(parts).strip() or raw_query.strip()


def classify_gateway(
    query: str,
    fields: Dict[str, str],
    event: str,
    cards: List[Dict[str, Any]],
    current: Dict[str, Any] | None,
) -> Dict[str, Any]:
    categories = matched_categories(" ".join([query, " ".join(fields.values()), event]))
    if event == "tool-error":
        categories.extend(match_tool_failures(query))
    categories = sorted(set(categories))
    top = cards[0] if cards else {}
    top_score = float(top.get("score") or 0)
    has_target_fields = any(fields.get(key) for key in TARGET_FIELD_KEYS)
    has_query = bool(query.strip())
    high_signal = bool(cards) and (
        top_score >= 12
        or (top_score >= 8 and (has_target_fields or categories))
        or (top_score >= 5 and bool(set(categories) & (IDENTITY_CATEGORIES | PREFLIGHT_CATEGORIES)))
    )
    if not has_query and not has_target_fields:
        return {
            "decision": "skip",
            "action": "skip",
            "event": event,
            "reason": "empty query/fields; no task memory requested",
            "confidence": "low",
            "categories": categories,
            "top_score": top_score,
        }
    if event == "closeout":
        return {
            "decision": "skip",
            "action": "skip",
            "event": event,
            "reason": "closeout should use owner exports/hooks, not context injection",
            "confidence": "low",
            "categories": categories,
            "top_score": top_score,
        }
    if not categories and not high_signal and not has_target_fields:
        return {
            "decision": "skip",
            "action": "skip",
            "event": event,
            "reason": "simple/local task signature; avoid unnecessary memory injection",
            "confidence": "low",
            "categories": categories,
            "top_score": top_score,
        }
    if not cards:
        return {
            "decision": "skip",
            "action": "skip",
            "event": event,
            "reason": "trigger matched but xmem has no indexed candidates; fail open",
            "confidence": "low",
            "categories": categories,
            "top_score": top_score,
        }

    identity = bool(set(categories) & IDENTITY_CATEGORIES) or has_target_fields
    preflight = bool(set(categories) & PREFLIGHT_CATEGORIES)
    if identity and preflight:
        action = "resume+preflight"
    elif identity:
        action = "resume"
    elif preflight or high_signal or current:
        action = "preflight"
    else:
        action = "resume"

    confidence = "high" if top_score >= 12 else "medium" if top_score >= 5 else "low"
    reason_parts = []
    if categories:
        reason_parts.append("matched " + ",".join(categories[:5]))
    if has_target_fields:
        reason_parts.append("structured target fields present")
    if high_signal:
        reason_parts.append(f"top_score={top_score:g}")
    return {
        "decision": "inject",
        "action": action,
        "event": event,
        "reason": "; ".join(reason_parts) or "matched indexed memory",
        "confidence": confidence,
        "categories": categories,
        "top_score": top_score,
    }


def matched_categories(text: str) -> List[str]:
    out: List[str] = []
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                out.append(category)
                break
    return out


def match_tool_failures(text: str) -> List[str]:
    for pattern in TOOL_FAILURE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return ["tool_failure"]
    return []


def filter_gateway_cards(query: str, cards: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    if not looks_like_ad_text_in_log(query):
        return cards, []
    kept = [card for card in cards if not is_ad_specific_card(card)]
    if len(kept) == len(cards):
        return cards, []
    return kept, ["ads/log noise filter active; do not infer an advertising task from log text or raw config snippets"]


def looks_like_ad_text_in_log(query: str) -> bool:
    text = query.lower()
    has_log_marker = any(marker in text for marker in (
        "log",
        "logs",
        "stdout",
        "stderr",
        "grep output",
        "raw output",
        "日志",
        "输出",
        "文本",
    ))
    has_ad_marker = any(marker in text for marker in ("ads.txt", "ad config", "广告配置", "广告文本", "广告"))
    explicit_not_ad_task = any(marker in text for marker in (
        "不是在修广告",
        "不是修广告",
        "not fixing ads",
        "not an ad task",
        "not ads task",
    ))
    log_contains_marker = any(marker in text for marker in (
        "log contains",
        "日志里",
        "日志中",
        "输出里",
        "输出中",
        "contains ads",
        "包含 ads",
        "包含广告",
    ))
    direct_ad_task = any(marker in text for marker in (
        "广告位",
        "ad slot",
        "gpt slot",
        "lazyload",
        "lazy load",
        "新增广告",
        "添加广告",
        "修复广告",
        "改广告",
        "ads.txt 404",
        "ads.txt group",
    ))
    if explicit_not_ad_task:
        return has_ad_marker or log_contains_marker
    return has_log_marker and has_ad_marker and log_contains_marker and not direct_ad_task


def is_ad_specific_card(card: Dict[str, Any]) -> bool:
    card_id = str(card.get("card_id") or "").lower()
    if card_id.startswith("ads.") or card_id.startswith("scmp.ads-sheet."):
        return True
    title = str(card.get("title") or "").lower()
    if title.startswith("ads ") or " ad slot" in title or "ads.txt" in title:
        return True
    return False


def build_gateway_packet(
    *,
    query: str,
    raw_query: str,
    fields: Dict[str, str],
    cwd: Path | None,
    current: Dict[str, Any] | None,
    cards: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    profile: Dict[str, Any],
    budget: int,
    dry_run: bool,
    filter_warnings: List[str] | None = None,
) -> Dict[str, Any]:
    packet: Dict[str, Any] = {
        "schema": "xmem.gateway.v1",
        "truth_policy": "files/code/runtime are truth; sqlite is generated index/cache; gateway is fail-open",
        "decision": profile["decision"],
        "dry_run": dry_run,
        "event": profile.get("event") or "pre-task",
        "action": profile["action"],
        "reason": profile["reason"],
        "confidence": profile["confidence"],
        "categories": profile.get("categories") or [],
        "budget": budget,
        "query_input": {
            "raw_query": raw_query,
            "search_query": query,
            "structured_fields": fields,
            "cwd": str(cwd.resolve()) if cwd else str(Path.cwd().resolve()),
        },
        "search": search_summary(cards),
        "packet": {},
        "warnings": filter_warnings or [],
        "next_reads": [],
    }
    if profile["decision"] != "inject":
        return packet

    action = str(profile.get("action") or "")
    max_items = item_budget(budget)
    resume_data: Dict[str, Any] | None = None
    preflight_data: Dict[str, Any] | None = None
    if "resume" in action:
        resume_data = build_resume(query, current, cards, events, structured_fields=fields, raw_query=raw_query)
    if "preflight" in action:
        preflight_data = build_preflight(query, current, cards, events, structured_fields=preflight_fields(fields), raw_query=raw_query)
    if not resume_data and preflight_data:
        resume_like = resume_memory_from_preflight(preflight_data)
    else:
        resume_like = resume_data or {}

    compact_packet = {
        "identity": limit_identity(resume_like.get("identity") or {}, max_items),
        "current_gate": resume_like.get("current_gate") or gate_from_preflight(preflight_data or {}),
        "historical_pitfalls": compact_card_items(resume_like.get("historical_pitfalls") or [], max_items),
        "invariants": compact_card_items(resume_like.get("invariants") or [], max_items),
        "methods": compact_card_items(resume_like.get("methods") or [], max_items),
        "must_keep": (resume_like.get("must_keep") or [])[:max_items],
        "avoid": (resume_like.get("avoid") or [])[:max_items],
        "required_checks": (resume_like.get("required_checks") or [])[:max_items],
        "token_savers": (resume_like.get("token_savers") or [])[:max_items],
        "recent_evidence": gateway_evidence(cards, resume_like, max_items),
        "next_action": resume_like.get("next_action") or (preflight_data or {}).get("action", ""),
    }
    packet["packet"] = compact_packet
    warnings: List[str] = []
    next_reads: List[str] = []
    for source in (resume_data or {}, preflight_data or {}):
        warnings.extend(str(item) for item in source.get("warnings") or [])
        next_reads.extend(str(item) for item in source.get("next_reads") or [])
    packet["warnings"] = unique_strings((filter_warnings or []) + warnings)[:max_items]
    packet["next_reads"] = unique_strings(next_reads)[:max_items]
    return packet


def preflight_fields(fields: Dict[str, str]) -> Dict[str, str]:
    return {key: value for key, value in fields.items() if key in {"domain", "service", "repo", "project", "task", "mode"}}


def resume_memory_from_preflight(packet: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "identity": {
            "resolution_status": (packet.get("resolution") or {}).get("status", ""),
            "reason": (packet.get("resolution") or {}).get("reason", ""),
            "do_not_assume_single_project": (packet.get("resolution") or {}).get("do_not_assume_single_project", False),
            "current": packet.get("current") or {},
            "registry_candidates": packet.get("matched_projects") or [],
        },
        "current_gate": gate_from_preflight(packet),
        "historical_pitfalls": packet.get("known_bug_patterns") or [],
        "invariants": packet.get("invariants") or [],
        "methods": packet.get("methods") or [],
        "must_keep": packet.get("must_keep") or [],
        "avoid": packet.get("avoid") or [],
        "required_checks": packet.get("required_checks") or [],
        "token_savers": [],
        "recent_evidence": packet.get("source_refs") or [],
        "next_action": packet.get("action") or "",
    }


def gate_from_preflight(packet: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "readiness": packet.get("readiness", ""),
        "severity": packet.get("severity", ""),
        "risk_level": packet.get("risk_level", ""),
        "action": packet.get("action", ""),
        "can_proceed": packet.get("can_proceed", ""),
        "blockers": packet.get("blockers") or [],
        "required_before_edit": packet.get("required_before_edit") or [],
        "required_before_deploy": packet.get("required_before_deploy") or [],
    }


def limit_identity(identity: Dict[str, Any], max_items: int) -> Dict[str, Any]:
    out = dict(identity)
    value = out.get("registry_candidates")
    if isinstance(value, list):
        out["registry_candidates"] = compact_card_items(value, max_items)
    traffic = out.get("traffic_switch")
    if isinstance(traffic, dict) and traffic:
        out["traffic_switch"] = compact_card(traffic)
    elif isinstance(traffic, list):
        out["traffic_switch"] = compact_card_items(traffic, max_items)
    else:
        out["traffic_switch"] = {}
    return out


def compact_card_items(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    return [compact_card(item) for item in items[:limit]]


def compact_card(item: Dict[str, Any]) -> Dict[str, Any]:
    source_ref = item.get("source_ref") or item.get("source_path") or item.get("evidence_ref") or ""
    out = {
        "id": item.get("id", ""),
        "type": item.get("type", ""),
        "truth": item.get("truth", ""),
        "confidence": item.get("confidence", ""),
        "score": item.get("score", ""),
        "source": item.get("source", ""),
        "title": item.get("title", ""),
        "why": item.get("why", ""),
        "source_ref": source_ref,
    }
    for key in (
        "project",
        "template",
        "repo",
        "prod_service",
        "validation_service",
        "prod_branch_hint",
        "validation_branch_hint",
        "approval_group",
    ):
        if item.get(key):
            out[key] = item.get(key)
    return out


def gateway_evidence(cards: List[Dict[str, Any]], resume_like: Dict[str, Any], limit: int) -> List[str]:
    refs: List[str] = []
    for card in cards[:limit * 2]:
        ref = str(card.get("source_ref") or card.get("path") or "").strip()
        if ref:
            refs.append(ref)
    refs.extend(str(item) for item in resume_like.get("recent_evidence") or [])
    return unique_strings(refs)[:limit]


def search_summary(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    top = cards[0] if cards else {}
    return {
        "matches": len(cards),
        "top_card": top.get("card_id", ""),
        "top_score": top.get("score", 0),
        "top_status": top.get("status", ""),
        "top_confidence": top.get("confidence", 0),
        "top_source": top.get("source", ""),
        "sources": sorted({str(card.get("source") or "") for card in cards if card.get("source")})[:6],
    }


def item_budget(budget: int) -> int:
    if budget <= 350:
        return 2
    if budget <= 700:
        return 4
    if budget <= 1200:
        return 6
    return 8


def merge_cards(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in [*primary, *extra]:
        card_id = str(card.get("card_id") or "")
        key = card_id or str(card.get("path") or id(card))
        if key in seen:
            continue
        seen.add(key)
        merged.append(card)
    merged.sort(key=lambda item: (float(item.get("score") or 0), float(item.get("confidence") or 0)), reverse=True)
    return merged


def record_gateway_event(packet: Dict[str, Any], cards: List[Dict[str, Any]]) -> None:
    search = packet.get("search") or {}
    decision = str(packet.get("decision") or "skip")
    event = f"gateway.{decision}"
    matches = int(search.get("matches") or 0)
    estimated = matches * 1200 if decision == "inject" else 0
    append_jsonl(
        home_dir() / "gain.jsonl",
        {
            "ts": utc_now(),
            "event": event,
            "source": "gateway",
            "query": (packet.get("query_input") or {}).get("search_query", ""),
            "query_hash": query_hash((packet.get("query_input") or {}).get("search_query", "")),
            "matches": matches,
            "cards_considered": len(cards),
            "estimated_tokens_saved": estimated,
            "estimate_formula": "matches * 1200" if estimated else "",
            "estimate_kind": "rough_upper_bound_not_billing" if estimated else "",
            "top_card": search.get("top_card", ""),
            "top_score": search.get("top_score", 0),
            "top_status": search.get("top_status", ""),
            "top_confidence": search.get("top_confidence", 0),
            "top_why": "",
            "sources": search.get("sources") or [],
        },
    )


def redact_fields(fields: Dict[str, str]) -> Dict[str, str]:
    return {str(key).strip(): redact_text(str(value)) for key, value in fields.items() if str(key).strip() and str(value).strip()}


def redact_text(text: str) -> str:
    out = str(text)
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def unique_strings(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
