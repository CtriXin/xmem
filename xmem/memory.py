from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .project import card_from_file, detect_project
from .search import search_cards
from .store import connect, log_event, rows, upsert_card, upsert_project
from .trellis_policy import D3_REF, REGISTRY_REF, direct_promotion_error, is_trellis_card, source_sha256, trellis_source_kind, trellis_warnings
from .util import append_jsonl, emit_yaml, git_root, home_dir, query_terms, slugify, utc_now


MEMORY_TYPES = {
    "preference",
    "project_fact",
    "decision",
    "invariant",
    "bug_pattern",
    "rejected_option",
    "workflow_lesson",
    "source_pointer",
    "memory",
}

PROJECT_SCOPES = {"project", "repo", "task", "workflow"}
FTS_SIGNATURE_VERSION = "v1"
REDACTION_PATTERNS = [
    re.compile(r"<private>.*?</private>", re.I | re.S),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"\b(sk|sm|ghp|github_pat|xox[baprs])_[A-Za-z0-9_\-]{12,}\b"),
]


def capture_memories(
    text: str,
    *,
    cwd: Path | None = None,
    scope: str = "project",
    memory_type: str = "",
    confidence: float = 0.55,
    evidence_path: str = "",
    ttl: str = "",
    supersedes: Iterable[str] = (),
    aliases: Iterable[str] = (),
    source: str = "manual",
) -> Dict[str, Any]:
    root, project = current_project(cwd)
    clean = redact_text(text).strip()
    if not clean:
        raise SystemExit("capture text is empty after redaction")

    created: List[Dict[str, Any]] = []
    for item in extract_memory_items(clean, memory_type):
        pending = build_pending_memory(
            item,
            root=root,
            project=project,
            scope=str(item.get("scope") or scope),
            confidence=parse_confidence(item.get("confidence"), confidence),
            evidence_path=str(item.get("evidence_path") or evidence_path),
            ttl=str(item.get("ttl") or ttl),
            supersedes=[*list(supersedes), *split_list(str(item.get("supersedes") or ""))],
            aliases=[*list(aliases), *split_list(str(item.get("aliases") or ""))],
            source=str(item.get("source") or source),
        )
        path = pending_path_for(pending, root=root)
        write_pending(path, pending)
        created.append({"id": pending["id"], "path": str(path), "type": pending["type"], "title": pending["title"]})

    return {"created": len(created), "pending": created}


def review_pending(*, cwd: Path | None = None, include_all: bool = False, limit: int = 20) -> Dict[str, Any]:
    root, _project = current_project(cwd)
    records = load_pending_records(root=root, include_all=include_all)
    pending = [record for _path, record in records if record.get("status") == "pending"]
    pending.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if limit:
        pending = pending[:limit]
    return {
        "schema": "xmem.pending_review.v1",
        "count": len(pending),
        "pending": [compact_pending(item) for item in pending],
    }


def promote_memory(
    memory_id: str,
    *,
    cwd: Path | None = None,
    verified: bool = False,
    target_scope: str = "",
) -> Dict[str, Any]:
    root, project = current_project(cwd)
    try:
        path, pending = find_pending(memory_id, root=root)
    except SystemExit:
        card = find_indexed_card(memory_id)
        if card and is_trellis_card(card):
            raise SystemExit(direct_promotion_error())
        raise
    if pending.get("status") != "pending":
        raise SystemExit(f"pending memory is not promotable: {memory_id} status={pending.get('status')}")

    if target_scope:
        pending.setdefault("scope", {})["kind"] = target_scope
    status = "verified" if verified else "partial"
    card = card_from_pending(pending, root=root, project=project, status=status)
    card_path = card_path_for(card, pending, root=root)
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(emit_yaml(card) + "\n", encoding="utf-8")

    with connect() as conn:
        if project:
            upsert_project(conn, project)
        else:
            upsert_project(conn, user_project())
        parsed = card_from_file(card_path, card.get("project_id") or "user")
        upsert_card(conn, parsed)
        log_event(
            conn,
            "memory.promote",
            project_id=card.get("project_id"),
            card_id=card["id"],
            payload={"pending_id": pending["id"], "path": str(card_path), "status": status},
        )
        conn.commit()

    pending["status"] = "promoted"
    pending["promoted_at"] = utc_now()
    pending["promoted_card_id"] = card["id"]
    pending["promoted_path"] = str(card_path)
    write_pending(path, pending)
    return {"id": pending["id"], "card_id": card["id"], "path": str(card_path), "status": status}


def forget_memory(memory_id: str, *, reason: str = "", cwd: Path | None = None) -> Dict[str, Any]:
    root, _project = current_project(cwd)
    try:
        path, pending = find_pending(memory_id, root=root)
    except SystemExit:
        marker = lifecycle_marker("forgotten", memory_id, reason=reason)
        write_lifecycle_marker(marker)
        return {"id": memory_id, "status": "forgotten", "scope": "card", "path": marker["path"]}

    pending["status"] = "forgotten"
    pending["forgotten_at"] = utc_now()
    pending["forget_reason"] = reason
    write_pending(path, pending)
    return {"id": pending["id"], "status": "forgotten", "scope": "pending", "path": str(path)}


def supersede_memory(old_id: str, new_id: str, *, reason: str = "", cwd: Path | None = None) -> Dict[str, Any]:
    root, _project = current_project(cwd)
    marker = lifecycle_marker("superseded", old_id, new_id=new_id, reason=reason)
    write_lifecycle_marker(marker)
    try:
        path, pending = find_pending(old_id, root=root)
    except SystemExit:
        return {"old_id": old_id, "new_id": new_id, "status": "superseded", "path": marker["path"]}
    pending["status"] = "superseded"
    pending["superseded_at"] = utc_now()
    pending["superseded_by"] = new_id
    pending["supersede_reason"] = reason
    write_pending(path, pending)
    return {"old_id": old_id, "new_id": new_id, "status": "superseded", "path": str(path)}


def build_recall(query: str, *, cwd: Path | None = None, limit: int = 8, include_pending: bool = False) -> Dict[str, Any]:
    root, project = current_project(cwd)
    base_cards = search_cards(query, max(limit * 4, 20), gain_event="recall")
    fts_cards = fts_search_cards(query, max(limit * 4, 20))
    cards = rank_recall_cards(query, merge_card_lists(base_cards, fts_cards), project=project)
    lifecycle = load_lifecycle()
    filtered = []
    for card in cards:
        cid = str(card.get("card_id") or "")
        if cid in lifecycle["forgotten"] or cid in lifecycle["superseded"]:
            continue
        filtered.append(card)
    memories = [compact_recall_card(card) for card in filtered[:limit]]
    warnings = trellis_warnings(filtered[:limit])
    pending: List[Dict[str, Any]] = []
    if include_pending:
        pending = [
            compact_pending(record)
            for _path, record in load_pending_records(root=root, include_all=False)
            if record.get("status") == "pending"
        ][: max(3, limit // 2)]
    return {
        "schema": "xmem.recall.v1",
        "query": query,
        "generated_at": utc_now(),
        "scope": recall_scope(root, project),
        "ranking": {
            "mode": "local_hybrid",
            "signals": [
                "sqlite_fts5",
                "keyword",
                "semantic_lite",
                "project_match",
                "confidence",
                "recency",
                "ttl_decay",
                "lifecycle_filter",
            ],
        },
        "warnings": warnings,
        "memories": memories,
        "pending": pending,
        "profile_refs": profile_refs(root, project),
        "token_estimate": estimate_tokens(memories),
        "next_reads": [item["path"] for item in memories if item.get("path")][:5],
    }


def synthesize_profile(*, cwd: Path | None = None, write: bool = True) -> Dict[str, Any]:
    root, project = current_project(cwd)
    with connect() as conn:
        cards = rows(conn, "SELECT * FROM cards ORDER BY updated_at DESC LIMIT 400")
        events = rows(conn, "SELECT * FROM events ORDER BY id DESC LIMIT 30")

    user_cards = [
        card
        for card in cards
        if str(card.get("type") or "") in {"preference", "rejected_option", "invariant", "workflow_lesson", "memory"}
    ][:30]
    project_cards = []
    if project:
        pid = project.get("project_id")
        project_cards = [card for card in cards if card.get("project_id") == pid][:40]

    user_md = render_profile_markdown(
        "User Memory Profile",
        static_cards=[card for card in user_cards if float(card.get("confidence") or 0) >= 0.7],
        dynamic_cards=[card for card in user_cards if float(card.get("confidence") or 0) < 0.7],
        events=events,
    )
    project_md = ""
    project_path = ""
    if project:
        project_md = render_profile_markdown(
            f"Project Memory Profile: {project.get('name') or project.get('project_id')}",
            static_cards=[card for card in project_cards if str(card.get("status")) == "verified"],
            dynamic_cards=[card for card in project_cards if str(card.get("status")) != "verified"],
            events=events,
        )

    paths: Dict[str, str] = {}
    if write:
        user_path = home_dir() / "profile" / "user.md"
        user_path.parent.mkdir(parents=True, exist_ok=True)
        user_path.write_text(user_md, encoding="utf-8")
        paths["user"] = str(user_path)
        if project and project_md:
            project_slug = slugify(str(project.get("project_id") or project.get("name") or "project"), "project")
            out = home_dir() / "profile" / "projects" / f"{project_slug}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(project_md, encoding="utf-8")
            paths["project"] = str(out)
            project_path = str(out)

    return {
        "schema": "xmem.profile.v1",
        "generated_at": utc_now(),
        "paths": paths,
        "project": project.get("project_id") if project else "",
        "user_profile": user_md,
        "project_profile": project_md,
        "project_profile_path": project_path,
    }


def format_recall(packet: Dict[str, Any]) -> str:
    lines = ["xmem_recall:", f"  query: {packet['query']}", f"  mode: {packet['ranking']['mode']}"]
    scope = packet.get("scope") or {}
    if scope.get("project_id"):
        lines.append(f"  project: {scope['project_id']}")
    warnings = packet.get("warnings") or []
    if warnings:
        lines.append("  warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")
    lines.append("  memories:")
    for item in packet.get("memories") or []:
        lines.append(
            f"  - id: {item['id']} score={item['score']} truth={item['truth']} confidence={item['confidence']} title={item['title']}"
        )
        if item.get("summary"):
            lines.append(f"    summary: {item['summary']}")
        if item.get("path"):
            lines.append(f"    path: {item['path']}")
    if not packet.get("memories"):
        lines.append("  - none")
    return "\n".join(lines)


def format_pending_review(packet: Dict[str, Any]) -> str:
    lines = [f"xmem_pending: count={packet.get('count', 0)}"]
    for item in packet.get("pending") or []:
        lines.append(f"- {item['id']} [{item['type']}] confidence={item['confidence']} title={item['title']}")
        lines.append(f"  scope: {item.get('scope')}")
        lines.append(f"  path: {item.get('path')}")
    if not packet.get("pending"):
        lines.append("- none")
    return "\n".join(lines)


def current_project(cwd: Path | None) -> Tuple[Path | None, Dict[str, Any] | None]:
    base = cwd or Path.cwd()
    try:
        root = git_root(base)
        return root, detect_project(root)
    except Exception:
        return None, None


def extract_memory_items(text: str, memory_type: str = "") -> List[Dict[str, str]]:
    forced = normalize_memory_type(memory_type)
    structured = extract_structured_memory_items(text, forced)
    if structured:
        return structured
    items: List[Dict[str, str]] = []
    pattern = re.compile(r"^\s*(?:[-*]\s*)?(preference|project_fact|decision|invariant|bug_pattern|rejected_option|workflow_lesson|source_pointer|memory)\s*[:：-]\s*(.+)$", re.I)
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        mtype = forced or normalize_memory_type(match.group(1))
        body = match.group(2).strip()
        if body:
            items.append({"type": mtype, "summary": body, "title": title_from_summary(body)})
    if items:
        return items
    mtype = forced or infer_memory_type(text)
    return [{"type": mtype, "summary": compact_text(text, 1000), "title": title_from_summary(text)}]


def extract_structured_memory_items(text: str, forced_type: str = "") -> List[Dict[str, str]]:
    fields = {
        "type",
        "kind",
        "summary",
        "title",
        "scope",
        "confidence",
        "evidence_path",
        "evidence",
        "ttl",
        "supersedes",
        "aliases",
        "source",
    }
    blocks: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    last_key = ""
    saw_field = False
    for raw in [*text.splitlines(), "---"]:
        line = raw.rstrip()
        if line.strip() == "---":
            if current.get("summary") or current.get("title"):
                item = normalize_structured_memory(current, forced_type)
                if item:
                    blocks.append(item)
            current = {}
            last_key = ""
            continue
        match = re.match(r"^\s*(type|kind|summary|title|scope|confidence|evidence_path|evidence|ttl|supersedes|aliases|source)\s*[:=]\s*(.*)$", line, re.I)
        if match:
            key = match.group(1).strip().lower()
            value = match.group(2).strip()
            if key in fields:
                if key == "evidence":
                    key = "evidence_path"
                current[key] = value
                last_key = key
                saw_field = True
            continue
        if last_key in {"summary", "title"} and line.strip():
            current[last_key] = f"{current.get(last_key, '')}\n{line.strip()}".strip()

    return blocks if saw_field else []


def normalize_structured_memory(data: Dict[str, str], forced_type: str) -> Dict[str, str]:
    mtype = forced_type or normalize_memory_type(data.get("type") or data.get("kind") or "")
    if not mtype:
        mtype = infer_memory_type(data.get("summary") or data.get("title") or "")
    summary = compact_text(data.get("summary") or data.get("title") or "", 1400)
    if not summary:
        return {}
    title = compact_text(data.get("title") or title_from_summary(summary), 90)
    return {
        "type": mtype,
        "summary": summary,
        "title": title,
        "scope": data.get("scope", ""),
        "confidence": data.get("confidence", ""),
        "evidence_path": data.get("evidence_path", ""),
        "ttl": data.get("ttl", ""),
        "supersedes": data.get("supersedes", ""),
        "aliases": data.get("aliases", ""),
        "source": data.get("source", ""),
    }


def build_pending_memory(
    item: Dict[str, str],
    *,
    root: Path | None,
    project: Dict[str, Any] | None,
    scope: str,
    confidence: float,
    evidence_path: str,
    ttl: str,
    supersedes: List[str],
    aliases: List[str],
    source: str,
) -> Dict[str, Any]:
    summary = compact_text(item["summary"], 1400)
    project_id = project.get("project_id") if project else ""
    seed = "|".join([item["type"], scope, project_id, summary])
    mid = f"pending.{item['type']}.{slugify(item['title'], 'memory')}.{hash_seed(seed)}"
    return {
        "schema": "xmem.pending_memory.v1",
        "id": mid,
        "status": "pending",
        "type": item["type"],
        "title": item["title"],
        "summary": summary,
        "scope": {
            "kind": scope,
            "project_id": project_id,
            "cwd": str(root or ""),
            "repo": project.get("remote", "") if project else "",
        },
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 3),
        "aliases": sorted(set([alias for alias in aliases if alias])),
        "ttl": ttl,
        "supersedes": supersedes,
        "evidence": [{"kind": source, "path": evidence_path or str(root or ""), "ref": ""}],
        "created_at": utc_now(),
    }


def pending_path_for(pending: Dict[str, Any], *, root: Path | None) -> Path:
    scope = (pending.get("scope") or {}).get("kind") or "project"
    base = root / ".xmem" / "pending" if root and scope in PROJECT_SCOPES else home_dir() / "pending"
    return base / f"{pending['id']}.json"


def write_pending(path: Path, pending: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_pending_records(*, root: Path | None, include_all: bool) -> List[Tuple[Path, Dict[str, Any]]]:
    dirs = [home_dir() / "pending"]
    if root:
        dirs.insert(0, root / ".xmem" / "pending")
    if include_all:
        dirs.extend(path for path in home_dir().glob("sources/*/.xmem/pending") if path.is_dir())
    records: List[Tuple[Path, Dict[str, Any]]] = []
    seen: set[str] = set()
    for directory in dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            mid = str(record.get("id") or path.stem)
            if mid in seen:
                continue
            seen.add(mid)
            record["_path"] = str(path)
            records.append((path, record))
    return records


def find_pending(memory_id: str, *, root: Path | None) -> Tuple[Path, Dict[str, Any]]:
    for path, record in load_pending_records(root=root, include_all=True):
        if memory_id in {str(record.get("id")), path.stem}:
            return path, record
    raise SystemExit(f"pending memory not found: {memory_id}")


def find_indexed_card(card_id: str) -> Dict[str, Any] | None:
    with connect() as conn:
        found = rows(conn, "SELECT * FROM cards WHERE card_id=? LIMIT 1", (card_id,))
    return found[0] if found else None


def promote_trellis_memory(
    *,
    source_card: str,
    decision: str,
    decided_by: str,
    basis: str,
    basis_file: str = "",
    output_type: str = "source_pointer",
    summary: str = "",
    evidence: str = "",
    cwd: Path | None = None,
) -> Dict[str, Any]:
    root, _project = current_project(cwd)
    card = find_indexed_card(source_card)
    if not card:
        raise SystemExit(f"Trellis source card not found: {source_card}")
    if not is_trellis_card(card):
        raise SystemExit(f"source card is not a Trellis artifact: {source_card}")

    safe_decision = decision.strip().lower()
    if safe_decision not in {"distill", "reject", "keep-pointer"}:
        raise SystemExit(f"unsupported Trellis promotion decision: {decision}")
    basis_text = basis.strip()
    if basis_file:
        basis_path = Path(basis_file).expanduser()
        basis_text = (basis_text + "\n" + basis_path.read_text(encoding="utf-8", errors="ignore")).strip()
    if not decided_by.strip() or not basis_text:
        raise SystemExit("promote-trellis requires --decided-by and --basis/--basis-file")

    output_pending_id = ""
    output_path = ""
    output_status = ""
    normalized_type = output_type.strip().lower().replace("-", "_") or "source_pointer"
    if safe_decision == "distill":
        if not summary.strip():
            raise SystemExit("promote-trellis --decision distill requires --summary")
        evidence_path = evidence or str(card.get("path") or card.get("source_ref") or "")
        captured = capture_memories(
            f"{normalized_type}: {summary.strip()}",
            cwd=root or cwd,
            scope="project",
            memory_type=normalized_type,
            confidence=0.65,
            evidence_path=evidence_path,
            aliases=[source_card, "trellis-distilled"],
            source="trellis-promotion",
        )
        pending = captured["pending"][0]
        output_pending_id = pending["id"]
        output_path = pending["path"]
        output_status = "pending"

    audit = {
        "timestamp": utc_now(),
        "actor": "xmem",
        "command": "promote-trellis",
        "decision": safe_decision,
        "decided_by": decided_by.strip(),
        "basis": basis_text,
        "basis_file": basis_file or "",
        "source_card_id": source_card,
        "source_repo": card.get("project_id") or "",
        "source_path": card.get("path") or "",
        "source_ref": card.get("source_ref") or "",
        "source_kind": trellis_source_kind(card),
        "source_sha256": source_sha256(Path(str(card.get("path") or "")), str(card.get("body") or "")),
        "output_card_id": "",
        "output_pending_id": output_pending_id,
        "output_path": output_path,
        "output_status": output_status,
        "d3_ref": D3_REF,
        "registry_ref": REGISTRY_REF,
        "result": "audit_recorded",
    }
    audit_path = home_dir() / "audit" / "trellis-promotion-audit.jsonl"
    append_jsonl(audit_path, audit)
    with connect() as conn:
        log_event(conn, "trellis.promote_decision", card_id=source_card, project_id=str(card.get("project_id") or ""), payload=audit)
        conn.commit()
    return {
        "schema": "xmem.trellis_promotion.v1",
        "decision": safe_decision,
        "source_card_id": source_card,
        "output_pending_id": output_pending_id,
        "output_path": output_path,
        "audit_path": str(audit_path),
    }


def card_from_pending(
    pending: Dict[str, Any],
    *,
    root: Path | None,
    project: Dict[str, Any] | None,
    status: str,
) -> Dict[str, Any]:
    scope = pending.get("scope") or {}
    project_id = scope.get("project_id") or (project.get("project_id") if project else "user")
    card_id = f"memory.{pending['type']}.{slugify(pending['title'], 'memory')}.{hash_seed(pending['id'])}"
    return {
        "id": card_id,
        "type": pending["type"],
        "title": pending["title"],
        "project_id": project_id,
        "scope": scope,
        "status": status,
        "confidence": 0.9 if status == "verified" else pending.get("confidence", 0.55),
        "aliases": pending.get("aliases") or [],
        "basis": ["captured_pending_memory", "human_or_agent_promoted" if status == "verified" else "needs_owner_review"],
        "summary": pending.get("summary", ""),
        "ttl": pending.get("ttl", ""),
        "supersedes": pending.get("supersedes") or [],
        "evidence": pending.get("evidence") or [],
        "created_at": pending.get("created_at", ""),
        "last_checked_at": utc_now(),
        "source_ref": pending.get("_path", pending.get("id", "")),
    }


def card_path_for(card: Dict[str, Any], pending: Dict[str, Any], *, root: Path | None) -> Path:
    scope = (pending.get("scope") or {}).get("kind") or "project"
    filename = f"{slugify(card['id'], 'memory')}.yaml"
    if root and scope in PROJECT_SCOPES:
        return root / ".xmem" / "cards" / filename
    return home_dir() / "cards" / filename


def user_project() -> Dict[str, Any]:
    return {
        "project_id": "user",
        "name": "user",
        "root": str(home_dir()),
        "remote": "",
        "branch": "",
        "tech_stack": "memory",
        "aliases": ["user", "global user memory"],
        "status": "partial",
        "updated_at": utc_now(),
        "source": "xmem-memory",
    }


def fts_search_cards(query: str, limit: int) -> List[Dict[str, Any]]:
    match = fts_match_query(query)
    if not match:
        return []
    try:
        with connect() as conn:
            ensure_memory_fts(conn)
            found = rows(
                conn,
                """SELECT card_id, title, body, aliases, project_id, type, status, confidence, path, source_ref,
                          bm25(memory_fts) AS rank
                   FROM memory_fts
                   WHERE memory_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (match, limit),
            )
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(found):
        item = {
            "card_id": row.get("card_id"),
            "title": row.get("title"),
            "body": row.get("body"),
            "aliases_json": row.get("aliases") or "[]",
            "project_id": row.get("project_id"),
            "type": row.get("type"),
            "status": row.get("status"),
            "confidence": row.get("confidence"),
            "path": row.get("path"),
            "source_ref": row.get("source_ref"),
            "score": round(6.0 - min(index * 0.2, 4.0), 3),
            "why": ["sqlite_fts5"],
        }
        out.append(item)
    return out


def ensure_memory_fts(conn: Any) -> None:
    conn.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        card_id UNINDEXED,
        title,
        body,
        aliases,
        project_id UNINDEXED,
        type UNINDEXED,
        status UNINDEXED,
        confidence UNINDEXED,
        path UNINDEXED,
        source_ref UNINDEXED
    )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS memory_fts_meta(key TEXT PRIMARY KEY, value TEXT)")
    signature = memory_fts_signature(conn)
    cached = conn.execute("SELECT value FROM memory_fts_meta WHERE key='cards_signature'").fetchone()
    try:
        indexed_count = int(conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0])
    except Exception:
        indexed_count = 0
    if cached and cached[0] == signature and indexed_count:
        return

    conn.execute("DELETE FROM memory_fts")
    for card in rows(conn, "SELECT * FROM cards"):
        conn.execute(
            """INSERT INTO memory_fts(card_id,title,body,aliases,project_id,type,status,confidence,path,source_ref)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                card.get("card_id"),
                card.get("title"),
                card.get("body"),
                " ".join(json.loads(card.get("aliases_json") or "[]")),
                card.get("project_id"),
                card.get("type"),
                card.get("status"),
                str(card.get("confidence") or ""),
                card.get("path"),
                card.get("source_ref"),
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO memory_fts_meta(key, value) VALUES('cards_signature', ?)",
        (signature,),
    )
    conn.commit()


def memory_fts_signature(conn: Any) -> str:
    row = conn.execute(
        """SELECT COUNT(*),
                  COALESCE(MAX(updated_at), ''),
                  COALESCE(SUM(LENGTH(card_id)), 0),
                  COALESCE(SUM(LENGTH(title)), 0),
                  COALESCE(SUM(LENGTH(body)), 0),
                  COALESCE(SUM(LENGTH(aliases_json)), 0)
           FROM cards"""
    ).fetchone()
    return json.dumps([FTS_SIGNATURE_VERSION, *list(row)], separators=(",", ":"))


def rank_recall_cards(query: str, cards: List[Dict[str, Any]], *, project: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    project_id = project.get("project_id") if project else ""
    query_fingerprint = semantic_fingerprint(query)
    ranked: List[Dict[str, Any]] = []
    for card in cards:
        score = float(card.get("score") or 0)
        why = list(card.get("why") or [])
        semantic = semantic_similarity(query_fingerprint, card_semantic_text(card))
        if semantic:
            score += min(semantic * 4.0, 3.0)
            why.append(f"semantic_lite:{semantic:.2f}")
        if project_id and card.get("project_id") == project_id:
            score += 2.5
            why.append("project_match")
        confidence = float(card.get("confidence") or 0)
        score += confidence * 1.5
        updated = recency_boost(str(card.get("updated_at") or ""))
        if updated:
            score += updated
            why.append("recency")
        ttl_penalty = ttl_decay_penalty(card)
        if ttl_penalty:
            score -= ttl_penalty
            why.append(f"ttl_decay:-{ttl_penalty:.1f}")
        card = dict(card)
        card["recall_score"] = round(score, 3)
        card["why"] = why
        ranked.append(card)
    ranked.sort(key=lambda item: (float(item.get("recall_score") or 0), float(item.get("confidence") or 0)), reverse=True)
    return ranked


def merge_card_lists(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for card in group:
            cid = str(card.get("card_id") or "")
            if not cid:
                continue
            if cid not in merged or float(card.get("score") or 0) > float(merged[cid].get("score") or 0):
                merged[cid] = dict(card)
            else:
                why = list(merged[cid].get("why") or [])
                why.extend(item for item in (card.get("why") or []) if item not in why)
                merged[cid]["why"] = why
    return list(merged.values())


def compact_recall_card(card: Dict[str, Any]) -> Dict[str, Any]:
    aliases = []
    try:
        aliases = json.loads(card.get("aliases_json") or "[]")
    except Exception:
        pass
    return {
        "id": card.get("card_id"),
        "type": card.get("type"),
        "title": card.get("title"),
        "truth": card.get("status"),
        "confidence": round(float(card.get("confidence") or 0), 3),
        "score": round(float(card.get("recall_score") or card.get("score") or 0), 3),
        "why": card.get("why") or [],
        "aliases": aliases[:6],
        "summary": summary_from_body(str(card.get("body") or "")),
        "path": card.get("path") or card.get("source_ref"),
        "source": card.get("source") or "",
        "source_ref": card.get("source_ref"),
    }


def compact_pending(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": record.get("id"),
        "type": record.get("type"),
        "title": record.get("title"),
        "summary": compact_text(str(record.get("summary") or ""), 260),
        "scope": record.get("scope"),
        "confidence": record.get("confidence"),
        "created_at": record.get("created_at"),
        "path": record.get("_path"),
    }


def load_lifecycle() -> Dict[str, set[str]]:
    result = {"forgotten": set(), "superseded": set()}
    base = home_dir() / "lifecycle"
    for kind in ("forgotten", "superseded"):
        directory = base / kind
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            target = str(data.get("target_id") or "")
            if target:
                result[kind].add(target)
    return result


def lifecycle_marker(kind: str, target_id: str, *, new_id: str = "", reason: str = "") -> Dict[str, Any]:
    marker_id = f"{kind}.{slugify(target_id, 'memory')}.{hash_seed(target_id + new_id + reason)}"
    path = home_dir() / "lifecycle" / kind / f"{marker_id}.json"
    return {
        "schema": "xmem.memory_lifecycle.v1",
        "id": marker_id,
        "kind": kind,
        "target_id": target_id,
        "new_id": new_id,
        "reason": reason,
        "created_at": utc_now(),
        "path": str(path),
    }


def write_lifecycle_marker(marker: Dict[str, Any]) -> None:
    path = Path(marker["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_profile_markdown(
    title: str,
    *,
    static_cards: List[Dict[str, Any]],
    dynamic_cards: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> str:
    lines = [f"# {title}", "", f"generated_at: {utc_now()}", "", "## Static", ""]
    lines.extend(markdown_card_lines(static_cards[:16]))
    lines.extend(["", "## Dynamic", ""])
    lines.extend(markdown_card_lines(dynamic_cards[:16]))
    lines.extend(["", "## Recent Events", ""])
    if not events:
        lines.append("- none")
    for event in events[:10]:
        lines.append(f"- {event.get('ts')} {event.get('event')} {event.get('card_id') or ''}".strip())
    lines.append("")
    return "\n".join(lines)


def markdown_card_lines(cards: List[Dict[str, Any]]) -> List[str]:
    if not cards:
        return ["- none"]
    lines = []
    for card in cards:
        title = card.get("title") or card.get("card_id")
        status = card.get("status") or "unknown"
        lines.append(f"- `{card.get('card_id')}` [{status}] {title}")
    return lines


def profile_refs(root: Path | None, project: Dict[str, Any] | None) -> Dict[str, str]:
    refs = {"user": str(home_dir() / "profile" / "user.md")}
    if project:
        refs["project"] = str(home_dir() / "profile" / "projects" / f"{slugify(str(project.get('project_id')), 'project')}.md")
    return refs


def recall_scope(root: Path | None, project: Dict[str, Any] | None) -> Dict[str, str]:
    return {
        "cwd": str(root or ""),
        "project_id": str(project.get("project_id") if project else ""),
        "project_name": str(project.get("name") if project else ""),
    }


def normalize_memory_type(value: str) -> str:
    value = (value or "").strip().lower().replace("-", "_")
    if not value:
        return ""
    if value not in MEMORY_TYPES:
        raise SystemExit(f"unsupported memory type: {value}")
    return value


def infer_memory_type(text: str) -> str:
    low = text.lower()
    if any(token in low for token in ("不要", "别用", "禁用", "do not", "don't", "avoid", "rejected")):
        return "rejected_option"
    if any(token in low for token in ("prefer", "偏好", "喜欢", "希望")):
        return "preference"
    if any(token in low for token in ("bug", "失败", "报错", "regression")):
        return "bug_pattern"
    if any(token in low for token in ("must", "必须", "invariant", "guard")):
        return "invariant"
    if any(token in low for token in ("decide", "decision", "决定")):
        return "decision"
    return "memory"


def title_from_summary(text: str) -> str:
    first = re.sub(r"\s+", " ", text.strip()).strip("-* ")
    first = first.split(". ")[0].split("。")[0]
    return compact_text(first, 90) or "memory"


def summary_from_body(body: str) -> str:
    for key in ("summary:", "title:"):
        match = re.search(rf"^{key}\s*(.+)$", body, re.M)
        if match:
            return compact_text(match.group(1).strip(), 260)
    return compact_text(body, 260)


def compact_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def redact_text(text: str) -> str:
    out = text
    for pattern in REDACTION_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def hash_seed(seed: str) -> str:
    import hashlib

    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def fts_match_query(query: str) -> str:
    terms = [term for term in query_terms(query) if len(term) >= 2][:8]
    quoted = []
    for term in terms:
        safe = term.replace('"', '""')
        if safe:
            quoted.append(f'"{safe}"')
    return " OR ".join(quoted)


def recency_boost(value: str) -> float:
    if not value:
        return 0.0
    try:
        updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    days = (datetime.now(timezone.utc) - updated).days
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.5
    return 0.0


def estimate_tokens(items: Iterable[Dict[str, Any]]) -> int:
    total = 0
    for item in items:
        total += len(json.dumps(item, ensure_ascii=False)) // 4
    return total


def card_semantic_text(card: Dict[str, Any]) -> str:
    aliases = ""
    try:
        aliases = " ".join(json.loads(card.get("aliases_json") or "[]"))
    except Exception:
        aliases = str(card.get("aliases_json") or "")
    return "\n".join(
        str(card.get(key) or "")
        for key in ("card_id", "type", "title", "body", "project_id", "source_ref", "path")
    ) + "\n" + aliases


def semantic_fingerprint(text: str) -> set[str]:
    tokens = query_terms(text)
    grams: set[str] = set(tokens)
    normalized = re.sub(r"\s+", "", text.lower())
    for size in (3, 4):
        if len(normalized) >= size:
            grams.update(normalized[index : index + size] for index in range(0, min(len(normalized) - size + 1, 120)))
    for token in tokens:
        if len(token) > 4:
            grams.add(token[:4])
            grams.add(token[-4:])
    return grams


def semantic_similarity(query_fingerprint: set[str], text: str) -> float:
    if not query_fingerprint:
        return 0.0
    other = semantic_fingerprint(text)
    if not other:
        return 0.0
    overlap = len(query_fingerprint & other)
    if not overlap:
        return 0.0
    return overlap / max(8, min(len(query_fingerprint), len(other)))


def ttl_decay_penalty(card: Dict[str, Any]) -> float:
    ttl = extract_ttl(card)
    if not ttl:
        return 0.0
    low = ttl.lower()
    if low in {"durable", "permanent", "never", "none"}:
        return 0.0
    updated = card_updated_at(card)
    if updated is None:
        return 0.0
    days = (datetime.now(timezone.utc) - updated).days
    ttl_days = ttl_to_days(low)
    if ttl_days <= 0 or days <= ttl_days:
        return 0.0
    overdue = days - ttl_days
    return min(3.5, 0.8 + overdue / max(ttl_days, 1))


def extract_ttl(card: Dict[str, Any]) -> str:
    body = str(card.get("body") or "")
    match = re.search(r"^ttl:\s*(.+)$", body, re.M)
    if match:
        return match.group(1).strip().strip("'\"")
    return str(card.get("ttl") or "")


def card_updated_at(card: Dict[str, Any]) -> datetime | None:
    body = str(card.get("body") or "")
    for key in ("last_checked_at", "created_at", "updated_at"):
        match = re.search(rf"^\s*{key}:\s*(.+)$", body, re.M)
        value = match.group(1).strip() if match else str(card.get(key) or "")
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def ttl_to_days(value: str) -> int:
    match = re.match(r"^(\d+)\s*(d|day|days|w|week|weeks|m|month|months|y|year|years)?$", value.strip())
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2) or "d"
    if unit.startswith("w"):
        return amount * 7
    if unit.startswith("m"):
        return amount * 30
    if unit.startswith("y"):
        return amount * 365
    return amount


def split_list(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[,;\n]+", value)
    return [part.strip() for part in parts if part.strip()]


def parse_confidence(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
