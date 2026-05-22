from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .util import git_root, home_dir, read_json, utc_now, write_json


def sources_path() -> Path:
    return home_dir() / "sources.json"


def load_sources() -> Dict[str, Any]:
    data = read_json(sources_path(), {})
    if not isinstance(data, dict):
        data = {}
    roots = data.get("local_roots")
    if not isinstance(roots, list):
        roots = []
    data["local_roots"] = [item for item in roots if isinstance(item, dict)]
    return data


def register_local_root(path: Path, reason: str = "xmem.new") -> Path:
    root = git_root(path)
    data = load_sources()
    roots: List[Dict[str, Any]] = data["local_roots"]
    root_text = str(root)
    now = utc_now()
    for item in roots:
        if item.get("root") == root_text:
            item["last_seen_at"] = now
            item["reason"] = reason
            break
    else:
        roots.append({"root": root_text, "reason": reason, "registered_at": now, "last_seen_at": now})
    data["local_roots"] = sorted(roots, key=lambda item: str(item.get("root", "")))
    write_json(sources_path(), data)
    return root


def registered_roots(extra_roots: Iterable[Path] = ()) -> List[Path]:
    seen: set[str] = set()
    roots: List[Path] = []
    for item in load_sources().get("local_roots", []):
        root = item.get("root")
        if root:
            candidate = Path(str(root)).expanduser()
            key = str(candidate)
            if key not in seen:
                roots.append(candidate)
                seen.add(key)
    for root in extra_roots:
        candidate = root.expanduser()
        key = str(candidate)
        if key not in seen:
            roots.append(candidate)
            seen.add(key)
    return roots


def index_registered_sources(extra_roots: Iterable[Path] = ()) -> Dict[str, Any]:
    from .project import index_local

    result: Dict[str, Any] = {"roots": 0, "cards": 0, "skipped": []}
    for root in registered_roots(extra_roots):
        if not root.exists():
            result["skipped"].append({"root": str(root), "reason": "missing"})
            continue
        if not (root / ".xmem").exists():
            result["skipped"].append({"root": str(root), "reason": "no .xmem"})
            continue
        result["roots"] += 1
        result["cards"] += index_local(root)
    return result


def audit_local_sources(extra_roots: Iterable[Path] = ()) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "roots": 0,
        "roots_with_cards": 0,
        "cards": 0,
        "knowledge_cards": 0,
        "tracked_cards": 0,
        "local_only_cards": 0,
        "local_only_knowledge_cards": 0,
        "ignored_cards": 0,
        "ignored_knowledge_cards": 0,
        "untracked_cards": 0,
        "untracked_knowledge_cards": 0,
        "missing_roots": 0,
        "details": [],
    }
    for root in registered_roots(extra_roots):
        detail = audit_one_local_source(root)
        result["roots"] += 1
        if detail["status"] == "missing":
            result["missing_roots"] += 1
        if detail["cards"]:
            result["roots_with_cards"] += 1
        for key in (
            "cards",
            "knowledge_cards",
            "tracked_cards",
            "local_only_cards",
            "local_only_knowledge_cards",
            "ignored_cards",
            "ignored_knowledge_cards",
            "untracked_cards",
            "untracked_knowledge_cards",
        ):
            result[key] += int(detail.get(key) or 0)
        if detail["cards"] or detail["status"] in {"missing", "no_git"}:
            result["details"].append(detail)
    return result


def audit_one_local_source(root: Path) -> Dict[str, Any]:
    root = root.expanduser()
    detail: Dict[str, Any] = {
        "root": str(root),
        "status": "missing",
        "cards": 0,
        "knowledge_cards": 0,
        "tracked_cards": 0,
        "local_only_cards": 0,
        "local_only_knowledge_cards": 0,
        "ignored_cards": 0,
        "ignored_knowledge_cards": 0,
        "untracked_cards": 0,
        "untracked_knowledge_cards": 0,
        "sample_local_only": [],
    }
    if not root.exists():
        return detail
    cards_dir = root / ".xmem" / "cards"
    cards = sorted(cards_dir.glob("*.yaml")) if cards_dir.exists() else []
    rels = [".xmem/cards/" + card.name for card in cards]
    identity_rels = {".xmem/cards/project.identity.yaml"}
    knowledge_rels = [rel for rel in rels if rel not in identity_rels]
    detail["cards"] = len(cards)
    detail["knowledge_cards"] = len(knowledge_rels)
    if not cards:
        detail["status"] = "no_cards"
        return detail

    if not git_available(root):
        detail["status"] = "local_only"
        detail["local_only_cards"] = len(cards)
        detail["local_only_knowledge_cards"] = len(knowledge_rels)
        detail["sample_local_only"] = (knowledge_rels or rels)[:5]
        return detail

    tracked = set(git_lines(root, ["ls-files", "--", ".xmem/cards"]))
    ignored = set(git_lines(root, ["ls-files", "--others", "--ignored", "--exclude-standard", "--", ".xmem/cards"]))
    untracked = set(git_lines(root, ["ls-files", "--others", "--exclude-standard", "--", ".xmem/cards"]))

    tracked_cards = [rel for rel in rels if rel in tracked]
    ignored_cards = [rel for rel in rels if rel in ignored]
    untracked_cards = [rel for rel in rels if rel in untracked]
    local_only = [rel for rel in rels if rel not in tracked]
    local_only_knowledge = [rel for rel in knowledge_rels if rel not in tracked]

    detail["tracked_cards"] = len(tracked_cards)
    detail["ignored_cards"] = len(ignored_cards)
    detail["ignored_knowledge_cards"] = len([rel for rel in knowledge_rels if rel in ignored])
    detail["untracked_cards"] = len(untracked_cards)
    detail["untracked_knowledge_cards"] = len([rel for rel in knowledge_rels if rel in untracked])
    detail["local_only_cards"] = len(local_only)
    detail["local_only_knowledge_cards"] = len(local_only_knowledge)
    detail["sample_local_only"] = (local_only_knowledge or local_only)[:5]
    if not local_only:
        detail["status"] = "portable"
    elif tracked_cards:
        detail["status"] = "mixed"
    else:
        detail["status"] = "local_only"
    return detail


def git_available(root: Path) -> bool:
    return bool(git_lines(root, ["rev-parse", "--show-toplevel"]))


def git_lines(root: Path, args: List[str]) -> List[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
