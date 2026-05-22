from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

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
            "context_queries": context_total,
            "context_hits": events.get("context.hit", 0),
            "context_misses": events.get("context.miss", 0),
            "context_hit_rate": hit_rate,
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


def format_gain_dashboard(data: Dict[str, object], *, color: bool = False) -> str:
    observed = data.get("observed") if isinstance(data.get("observed"), dict) else {}
    by_event = data.get("by_event") if isinstance(data.get("by_event"), list) else []
    top_queries = data.get("top_queries") if isinstance(data.get("top_queries"), list) else []
    guardrails = data.get("recent_guardrails") if isinstance(data.get("recent_guardrails"), list) else []
    hit_rate = float(observed.get("context_hit_rate", 0) or 0)
    max_event_saved = max([int(item.get("estimated_tokens_saved") or 0) for item in by_event] or [0])
    max_query_saved = max([int(item.get("estimated_tokens_saved") or 0) for item in top_queries] or [0])
    title = paint("XMEM Gain (Global Scope)", "green", color)
    rule = paint("=" * 88, "dim", color)
    thin = paint("-" * 88, "dim", color)
    lines = [
        title,
        rule,
        "",
        metric("Observed log rows", int(data.get("rows") or 0), color),
        metric(
            "Context queries",
            f"{int(observed.get('context_queries') or 0)} "
            f"(hit {int(observed.get('context_hits') or 0)} / miss {int(observed.get('context_misses') or 0)})",
            color,
        ),
        metric("Context hit rate", f"{hit_rate:.1f}%", color, value_style=rate_style(hit_rate)),
        metric("Matches returned", int(data.get("matches") or 0), color),
        metric("Guardrail checks", int(observed.get("guardrail_checks") or 0), color),
        metric("Rule prevented events", int(observed.get("guardrail_prevented") or 0), color, value_style="yellow"),
        metric("Est. tokens saved", human_number(int(data.get("estimated_tokens_saved") or 0)), color, value_style="green"),
        metric("Est. bugs prevented", int(data.get("estimated_bug_prevented") or 0), color, value_style="yellow"),
        metric("Confidence note", "events observed; token/bug savings estimated", color, value_style="dim"),
        metric("Efficiency meter", f"{bar(hit_rate, color=color)} {paint(f'{hit_rate:.1f}%', rate_style(hit_rate), color)}", color),
        "",
        paint("By Event", "green", color),
        thin,
        f"{'#':>3}  {'Event':<22} {'Count':>7} {'EstSaved':>10} {'Matches':>8} {'Bugs':>5}  {'Impact':<18}",
    ]
    for idx, item in enumerate(by_event[:10], 1):
        saved = int(item.get("estimated_tokens_saved") or 0)
        bugs = int(item.get("estimated_bug_prevented") or 0)
        event = compact_cell(item.get("event", ""), 22)
        lines.append(
            f"{idx:>3}. {cell(event, 22, 'cyan', color)} "
            f"{int(item.get('count') or 0):>7} "
            f"{paint(f'{human_number(saved):>10}', 'green' if saved else 'dim', color)} "
            f"{int(item.get('matches') or 0):>8} "
            f"{paint(f'{bugs:>5}', 'yellow' if bugs else 'dim', color)}  "
            f"{impact_bar(saved, max_event_saved, color=color)}"
        )
    if not by_event:
        lines.append("  - no gain events logged yet")
    lines.extend([
        "",
        paint("Top Queries", "green", color),
        thin,
        f"{'#':>3}  {'Query':<42} {'Count':>7} {'EstSaved':>10} {'Matches':>8}  {'Impact':<18}",
    ])
    for idx, item in enumerate(top_queries[:10], 1):
        query = compact_cell(item.get("query", ""), 42)
        saved = int(item.get("estimated_tokens_saved") or 0)
        lines.append(
            f"{idx:>3}. {cell(query, 42, 'cyan', color)} "
            f"{int(item.get('count') or 0):>7} "
            f"{paint(f'{human_number(saved):>10}', 'green' if saved else 'dim', color)} "
            f"{int(item.get('matches') or 0):>8}  "
            f"{impact_bar(saved, max_query_saved, color=color)}"
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
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."


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
    text = str(value)
    return paint(f"{text:<{width}}", style, color)
