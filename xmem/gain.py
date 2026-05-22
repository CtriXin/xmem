from __future__ import annotations

from collections import Counter
import hashlib
import os
from pathlib import Path
import shutil
import unicodedata
from typing import Any, Dict, List, Optional

from .util import append_jsonl, home_dir, load_jsonl, slugify, utc_now, write_json


def summarize_gain(limit: Optional[int] = None) -> Dict[str, object]:
    rows = load_jsonl(home_dir() / "gain.jsonl", limit=limit)
    events = Counter(row.get("event", "unknown") for row in rows)
    queries = Counter(str(row.get("query") or "") for row in rows if row.get("query"))
    top_cards = {str(row.get("top_card") or "") for row in rows if row.get("top_card")}
    tokens = sum(int(row.get("estimated_tokens_saved") or 0) for row in rows)
    actual_tokens = sum(int(row.get("actual_tokens_saved") or 0) for row in rows)
    bugs = sum(int(row.get("estimated_bug_prevented") or 0) for row in rows)
    matches = sum(int(row.get("matches") or 0) for row in rows)
    event_rows = aggregate_events(rows)
    query_rows = aggregate_queries(rows)
    calibration = build_calibration(rows, query_rows)
    context_total = events.get("context.hit", 0) + events.get("context.miss", 0)
    preflight_total = events.get("preflight.hit", 0) + events.get("preflight.miss", 0)
    retrieval_hits = sum(value for key, value in events.items() if str(key).endswith(".hit"))
    retrieval_misses = sum(value for key, value in events.items() if str(key).endswith(".miss"))
    retrieval_total = retrieval_hits + retrieval_misses
    hit_rate = round(events.get("context.hit", 0) / context_total * 100, 1) if context_total else 0.0
    guardrails = [
        {
            "ts": row.get("ts", ""),
            "event": row.get("event", ""),
            "warnings": int(row.get("warnings") or 0),
            "matched_cards": int(row.get("matched_cards") or 0),
        }
        for row in rows
        if str(row.get("event") or "").startswith(("rule.", "check."))
    ][-10:]
    recent_queries = [
        {
            "ts": row.get("ts", ""),
            "query": row.get("query", ""),
            "event": row.get("event", ""),
            "matches": int(row.get("matches") or 0),
            "top_card": row.get("top_card", ""),
            "top_score": row.get("top_score", 0),
            "sources": row.get("sources", []),
        }
        for row in rows
        if row.get("query")
    ][-10:]
    return {
        "events": dict(events),
        "limit": limit,
        "scope": "all" if limit is None else "latest",
        "estimated_tokens_saved": tokens,
        "actual_tokens_saved": actual_tokens,
        "estimated_bug_prevented": bugs,
        "matches": matches,
        "rows": len(rows),
        "observed": {
            "logged_rows": len(rows),
            "unique_queries": len(queries),
            "unique_top_cards": len(top_cards),
            "retrieval_calls": retrieval_total,
            "retrieval_hits": retrieval_hits,
            "retrieval_misses": retrieval_misses,
            "context_queries": context_total,
            "context_hits": events.get("context.hit", 0),
            "context_misses": events.get("context.miss", 0),
            "context_hit_rate": hit_rate,
            "preflight_queries": preflight_total,
            "preflight_hits": events.get("preflight.hit", 0),
            "preflight_misses": events.get("preflight.miss", 0),
            "guardrail_checks": sum(value for key, value in events.items() if str(key).startswith(("check.", "rule."))),
            "guardrail_prevented": events.get("rule.prevented", 0),
        },
        "calibration": calibration,
        "by_event": event_rows,
        "top_queries": query_rows or [{"query": query, "count": count} for query, count in queries.most_common(10)],
        "recent_queries": recent_queries,
        "recent_guardrails": guardrails,
    }


def aggregate_events(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        event = str(row.get("event") or "unknown")
        item = out.setdefault(event, {"event": event, "count": 0, "estimated_tokens_saved": 0, "estimated_bug_prevented": 0, "matches": 0})
        item["count"] += 1
        item["estimated_tokens_saved"] += int(row.get("estimated_tokens_saved") or 0)
        item["estimated_bug_prevented"] += int(row.get("estimated_bug_prevented") or 0)
        item["matches"] += int(row.get("matches") or 0)
    return sorted(out.values(), key=lambda item: (int(item["estimated_tokens_saved"]), int(item["count"])), reverse=True)


def aggregate_queries(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        query = str(row.get("query") or "").strip()
        if not query:
            continue
        item = out.setdefault(query, {"query": query, "count": 0, "estimated_tokens_saved": 0, "matches": 0, "top_card": "", "sources": []})
        item["count"] += 1
        item["estimated_tokens_saved"] += int(row.get("estimated_tokens_saved") or 0)
        item["matches"] += int(row.get("matches") or 0)
        if row.get("top_card"):
            item["top_card"] = row.get("top_card", "")
        for source in row.get("sources") or []:
            if source not in item["sources"]:
                item["sources"].append(source)
    return sorted(out.values(), key=lambda item: (int(item["estimated_tokens_saved"]), int(item["count"])), reverse=True)[:10]


def build_calibration(rows: List[Dict[str, Any]], top_queries: List[Dict[str, Any]]) -> Dict[str, object]:
    retrieval_rows = [row for row in rows if row.get("query")]
    estimated_rows = [row for row in rows if int(row.get("estimated_tokens_saved") or 0) or int(row.get("estimated_bug_prevented") or 0)]
    confirmed = [row for row in rows if str(row.get("event") or "") == "gain.confirmed"]
    rejected = [row for row in rows if str(row.get("event") or "") == "gain.rejected"]
    outcome_rows = [row for row in rows if str(row.get("event") or "").startswith("outcome.")]
    score_rows = [row for row in retrieval_rows if float(row.get("top_score") or 0) > 0]
    verified_rows = [row for row in retrieval_rows if str(row.get("top_status") or "") == "verified"]
    avg_score = round(sum(float(row.get("top_score") or 0) for row in score_rows) / len(score_rows), 2) if score_rows else 0.0
    status = "telemetry_only"
    confidence = "low"
    if confirmed or rejected or outcome_rows:
        status = "partially_calibrated"
        confidence = "medium"
    elif score_rows:
        status = "proxy_only"
    needs_review = []
    for item in top_queries[:5]:
        saved = int(item.get("estimated_tokens_saved") or 0)
        if saved <= 0:
            continue
        needs_review.append(
            {
                "query": item.get("query", ""),
                "count": int(item.get("count") or 0),
                "matches": int(item.get("matches") or 0),
                "rough_tokens": saved,
                "reason": "high rough estimate without confirmed outcome",
            }
        )
    return {
        "status": status,
        "confidence": confidence,
        "confirmed": len(confirmed),
        "rejected": len(rejected),
        "confirmed_actual_tokens_saved": sum(int(row.get("actual_tokens_saved") or 0) for row in confirmed),
        "confirmed_bug_prevented": sum(int(row.get("estimated_bug_prevented") or 0) for row in confirmed),
        "outcomes": len(outcome_rows),
        "successful_outcomes": sum(1 for row in outcome_rows if str(row.get("outcome") or "") in {"success", "verified", "fixed"}),
        "retrieval_rows": len(retrieval_rows),
        "estimated_rows": len(estimated_rows),
        "scored_rows": len(score_rows),
        "verified_top_rows": len(verified_rows),
        "avg_top_score": avg_score,
        "needs_review": needs_review,
        "truth_statement": "telemetry/proxy only; cannot prove actual token savings or avoided production bugs without confirmation",
    }


def record_gain_confirmation(
    action: str,
    query: str,
    *,
    note: str = "",
    task: str = "",
    actual_tokens_saved: int = 0,
    bug_prevented: bool = False,
) -> Dict[str, object]:
    if action not in {"confirmed", "rejected"}:
        raise ValueError("action must be confirmed or rejected")
    row = {
        "ts": utc_now(),
        "event": f"gain.{action}",
        "query": query,
        "task": task,
        "note": note,
        "actual_tokens_saved": int(actual_tokens_saved or 0),
        "estimated_bug_prevented": 1 if bug_prevented else 0,
        "estimate_kind": "human_confirmed_outcome" if action == "confirmed" else "human_rejected_outcome",
    }
    row["feedback"] = write_gain_feedback(row)
    append_jsonl(home_dir() / "gain.jsonl", row)
    return row


def record_task_outcome(
    event: str,
    text: str,
    *,
    project_id: str = "",
    matches: List[Dict[str, Any]] | None = None,
    verified: bool = False,
) -> Dict[str, object]:
    outcome = "verified" if verified else ("fixed" if event in {"fix", "bug"} else "success")
    row = {
        "ts": utc_now(),
        "event": f"outcome.{event}",
        "outcome": outcome,
        "project_id": project_id,
        "query": text[:300],
        "task": text[:300],
        "matched_cards": len(matches or []),
        "matched_card_ids": [item.get("id", "") for item in (matches or [])[:8]],
        "estimate_kind": "task_outcome_signal_not_proof",
        "verified": bool(verified),
    }
    row["feedback"] = write_gain_feedback(row, internal_only=True)
    append_jsonl(home_dir() / "gain.jsonl", row)
    return row


def write_gain_feedback(row: Dict[str, object], *, internal_only: bool = False) -> List[Dict[str, str]]:
    feedback: List[Dict[str, str]] = []
    feedback.append(write_gain_feedback_json(row))
    event = str(row.get("event") or "")
    if not internal_only and event == "gain.confirmed":
        feedback.append(write_project_wiki_gain_request(row))
    if not internal_only and (event == "gain.rejected" or int(row.get("estimated_bug_prevented") or 0) > 0):
        feedback.append(write_issue_gain_seed(row))
    return feedback


def write_gain_feedback_json(row: Dict[str, object]) -> Dict[str, str]:
    fid = gain_feedback_id(row)
    out = home_dir() / "outbox" / "gain-feedback" / f"{fid}.json"
    write_json(out, {"status": "pending_review", "kind": "xmem_gain_feedback", "row": row, "createdAt": utc_now()})
    return {"target": "gain-feedback", "status": "outbox", "path": str(out), "id": fid}


def write_project_wiki_gain_request(row: Dict[str, object]) -> Dict[str, str]:
    fid = gain_feedback_id(row)
    request = {
        "status": "pending",
        "risk": "low",
        "actor": "xmem-gain",
        "action": "review_xmem_gain_outcome",
        "targetEntityId": "unknown",
        "payload": {
            "type": "xmem_gain_outcome",
            "query": row.get("query", ""),
            "task": row.get("task", ""),
            "note": row.get("note", ""),
            "actualTokensSaved": row.get("actual_tokens_saved", 0),
            "source": "xmem gain confirm",
        },
        "validation": [{"label": "human/outcome signal captured", "ok": True, "detail": str(row.get("event", ""))}],
        "evidenceIds": [f"xmem-gain:{fid}"],
        "receivedAt": utc_now(),
        "id": "wr_gain_" + fid,
    }
    inbox = Path(os.environ.get("XMEM_PROJECT_WIKI", "/Users/xin/project-wiki")).expanduser() / "data" / "agent-inbox.jsonl"
    if inbox.parent.exists():
        append_jsonl(inbox, request)
        return {"target": "project-wiki", "status": "queued", "path": str(inbox), "id": request["id"]}
    out = home_dir() / "outbox" / "project-wiki" / f"{request['id']}.json"
    write_json(out, request)
    return {"target": "project-wiki", "status": "outbox", "path": str(out), "id": request["id"]}


def write_issue_gain_seed(row: Dict[str, object]) -> Dict[str, str]:
    fid = gain_feedback_id(row)
    out = home_dir() / "outbox" / "issue-tracking" / f"gain_{fid}.md"
    body = "\n".join(
        [
            "# XMEM Gain Feedback",
            "",
            f"- Issue: gain_{fid}",
            "- Source: xmem gain outcome",
            f"- Event: {row.get('event', '')}",
            f"- Query: {row.get('query', '')}",
            f"- Task: {row.get('task', '')}",
            f"- Actual tokens saved: {row.get('actual_tokens_saved', 0)}",
            f"- Bug prevented hint: {row.get('estimated_bug_prevented', 0)}",
            "",
            "## Note",
            str(row.get("note", "")),
            "",
            "## Review",
            "- Decide whether this should become an Issue Record bug-pattern, regression guard, or rejected gain calibration.",
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    return {"target": "issue-tracking", "status": "outbox", "path": str(out), "id": f"gain_{fid}"}


def gain_feedback_id(row: Dict[str, object]) -> str:
    raw = "|".join(str(row.get(key, "")) for key in ("event", "query", "task", "note", "ts"))
    return f"{slugify(str(row.get('query') or row.get('event') or 'gain'), 'gain')[:48]}-{hashlib.sha1(raw.encode()).hexdigest()[:10]}"


def format_gain_dashboard(data: Dict[str, object], *, color: bool = False, width: Optional[int] = None) -> str:
    observed = data.get("observed") if isinstance(data.get("observed"), dict) else {}
    by_event = data.get("by_event") if isinstance(data.get("by_event"), list) else []
    top_queries = data.get("top_queries") if isinstance(data.get("top_queries"), list) else []
    guardrails = data.get("recent_guardrails") if isinstance(data.get("recent_guardrails"), list) else []
    calibration = data.get("calibration") if isinstance(data.get("calibration"), dict) else {}
    hit_rate = float(observed.get("context_hit_rate", 0) or 0)
    max_event_saved = max([int(item.get("estimated_tokens_saved") or 0) for item in by_event] or [0])
    max_query_saved = max([int(item.get("estimated_tokens_saved") or 0) for item in top_queries] or [0])
    terminal_width = width or shutil.get_terminal_size((96, 24)).columns
    dashboard_width = min(104, max(72, terminal_width))
    impact_width = 10 if dashboard_width < 96 else 12
    event_width = min(22, max(16, dashboard_width - impact_width - 37))
    query_width = max(24, dashboard_width - impact_width - 32)
    title = paint("XMEM Gain 收益面板 (Global Scope)", "green", color)
    rule = paint("=" * dashboard_width, "dim", color)
    thin = paint("-" * dashboard_width, "dim", color)
    lines = [
        title,
        rule,
        "",
        metric("统计范围", gain_scope_text(data), color, value_style="dim"),
        metric("读取日志行数", int(data.get("rows") or 0), color),
        metric(
            "去重查询/card",
            f"{int(observed.get('unique_queries') or 0)} queries / {int(observed.get('unique_top_cards') or 0)} top cards",
            color,
        ),
        metric(
            "检索调用",
            f"{int(observed.get('retrieval_calls') or 0)} "
            f"(hit {int(observed.get('retrieval_hits') or 0)} / miss {int(observed.get('retrieval_misses') or 0)})",
            color,
        ),
        metric(
            "context 查询",
            f"{int(observed.get('context_queries') or 0)} "
            f"(hit {int(observed.get('context_hits') or 0)} / miss {int(observed.get('context_misses') or 0)})",
            color,
        ),
        metric(
            "preflight 预检",
            f"{int(observed.get('preflight_queries') or 0)} "
            f"(hit {int(observed.get('preflight_hits') or 0)} / miss {int(observed.get('preflight_misses') or 0)})",
            color,
        ),
        metric("context 命中率", f"{hit_rate:.1f}%（有候选率，非正确率）", color, value_style=rate_style(hit_rate)),
        metric("返回候选累计", int(data.get("matches") or 0), color),
        metric("check 运行次数", int(observed.get("guardrail_checks") or 0), color),
        metric("规则告警次数", int(observed.get("guardrail_prevented") or 0), color, value_style="yellow"),
        metric("理论少读 tokens", human_number(int(data.get("estimated_tokens_saved") or 0)), color, value_style="green"),
        metric("确认省 tokens", human_number(int(data.get("actual_tokens_saved") or 0)), color, value_style="green"),
        metric("风险提示次数", int(data.get("estimated_bug_prevented") or 0), color, value_style="yellow"),
        metric("日志计数字段", "rows / hit / miss / check / matches", color, value_style="dim"),
        metric("估算字段(非事实)", "理论少读 tokens；不是账单/真实省量", color, value_style="dim"),
        metric("命中口径", "hit=搜到候选，不代表人工确认正确", color, value_style="dim"),
        metric("收益口径", "未校准，只能看趋势，不能证明立竿见影", color, value_style="dim"),
        metric("token 估算公式", "context/preflight matches * 1200", color, value_style="dim"),
        metric(
            "自校准状态",
            f"{calibration_status_text(calibration)}；outcomes={int(calibration.get('outcomes') or 0)} avg top_score={float(calibration.get('avg_top_score') or 0):.2f}",
            color,
            value_style="yellow" if calibration.get("confidence") == "low" else "green",
        ),
        metric("命中率进度", f"{bar(hit_rate, color=color)} {paint(f'{hit_rate:.1f}%', rate_style(hit_rate), color)}", color),
        "",
        paint("按事件", "green", color),
        thin,
        f"{'#':>3}  {pad_display('事件', event_width)} {pad_display('次数', 6, 'right')} "
        f"{pad_display('粗估Token', 9, 'right')} {pad_display('Matches', 7, 'right')} "
        f"{pad_display('风险', 4, 'right')}  {pad_display('影响', impact_width)}",
    ]
    for idx, item in enumerate(by_event[:10], 1):
        saved = int(item.get("estimated_tokens_saved") or 0)
        bugs = int(item.get("estimated_bug_prevented") or 0)
        event = compact_cell(item.get("event", ""), event_width)
        lines.append(
            f"{idx:>3}. {cell(event, event_width, 'cyan', color)} "
            f"{int(item.get('count') or 0):>6} "
            f"{paint(f'{human_number(saved):>9}', 'green' if saved else 'dim', color)} "
            f"{int(item.get('matches') or 0):>7} "
            f"{paint(f'{bugs:>4}', 'yellow' if bugs else 'dim', color)}  "
            f"{impact_bar(saved, max_event_saved, impact_width, color=color)}"
        )
    if not by_event:
        lines.append("  - 暂无 gain event 日志")
    lines.extend([
        "",
        paint("Top 查询", "green", color),
        thin,
        f"{'#':>3}  {pad_display('查询', query_width)} {pad_display('次数', 6, 'right')} "
        f"{pad_display('粗估Token', 9, 'right')} {pad_display('Matches', 7, 'right')}  "
        f"{pad_display('影响', impact_width)}",
    ])
    for idx, item in enumerate(top_queries[:10], 1):
        query = compact_cell(item.get("query", ""), query_width)
        saved = int(item.get("estimated_tokens_saved") or 0)
        lines.append(
            f"{idx:>3}. {cell(query, query_width, 'cyan', color)} "
            f"{int(item.get('count') or 0):>6} "
            f"{paint(f'{human_number(saved):>9}', 'green' if saved else 'dim', color)} "
            f"{int(item.get('matches') or 0):>7}  "
            f"{impact_bar(saved, max_query_saved, impact_width, color=color)}"
        )
    if not top_queries:
        lines.append("  - 暂无 query event 日志")
    review_items = calibration.get("needs_review") if isinstance(calibration.get("needs_review"), list) else []
    lines.extend(["", paint("待校准高估项", "green", color), thin])
    if review_items:
        for item in review_items[:5]:
            lines.append(
                f"- {compact_cell(item.get('query', ''), max(24, query_width - 10))}: "
                f"粗估={human_number(int(item.get('rough_tokens') or 0))} "
                f"count={int(item.get('count') or 0)} matches={int(item.get('matches') or 0)}"
            )
    else:
        lines.append("- 暂无需要校准的高估项")
    lines.extend(["", paint("最近 guardrail", "green", color), thin])
    if guardrails:
        for item in guardrails[-5:]:
            warnings = int(item.get("warnings") or 0)
            event_style = "yellow" if warnings else "cyan"
            lines.append(
                f"- {paint(item.get('event'), event_style, color)}: warnings={paint(warnings, 'yellow' if warnings else 'dim', color)} "
                f"matched_cards={int(item.get('matched_cards') or 0)}"
            )
    else:
        lines.append("- 暂无 guardrail check 日志")
    return "\n".join(lines)


def human_number(value: int) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def gain_scope_text(data: Dict[str, object]) -> str:
    if data.get("scope") == "all":
        return "全部 gain 日志（含 agent 自测）"
    limit = data.get("limit")
    return f"最近 {int(limit or 0)} 条 gain 日志（含 agent 自测）"


def calibration_status_text(calibration: Dict[str, object]) -> str:
    status = str(calibration.get("status") or "telemetry_only")
    confidence = str(calibration.get("confidence") or "low")
    if status == "partially_calibrated":
        label = "部分校准"
    elif status == "proxy_only":
        label = "只有 proxy"
    else:
        label = "只有 telemetry"
    return f"{label} / {confidence}"


def compact_cell(value: object, width: int) -> str:
    text = " ".join(str(value or "").split())
    return truncate_display(text, width)


def display_width(value: object) -> int:
    width = 0
    for char in str(value):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def truncate_display(value: object, width: int) -> str:
    text = str(value or "")
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    ellipsis = "..."
    ellipsis_width = display_width(ellipsis)
    if width <= ellipsis_width:
        return "." * width
    out = []
    used = 0
    target = width - ellipsis_width
    for char in text:
        char_width = display_width(char)
        if used + char_width > target:
            break
        out.append(char)
        used += char_width
    return "".join(out) + ellipsis


def pad_display(value: object, width: int, align: str = "left") -> str:
    text = truncate_display(value, width)
    padding = max(0, width - display_width(text))
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def bar(percent: float, width: int = 28, *, color: bool = False) -> str:
    filled = max(0, min(width, round(width * percent / 100)))
    if not color:
        return "[" + "#" * filled + "." * (width - filled) + "]"
    return "[" + paint(" " * filled, f"bg_{rate_style(percent)}", color) + paint(" " * (width - filled), "bg_dim", color) + "]"


def impact_bar(value: int, max_value: int, width: int = 18, *, color: bool = False) -> str:
    if max_value <= 0 or value <= 0:
        return paint("." * width, "dim", color)
    filled = max(1, min(width, round(width * value / max_value)))
    if not color:
        return "#" * filled + "." * (width - filled)
    return paint(" " * filled, "bg_cyan", color) + paint(" " * (width - filled), "bg_dim", color)


def metric(label: str, value: object, color: bool, *, value_style: str = "plain") -> str:
    return f"{pad_display(label + ':', 24)} {paint(value, value_style, color)}"


def rate_style(percent: float) -> str:
    if percent >= 80:
        return "green"
    if percent >= 50:
        return "yellow"
    return "red"


ANSI = {
    "plain": "",
    "dim": "\033[2m",
    "green": "\033[92m",
    "cyan": "\033[96m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "bg_green": "\033[48;5;71m",
    "bg_yellow": "\033[48;5;179m",
    "bg_red": "\033[48;5;167m",
    "bg_cyan": "\033[48;5;73m",
    "bg_dim": "\033[48;5;238m",
}


def paint(value: object, style: str, enabled: bool) -> str:
    text = str(value)
    code = ANSI.get(style, "")
    if not enabled or not code:
        return text
    return f"{code}{text}\033[0m"


def cell(value: object, width: int, style: str, color: bool) -> str:
    return paint(pad_display(value, width), style, color)
