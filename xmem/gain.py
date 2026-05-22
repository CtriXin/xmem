from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .util import home_dir, load_jsonl


def summarize_gain(limit: int = 500) -> Dict[str, object]:
    rows = load_jsonl(home_dir() / "gain.jsonl", limit=limit)
    events = Counter(row.get("event", "unknown") for row in rows)
    tokens = sum(int(row.get("estimated_tokens_saved") or 0) for row in rows)
    bugs = sum(int(row.get("estimated_bug_prevented") or 0) for row in rows)
    matches = sum(int(row.get("matches") or 0) for row in rows)
    return {"events": dict(events), "estimated_tokens_saved": tokens, "estimated_bug_prevented": bugs, "matches": matches, "rows": len(rows)}
