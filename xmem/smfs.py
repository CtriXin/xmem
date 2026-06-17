from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .memory import build_recall, compact_text, current_project
from .store import connect, rows
from .util import home_dir, slugify, utc_now


def export_smfs(*, cwd: Path | None = None, limit: int = 500) -> Dict[str, Any]:
    root, project = current_project(cwd)
    out_root = home_dir() / "smfs"
    cards_dir = out_root / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        cards = rows(conn, "SELECT * FROM cards ORDER BY updated_at DESC LIMIT ?", (limit,))

    written: List[Dict[str, Any]] = []
    for card in cards:
        project_id = str(card.get("project_id") or "global")
        card_id = str(card.get("card_id") or "memory")
        path = cards_dir / slugify(project_id, "project") / f"{slugify(card_id, 'memory')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = render_card_markdown(card)
        path.write_text(body, encoding="utf-8")
        written.append({"id": card_id, "project_id": project_id, "path": str(path)})

    index_path = out_root / "index.jsonl"
    index_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in written) + ("\n" if written else ""), encoding="utf-8")
    return {
        "schema": "xmem.smfs_export.v1",
        "generated_at": utc_now(),
        "root": str(out_root),
        "cards": len(written),
        "index": str(index_path),
        "project": project.get("project_id") if project else "",
        "cwd": str(root or cwd or ""),
    }


def grep_smfs(query: str, *, cwd: Path | None = None, limit: int = 8) -> Dict[str, Any]:
    packet = build_recall(query, cwd=cwd, limit=limit, include_pending=True)
    hits = []
    for item in packet.get("memories") or []:
        hits.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "score": item.get("score"),
                "truth": item.get("truth"),
                "why": item.get("why") or [],
                "source_ref": item.get("source_ref") or item.get("path"),
                "smfs_path": smfs_path_for(item),
                "snippet": compact_text(str(item.get("summary") or ""), 220),
            }
        )
    return {
        "schema": "xmem.smfs_grep.v1",
        "query": query,
        "mode": "semantic_grep_lite",
        "signals": packet.get("ranking", {}).get("signals") or [],
        "hits": hits,
        "pending_hints": packet.get("pending") or [],
        "token_estimate": packet.get("token_estimate", 0),
    }


def format_smfs_export(packet: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "xmem_smfs:",
            f"  root: {packet.get('root')}",
            f"  cards: {packet.get('cards')}",
            f"  index: {packet.get('index')}",
        ]
    )


def format_smfs_grep(packet: Dict[str, Any]) -> str:
    lines = ["xmem_semantic_grep:", f"  query: {packet.get('query')}", f"  mode: {packet.get('mode')}", "  hits:"]
    for hit in packet.get("hits") or []:
        lines.append(
            f"  - id: {hit.get('id')} score={hit.get('score')} truth={hit.get('truth')} title={hit.get('title')}"
        )
        if hit.get("snippet"):
            lines.append(f"    snippet: {hit.get('snippet')}")
        if hit.get("source_ref"):
            lines.append(f"    source_ref: {hit.get('source_ref')}")
        lines.append(f"    smfs_path: {hit.get('smfs_path')}")
    if not packet.get("hits"):
        lines.append("  - none")
    pending = packet.get("pending_hints") or []
    if pending:
        lines.append("  pending_hints:")
        for item in pending[:3]:
            lines.append(f"  - {item.get('id')} type={item.get('type')} truth=unpromoted_pending")
    return "\n".join(lines)


def render_card_markdown(card: Dict[str, Any]) -> str:
    aliases = []
    try:
        aliases = json.loads(card.get("aliases_json") or "[]")
    except Exception:
        pass
    lines = [
        f"# {card.get('title') or card.get('card_id')}",
        "",
        f"id: {card.get('card_id')}",
        f"type: {card.get('type')}",
        f"project_id: {card.get('project_id')}",
        f"truth: {card.get('status')}",
        f"confidence: {card.get('confidence')}",
        f"updated_at: {card.get('updated_at')}",
        f"source_ref: {card.get('source_ref') or card.get('path')}",
        "aliases: " + ", ".join(str(item) for item in aliases),
        "",
        "## Body",
        "",
        str(card.get("body") or "").strip(),
        "",
    ]
    return "\n".join(lines)


def smfs_path_for(item: Dict[str, Any]) -> str:
    card_id = str(item.get("id") or "memory")
    project = source_project_hint(item)
    return str(home_dir() / "smfs" / "cards" / slugify(project, "project") / f"{slugify(card_id, 'memory')}.md")


def source_project_hint(item: Dict[str, Any]) -> str:
    source = str(item.get("source_ref") or item.get("path") or "")
    match = re.search(r"/([^/]+)/\.xmem/cards/", source)
    if match:
        return match.group(1)
    return "global"
