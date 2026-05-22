from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .importers import bug_pattern_to_export_card, iter_jsonl, looks_like_bug_pattern, normalize_status
from .store import db_path

VALID_STATUSES = {"verified", "inferred", "partial", "stale", "disputed", "unknown"}


def default_source_paths() -> List[Dict[str, str]]:
    project_wiki = Path(os.environ.get("XMEM_PROJECT_WIKI", "/Users/xin/project-wiki")).expanduser()
    issue_tracking = Path(os.environ.get("XMEM_ISSUE_TRACKING", "/Users/xin/issue-tracking")).expanduser()
    return [
        {"kind": "project-wiki-export", "path": str(project_wiki / "data" / "xmem-export.cards.jsonl")},
        {"kind": "issue-tracking-export", "path": str(issue_tracking / "index" / "xmem-export.cards.jsonl")},
        {"kind": "issue-bug-patterns", "path": str(issue_tracking / "index" / "bug-patterns.jsonl")},
    ]


def check_source_exports(paths: Iterable[Dict[str, str]] | None = None) -> Dict[str, Any]:
    source_paths = list(paths or default_source_paths())
    freshness = source_freshness(source_paths)
    entries: List[Dict[str, Any]] = []
    seen_ids: Dict[str, str] = {}
    duplicate_ids: List[Dict[str, str]] = []
    for item in source_paths:
        kind = item["kind"]
        path = Path(item["path"]).expanduser()
        result = check_one_export(path, kind)
        for card_id in result.get("ids", []):
            previous = seen_ids.get(card_id)
            if previous:
                duplicate_ids.append({"id": card_id, "first": previous, "second": str(path)})
            else:
                seen_ids[card_id] = str(path)
        result.pop("ids", None)
        entries.append(result)
    total_errors = sum(len(x.get("errors", [])) for x in entries) + len(duplicate_ids)
    total_warnings = sum(len(x.get("warnings", [])) for x in entries)
    optional_missing = sum(1 for x in entries if x.get("optional_missing"))
    return {
        "status": "error" if total_errors else ("stale" if freshness["stale_exports"] else ("warn" if total_warnings else "ok")),
        "errors": total_errors,
        "warnings": total_warnings,
        "optional_missing": optional_missing,
        "freshness": freshness,
        "stale_exports": freshness["stale_exports"],
        "duplicate_ids": duplicate_ids,
        "exports": entries,
    }


def source_freshness(paths: Iterable[Dict[str, str]] | None = None) -> Dict[str, Any]:
    source_paths = list(paths or default_source_paths())
    registry = db_path()
    registry_mtime = registry.stat().st_mtime if registry.exists() else 0.0
    checked: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    for item in source_paths:
        path = Path(item["path"]).expanduser()
        exists = path.exists()
        mtime = path.stat().st_mtime if exists else 0.0
        row = {"kind": item["kind"], "path": str(path), "exists": exists, "mtime": mtime}
        checked.append(row)
        if exists and (not registry.exists() or mtime > registry_mtime + 1.0):
            stale.append(row)
    return {
        "status": "missing_registry" if not registry.exists() else ("stale" if stale else "fresh"),
        "registry": str(registry),
        "registry_exists": registry.exists(),
        "registry_mtime": registry_mtime,
        "stale_exports": len(stale),
        "stale": stale,
        "checked": checked,
    }


def check_one_export(path: Path, kind: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "kind": kind,
        "path": str(path),
        "exists": path.exists(),
        "rows": 0,
        "ids": [],
        "errors": [],
        "warnings": [],
    }
    if not path.exists():
        result["optional_missing"] = True
        return result
    try:
        rows = list(iter_jsonl(path))
    except SystemExit as exc:
        result["errors"].append(str(exc))
        return result
    local_ids: set[str] = set()
    for line_no, raw in rows:
        result["rows"] += 1
        item = bug_pattern_to_export_card(raw) if kind == "issue-bug-patterns" or looks_like_bug_pattern(raw) else raw
        card_id = str(item.get("id") or item.get("card_id") or "")
        if not card_id:
            result["warnings"].append(f"line {line_no}: missing id; importer will generate one")
            card_id = f"{path}:{line_no}"
        if card_id in local_ids:
            result["errors"].append(f"line {line_no}: duplicate id in file: {card_id}")
        local_ids.add(card_id)
        result["ids"].append(card_id)
        validate_card_shape(item, line_no, result, kind)
    if result["rows"] == 0:
        result["warnings"].append("empty export")
    return result


def validate_card_shape(item: Dict[str, Any], line_no: int, result: Dict[str, Any], kind: str) -> None:
    if not item.get("title"):
        result["errors"].append(f"line {line_no}: missing title")
    if not item.get("type"):
        result["warnings"].append(f"line {line_no}: missing type; importer will default/infer")
    truth = item.get("truth") if isinstance(item.get("truth"), dict) else {}
    status = str(truth.get("status") or item.get("status") or "")
    if not status:
        result["errors"].append(f"line {line_no}: missing truth.status")
    elif status not in VALID_STATUSES:
        normalized = normalize_status(status)
        if normalized == "unknown":
            result["errors"].append(f"line {line_no}: invalid truth.status: {status}")
        else:
            result["warnings"].append(f"line {line_no}: non-standard truth.status {status}; importer normalizes to {normalized}")
    confidence = truth.get("confidence", item.get("confidence"))
    if confidence is None:
        result["warnings"].append(f"line {line_no}: missing truth.confidence")
    else:
        try:
            value = float(confidence)
            if value < 0 or value > 1:
                result["errors"].append(f"line {line_no}: confidence outside 0..1")
        except (TypeError, ValueError):
            result["errors"].append(f"line {line_no}: confidence is not a number")
    if not item.get("summary"):
        result["warnings"].append(f"line {line_no}: missing summary")
    aliases = item.get("aliases")
    if aliases is not None and not isinstance(aliases, list):
        result["errors"].append(f"line {line_no}: aliases must be a list")
    if kind == "issue-bug-patterns":
        for key in ("symptom", "root_cause", "fix_pattern", "verification", "regression_guard"):
            if not item.get(key):
                result["warnings"].append(f"line {line_no}: missing {key}")


def compact_source_health(data: Dict[str, Any]) -> List[str]:
    lines = [
        f"source_exports: {data['status']} errors={data['errors']} "
        f"warnings={data['warnings']} optional_missing={data.get('optional_missing', 0)} "
        f"stale_exports={data.get('stale_exports', 0)}"
    ]
    for item in data.get("exports", []):
        marker = "ok" if item.get("exists") and not item.get("errors") else ("missing" if not item.get("exists") else "error")
        lines.append(f"- {item['kind']}: {marker} rows={item.get('rows', 0)} path={item['path']}")
    for dup in data.get("duplicate_ids", [])[:5]:
        lines.append(f"- duplicate_id: {dup['id']}")
    return lines
