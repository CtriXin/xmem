"""hook.inject 埋点与消费率统计。

回答一个问题：agent-hook 注入进对话的 card，assistant 后来到底引用了没有。
写入侧只在 hook 真正发出非空文本时追加 gain.jsonl 事件；读取侧扫描
transcript 的 assistant 行找 shown card id。口径是下限估计：assistant 没
逐字提 id 但用了内容的情况测不到，也不代表召回内容正确。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .util import append_jsonl, home_dir, load_jsonl, utc_now

TRANSCRIPT_MAX_BYTES = 50_000_000


def record_hook_inject(result: Dict[str, Any], output: str) -> Dict[str, Any]:
    recall = result.get("recall") or {}
    memories = recall.get("memories") or []
    card_ids = [str(item.get("id") or "") for item in memories if item.get("id")]
    shown = [cid for cid in card_ids if cid in output]
    row = {
        "ts": utc_now(),
        "event": "hook.inject",
        "source": "agent-hook",
        "host": str(result.get("host") or ""),
        "hook_event": str(result.get("event") or ""),
        "action": str(result.get("action") or ""),
        "cwd": str(result.get("cwd") or ""),
        "project_id": str((recall.get("scope") or {}).get("project_id") or ""),
        "session_id": str(result.get("session_id") or ""),
        "transcript_path": str(result.get("transcript_path") or ""),
        "card_ids": card_ids,
        "shown_card_ids": shown,
        "pending_count": len(recall.get("pending") or []),
        "output_chars": len(output),
    }
    append_jsonl(home_dir() / "gain.jsonl", row)
    return row


def build_hook_outcomes(*, days: int = 14, host: str = "") -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [
        row
        for row in load_jsonl(home_dir() / "gain.jsonl")
        if row.get("event") == "hook.inject"
        and (not host or row.get("host") == host)
        and _row_in_window(row, cutoff)
    ]

    transcript_cache: Dict[str, Optional[str]] = {}
    card_injected: Counter = Counter()
    card_consumed: Counter = Counter()
    by_host: Dict[str, Dict[str, int]] = {}
    injects_no_cards = 0
    injects_unmeasured = 0
    injects_consumed = 0
    measured = 0

    for row in rows:
        host_key = str(row.get("host") or "unknown")
        host_stats = by_host.setdefault(host_key, {"injects": 0, "measured": 0, "consumed": 0})
        host_stats["injects"] += 1
        shown = [str(cid) for cid in row.get("shown_card_ids") or [] if cid]
        if not shown:
            injects_no_cards += 1
            continue
        for cid in shown:
            card_injected[cid] += 1
        text = _assistant_text(str(row.get("transcript_path") or ""), transcript_cache)
        if text is None:
            injects_unmeasured += 1
            continue
        measured += 1
        host_stats["measured"] += 1
        hit_ids = [cid for cid in shown if cid in text]
        for cid in hit_ids:
            card_consumed[cid] += 1
        if hit_ids:
            injects_consumed += 1
            host_stats["consumed"] += 1

    cards = [
        {"card_id": cid, "injected": count, "consumed": card_consumed.get(cid, 0)}
        for cid, count in card_injected.most_common(20)
    ]
    return {
        "schema": "xmem.hook_outcomes.v1",
        "generated_at": utc_now(),
        "window_days": days,
        "host_filter": host,
        "injects_total": len(rows),
        "injects_with_cards": len(rows) - injects_no_cards,
        "injects_no_cards": injects_no_cards,
        "injects_measured": measured,
        "injects_unmeasured": injects_unmeasured,
        "injects_consumed": injects_consumed,
        "consumption_rate": round(injects_consumed * 100.0 / measured, 1) if measured else None,
        "by_host": by_host,
        "cards": cards,
        "caveats": [
            "引用=同一 transcript 的 assistant 侧行内出现 shown card id；下限估计",
            "不代表召回内容正确，只代表 assistant 至少提到了这张卡",
            "transcript 缺失/被清理的注入计入 unmeasured，不进消费率分母",
        ],
    }


def format_hook_outcomes(data: Dict[str, Any]) -> str:
    rate = data.get("consumption_rate")
    lines = [
        f"XMEM Hook 注入消费率（近 {data['window_days']} 天"
        + (f"，host={data['host_filter']}" if data.get("host_filter") else "")
        + "）",
        f"  注入次数:        {data['injects_total']}",
        f"  含卡片注入:      {data['injects_with_cards']} （无卡片纯噪音注入: {data['injects_no_cards']}）",
        f"  可测注入:        {data['injects_measured']} （unmeasured: {data['injects_unmeasured']}）",
        f"  被引用注入:      {data['injects_consumed']}",
        f"  消费率:          {'--' if rate is None else f'{rate}%'}（分母=可测含卡注入）",
    ]
    for host_key, stats in sorted((data.get("by_host") or {}).items()):
        lines.append(
            f"  host {host_key}: injects={stats['injects']} measured={stats['measured']} consumed={stats['consumed']}"
        )
    cards = data.get("cards") or []
    if cards:
        lines.append("  Top cards:")
        for item in cards[:10]:
            lines.append(
                f"  - {item['card_id']} injected={item['injected']} consumed={item['consumed']}"
            )
    lines.append("  口径:")
    for caveat in data.get("caveats") or []:
        lines.append(f"  - {caveat}")
    return "\n".join(lines)


def _row_in_window(row: Dict[str, Any], cutoff: datetime) -> bool:
    raw = str(row.get("ts") or "")
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def _assistant_text(transcript_path: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    if not transcript_path:
        return None
    if transcript_path in cache:
        return cache[transcript_path]
    text = _load_assistant_text(Path(transcript_path))
    cache[transcript_path] = text
    return text


def _load_assistant_text(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    chunks: List[str] = []
    read_bytes = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                read_bytes += len(line)
                if read_bytes > TRANSCRIPT_MAX_BYTES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _is_assistant_entry(entry):
                    chunks.append(line)
    except OSError:
        return None
    return "\n".join(chunks)


def _is_assistant_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    # Claude Code transcript: {"type": "assistant", "message": {...}}. Hook 注入
    # 本身落在 user/system 行里，所以只数 assistant 行才不会把注入算成引用。
    if entry.get("type") == "assistant":
        return True
    if entry.get("type") in {"user", "system", "summary", "progress"}:
        return False
    role = entry.get("role")
    message = entry.get("message")
    if not role and isinstance(message, dict):
        role = message.get("role")
    return role == "assistant"
