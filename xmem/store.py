from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .util import append_jsonl, home_dir, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY,
  name TEXT,
  root TEXT,
  remote TEXT,
  branch TEXT,
  tech_stack TEXT,
  aliases_json TEXT,
  status TEXT,
  updated_at TEXT,
  source TEXT
);
CREATE TABLE IF NOT EXISTS cards (
  card_id TEXT PRIMARY KEY,
  project_id TEXT,
  type TEXT,
  title TEXT,
  path TEXT,
  status TEXT,
  confidence REAL,
  aliases_json TEXT,
  body TEXT,
  updated_at TEXT,
  source TEXT,
  source_ref TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  card_id TEXT,
  project_id TEXT,
  kind TEXT,
  ref TEXT,
  path TEXT,
  title TEXT,
  status TEXT,
  body TEXT,
  updated_at TEXT,
  source TEXT
);
CREATE TABLE IF NOT EXISTS aliases (
  alias TEXT PRIMARY KEY,
  entity_kind TEXT,
  entity_id TEXT,
  score REAL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  actor TEXT,
  event TEXT,
  project_id TEXT,
  card_id TEXT,
  payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_cards_project ON cards(project_id);
CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(type);
CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_alias_entity ON aliases(entity_kind, entity_id);
"""


def db_path() -> Path:
    explicit = os.environ.get("XMEM_REGISTRY_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return home_dir() / "registry.sqlite"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_project(conn: sqlite3.Connection, project: Dict[str, Any]) -> None:
    aliases = project.get("aliases") or []
    conn.execute(
        """INSERT INTO projects(project_id,name,root,remote,branch,tech_stack,aliases_json,status,updated_at,source)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id) DO UPDATE SET
             name=excluded.name, root=excluded.root, remote=excluded.remote, branch=excluded.branch,
             tech_stack=excluded.tech_stack, aliases_json=excluded.aliases_json, status=excluded.status,
             updated_at=excluded.updated_at, source=excluded.source""",
        (
            project["project_id"], project.get("name", ""), project.get("root", ""), project.get("remote", ""),
            project.get("branch", ""), project.get("tech_stack", ""), json.dumps(aliases, ensure_ascii=False),
            project.get("status", "verified"), project.get("updated_at", utc_now()), project.get("source", "local"),
        ),
    )
    for alias in aliases + [project.get("name", ""), project["project_id"]]:
        upsert_alias(conn, alias, "project", project["project_id"], 1.0)


def upsert_card(conn: sqlite3.Connection, card: Dict[str, Any]) -> None:
    aliases = card.get("aliases") or []
    conn.execute(
        """INSERT INTO cards(card_id,project_id,type,title,path,status,confidence,aliases_json,body,updated_at,source,source_ref)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(card_id) DO UPDATE SET
             project_id=excluded.project_id, type=excluded.type, title=excluded.title, path=excluded.path,
             status=excluded.status, confidence=excluded.confidence, aliases_json=excluded.aliases_json,
             body=excluded.body, updated_at=excluded.updated_at, source=excluded.source, source_ref=excluded.source_ref""",
        (
            card["card_id"], card.get("project_id", ""), card.get("type", "fact"), card.get("title", ""),
            card.get("path", ""), card.get("status", "unknown"), float(card.get("confidence", 0.0)),
            json.dumps(aliases, ensure_ascii=False), card.get("body", ""), card.get("updated_at", utc_now()),
            card.get("source", "local"), card.get("source_ref", ""),
        ),
    )
    for alias in aliases + [card.get("title", ""), card["card_id"]]:
        upsert_alias(conn, alias, "card", card["card_id"], float(card.get("confidence", 0.5)))


def upsert_evidence(conn: sqlite3.Connection, evidence: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO evidence(evidence_id,card_id,project_id,kind,ref,path,title,status,body,updated_at,source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(evidence_id) DO UPDATE SET
             card_id=excluded.card_id, project_id=excluded.project_id, kind=excluded.kind, ref=excluded.ref,
             path=excluded.path, title=excluded.title, status=excluded.status, body=excluded.body,
             updated_at=excluded.updated_at, source=excluded.source""",
        (
            evidence["evidence_id"], evidence.get("card_id", ""), evidence.get("project_id", ""),
            evidence.get("kind", "note"), evidence.get("ref", ""), evidence.get("path", ""),
            evidence.get("title", ""), evidence.get("status", "unknown"), evidence.get("body", ""),
            evidence.get("updated_at", utc_now()), evidence.get("source", "local"),
        ),
    )


def upsert_alias(conn: sqlite3.Connection, alias: str, kind: str, entity_id: str, score: float) -> None:
    alias = (alias or "").strip()
    if not alias:
        return
    conn.execute(
        """INSERT INTO aliases(alias,entity_kind,entity_id,score,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(alias) DO UPDATE SET
             entity_kind=excluded.entity_kind, entity_id=excluded.entity_id, score=MAX(score, excluded.score), updated_at=excluded.updated_at""",
        (alias.lower(), kind, entity_id, score, utc_now()),
    )


def log_event(conn: sqlite3.Connection, event: str, actor: str = "xmem", project_id: str = "", card_id: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
    ts = utc_now()
    row = {
        "ts": ts,
        "actor": actor,
        "event": event,
        "project_id": project_id,
        "card_id": card_id,
        "payload": payload or {},
    }
    conn.execute(
        "INSERT INTO events(ts,actor,event,project_id,card_id,payload_json) VALUES(?,?,?,?,?,?)",
        (ts, actor, event, project_id, card_id, json.dumps(payload or {}, ensure_ascii=False)),
    )
    append_jsonl(home_dir() / "events.jsonl", row)


def rows(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(args)).fetchall()]
