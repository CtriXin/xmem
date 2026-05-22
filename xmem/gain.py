from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .util import home_dir, load_jsonl


def summarize_gain(limit: int = 500) -> Dict[str, object]:
    rows = load_jsonl(home_dir() / "gain.jsonl", limit=limit)
    events = Counter(row.get("event", "unknown") for row in rows)
    queries = Counter(str(row.get("query") or "") for row in rows if row.get("query"))
    tokens = sum(int(row.get("estimated_tokens_saved") or 0) for row in rows)
    bugs = sum(int(row.get("estimated_bug_prevented") or 0) for row in rows)
    matches = sum(int(row.get("matches") or 0) for row in rows)
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
        "top_queries": [{"query": query, "count": count} for query, count in queries.most_common(10)],
        "recent_queries": recent_queries,
        "recent_guardrails": guardrails,
    }
