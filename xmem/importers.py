from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .store import connect, log_event, upsert_card, upsert_evidence, upsert_project
from .util import field_from_text, flatten_strings, read_json, slugify, utc_now


def import_project_wiki(path: Path) -> Dict[str, int]:
    idx = path / "data" / "project-hub.index.json"
    data = read_json(idx, {})
    if not isinstance(data, dict) or "entities" not in data:
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
    return {"cards": cards, "projects": projects}


def import_issue_tracking(path: Path) -> Dict[str, int]:
    issues_dir = path / "issues"
    if not issues_dir.exists():
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
    return {"cards": count, "evidence": evidence}


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
