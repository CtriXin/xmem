from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .store import connect, rows
from .util import append_jsonl, home_dir, load_jsonl, normalize_text, query_hash, query_terms, query_variants, utc_now


# Query terms that substring-match a large fraction of the card alias/metadata
# blob and therefore carry no real signal. Kept small and conservative: common
# CJK function words plus pasted-screenshot / url noise. Identity still flows
# through the variant / exact-alias path, so dropping these only removes noise.
NOISE_TERMS = frozenset({
    "需要", "没有", "可以", "应该", "继续", "已经", "这个", "那个", "就是", "什么",
    "怎么", "我们", "你们", "他们", "这样", "那样", "但是", "所以", "因为", "如果",
    "或者", "还是", "一个", "这些", "那些", "知道", "觉得", "现在", "然后", "出来",
    "image", "img", "png", "jpg", "jpeg", "screenshot",
    "the", "and", "for", "you", "are", "this", "that", "with", "none", "null",
    # Function words carry no identity. "per" (from a UI label "Hours per Day")
    # was still scoring a project card on 2026-09-03 after the boundary fix.
    "per", "from", "into", "when", "what", "which", "was", "were", "has", "have",
    "but", "not", "its", "out", "over", "under", "then", "than",
})


def is_weak_term(term_norm: str) -> bool:
    """True for a query term too generic to count as alias/metadata evidence.

    A single character or bare stopword substring-matches a large share of the
    2k+ card alias blob, so an unanchored ``in`` test turns it into a high-scoring
    phantom hit (e.g. ``alias_term:需要``). Real identity matching goes through the
    variant / exact-alias path, so skipping these terms only drops noise.
    """
    return len(term_norm) < 2 or term_norm in NOISE_TERMS


def digit_boundary_match(term_norm: str, text: str) -> bool:
    """Match a pure-number term only when it stands as a whole number in ``text``.

    Stops ``2`` from matching inside ``0602`` / ``v5.2`` / dates while still letting
    a real numeric id like ``4638`` match ``adx-4638``.
    """
    if not term_norm:
        return False
    return re.search(r"(?<![0-9])" + re.escape(term_norm) + r"(?![0-9])", text) is not None


_LATIN_TERM_RE = re.compile(r"^[a-z0-9]+$")


_SEPARATED_ALIAS_RE = re.compile(r"[.\-_]")


def is_separated_alias(alias_norm: str) -> bool:
    """True for a structured identifier (``a.b.c`` / ``a-b-c``), not prose.

    ``normalize_text`` strips whitespace, so a multi-word title collapses into
    one run ("Ads Context" -> "adscontext") and has no internal boundary left to
    anchor on. Only aliases that kept a real separator can be boundary-matched.
    """
    return bool(_SEPARATED_ALIAS_RE.search(alias_norm))


def latin_boundary_match(term_norm: str, text: str) -> bool:
    """Match a latin term only at a label boundary of ``text``.

    Domain/service aliases keep their ``.``/``-``/``_`` separators through
    ``normalize_text``, so a real identity hit lands on a whole label. Without
    this, a plain ``in`` test lets an ordinary English word from the task text
    impersonate a domain: on 2026-09-03 (SCM-99) the term ``day`` — from the UI
    label "Days per Week" — substring-hit ``playdaysi.com``, ``likeplaygo.com``
    and ``gogame.smartcard.creditcard``, scoring them above the actual target.
    """
    for match in re.finditer(re.escape(term_norm), text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if not before.isalnum() and not after.isalnum():
            return True
    return False


def search_cards(query: str, limit: int = 10, *, record_gain: bool = True, gain_event: str = "search") -> List[Dict[str, Any]]:
    terms = query_terms(query)[:32]
    variants = [variant for variant in query_variants(query) if len(variant) <= 240][:4]
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
        body_head = body[:20000]
        body_lower = body_head.lower()
        body_norm = ""
        meta = "\n".join(str(x).lower() for x in meta_parts)
        alias_values = alias_parts + [card.get("title", ""), card.get("card_id", "")]
        alias_text = "\n".join(str(x).lower() for x in alias_values)
        alias_norms = []
        for value in alias_values:
            alias_norm = normalize_text(value)
            if alias_norm:
                alias_norms.append(alias_norm)
        meta_norm = normalize_text(meta)
        # Per-part copies so a latin term can be boundary-checked against each
        # metadata value on its own; the joined blob loses those boundaries.
        meta_norms = [n for n in (normalize_text(str(x).lower()) for x in meta_parts) if n]
        alias_norm = normalize_text(alias_text)
        score = 0.0
        why: List[str] = []

        def body_contains(needle: str, raw: str) -> bool:
            nonlocal body_norm
            if not needle:
                return False
            raw_lower = str(raw or "").lower()
            if raw_lower and raw_lower in body_lower:
                return True
            if needle not in body_lower:
                return False
            if not body_norm:
                body_norm = normalize_text(body_head)
            return needle in body_norm

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
            elif body_contains(loose_variant, variant):
                score += 1.5
                why.append(f"body_match:{variant}")
        for term in terms:
            if not term:
                continue
            term_norm = normalize_text(term)
            if not term_norm or is_weak_term(term_norm):
                # Single chars / stopwords match nearly every card's alias blob.
                continue
            if term_norm.isdigit():
                # Pure numbers only count at numeric boundaries, so a bare "2"
                # no longer phantom-matches inside "0602" / "v5.2" / dates.
                in_alias = digit_boundary_match(term_norm, alias_norm)
                in_meta = digit_boundary_match(term_norm, meta_norm)
            elif _LATIN_TERM_RE.match(term_norm):
                # On structured aliases (domains, service slugs) a latin term must
                # land on a whole label. Prose aliases lost their spaces in
                # normalization, so they keep the plain substring test.
                in_alias = any(
                    latin_boundary_match(term_norm, a) if is_separated_alias(a) else term_norm in a
                    for a in alias_norms
                )
                in_meta = any(
                    latin_boundary_match(term_norm, m) if is_separated_alias(m) else term_norm in m
                    for m in meta_norms
                )
            else:
                # CJK has no word boundary to anchor on; keep the substring test.
                in_alias = term_norm in alias_norm
                in_meta = term_norm in meta_norm
            if in_alias:
                score += 3.0
                why.append(f"alias_term:{term}")
            elif in_meta:
                score += 2.0
                why.append(f"metadata_term:{term}")
            elif body_contains(term_norm, term):
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
        append_jsonl(home_dir() / "gain.jsonl", {
            "ts": utc_now(),
            "event": f"{safe_event}.hit" if result else f"{safe_event}.miss",
            "source": safe_event,
            "query": query,
            "matches": len(result),
            "cards_considered": len(cards),
            "estimated_tokens_saved": None,
            "estimate_formula": "",
            "estimate_kind": "unknown; retrieval is not measured benefit",
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
