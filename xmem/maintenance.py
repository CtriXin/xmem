from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .memory import (
    card_semantic_text,
    current_project,
    load_pending_records,
    semantic_fingerprint,
    ttl_decay_penalty,
)
from .store import connect, rows
from .util import utc_now


def build_memory_maintenance(*, cwd: Path | None = None, limit: int = 250) -> Dict[str, Any]:
    root, project = current_project(cwd)
    with connect() as conn:
        cards = rows(conn, "SELECT * FROM cards ORDER BY updated_at DESC LIMIT ?", (limit,))
    pending = [record for _path, record in load_pending_records(root=root, include_all=False)]
    expired = expired_cards(cards)
    duplicate_cards = duplicate_card_candidates(cards)
    duplicate_pending = duplicate_pending_candidates(pending)
    return {
        "schema": "xmem.memory_maintenance.v1",
        "generated_at": utc_now(),
        "project": project.get("project_id") if project else "",
        "cwd": str(root or cwd or ""),
        "status": "review_needed" if expired or duplicate_cards or duplicate_pending else "ok",
        "expired_or_decayed": expired,
        "duplicate_cards": duplicate_cards,
        "duplicate_pending": duplicate_pending,
        "recommendations": recommendations(expired, duplicate_cards, duplicate_pending),
    }


def expired_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for card in cards:
        penalty = ttl_decay_penalty(card)
        if penalty <= 0:
            continue
        out.append(
            {
                "id": card.get("card_id"),
                "title": card.get("title"),
                "truth": card.get("status"),
                "penalty": round(penalty, 3),
                "source_ref": card.get("source_ref") or card.get("path"),
                "action": "review_ttl_or_supersede",
            }
        )
    return out[:30]


def duplicate_card_candidates(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    prepared = []
    for card in cards[:300]:
        prepared.append((card, semantic_fingerprint(card_semantic_text(card))))
    for index, (left, left_fp) in enumerate(prepared):
        if not left_fp:
            continue
        for right, right_fp in prepared[index + 1 : min(index + 60, len(prepared))]:
            if left.get("card_id") == right.get("card_id"):
                continue
            if left.get("project_id") != right.get("project_id") or left.get("type") != right.get("type"):
                continue
            score = fingerprint_similarity(left_fp, right_fp)
            if score >= 0.72:
                candidates.append(
                    {
                        "left": left.get("card_id"),
                        "right": right.get("card_id"),
                        "project_id": left.get("project_id"),
                        "type": left.get("type"),
                        "similarity": round(score, 3),
                        "action": "review_duplicate_or_supersede",
                    }
                )
                if len(candidates) >= 30:
                    return candidates
    return candidates


def duplicate_pending_candidates(pending: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    active = [item for item in pending if item.get("status") == "pending"]
    prepared = [(item, semantic_fingerprint(str(item.get("summary") or item.get("title") or ""))) for item in active]
    for index, (left, left_fp) in enumerate(prepared):
        for right, right_fp in prepared[index + 1 :]:
            if left.get("type") != right.get("type"):
                continue
            score = fingerprint_similarity(left_fp, right_fp)
            if score >= 0.8:
                candidates.append(
                    {
                        "left": left.get("id"),
                        "right": right.get("id"),
                        "type": left.get("type"),
                        "similarity": round(score, 3),
                        "action": "merge_pending_or_forget_duplicate",
                    }
                )
                if len(candidates) >= 30:
                    return candidates
    return candidates


def fingerprint_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(8, min(len(left), len(right)))


def recommendations(
    expired: List[Dict[str, Any]],
    duplicate_cards: List[Dict[str, Any]],
    duplicate_pending: List[Dict[str, Any]],
) -> List[str]:
    out: List[str] = []
    if expired:
        out.append("review expired TTL cards; use xmem supersede/forget only after evidence confirms replacement")
    if duplicate_cards:
        out.append("review duplicate card candidates; prefer supersedes over overwriting old memory")
    if duplicate_pending:
        out.append("merge or forget duplicate pending memories before promotion")
    if not out:
        out.append("no maintenance action needed")
    return out


def format_memory_maintenance(packet: Dict[str, Any]) -> str:
    lines = ["xmem_memory_maintenance:", f"  status: {packet.get('status')}", f"  project: {packet.get('project') or ''}"]
    lines.append(f"  expired_or_decayed: {len(packet.get('expired_or_decayed') or [])}")
    for item in (packet.get("expired_or_decayed") or [])[:8]:
        lines.append(f"  - expired: {item.get('id')} penalty={item.get('penalty')} source={item.get('source_ref')}")
    lines.append(f"  duplicate_cards: {len(packet.get('duplicate_cards') or [])}")
    for item in (packet.get("duplicate_cards") or [])[:8]:
        lines.append(f"  - duplicate_card: {item.get('left')} ~= {item.get('right')} similarity={item.get('similarity')}")
    lines.append(f"  duplicate_pending: {len(packet.get('duplicate_pending') or [])}")
    for item in (packet.get("duplicate_pending") or [])[:8]:
        lines.append(f"  - duplicate_pending: {item.get('left')} ~= {item.get('right')} similarity={item.get('similarity')}")
    lines.append("  recommendations:")
    lines.extend(f"  - {item}" for item in packet.get("recommendations") or [])
    return "\n".join(lines)
