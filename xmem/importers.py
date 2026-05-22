from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .store import connect, log_event, upsert_card, upsert_evidence, upsert_project
from .util import field_from_text, flatten_strings, read_json, slugify, utc_now

VALID_STATUSES = {"verified", "inferred", "partial", "stale", "disputed", "unknown"}


def import_project_wiki(path: Path) -> Dict[str, int]:
    path = path.expanduser()
    idx = path / "data" / "project-hub.index.json"
    export = path / "data" / "xmem-export.cards.jsonl"
    data = read_json(idx, {})
    if not isinstance(data, dict) or "entities" not in data:
        if export.exists():
            exported = import_xmem_export(export, source="project-wiki-export")
            return {"cards": 0, "projects": 0, "export_cards": exported["cards"], "evidence": exported["evidence"]}
        raise SystemExit(f"project-wiki index not found: {idx}")
    cards = 0
    projects = 0
    with connect() as conn:
        for ent in data.get("entities", []):
            eid = str(ent.get("id") or "")
            etype = str(ent.get("type") or "Entity")
            fields = ent.get("fields") or {}
            aliases = list(dict.fromkeys([str(x) for x in [ent.get("name"), ent.get("title"), *ent.get("aliases", [])] if x]))
            aliases += [str(x) for x in flatten_strings({k: fields.get(k) for k in ("git", "local", "service", "alias", "aliases") if k in fields}) if x]
            project_id = slugify(eid.replace(":", "."), "wiki-entity")
            if etype in {"Service", "Repo", "Domain", "Project"}:
                upsert_project(conn, {
                    "project_id": project_id,
                    "name": str(ent.get("name") or ent.get("title") or eid),
                    "root": str(fields.get("localPath") or ""),
                    "remote": str(fields.get("git") or ""),
                    "branch": str(fields.get("actualGitBranch") or fields.get("serviceBranch") or ""),
                    "tech_stack": str(fields.get("techStack") or ""),
                    "aliases": aliases[:80],
                    "status": "verified" if ent.get("confidence", 0) >= 1 else "inferred",
                    "updated_at": str(ent.get("updatedAt") or utc_now()),
                    "source": "project-wiki",
                })
                projects += 1
            body = json.dumps(ent, ensure_ascii=False, sort_keys=True)
            upsert_card(conn, {
                "card_id": f"project-wiki.{project_id}",
                "project_id": project_id,
                "type": f"wiki.{etype.lower()}",
                "title": str(ent.get("title") or ent.get("name") or eid),
                "path": str(idx),
                "status": "verified" if ent.get("confidence", 0) >= 1 else "inferred",
                "confidence": float(ent.get("confidence") or 0.7),
                "aliases": aliases[:80],
                "body": body,
                "updated_at": str(ent.get("updatedAt") or utc_now()),
                "source": "project-wiki",
                "source_ref": eid,
            })
            cards += 1
        log_event(conn, "import.project-wiki", payload={"path": str(path), "cards": cards, "projects": projects})
        conn.commit()
    result = {"cards": cards, "projects": projects}
    if export.exists():
        exported = import_xmem_export(export, source="project-wiki-export")
        result["export_cards"] = exported["cards"]
        result["evidence"] = exported["evidence"]
    return result


def import_issue_tracking(path: Path) -> Dict[str, int]:
    path = path.expanduser()
    issues_dir = path / "issues"
    if not issues_dir.exists():
        export = path / "index" / "xmem-export.cards.jsonl"
        patterns = path / "index" / "bug-patterns.jsonl"
        if export.exists() or patterns.exists():
            result = {"cards": 0, "evidence": 0}
            if export.exists():
                exported = import_xmem_export(export, source="issue-tracking-export", skip_bug_patterns=patterns.exists())
                result["export_cards"] = exported["cards"]
                result["evidence"] += exported["evidence"]
            if patterns.exists():
                imported = import_bug_patterns(patterns, source="issue-bug-patterns")
                result["bug_patterns"] = imported["cards"]
                result["evidence"] += imported["evidence"]
            return result
        raise SystemExit(f"issue-tracking issues dir not found: {issues_dir}")
    count = 0
    evidence = 0
    with connect() as conn:
        for issue in issues_dir.glob("**/issue.md"):
            text = issue.read_text(encoding="utf-8", errors="ignore")
            slug = field_from_text(text, "Issue") or issue.parent.name
            project = field_from_text(text, "Project") or issue.parent.parent.name
            repo = field_from_text(text, "Repo path")
            branch = field_from_text(text, "Branch")
            title = field_from_text(text, "Task name") or slug
            status_raw = field_from_text(text, "Status") or "unknown"
            todo = "[TODO" in text or "path/to/file" in text
            status = "inferred" if todo else ("verified" if any(x in status_raw.lower() for x in ["done", "verified", "closed"]) else "partial")
            project_id = slugify(project)
            aliases = list(dict.fromkeys([project, slug, title, field_from_text(text, "Service"), field_from_text(text, "Domain"), branch]))
            upsert_project(conn, {
                "project_id": project_id,
                "name": project,
                "root": repo,
                "remote": field_from_text(text, "Remote URL"),
                "branch": branch,
                "tech_stack": "",
                "aliases": [a for a in aliases if a],
                "status": "inferred" if todo else "verified",
                "updated_at": utc_now(),
                "source": "issue-tracking",
            })
            card_id = f"issue.{slugify(slug)}"
            upsert_card(conn, {
                "card_id": card_id,
                "project_id": project_id,
                "type": "evidence.issue",
                "title": title,
                "path": str(issue),
                "status": status,
                "confidence": 0.9 if status == "verified" else 0.45,
                "aliases": [a for a in aliases if a],
                "body": text,
                "updated_at": utc_now(),
                "source": "issue-tracking",
                "source_ref": str(issue),
            })
            ev_id = "issue-tracking." + hashlib.sha1(str(issue).encode()).hexdigest()[:16]
            upsert_evidence(conn, {
                "evidence_id": ev_id,
                "card_id": card_id,
                "project_id": project_id,
                "kind": "issue-record",
                "ref": slug,
                "path": str(issue),
                "title": title,
                "status": status,
                "body": summarize_issue(text),
                "updated_at": utc_now(),
                "source": "issue-tracking",
            })
            count += 1
            evidence += 1
        log_event(conn, "import.issue-tracking", payload={"path": str(path), "cards": count, "evidence": evidence})
        conn.commit()
    result = {"cards": count, "evidence": evidence}
    export = path / "index" / "xmem-export.cards.jsonl"
    patterns = path / "index" / "bug-patterns.jsonl"
    if export.exists():
        exported = import_xmem_export(export, source="issue-tracking-export", skip_bug_patterns=patterns.exists())
        result["export_cards"] = exported["cards"]
        result["evidence"] += exported["evidence"]
    if patterns.exists():
        imported = import_bug_patterns(patterns, source="issue-bug-patterns")
        result["bug_patterns"] = imported["cards"]
        result["evidence"] += imported["evidence"]
    return result


def import_xmem_export(path: Path, source: str = "xmem-export", *, skip_bug_patterns: bool = False) -> Dict[str, int]:
    """Import compact JSONL cards exported by Project Wiki or Issue Record."""
    files = export_files(path)
    if not files:
        return {"cards": 0, "evidence": 0, "skipped": 1}
    cards = 0
    evidence = 0
    skipped_bug_patterns = 0
    with connect() as conn:
        for file in files:
            for line_no, item in iter_jsonl(file):
                if skip_bug_patterns and looks_like_bug_pattern(item):
                    skipped_bug_patterns += 1
                    continue
                if source.startswith("issue-") and looks_like_bug_pattern(item):
                    item = bug_pattern_to_export_card(item)
                card = card_from_export_item(item, file, line_no, source)
                upsert_project_from_export(conn, item, card, source)
                upsert_card(conn, card)
                cards += 1
                for ev in evidence_from_export_item(item, card, source):
                    upsert_evidence(conn, ev)
                    evidence += 1
        log_event(conn, "import.xmem-export", payload={"path": str(path), "source": source, "cards": cards, "evidence": evidence, "skipped_bug_patterns": skipped_bug_patterns})
        conn.commit()
    return {"cards": cards, "evidence": evidence, "skipped_bug_patterns": skipped_bug_patterns}


def import_bug_patterns(path: Path, source: str = "issue-bug-patterns") -> Dict[str, int]:
    files = export_files(path, default_name="bug-patterns.jsonl")
    if not files:
        return {"cards": 0, "evidence": 0, "skipped": 1}
    cards = 0
    evidence = 0
    with connect() as conn:
        for file in files:
            for line_no, item in iter_jsonl(file):
                exported = bug_pattern_to_export_card(item)
                card = card_from_export_item(exported, file, line_no, source)
                upsert_card(conn, card)
                cards += 1
                for ev in evidence_from_export_item(exported, card, source):
                    upsert_evidence(conn, ev)
                    evidence += 1
        log_event(conn, "import.bug-patterns", payload={"path": str(path), "source": source, "cards": cards, "evidence": evidence})
        conn.commit()
    return {"cards": cards, "evidence": evidence}


def export_files(path: Path, default_name: str = "xmem-export.cards.jsonl") -> List[Path]:
    base = path.expanduser()
    if base.is_file():
        return [base]
    if base.is_dir():
        direct = base / default_name
        files = [direct] if direct.exists() else []
        files.extend(sorted(p for p in base.glob(f"**/{default_name}") if p not in files))
        return files
    return []


def iter_jsonl(path: Path) -> Iterable[tuple[int, Dict[str, Any]]]:
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(item, dict):
                raise SystemExit(f"xmem export row must be an object at {path}:{line_no}")
            yield line_no, item


def card_from_export_item(item: Dict[str, Any], file: Path, line_no: int, source: str) -> Dict[str, Any]:
    truth = item.get("truth") if isinstance(item.get("truth"), dict) else {}
    cid = str(item.get("id") or item.get("card_id") or export_row_id(item, file, line_no))
    status = normalize_status(truth.get("status") or item.get("status") or "unknown")
    confidence = safe_float(truth.get("confidence", item.get("confidence")), 0.95 if status == "verified" else 0.5)
    updated_at = str(truth.get("last_checked_at") or item.get("updatedAt") or item.get("updated_at") or utc_now())
    aliases = export_aliases(item)
    body = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return {
        "card_id": cid,
        "project_id": str(item.get("project_id") or project_from_export(item)),
        "type": str(item.get("type") or "fact"),
        "title": str(item.get("title") or item.get("name") or cid),
        "path": str(file),
        "status": status,
        "confidence": confidence,
        "aliases": aliases[:100],
        "body": body,
        "updated_at": updated_at,
        "source": source,
        "source_ref": str(item.get("source_ref") or item.get("sourcePath") or f"{file}:{line_no}"),
    }


def bug_pattern_to_export_card(item: Dict[str, Any]) -> Dict[str, Any]:
    title = str(item.get("title") or item.get("name") or item.get("symptom") or "bug pattern")
    cid = str(item.get("id") or item.get("card_id") or f"issue-pattern.{slugify(title)}")
    symptom = bug_field(item, "symptom", "symptoms")
    root_cause = bug_field(item, "root_cause", "rootCause")
    fix_pattern = bug_field(item, "fix_pattern", "fix", "fixPattern", "guardrail")
    verification = bug_field(item, "verification", "checks", "check")
    regression_guard = bug_field(item, "regression_guard", "guardrail", "guard")
    aliases = list(dict.fromkeys([str(x) for x in flatten_values({
        "aliases": item.get("aliases"),
        "symptom": symptom,
        "root_cause": root_cause,
        "fix_pattern": fix_pattern,
        "regression_guard": regression_guard,
    }) if x]))
    truth = item.get("truth") if isinstance(item.get("truth"), dict) else {}
    truth = {
        **truth,
        "status": normalize_status(truth.get("status") or item.get("status") or "partial"),
        "confidence": truth.get("confidence", item.get("confidence") or 0.7),
        "basis": truth.get("basis") or ["issue_record_pattern"],
        "last_checked_at": truth.get("last_checked_at") or item.get("updatedAt") or item.get("updated_at") or utc_now(),
    }
    summary = item.get("summary") or summarize_bug_pattern(item)
    out = dict(item)
    out.update({
        "id": cid,
        "type": item.get("type") or "rule",
        "title": title,
        "aliases": aliases,
        "truth": truth,
        "summary": summary,
        "symptom": symptom,
        "root_cause": root_cause,
        "fix_pattern": fix_pattern,
        "verification": verification,
        "regression_guard": regression_guard,
    })
    return out


def summarize_bug_pattern(item: Dict[str, Any]) -> str:
    parts = []
    for key in ("symptom", "root_cause", "fix_pattern", "verification", "regression_guard"):
        value = bug_field(item, key)
        if value:
            parts.append(f"{key}: {value}")
    return " | ".join(str(x) for x in parts)[:1600]


def looks_like_bug_pattern(item: Dict[str, Any]) -> bool:
    truth = item.get("truth") if isinstance(item.get("truth"), dict) else {}
    keys = {"symptom", "symptoms", "root_cause", "rootCause", "fix_pattern", "guardrail", "verification", "checks", "regression_guard"}
    return any(key in item for key in keys) or any(key in truth for key in keys)


def bug_field(item: Dict[str, Any], *names: str) -> Any:
    truth = item.get("truth") if isinstance(item.get("truth"), dict) else {}
    for name in names:
        if item.get(name):
            return item.get(name)
        if truth.get(name):
            return truth.get(name)
    return ""


def upsert_project_from_export(conn: Any, item: Dict[str, Any], card: Dict[str, Any], source: str) -> None:
    ctype = str(card.get("type") or "")
    project_id = str(card.get("project_id") or "")
    if not project_id or not (ctype.startswith("wiki.") or ctype == "identity"):
        return
    current = item.get("current") if isinstance(item.get("current"), dict) else {}
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    upsert_project(conn, {
        "project_id": project_id,
        "name": str(item.get("name") or item.get("title") or project_id),
        "root": str(current.get("repo_path") or fields.get("localPath") or current.get("root") or ""),
        "remote": str(current.get("remote") or fields.get("git") or ""),
        "branch": str(current.get("branch") or current.get("latest_known_branch") or fields.get("actualGitBranch") or fields.get("serviceBranch") or ""),
        "tech_stack": str(current.get("tech_stack") or fields.get("techStack") or ""),
        "aliases": card.get("aliases", []),
        "status": str(card.get("status") or "unknown"),
        "updated_at": str(card.get("updated_at") or utc_now()),
        "source": source,
    })


def evidence_from_export_item(item: Dict[str, Any], card: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    evidence_items = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    for idx, ev in enumerate(evidence_items):
        if not isinstance(ev, dict):
            ev = {"ref": str(ev)}
        key = json.dumps({"card": card["card_id"], "idx": idx, "ev": ev}, ensure_ascii=False, sort_keys=True)
        out.append({
            "evidence_id": f"{source}.{hashlib.sha1(key.encode()).hexdigest()[:16]}",
            "card_id": card["card_id"],
            "project_id": card.get("project_id", ""),
            "kind": str(ev.get("kind") or "source"),
            "ref": str(ev.get("ref") or ev.get("id") or ev.get("path") or ""),
            "path": str(ev.get("path") or ""),
            "title": str(ev.get("title") or card.get("title") or ""),
            "status": str(card.get("status") or "unknown"),
            "body": json.dumps(ev, ensure_ascii=False, sort_keys=True),
            "updated_at": str(card.get("updated_at") or utc_now()),
            "source": source,
        })
    return out


def export_aliases(item: Dict[str, Any]) -> List[str]:
    aliases = list(dict.fromkeys([str(x) for x in flatten_values({
        "aliases": item.get("aliases"),
        "title": item.get("title"),
        "name": item.get("name"),
        "scope": item.get("scope"),
        "relations": item.get("relations"),
        "current": item.get("current"),
    }) if x]))
    return aliases


def project_from_export(item: Dict[str, Any]) -> str:
    scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
    for key in ("project", "service", "repo", "domain", "entity"):
        if scope.get(key):
            return slugify(str(scope[key]))
    if str(item.get("type") or "").startswith("wiki.") and item.get("id"):
        return slugify(str(item["id"]).removeprefix("project-wiki."))
    return ""


def flatten_values(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from flatten_values(v)
    elif isinstance(value, list):
        for item in value:
            yield from flatten_values(item)
    else:
        yield str(value)


def export_row_id(item: Dict[str, Any], file: Path, line_no: int) -> str:
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return f"xmem-export.{slugify(file.stem)}.{line_no}.{hashlib.sha1(raw.encode()).hexdigest()[:10]}"


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_status(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    if status in VALID_STATUSES:
        return status
    if status in {"done", "closed", "passed", "pass"}:
        return "verified"
    if status in {"active", "open", "todo", "wip"}:
        return "partial"
    return "unknown"


def summarize_issue(text: str, limit: int = 1600) -> str:
    keys = ["Problem", "Fix summary", "Verification", "Impact Files"]
    parts: List[str] = []
    for key in keys:
        value = field_from_text(text, key)
        if value:
            parts.append(f"{key}: {value}")
    if not parts:
        parts.append(re.sub(r"\s+", " ", text[:limit]).strip())
    return "\n".join(parts)[:limit]
