from __future__ import annotations

from collections import Counter
import shutil
import unicodedata
from typing import Any, Dict, List, Optional

from .util import home_dir, load_jsonl


def summarize_gain(limit: int = 500) -> Dict[str, object]:
    rows = load_jsonl(home_dir() / "gain.jsonl", limit=limit)
    events = Counter(row.get("event", "unknown") for row in rows)
    queries = Counter(str(row.get("query") or "") for row in rows if row.get("query"))
    tokens = sum(int(row.get("estimated_tokens_saved") or 0) for row in rows)
    bugs = sum(int(row.get("estimated_bug_prevented") or 0) for row in rows)
    matches = sum(int(row.get("matches") or 0) for row in rows)
    event_rows = aggregate_events(rows)
    query_rows = aggregate_queries(rows)
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
        "estimated_tokens_saved": tokens,
        "estimated_bug_prevented": bugs,
        "matches": matches,
        "rows": len(rows),
        "observed": {
            "logged_rows": len(rows),
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


def format_gain_dashboard(data: Dict[str, object], *, color: bool = False, width: Optional[int] = None) -> str:
    observed = data.get("observed") if isinstance(data.get("observed"), dict) else {}
    by_event = data.get("by_event") if isinstance(data.get("by_event"), list) else []
    top_queries = data.get("top_queries") if isinstance(data.get("top_queries"), list) else []
    guardrails = data.get("recent_guardrails") if isinstance(data.get("recent_guardrails"), list) else []
    hit_rate = float(observed.get("context_hit_rate", 0) or 0)
    max_event_saved = max([int(item.get("estimated_tokens_saved") or 0) for item in by_event] or [0])
    max_query_saved = max([int(item.get("estimated_tokens_saved") or 0) for item in top_queries] or [0])
    terminal_width = width or shutil.get_terminal_size((96, 24)).columns
    dashboard_width = min(104, max(72, terminal_width))
    impact_width = 10 if dashboard_width < 96 else 12
    event_width = min(22, max(16, dashboard_width - impact_width - 37))
    query_width = max(24, dashboard_width - impact_width - 32)
    title = paint("XMEM Gain (Global Scope)", "green", color)
    rule = paint("=" * dashboard_width, "dim", color)
    thin = paint("-" * dashboard_width, "dim", color)
    lines = [
        title,
        rule,
        "",
        metric("Observed log rows", int(data.get("rows") or 0), color),
        metric(
            "Retrieval calls",
            f"{int(observed.get('retrieval_calls') or 0)} "
            f"(hit {int(observed.get('retrieval_hits') or 0)} / miss {int(observed.get('retrieval_misses') or 0)})",
            color,
        ),
        metric(
            "Context queries",
            f"{int(observed.get('context_queries') or 0)} "
            f"(hit {int(observed.get('context_hits') or 0)} / miss {int(observed.get('context_misses') or 0)})",
            color,
        ),
        metric(
            "Preflight queries",
            f"{int(observed.get('preflight_queries') or 0)} "
            f"(hit {int(observed.get('preflight_hits') or 0)} / miss {int(observed.get('preflight_misses') or 0)})",
            color,
        ),
        metric("Context hit rate", f"{hit_rate:.1f}%", color, value_style=rate_style(hit_rate)),
        metric("Matches returned", int(data.get("matches") or 0), color),
        metric("Guardrail checks", int(observed.get("guardrail_checks") or 0), color),
        metric("Rule prevented events", int(observed.get("guardrail_prevented") or 0), color, value_style="yellow"),
        metric("Est. tokens saved", human_number(int(data.get("estimated_tokens_saved") or 0)), color, value_style="green"),
        metric("Est. bugs prevented", int(data.get("estimated_bug_prevented") or 0), color, value_style="yellow"),
        metric("Accounting basis", "hit/miss/check logged; savings estimated", color, value_style="dim"),
        metric("Token formula", "context/preflight matches * 1200", color, value_style="dim"),
        metric("Efficiency meter", f"{bar(hit_rate, color=color)} {paint(f'{hit_rate:.1f}%', rate_style(hit_rate), color)}", color),
        "",
        paint("By Event", "green", color),
        thin,
        f"{'#':>3}  {pad_display('Event', event_width)} {'Count':>6} {'EstSaved':>9} {'Matches':>7} {'Bug':>4}  {pad_display('Impact', impact_width)}",
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
        lines.append("  - no gain events logged yet")
    lines.extend([
        "",
        paint("Top Queries", "green", color),
        thin,
        f"{'#':>3}  {pad_display('Query', query_width)} {'Count':>6} {'EstSaved':>9} {'Matches':>7}  {pad_display('Impact', impact_width)}",
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
        lines.append("  - no query events logged yet")
    lines.extend(["", paint("Recent Guardrails", "green", color), thin])
    if guardrails:
        for item in guardrails[-5:]:
            warnings = int(item.get("warnings") or 0)
            event_style = "yellow" if warnings else "cyan"
            lines.append(
                f"- {paint(item.get('event'), event_style, color)}: warnings={paint(warnings, 'yellow' if warnings else 'dim', color)} "
                f"matched_cards={int(item.get('matched_cards') or 0)}"
            )
    else:
        lines.append("- no guardrail checks logged yet")
    return "\n".join(lines)


def human_number(value: int) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


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
    return "[" + paint("#" * filled, rate_style(percent), color) + paint("." * (width - filled), "dim", color) + "]"


def impact_bar(value: int, max_value: int, width: int = 18, *, color: bool = False) -> str:
    if max_value <= 0 or value <= 0:
        return paint("." * width, "dim", color)
    filled = max(1, min(width, round(width * value / max_value)))
    if not color:
        return "#" * filled + "." * (width - filled)
    return paint(" " * filled, "bg_cyan", color) + paint(" " * (width - filled), "bg_dim", color)


def metric(label: str, value: object, color: bool, *, value_style: str = "plain") -> str:
    return f"{label + ':':<24} {paint(value, value_style, color)}"


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
