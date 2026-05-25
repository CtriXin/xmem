from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .store import connect, rows
from .util import append_jsonl, home_dir, load_jsonl, normalize_text, query_hash, query_terms, query_variants, utc_now


TOKEN_SAVING_EVENTS = {"context", "preflight", "resume", "gateway"}


def search_cards(query: str, limit: int = 10, *, record_gain: bool = True, gain_event: str = "search") -> List[Dict[str, Any]]:
    terms = query_terms(query)
    variants = query_variants(query)
    suppressions = suppressions_for_query(query)
    with connect() as conn:
        cards = rows(conn, "SELECT * FROM cards")
        projects = {r["project_id"]: r for r in rows(conn, "SELECT * FROM projects")}
    scored: List[Dict[str, Any]] = []
    for card in cards:
        aliases = json.loads(card.get("aliases_json") or "[]")
        project = projects.get(card.get("project_id") or "") or {}
        project_aliases = json.loads(project.get("aliases_json") or "[]")
        strong_parts = [
            card.get("card_id", ""),
            card.get("title", ""),
            project.get("project_id", ""),
            project.get("name", ""),
            project.get("root", ""),
            project.get("remote", ""),
            project.get("branch", ""),
        ]
        alias_parts = aliases + project_aliases
        meta_parts = strong_parts + [card.get("type", ""), card.get("source", "")] + alias_parts
        body = str(card.get("body", ""))
        meta = "\n".join(str(x).lower() for x in meta_parts)
        alias_values = alias_parts + [card.get("title", ""), card.get("card_id", "")]
        alias_text = "\n".join(str(x).lower() for x in alias_values)
        alias_norms = [normalize_text(x) for x in alias_values if normalize_text(x)]
        meta_norm = normalize_text(meta)
        alias_norm = normalize_text(alias_text)
        body_norm = normalize_text(body[:20000])
        score = 0.0
        why: List[str] = []
        for variant in variants:
            loose_variant = normalize_text(variant)
            if not loose_variant:
                continue
            if loose_variant in alias_norms:
                score += 16.0
                why.append(f"exact_alias:{variant}")
            elif loose_variant in alias_norm:
                score += 12.0
                why.append(f"alias_match:{variant}")
            elif loose_variant in meta_norm:
                score += 8.0
                why.append(f"metadata_match:{variant}")
            elif loose_variant in body_norm:
                score += 1.5
                why.append(f"body_match:{variant}")
        for term in terms:
            if not term:
                continue
            term_norm = normalize_text(term)
            if term_norm and term_norm in alias_norm:
                score += 3.0
                why.append(f"alias_term:{term}")
            elif term_norm and term_norm in meta_norm:
                score += 2.0
                why.append(f"metadata_term:{term}")
            elif term_norm and term_norm in body_norm:
                # Body matches are evidence hints, not identity proof.
                score += 0.35
                why.append(f"body_term:{term}")
        if score and card.get("source") == "project-wiki" and card.get("type", "").startswith("wiki."):
            score += 0.8
            why.append("registry_source:project-wiki")
        if score and card.get("type") in {"correction", "alias-correction"}:
            score += 2.0
            why.append("correction_overlay")
        if score and card.get("type") == "evidence.issue":
            score -= 0.25
            why.append("evidence_source:issue-tracking")
        if score:
            card = dict(card)
            raw_score = score * float(card.get("confidence") or 0.5)
            suppressed = suppressions.get(str(card.get("card_id") or ""))
            if suppressed:
                raw_score *= 0.2
                why.append(f"suppressed_for_query:{suppressed.get('reason') or 'irrelevant'}")
                card["suppressed_for_query"] = {
                    "reason": suppressed.get("reason") or "irrelevant",
                    "query_hash": suppressed.get("query_hash") or query_hash(query),
                }
            card["score"] = round(raw_score, 3)
            card["why"] = compact_reasons(why)
            scored.append(card)
    scored.sort(key=lambda x: (x["score"], x.get("confidence") or 0), reverse=True)
    result = scored[:limit]
    if record_gain:
        top = result[0] if result else {}
        safe_event = gain_event if gain_event.replace("_", "").replace("-", "").isalnum() else "search"
        estimated_tokens_saved = len(result) * 1200 if safe_event in TOKEN_SAVING_EVENTS else 0
        append_jsonl(home_dir() / "gain.jsonl", {
            "ts": utc_now(),
            "event": f"{safe_event}.hit" if result else f"{safe_event}.miss",
            "source": safe_event,
            "query": query,
            "matches": len(result),
            "cards_considered": len(cards),
            "estimated_tokens_saved": estimated_tokens_saved,
            "estimate_formula": "matches * 1200" if estimated_tokens_saved else "",
            "estimate_kind": "rough_upper_bound_not_billing" if estimated_tokens_saved else "",
            "top_card": top.get("card_id", ""),
            "top_score": top.get("score", 0),
            "top_status": top.get("status", ""),
            "top_confidence": top.get("confidence", 0),
            "top_why": top.get("why", ""),
            "sources": sorted({str(card.get("source") or "") for card in result if card.get("source")})[:6],
        })
    return result


def record_suppression(card_id: str, for_query: str, reason: str = "irrelevant") -> Dict[str, Any]:
    target = (for_query or "").strip()
    is_hash = bool(re.fullmatch(r"[a-f0-9]{12,40}", target))
    row = {
        "ts": utc_now(),
        "event": "suppress.irrelevant",
        "card_id": card_id.strip(),
        "query": "" if is_hash else target,
        "query_hash": target[:12] if is_hash else query_hash(target),
        "reason": reason.strip() or "irrelevant",
        "status": "active",
        "effect": "ranking_downweight_only",
    }
    append_jsonl(home_dir() / "suppressions.jsonl", row)
    append_jsonl(home_dir() / "gain.jsonl", {
        "ts": row["ts"],
        "event": "suppress.irrelevant",
        "source": "suppress",
        "query": row["query"] or row["query_hash"],
        "matches": 0,
        "cards_considered": 0,
        "estimated_tokens_saved": 0,
        "top_card": card_id.strip(),
        "top_score": 0,
        "top_status": "",
        "top_confidence": 0,
        "top_why": row["reason"],
        "sources": [],
    })
    return row


def suppressions_for_query(query: str) -> Dict[str, Dict[str, Any]]:
    current_hash = query_hash(query)
    current_norm = normalize_text(query)
    out: Dict[str, Dict[str, Any]] = {}
    for row in load_jsonl(home_dir() / "suppressions.jsonl"):
        if str(row.get("status") or "active") != "active":
            continue
        card_id = str(row.get("card_id") or "").strip()
        if not card_id:
            continue
        row_query = str(row.get("query") or "")
        row_hash = str(row.get("query_hash") or "")
        if not row_hash and row_query:
            row_hash = query_hash(row_query)
        if row_hash == current_hash or (row_query and normalize_text(row_query) == current_norm):
            out[card_id] = row
    return out


def compact_reasons(reasons: List[str], limit: int = 4) -> str:
    out: List[str] = []
    for reason in reasons:
        if reason not in out:
            out.append(reason)
        if len(out) >= limit:
            break
    return ";".join(out)


def latest_events(limit: int = 5) -> List[Dict[str, Any]]:
    with connect() as conn:
        out = rows(conn, "SELECT ts,actor,event,project_id,card_id,payload_json FROM events ORDER BY id DESC LIMIT ?", (limit,))
    return list(reversed(out))
