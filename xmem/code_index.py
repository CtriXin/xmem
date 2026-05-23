from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .project import detect_project
from .store import connect, log_event, rows, upsert_card, upsert_evidence, upsert_project
from .util import read_json, slugify, utc_now

MAX_HOTSPOTS_PER_PROVIDER = 12
MAX_SYMBOL_ALIASES_PER_HOTSPOT = 12
CODE_INDEX_SOURCE = "code-index-bridge"


def code_index_status(roots: Iterable[Path] | None = None) -> Dict[str, Any]:
    roots_list = [root.expanduser() for root in roots or []]
    found = [index for root in roots_list for index in detect_code_indexes(root)]
    providers: Dict[str, int] = {}
    for item in found:
        providers[item["provider"]] = providers.get(item["provider"], 0) + 1
    return {
        "codegraph_binary": shutil.which("codegraph") or "",
        "roots_checked": len(roots_list),
        "indexes": len(found),
        "providers": providers,
    }


def import_code_indexes(roots: Iterable[Path]) -> Dict[str, Any]:
    """Import compact generated code-index refs from map/codegraph DBs.

    This intentionally stores only availability, counts, top files, and symbol
    names. Code and the generated DB files remain the source truth.
    """
    result: Dict[str, Any] = {"roots": 0, "indexes": 0, "cards": 0, "evidence": 0, "errors": [], "sources": []}
    seen_roots: set[str] = set()
    with connect() as conn:
        for root in roots:
            base = root.expanduser()
            key = str(base)
            if key in seen_roots or not base.exists():
                continue
            seen_roots.add(key)
            indexes = detect_code_indexes(base)
            if not indexes:
                continue
            project = safe_detect_project(base)
            project_id = project.get("project_id") or fallback_project_id(base)
            if not rows(conn, "SELECT project_id FROM projects WHERE project_id = ?", (project_id,)):
                upsert_project(conn, {**project, "project_id": project_id, "source": CODE_INDEX_SOURCE})
            result["roots"] += 1
            root_cards = 0
            root_evidence = 0
            has_map = any(item["provider"] == "map" for item in indexes)
            for index in indexes:
                provider = index["provider"]
                role = "primary" if provider == "map" or not has_map else "optional"
                try:
                    summary = read_provider_summary(base, provider, index["db_path"], role)
                except Exception as exc:  # optional generated indexes must not break xmem sync
                    result["errors"].append({"root": str(base), "provider": provider, "error": str(exc)})
                    continue
                index_card = code_index_card(project_id, project, summary)
                upsert_card(conn, index_card)
                root_cards += 1
                ev = index_evidence(project_id, index_card, summary)
                upsert_evidence(conn, ev)
                root_evidence += 1
                for hotspot in summary.get("hotspots", [])[:MAX_HOTSPOTS_PER_PROVIDER]:
                    card = code_hotspot_card(project_id, project, summary, hotspot)
                    upsert_card(conn, card)
                    root_cards += 1
                    upsert_evidence(conn, hotspot_evidence(project_id, card, summary, hotspot))
                    root_evidence += 1
                result["indexes"] += 1
                result["sources"].append({
                    "root": str(base),
                    "provider": provider,
                    "role": role,
                    "db_path": str(index["db_path"]),
                    "hotspots": len(summary.get("hotspots") or []),
                })
            result["cards"] += root_cards
            result["evidence"] += root_evidence
        log_event(conn, "import.code-index", payload={k: v for k, v in result.items() if k != "sources"})
        conn.commit()
    return result


def detect_code_indexes(root: Path) -> List[Dict[str, Any]]:
    indexes: List[Dict[str, Any]] = []
    map_db = root / ".ai" / "map" / "map.db"
    if map_db.exists():
        indexes.append({"provider": "map", "db_path": map_db})
    codegraph_db = root / ".codegraph" / "codegraph.db"
    if codegraph_db.exists():
        indexes.append({"provider": "codegraph", "db_path": codegraph_db})
    return indexes


def safe_detect_project(root: Path) -> Dict[str, Any]:
    try:
        return detect_project(root)
    except Exception:
        project_id = fallback_project_id(root)
        return {
            "project_id": project_id,
            "name": root.name,
            "root": str(root),
            "remote": "",
            "branch": "",
            "git_sha": "",
            "tech_stack": "unknown",
            "aliases": [root.name, project_id],
            "status": "inferred",
            "updated_at": utc_now(),
            "source": CODE_INDEX_SOURCE,
        }


def fallback_project_id(root: Path) -> str:
    return f"{slugify(root.name, 'repo')}-{hashlib.sha1(str(root).encode()).hexdigest()[:8]}"


def read_provider_summary(root: Path, provider: str, db_path: Path, role: str) -> Dict[str, Any]:
    if provider == "map":
        return read_map_summary(root, db_path, role)
    if provider == "codegraph":
        return read_codegraph_summary(root, db_path, role)
    raise ValueError(f"unsupported code index provider: {provider}")


def read_map_summary(root: Path, db_path: Path, role: str) -> Dict[str, Any]:
    manifest_path = root / ".ai" / "map" / "manifest.json"
    manifest = read_json(manifest_path, {}) if manifest_path.exists() else {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        require_tables(conn, db_path, ["definitions"])
        meta = table_key_values(conn, "meta")
        definitions = scalar(conn, "SELECT COUNT(*) FROM definitions")
        languages = [r["language"] for r in conn.execute("SELECT language FROM definitions GROUP BY language ORDER BY COUNT(*) DESC")]
        hotspots = []
        for row in conn.execute(
            """
            SELECT file_path, language, COUNT(*) AS symbol_count
            FROM definitions
            GROUP BY file_path, language
            ORDER BY symbol_count DESC, file_path ASC
            LIMIT ?
            """,
            (MAX_HOTSPOTS_PER_PROVIDER,),
        ):
            hotspots.append({
                "file_path": row["file_path"],
                "language": row["language"],
                "symbol_count": int(row["symbol_count"] or 0),
                "symbols": map_symbols(conn, row["file_path"]),
            })
    indexed_at = str(meta.get("indexed_at") or manifest.get("indexedAt") or manifest.get("indexed_at") or utc_now())
    return {
        "provider": "map",
        "role": role,
        "root": str(root),
        "db_path": str(db_path),
        "manifest_path": str(manifest_path) if manifest_path.exists() else "",
        "status": "partial",
        "confidence": 0.62 if role == "primary" else 0.5,
        "indexed_at": indexed_at,
        "counts": {"definitions": int(definitions or 0), "files": len(hotspots)},
        "languages": languages,
        "meta": meta,
        "hotspots": hotspots,
    }


def read_codegraph_summary(root: Path, db_path: Path, role: str) -> Dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        require_tables(conn, db_path, ["nodes", "files"])
        files = scalar(conn, "SELECT COUNT(*) FROM files")
        nodes = scalar(conn, "SELECT COUNT(*) FROM nodes")
        edges = scalar(conn, "SELECT COUNT(*) FROM edges") if table_exists(conn, "edges") else 0
        languages = [r["language"] for r in conn.execute("SELECT language FROM files GROUP BY language ORDER BY COUNT(*) DESC")]
        latest_indexed = scalar(conn, "SELECT MAX(indexed_at) FROM files") or 0
        hotspots = []
        for row in conn.execute(
            """
            SELECT file_path, language, COUNT(*) AS symbol_count
            FROM nodes
            WHERE kind NOT IN ('file', 'import')
            GROUP BY file_path, language
            ORDER BY symbol_count DESC, file_path ASC
            LIMIT ?
            """,
            (MAX_HOTSPOTS_PER_PROVIDER,),
        ):
            hotspots.append({
                "file_path": row["file_path"],
                "language": row["language"],
                "symbol_count": int(row["symbol_count"] or 0),
                "symbols": codegraph_symbols(conn, row["file_path"]),
            })
    return {
        "provider": "codegraph",
        "role": role,
        "root": str(root),
        "db_path": str(db_path),
        "manifest_path": "",
        "status": "partial",
        "confidence": 0.58 if role == "primary" else 0.48,
        "indexed_at": str(latest_indexed or utc_now()),
        "counts": {"files": int(files or 0), "nodes": int(nodes or 0), "edges": int(edges or 0)},
        "languages": languages,
        "meta": {"codegraph_binary": shutil.which("codegraph") or ""},
        "hotspots": hotspots,
    }


def require_tables(conn: sqlite3.Connection, db_path: Path, names: Iterable[str]) -> None:
    missing = [name for name in names if not table_exists(conn, name)]
    if missing:
        raise ValueError(f"{db_path} missing tables: {', '.join(missing)}")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (name,)).fetchone()
    return bool(row)


def table_key_values(conn: sqlite3.Connection, name: str) -> Dict[str, str]:
    if not table_exists(conn, name):
        return {}
    out: Dict[str, str] = {}
    for row in conn.execute(f"SELECT key, value FROM {name}"):
        out[str(row["key"])] = str(row["value"])
    return out


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def map_symbols(conn: sqlite3.Connection, file_path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT symbol, kind, line
        FROM definitions
        WHERE file_path = ?
        ORDER BY line ASC, symbol ASC
        LIMIT ?
        """,
        (file_path, MAX_SYMBOL_ALIASES_PER_HOTSPOT),
    ):
        out.append({"name": row["symbol"], "kind": row["kind"], "line": int(row["line"] or 0)})
    return out


def codegraph_symbols(conn: sqlite3.Connection, file_path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT name, kind, start_line
        FROM nodes
        WHERE file_path = ? AND kind NOT IN ('file', 'import')
        ORDER BY start_line ASC, name ASC
        LIMIT ?
        """,
        (file_path, MAX_SYMBOL_ALIASES_PER_HOTSPOT),
    ):
        out.append({"name": row["name"], "kind": row["kind"], "line": int(row["start_line"] or 0)})
    return out


def code_index_card(project_id: str, project: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    provider = summary["provider"]
    root = Path(summary["root"])
    aliases = code_index_aliases(project, summary)
    return {
        "card_id": f"code-index.{project_id}.{provider}",
        "project_id": project_id,
        "type": "code.index",
        "title": f"Code index: {project.get('name') or root.name} ({provider}, {summary.get('role')})",
        "path": summary["db_path"],
        "status": summary.get("status", "partial"),
        "confidence": float(summary.get("confidence") or 0.5),
        "aliases": aliases,
        "body": json.dumps({k: v for k, v in summary.items() if k != "hotspots"}, ensure_ascii=False, sort_keys=True),
        "updated_at": utc_now(),
        "source": CODE_INDEX_SOURCE,
        "source_ref": summary["db_path"],
    }


def code_hotspot_card(project_id: str, project: Dict[str, Any], summary: Dict[str, Any], hotspot: Dict[str, Any]) -> Dict[str, Any]:
    provider = summary["provider"]
    rel = str(hotspot.get("file_path") or "")
    digest = hashlib.sha1(f"{summary['root']}:{provider}:{rel}".encode()).hexdigest()[:10]
    aliases = code_hotspot_aliases(project, summary, hotspot)
    db_path = summary["db_path"]
    return {
        "card_id": f"code-hotspot.{project_id}.{provider}.{digest}",
        "project_id": project_id,
        "type": "code.hotspot",
        "title": f"Hotspot: {rel} ({provider})",
        "path": str(Path(summary["root"]) / rel),
        "status": summary.get("status", "partial"),
        "confidence": float(summary.get("confidence") or 0.5),
        "aliases": aliases,
        "body": json.dumps({
            "provider": provider,
            "role": summary.get("role"),
            "root": summary["root"],
            "db_path": db_path,
            "file_path": rel,
            "language": hotspot.get("language"),
            "symbol_count": hotspot.get("symbol_count"),
            "symbols": hotspot.get("symbols") or [],
            "truth_policy": "generated code-index ref only; source files remain truth",
        }, ensure_ascii=False, sort_keys=True),
        "updated_at": utc_now(),
        "source": CODE_INDEX_SOURCE,
        "source_ref": f"{db_path}#{rel}",
    }


def code_index_aliases(project: Dict[str, Any], summary: Dict[str, Any]) -> List[str]:
    root = Path(summary["root"])
    aliases = [
        root.name,
        project.get("name", ""),
        project.get("project_id", ""),
        f"{root.name} code index",
        f"{summary['provider']} code index",
        f"{root.name} {summary['provider']}",
        *summary.get("languages", []),
    ]
    for key in ("project_type", "project_path"):
        if summary.get("meta", {}).get(key):
            aliases.append(str(summary["meta"][key]))
    return unique_nonempty(aliases)[:80]


def code_hotspot_aliases(project: Dict[str, Any], summary: Dict[str, Any], hotspot: Dict[str, Any]) -> List[str]:
    rel = str(hotspot.get("file_path") or "")
    symbols = [str(item.get("name") or "") for item in hotspot.get("symbols") or []]
    root = Path(summary["root"])
    aliases = [
        root.name,
        project.get("name", ""),
        project.get("project_id", ""),
        rel,
        Path(rel).name,
        str(hotspot.get("language") or ""),
        *symbols,
    ]
    return unique_nonempty(aliases)[:100]


def index_evidence(project_id: str, card: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    provider = summary["provider"]
    return {
        "evidence_id": f"code-index.{project_id}.{provider}.{hashlib.sha1(summary['db_path'].encode()).hexdigest()[:10]}",
        "card_id": card["card_id"],
        "project_id": project_id,
        "kind": "code-index",
        "ref": provider,
        "path": summary["db_path"],
        "title": card["title"],
        "status": card["status"],
        "body": json.dumps({"counts": summary.get("counts"), "languages": summary.get("languages"), "role": summary.get("role")}, ensure_ascii=False, sort_keys=True),
        "updated_at": utc_now(),
        "source": CODE_INDEX_SOURCE,
    }


def hotspot_evidence(project_id: str, card: Dict[str, Any], summary: Dict[str, Any], hotspot: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(hotspot.get("file_path") or "")
    provider = summary["provider"]
    key = f"{summary['root']}:{provider}:{rel}"
    return {
        "evidence_id": f"code-hotspot.{project_id}.{provider}.{hashlib.sha1(key.encode()).hexdigest()[:10]}",
        "card_id": card["card_id"],
        "project_id": project_id,
        "kind": "code-hotspot",
        "ref": rel,
        "path": str(Path(summary["root"]) / rel),
        "title": card["title"],
        "status": card["status"],
        "body": json.dumps({"symbols": hotspot.get("symbols") or [], "db_path": summary.get("db_path")}, ensure_ascii=False, sort_keys=True),
        "updated_at": utc_now(),
        "source": CODE_INDEX_SOURCE,
    }


def unique_nonempty(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out
