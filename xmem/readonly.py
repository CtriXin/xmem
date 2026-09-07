"""Historical lookup only: no schema setup, hooks, telemetry or pending memories."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

MAX_SOURCE_BYTES = 4_000_000


def registry_path() -> Path:
    if os.environ.get("XMEM_REGISTRY_PATH"):
        return Path(os.environ["XMEM_REGISTRY_PATH"]).expanduser()
    from .util import home_dir
    return home_dir() / "registry.sqlite"


def stride_context() -> bool:
    # Selects memory behaviour only. It is never authority to perform an action.
    return os.environ.get("STRIDE_EXECUTION_CONTEXT") == "stride-v1"


def source_state(card: dict) -> dict:
    path = Path(card.get("path") or "")
    digest = card.get("source_sha256")
    result = {"path": str(path), "indexed_at": card.get("indexed_at") or None,
              "source_checked_at": card.get("source_checked_at") or None,
              "source_mtime_ns": card.get("source_mtime_ns"), "indexed_sha256": digest,
              "state": "unknown"}
    if not path.is_absolute():
        return result
    try:
        before = path.stat()
        if not path.is_file():
            return result
        if before.st_size > MAX_SOURCE_BYTES:
            result["reason"] = "source exceeds bounded lookup size"
            return result
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
        result["current_sha256"] = actual
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            result["state"] = "changed"
        elif digest:
            result["state"] = "current" if digest == actual else "changed"
        else:
            result["reason"] = "legacy index has no source fingerprint; updated_at is not verification"
    except FileNotFoundError:
        result["state"] = "missing"
    except OSError as error:
        result["reason"] = type(error).__name__
    return result


def identity(card: dict) -> str:
    ref = card.get("source_ref")
    source = card.get("source", "")
    family = ("project-wiki" if source in {"project-wiki", "project-wiki-export"}
              else "issue-tracking" if source in {"issue-tracking", "issue-tracking-export"} else "")
    return family + ":" + ref if family and ref else "card:" + card["card_id"]


def assertion(card: dict) -> tuple:
    return card.get("status"), card.get("body", "")


def lookup(query: str, registry: Path | None = None, limit: int = 8) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("query must contain 1–2000 characters")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit must be 1–20")
    path = (registry or registry_path()).expanduser().absolute()
    packet = {"schema": "xmem.lookup.v1", "readonly": True, "historical_only": True,
              "query": query, "registry": str(path), "status": "available", "results": [],
              "limits": "Source pointers are historical evidence, never runtime truth or action authorization."}
    if not path.is_file():
        return {**packet, "status": "unavailable", "reason": "registry missing"}
    # immutable avoids SQLite's incidental shm/journal writes. It ignores WAL,
    # so refuse an uncheckpointed database instead of returning stale rows.
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        return {**packet, "status": "unavailable", "reason": "uncheckpointed WAL; lookup will not checkpoint or ignore it"}
    try:
        before = path.stat()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            cards = [dict(row) for row in db.execute("SELECT * FROM cards")]
            provenance = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='card_provenance'").fetchone():
                for row in db.execute("SELECT card_id,body FROM card_provenance"):
                    provenance.setdefault(row[0], []).append(json.loads(row[1]))
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size) or (wal.exists() and wal.stat().st_size):
            return {**packet, "status": "unavailable", "reason": "registry changed during lookup"}
    except (OSError, sqlite3.Error, ValueError) as error:
        return {**packet, "status": "unavailable", "reason": type(error).__name__}
    groups = {}
    terms = query.casefold().split()
    for card in cards:
        group = groups.setdefault(identity(card), [])
        group.extend(provenance.get(card["card_id"]) or [card])
    ranked = []
    for key, variants in groups.items():
        # A legacy duplicate may also be bootstrapped into provenance on a later
        # import. Remove only identical claims, never competing assertions.
        variants = list({(v["card_id"], v.get("source"), v.get("path"),
                          *assertion(v)): v for v in variants}.values())
        # Re-indexed provenance replaces the same producer, while legacy rows
        # remain distinct and visible. Different assertions are not adjudicated.
        corpus = "\n".join(str(v.get(k, "")) for v in variants for k in
                           ("card_id", "source_ref", "title", "aliases", "aliases_json", "body", "path")).casefold()
        if not all(term in corpus for term in terms):
            continue
        score = 100 if any(query.casefold() in {str(v.get(k, "")).casefold() for k in ("card_id", "source_ref", "title")} for v in variants) else 1
        states = [source_state(v) for v in variants]
        conflict = len({assertion(v) for v in variants}) > 1
        freshness = "changed" if any(s["state"] == "changed" for s in states) else "missing" if any(s["state"] == "missing" for s in states) else "current" if all(s["state"] == "current" for s in states) else "unknown"
        ranked.append((score, key, {"identity": key, "source_ref": variants[0].get("source_ref"),
            "state": "conflict" if conflict else freshness, "freshness": freshness,
            "conflict": conflict, "effective_truth": "disputed" if conflict else "historical",
            "variants": [{"card_id": v["card_id"], "source": v.get("source"),
                          "title": v.get("title"), "declared_status": v.get("status"),
                          "excerpt": str(v.get("body", ""))[:500], **state}
                         for v, state in zip(variants, states)]}))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    packet["results"] = [item[2] for item in ranked[:limit]]
    packet["matched_identities"] = len(ranked)
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="JSON is the default and only output format")
    args = parser.parse_args()
    try:
        result = lookup(args.query, args.registry, args.limit)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
