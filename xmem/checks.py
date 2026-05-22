from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, List

from .project import card_from_file, git_root
from .store import connect, rows
from .util import append_jsonl, git_value, home_dir, list_after_key, normalize_text, utc_now


RULE_TYPES = {"invariant", "rule", "guard"}


def check_diff(path: Path) -> Dict[str, object]:
    root = git_root(path)
    diff = git_value(root, "diff", "--", ".")
    changed_files = git_value(root, "diff", "--name-only", "--", ".").splitlines()
    cards = collect_rule_cards(root)
    warnings: List[Dict[str, str]] = []
    removed = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    relevant = 0
    for card in cards:
        text = str(card.get("body") or "")
        if not card_relevant_to_diff(card, changed_files, diff):
            continue
        relevant += 1
        terms = list_after_key(text, "warn_if_removed")
        for term in terms:
            if term and contains_term(removed, term):
                warnings.append(warning(card, term, "removed in git diff"))
        for term in list_after_key(text, "warn_if_added"):
            if term and contains_term(added, term):
                warnings.append(warning(card, term, "forbidden term added in git diff"))
        for term in list_after_key(text, "forbid"):
            if term and contains_term(added, term):
                warnings.append(warning(card, term, "forbidden invariant text added"))
    event = "rule.prevented" if warnings else "check.pass"
    append_jsonl(home_dir() / "gain.jsonl", {
        "ts": utc_now(), "event": event, "warnings": len(warnings),
        "checked_cards": len(cards), "matched_cards": relevant,
        "estimated_bug_prevented": 1 if warnings else 0,
        "estimate_kind": "risk_hint_not_actual_bug" if warnings else "",
        "changed_files": changed_files[:20],
        "warning_cards": [item.get("card", "") for item in warnings[:20]],
    })
    return {"root": str(root), "warnings": warnings, "checked_cards": len(cards), "matched_cards": relevant, "changed_files": changed_files}


def collect_rule_cards(root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    cards_dir = root / ".xmem" / "cards"
    if cards_dir.exists():
        for path in sorted(cards_dir.glob("*.yaml")):
            card = card_from_file(path, root.name)
            if str(card.get("type") or "") in RULE_TYPES:
                out.append(card)
                seen.add(str(card.get("card_id") or path))
    try:
        with connect() as conn:
            placeholders = ",".join("?" for _ in RULE_TYPES)
            for card in rows(conn, f"SELECT * FROM cards WHERE type IN ({placeholders})", sorted(RULE_TYPES)):
                key = str(card.get("card_id") or card.get("path") or "")
                if key and key not in seen:
                    seen.add(key)
                    out.append(card)
    except Exception:
        pass
    return out


def card_relevant_to_diff(card: Dict[str, Any], changed_files: List[str], diff: str) -> bool:
    body = str(card.get("body") or "")
    paths = list_after_key(body, "paths")
    if paths:
        if not changed_files:
            return False
        for pattern in paths:
            if any(matches_path_scope(path, str(pattern or "")) for path in changed_files):
                return True
        return False
    guard_terms = (
        list_after_key(body, "warn_if_removed")
        + list_after_key(body, "warn_if_added")
        + list_after_key(body, "forbid")
    )
    if any(term and contains_term(diff, term) for term in guard_terms):
        return True
    if not changed_files:
        return True
    haystack = normalize_text("\n".join(changed_files) + "\n" + diff[:12000])
    aliases = []
    try:
        import json
        aliases = json.loads(str(card.get("aliases_json") or "[]"))
    except Exception:
        aliases = []
    probes = [card.get("title", ""), card.get("card_id", ""), *aliases]
    return any(normalize_text(probe) and normalize_text(probe) in haystack for probe in probes)


def matches_path_scope(path: str, pattern: str) -> bool:
    cleaned = pattern.strip().strip('"')
    variants = [cleaned]
    if cleaned.startswith("**/"):
        variants.append(cleaned[3:])
    name = Path(path).name
    return any(fnmatch.fnmatch(path, item) or fnmatch.fnmatch(name, item) for item in variants if item)


def contains_term(text: str, term: str) -> bool:
    if re.search(re.escape(term), text, re.IGNORECASE):
        return True
    term_norm = normalize_text(term)
    return bool(term_norm and term_norm in normalize_text(text))


def warning(card: Dict[str, Any], term: str, reason: str) -> Dict[str, str]:
    return {
        "card": str(card.get("card_id") or Path(str(card.get("path") or "")).name),
        "title": str(card.get("title") or ""),
        "term": term,
        "reason": reason,
        "source": str(card.get("source") or ""),
        "source_ref": str(card.get("source_ref") or card.get("path") or ""),
        "severity": "warn",
    }
