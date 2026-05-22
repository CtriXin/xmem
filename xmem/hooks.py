from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .project import card_from_file, detect_project, index_local, init_project
from .search import search_cards
from .store import connect, log_event, upsert_card
from .util import append_jsonl, emit_yaml, git_root, home_dir, slugify, utc_now, write_json

PROJECT_MARKERS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", ".git")
MEMORY_EVENTS = {"note", "finish", "fix", "bug", "release", "deploy", "decision"}


def run_hook(
    event: str,
    text: str = "",
    path: Path | str = ".",
    destinations: Iterable[str] = ("auto",),
    verified: bool = False,
    target: str = "",
) -> Dict[str, Any]:
    event = event.strip().lower()
    if event == "status":
        return {"event": "status", "outbox": outbox_counts(), "project_wiki": project_wiki_status()}

    root = git_root(Path(path))
    project = ensure_hook_project(root)
    indexed = index_local(root) if (root / ".xmem").exists() else 0
    matches = explain_matches(text) if text else []
    dests = infer_destinations(event, text, destinations)

    result: Dict[str, Any] = {
        "event": event,
        "project": project,
        "root": str(root),
        "indexed_cards": indexed,
        "destinations": dests,
        "matches": matches,
        "outbox": {},
    }

    if event in MEMORY_EVENTS and text:
        card_path = write_hook_card(root, project, event, text, dests, verified, matches)
        index_card(card_path, project.get("project_id", ""))
        if (root / ".xmem").exists():
            result["indexed_cards"] = index_local(root)
        result["card"] = str(card_path)
        if "project-wiki" in dests:
            result["outbox"]["project_wiki"] = write_project_wiki_request(project, event, text, card_path, target, verified)
        if "issue-tracking" in dests:
            result["outbox"]["issue_tracking"] = write_issue_seed(project, event, text, card_path, verified)

    with connect() as conn:
        log_event(conn, "hook." + event, project_id=project.get("project_id", ""), payload={"text": text[:500], "destinations": dests})
        conn.commit()

    result["outbox_counts"] = outbox_counts()
    return result


def ensure_hook_project(root: Path) -> Dict[str, Any]:
    if looks_like_project(root) or (root / ".xmem").exists():
        return init_project(root)
    return detect_project(root)


def looks_like_project(root: Path) -> bool:
    return any((root / marker).exists() for marker in PROJECT_MARKERS)


def infer_destinations(event: str, text: str, destinations: Iterable[str]) -> List[str]:
    requested = [item for item in destinations if item]
    if not requested or requested == ["auto"]:
        dests = {"xmem"}
        haystack = f"{event}\n{text}".lower()
        if event in {"release", "deploy"} or re.search(r"\b(domain|service|repo|branch|deploy|pipeline|version)\b", haystack):
            dests.add("project-wiki")
        if event in {"finish", "fix", "bug", "issue", "release", "deploy"}:
            dests.add("issue-tracking")
        return sorted(dests)
    dests: set[str] = set()
    for item in requested:
        if item == "auto":
            dests.update(infer_destinations(event, text, []))
        elif item == "all":
            dests.update({"xmem", "project-wiki", "issue-tracking"})
        else:
            dests.add(item)
    dests.add("xmem")
    return sorted(dests)


def explain_matches(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for card in search_cards(text, 5):
        out.append(
            {
                "id": card.get("card_id", ""),
                "type": card.get("type", ""),
                "truth": card.get("status", ""),
                "score": card.get("score", 0),
                "why": card.get("why", ""),
                "source_ref": card.get("source_ref") or card.get("path", ""),
            }
        )
    return out


def write_hook_card(
    root: Path,
    project: Dict[str, Any],
    event: str,
    text: str,
    destinations: List[str],
    verified: bool,
    matches: List[Dict[str, Any]],
) -> Path:
    cid = hook_id(event, text)
    base = root / ".xmem" / "cards" if (root / ".xmem").exists() else home_dir() / "cards" / "hooks"
    path = base / f"{cid}.yaml"
    status = "verified" if verified else "partial"
    confidence = 0.9 if verified else 0.55
    aliases = [project.get("name", ""), project.get("project_id", ""), *project.get("aliases", [])]
    data: Dict[str, Any] = {
        "id": cid,
        "type": "hook.memory",
        "title": f"{event}: {text[:80]}",
        "scope": {"project": project.get("project_id", ""), "repo": project.get("root", str(root)), "event": event},
        "aliases": [item for item in dict.fromkeys(aliases) if item],
        "truth": {
            "status": status,
            "confidence": confidence,
            "basis": ["agent_hook"] + (["human_or_runtime_verified"] if verified else ["needs_review"]),
            "git_sha": project.get("git_sha", ""),
            "last_checked_at": utc_now(),
        },
        "summary": text,
        "destinations": destinations,
        "related_matches": [match.get("id", "") for match in matches[:5]],
        "evidence": [{"kind": "repo", "path": project.get("root", str(root)), "ref": project.get("branch", "")}],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(emit_yaml(data) + "\n", encoding="utf-8")
    return path


def hook_id(event: str, text: str) -> str:
    stamp = utc_now().replace("-", "").replace(":", "").replace("Z", "Z").split(".")[0]
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"hook.{slugify(event)}.{stamp}.{slugify(text[:48], 'memory')}.{digest}"


def index_card(path: Path, project_id: str) -> None:
    card = card_from_file(path, project_id)
    card["source"] = "hook-card"
    card["source_ref"] = str(path)
    with connect() as conn:
        upsert_card(conn, card)
        log_event(conn, "hook.card", project_id=project_id, card_id=card["card_id"], payload={"path": str(path)})
        conn.commit()


def write_project_wiki_request(project: Dict[str, Any], event: str, text: str, card_path: Path, target: str, verified: bool) -> Dict[str, Any]:
    root = Path(os.environ.get("XMEM_PROJECT_WIKI", "/Users/xin/project-wiki")).expanduser()
    inbox = root / "data" / "agent-inbox.jsonl"
    request = {
        "status": "pending",
        "risk": "low" if verified else "needs_review",
        "actor": "xmem-hook",
        "action": "append_record",
        "targetEntityId": target or f"project:{project.get('project_id', 'unknown')}",
        "payload": {
            "type": "xmem_hook_memory",
            "summary": text,
            "event": event,
            "project": project.get("project_id", ""),
            "displayName": project.get("name", ""),
            "aliases": project.get("aliases", []),
            "repo": project.get("remote", ""),
            "localPath": project.get("root", ""),
            "branch": project.get("branch", ""),
            "commit": project.get("git_sha", ""),
            "xmemCard": str(card_path),
            "source": "xmem hook append-only queue",
        },
        "validation": [{"label": "xmem hook captured", "ok": True, "detail": str(card_path)}],
        "evidenceIds": [f"xmem:{card_path}"],
        "receivedAt": utc_now(),
        "id": "wr_xmem_" + hashlib.sha1(f"{card_path}:{text}".encode("utf-8")).hexdigest()[:12],
    }
    if inbox.parent.exists():
        append_jsonl(inbox, request)
        return {"status": "queued", "path": str(inbox), "id": request["id"]}
    out = home_dir() / "outbox" / "project-wiki" / f"{request['id']}.json"
    write_json(out, request)
    return {"status": "outbox", "path": str(out), "id": request["id"], "reason": "project-wiki data dir missing"}


def write_issue_seed(project: Dict[str, Any], event: str, text: str, card_path: Path, verified: bool) -> Dict[str, Any]:
    iid = "issue_xmem_" + hashlib.sha1(f"{card_path}:{text}".encode("utf-8")).hexdigest()[:12]
    out = home_dir() / "outbox" / "issue-tracking" / f"{iid}.md"
    body = "\n".join(
        [
            "# Issue Seed",
            "",
            f"- Project: {project.get('project_id', '')}",
            f"- Repo path: {project.get('root', '')}",
            f"- Branch: {project.get('branch', '')}",
            f"- Issue: {iid}",
            f"- Task name: {text[:120]}",
            f"- Work type: {event}",
            f"- Status: {'verified' if verified else 'pending'}",
            f"- Source: xmem hook",
            f"- xmemCard: {card_path}",
            "",
            "## Summary",
            text,
            "",
            "## Next",
            "- Promote with issue-recorder when this becomes durable work evidence.",
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    return {"status": "seeded", "path": str(out), "id": iid}


def project_wiki_status() -> Dict[str, Any]:
    root = Path(os.environ.get("XMEM_PROJECT_WIKI", "/Users/xin/project-wiki")).expanduser()
    inbox = root / "data" / "agent-inbox.jsonl"
    return {"root": str(root), "inbox": str(inbox), "available": inbox.parent.exists()}


def outbox_counts() -> Dict[str, int]:
    base = home_dir() / "outbox"
    return {
        "project_wiki": count_files(base / "project-wiki"),
        "issue_tracking": count_files(base / "issue-tracking"),
    }


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.glob("*") if item.is_file())
