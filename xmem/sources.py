from __future__ import annotations

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
