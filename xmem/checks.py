from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from .project import git_root
from .util import append_jsonl, git_value, home_dir, list_after_key, utc_now


def check_diff(path: Path) -> Dict[str, object]:
    root = git_root(path)
    diff = git_value(root, "diff", "--", ".")
    cards = list((root / ".xmem" / "cards").glob("*.yaml")) if (root / ".xmem" / "cards").exists() else []
    warnings: List[Dict[str, str]] = []
    removed = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    for card in cards:
        text = card.read_text(encoding="utf-8", errors="ignore")
        terms = list_after_key(text, "warn_if_removed")
        for term in terms:
            if term and re.search(re.escape(term), removed, re.IGNORECASE):
                warnings.append({"card": card.name, "term": term, "reason": "removed in git diff"})
    event = "rule.prevented" if warnings else "check.pass"
    append_jsonl(home_dir() / "gain.jsonl", {
        "ts": utc_now(), "event": event, "warnings": len(warnings),
        "estimated_bug_prevented": 1 if warnings else 0,
    })
    return {"root": str(root), "warnings": warnings, "checked_cards": len(cards)}
