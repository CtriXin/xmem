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
    inbox = path / "data" / "agent-inbox.jsonl"
    data = read_json(idx, {})
    if not isinstance(data, dict) or "entities" not in data:
        result = {"cards": 0, "projects": 0}
        if export.exists():
            exported = import_xmem_export(export, source="project-wiki-export")
            result.update({"export_cards": exported["cards"], "evidence": exported["evidence"]})
        if inbox.exists():
            pending = import_project_wiki_agent_inbox(inbox)
            result.update({"pending_cards": pending["cards"], "pending_evidence": pending["evidence"]})
        if export.exists() or inbox.exists():
            return result
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
    if inbox.exists():
        pending = import_project_wiki_agent_inbox(inbox)
        result["pending_cards"] = pending["cards"]
        result["pending_evidence"] = pending["evidence"]
    return result


def import_project_wiki_agent_inbox(path: Path, source: str = "project-wiki-pending") -> Dict[str, int]:
    """Import Project Wiki pending writebacks as low-confidence hints.

    agent-inbox rows are not Project Wiki truth yet. They are only indexed so
    agents can discover pending mappings and then verify/promote them upstream.
    """
    if not path.exists():
        return {"cards": 0, "evidence": 0, "skipped": 1}
    cards = 0
    evidence = 0
    with connect() as conn:
        for line_no, item in iter_jsonl(path):
            if should_skip_pending_inbox_row(item):
                continue
            exported = pending_inbox_to_export_card(item, path, line_no)
            card = card_from_export_item(exported, path, line_no, source)
            upsert_card(conn, card)
            cards += 1
            for ev in evidence_from_export_item(exported, card, source):
                upsert_evidence(conn, ev)
                evidence += 1
        log_event(conn, "import.project-wiki-agent-inbox", payload={"path": str(path), "source": source, "cards": cards, "evidence": evidence})
        conn.commit()
    return {"cards": cards, "evidence": evidence}


def should_skip_pending_inbox_row(item: Dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    return status in {"rejected", "declined", "invalid", "superseded"}


def pending_inbox_to_export_card(item: Dict[str, Any], file: Path, line_no: int) -> Dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    row_id = str(item.get("id") or f"{line_no}-{hashlib.sha1(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]}")
    domains = string_list(payload.get("domains") or payload.get("domain"))
    service = str(payload.get("service") or "")
    project = str(payload.get("project") or "")
    target = str(item.get("targetEntityId") or "")
    title_subject = domains[0] if domains else (service or project or target or row_id)
    target_hint = service or project or target
    title = f"Pending Project Wiki writeback: {title_subject}"
    if target_hint and target_hint != title_subject:
        title += f" -> {target_hint}"
    aliases = pending_inbox_aliases(item, payload)
    confidence = pending_inbox_confidence(item)
    summary = str(payload.get("summary") or item.get("summary") or title)
    evidence = pending_inbox_evidence(item, payload, file, line_no, row_id)
    updated_at = str(item.get("receivedAt") or payload.get("verifiedAt") or payload.get("updatedAt") or utc_now())
    project_id = project or service or target.replace(":", ".") or (domains[0] if domains else "")
    out = {
        "id": f"project-wiki.pending.{slugify(row_id)}",
        "type": "wiki.pending",
        "title": title,
        "project_id": slugify(project_id, "project-wiki-pending"),
        "aliases": aliases[:120],
        "truth": {
            "status": "partial",
            "confidence": confidence,
            "basis": ["project_wiki_agent_inbox_pending_writeback"],
            "last_checked_at": updated_at,
            "use_policy": "hint_only_until_project_wiki_accepts",
        },
        "pending": {
            "id": row_id,
            "status": str(item.get("status") or "pending"),
            "risk": str(item.get("risk") or ""),
            "actor": str(item.get("actor") or ""),
            "action": str(item.get("action") or ""),
            "targetEntityId": target,
        },
        "current": {
            "service": service,
            "project": project,
            "domains": domains,
            "repo_path": str(payload.get("localPath") or ""),
            "remote": str(payload.get("repo") or ""),
            "branch": str(payload.get("branch") or payload.get("branchLine") or ""),
            "commit": str(payload.get("commit") or ""),
            "pipeline": str(payload.get("pipeline") or ""),
            "deploy_run": str(payload.get("deployRun") or ""),
            "version": str(payload.get("version") or ""),
        },
        "summary": summary,
        "source_ref": f"{file}:{line_no}#{row_id}",
        "evidence": evidence,
        "raw": item,
    }
    return out


def pending_inbox_confidence(item: Dict[str, Any]) -> float:
    validation = item.get("validation")
    checks = [row for row in validation if isinstance(row, dict)] if isinstance(validation, list) else []
    ok = sum(1 for row in checks if row.get("ok") is True)
    failed = sum(1 for row in checks if row.get("ok") is False)
    risk = str(item.get("risk") or "").lower()
    confidence = 0.45 + min(ok, 3) * 0.04 - min(failed, 2) * 0.07
    if "low" in risk:
        confidence += 0.03
    if "medium" in risk:
        confidence -= 0.03
    if "high" in risk or "blocked" in risk:
        confidence -= 0.08
    return max(0.25, min(0.6, round(confidence, 2)))


def pending_inbox_aliases(item: Dict[str, Any], payload: Dict[str, Any]) -> List[str]:
    fields = {
        "target": item.get("targetEntityId"),
        "type": payload.get("type"),
        "project": payload.get("project"),
        "displayName": payload.get("displayName"),
        "aliases": payload.get("aliases"),
        "sourceNames": payload.get("sourceNames"),
        "sourceIds": payload.get("sourceIds"),
        "domains": payload.get("domains") or payload.get("domain"),
        "service": payload.get("service"),
        "repo": payload.get("repo"),
        "repoShort": payload.get("repoShort"),
        "localPath": payload.get("localPath"),
        "branch": payload.get("branch"),
        "branchLine": payload.get("branchLine"),
        "pipeline": payload.get("pipeline"),
        "deployRun": payload.get("deployRun"),
        "version": payload.get("version"),
        "issueRecorder": payload.get("issueRecorder"),
        "issueIds": payload.get("issueIds"),
    }
    out = []
    for value in flatten_values(fields):
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def pending_inbox_evidence(item: Dict[str, Any], payload: Dict[str, Any], file: Path, line_no: int, row_id: str) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = [
        {
            "kind": "project-wiki-agent-inbox",
            "path": str(file),
            "ref": f"{line_no}:{row_id}",
            "status": str(item.get("status") or "pending"),
            "title": "Project Wiki pending writeback row",
        }
    ]
    for ref in string_list(item.get("evidenceIds")) + string_list(payload.get("evidence")):
        evidence.append({"kind": "pending-evidence-ref", "ref": ref, "path": ref})
    issue = str(payload.get("issueRecorder") or "")
    if issue:
        evidence.append({"kind": "issue-record", "path": issue, "ref": issue})
    return evidence


def string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


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


def import_context_docs(path: Path) -> Dict[str, int]:
    root = path.expanduser()
    files = collect_context_docs(root)
    return import_markdown_cards(root, files, "context-docs", classify_context_doc)


def import_openspec(path: Path) -> Dict[str, int]:
    root = path.expanduser()
    files = collect_prefixed_markdown(root, [("openspec/specs", "**/*.md"), ("openspec/changes", "**/*.md")])
    return import_markdown_cards(root, files, "openspec", classify_openspec_doc)


def import_speckit(path: Path) -> Dict[str, int]:
    root = path.expanduser()
    files = collect_prefixed_markdown(
        root,
        [
            (".specify/memory", "**/*.md"),
            (".specify/specs", "**/*.md"),
            ("specs", "**/spec.md"),
            ("specs", "**/plan.md"),
            ("specs", "**/tasks.md"),
        ],
    )
    return import_markdown_cards(root, files, "speckit", classify_speckit_doc)


def import_trellis(path: Path) -> Dict[str, int]:
    root = path.expanduser()
    files = collect_prefixed_markdown(
        root,
        [
            (".trellis/spec", "**/*.md"),
            (".trellis/tasks", "**/*.md"),
            (".trellis/workspace", "**/*.md"),
        ],
    )
    return import_markdown_cards(root, files, "trellis", classify_trellis_doc)


def import_project_memory_sources(path: Path) -> Dict[str, Any]:
    root = path.expanduser()
    result = {
        "context_docs": import_context_docs(root),
        "openspec": import_openspec(root),
        "speckit": import_speckit(root),
        "trellis": import_trellis(root),
    }
    result["cards"] = sum(int(item.get("cards") or 0) for item in result.values() if isinstance(item, dict))
    result["evidence"] = sum(int(item.get("evidence") or 0) for item in result.values() if isinstance(item, dict))
    return result


def import_project_memory_roots(roots: Iterable[Path]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"roots": 0, "cards": 0, "evidence": 0, "sources": []}
    seen: set[str] = set()
    for root in roots:
        base = root.expanduser()
        key = str(base)
        if key in seen or not base.exists():
            continue
        seen.add(key)
        imported = import_project_memory_sources(base)
        cards = int(imported.get("cards") or 0)
        evidence = int(imported.get("evidence") or 0)
        if cards or evidence:
            result["roots"] += 1
            result["cards"] += cards
            result["evidence"] += evidence
            result["sources"].append({"root": str(base), "cards": cards, "evidence": evidence})
    return result


def collect_context_docs(root: Path) -> List[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".md" else []
    patterns = [
        "CONTEXT.md",
        "CONTEXT-MAP.md",
        "CONTEXT*.md",
        "docs/adr/*.md",
        "adr/*.md",
        "docs/decisions/*.md",
    ]
    return unique_existing(root, patterns)


def collect_prefixed_markdown(root: Path, specs: Iterable[tuple[str, str]]) -> List[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".md" else []
    files: List[Path] = []
    for prefix, pattern in specs:
        base = root / prefix
        if base.exists():
            files.extend(p for p in base.glob(pattern) if p.is_file() and p.suffix.lower() == ".md")
    return unique_files(files)


def unique_existing(root: Path, patterns: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        files.extend(p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() == ".md")
    return unique_files(files)


def unique_files(files: Iterable[Path]) -> List[Path]:
    seen: set[str] = set()
    out: List[Path] = []
    for file in files:
        key = str(file.expanduser())
        if key not in seen:
            seen.add(key)
            out.append(file)
    return sorted(out)


def import_markdown_cards(root: Path, files: List[Path], source: str, classifier: Any) -> Dict[str, int]:
    if not files:
        return {"cards": 0, "evidence": 0, "skipped": str(root)}
    base = root if root.is_dir() else root.parent
    project_id = slugify(base.name, "project")
    cards = 0
    evidence = 0
    with connect() as conn:
        upsert_project(conn, {
            "project_id": project_id,
            "name": base.name,
            "root": str(base),
            "remote": "",
            "branch": "",
            "tech_stack": "",
            "aliases": [base.name, project_id],
            "status": "inferred",
            "updated_at": utc_now(),
            "source": source,
        })
        for file in files:
            text = read_markdown(file)
            meta = classifier(base, file, text)
            rel = relative_path(base, file)
            card_id = f"{source}.{project_id}.{slugify(str(rel), 'doc')}.{hashlib.sha1(str(rel).encode()).hexdigest()[:8]}"
            card = {
                "card_id": card_id,
                "project_id": project_id,
                "type": meta["type"],
                "title": meta["title"],
                "path": str(file),
                "status": meta["status"],
                "confidence": meta["confidence"],
                "aliases": meta["aliases"],
                "body": text,
                "updated_at": utc_now(),
                "source": source,
                "source_ref": str(rel),
            }
            upsert_card(conn, card)
            upsert_evidence(conn, {
                "evidence_id": f"{source}.{hashlib.sha1(str(file).encode()).hexdigest()[:16]}",
                "card_id": card_id,
                "project_id": project_id,
                "kind": meta["evidence_kind"],
                "ref": str(rel),
                "path": str(file),
                "title": meta["title"],
                "status": meta["status"],
                "body": summarize_markdown(text),
                "updated_at": utc_now(),
                "source": source,
            })
            cards += 1
            evidence += 1
        log_event(conn, f"import.{source}", payload={"path": str(root), "cards": cards, "evidence": evidence})
        conn.commit()
    return {"cards": cards, "evidence": evidence}


def classify_context_doc(root: Path, file: Path, text: str) -> Dict[str, Any]:
    rel = str(relative_path(root, file)).lower()
    is_adr = "/adr/" in rel or rel.startswith("adr/") or "/decisions/" in rel
    status = markdown_status(text)
    return markdown_meta(
        root,
        file,
        text,
        card_type="decision.adr" if is_adr else "context.terms",
        evidence_kind="adr" if is_adr else "context-doc",
        status=status if is_adr else "partial",
        confidence=0.85 if is_adr and status == "verified" else (0.72 if not is_adr else 0.65),
    )


def classify_openspec_doc(root: Path, file: Path, text: str) -> Dict[str, Any]:
    rel = str(relative_path(root, file)).lower()
    if "openspec/specs/" in rel:
        card_type = "spec.current"
        status = "partial"
        confidence = 0.78
    elif rel.endswith("tasks.md"):
        card_type = "spec.task"
        status = "partial"
        confidence = 0.55
    else:
        card_type = "spec.change"
        status = "partial"
        confidence = 0.62
    return markdown_meta(root, file, text, card_type=card_type, evidence_kind="openspec", status=status, confidence=confidence)


def classify_speckit_doc(root: Path, file: Path, text: str) -> Dict[str, Any]:
    rel = str(relative_path(root, file)).lower()
    if "constitution" in rel:
        card_type = "spec.constitution"
        confidence = 0.78
    elif rel.endswith("tasks.md"):
        card_type = "spec.task"
        confidence = 0.55
    elif rel.endswith("plan.md"):
        card_type = "spec.plan"
        confidence = 0.58
    else:
        card_type = "spec.current"
        confidence = 0.68
    return markdown_meta(root, file, text, card_type=card_type, evidence_kind="speckit", status="partial", confidence=confidence)


def classify_trellis_doc(root: Path, file: Path, text: str) -> Dict[str, Any]:
    rel = str(relative_path(root, file)).lower()
    if ".trellis/workspace/" in rel:
        card_type = "memory"
        status = "inferred"
        confidence = 0.45
        evidence_kind = "trellis-workspace"
    elif ".trellis/tasks/" in rel:
        card_type = "spec.task"
        status = "partial"
        confidence = 0.55
        evidence_kind = "trellis-task"
    else:
        card_type = "spec.current"
        status = "partial"
        confidence = 0.65
        evidence_kind = "trellis-spec"
    return markdown_meta(root, file, text, card_type=card_type, evidence_kind=evidence_kind, status=status, confidence=confidence)


def markdown_meta(root: Path, file: Path, text: str, *, card_type: str, evidence_kind: str, status: str, confidence: float) -> Dict[str, Any]:
    title = markdown_title(text) or file.stem
    aliases = list(dict.fromkeys([title, file.stem, root.name, *markdown_headings(text)[:8]]))
    return {
        "type": card_type,
        "title": title,
        "status": normalize_status(status),
        "confidence": confidence,
        "aliases": [alias for alias in aliases if alias][:30],
        "evidence_kind": evidence_kind,
    }


def read_markdown(path: Path, limit: int = 60000) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")[:limit]


def relative_path(root: Path, file: Path) -> Path:
    try:
        return file.relative_to(root)
    except ValueError:
        return Path(file.name)


def markdown_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:96]
    return ""


def markdown_headings(text: str) -> List[str]:
    out: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title and title not in out:
                out.append(title)
    return out


def markdown_status(text: str) -> str:
    value = field_from_text(text, "Status") or field_from_text(text, "status")
    value = value.lower()
    if any(item in value for item in ("accepted", "adopted", "done", "approved")):
        return "verified"
    if any(item in value for item in ("rejected", "superseded", "deprecated")):
        return "stale"
    if any(item in value for item in ("proposed", "draft", "wip")):
        return "partial"
    return "partial"


def summarize_markdown(text: str, limit: int = 1600) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


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
