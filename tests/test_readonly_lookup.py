"""All registry/source data here is temporary; no installed history is accessed."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from xmem import agent_hooks, gateway
from xmem.gain import summarize_gain, summarize_card_gain
from xmem.importers import import_project_wiki, import_bug_patterns, pending_inbox_to_export_card
from xmem.readonly import lookup
from xmem.store import connect, upsert_card

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XMEM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XMEM_REGISTRY_PATH", str(tmp_path / "registry.sqlite"))
    monkeypatch.delenv("STRIDE_EXECUTION_CONTEXT", raising=False)
    return tmp_path


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "directory")
            for p in root.rglob("*")}


def card(root, card_id="index.demo", source="project-wiki", status="verified", body="declared target"):
    path = root / (source + ".json")
    path.write_text(body)
    return dict(card_id=card_id, source=source, source_ref="service:demo", path=str(path),
                title="Demo service", status=status, body=body, confidence=.9, aliases=["demo"])


def cli(root, *args, input="", env=None):
    result = subprocess.run([str(ROOT / "bin/xmem"), *args], cwd=root,
                            env=env or dict(os.environ), text=True, input=input, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_lookup_absent_registry_creates_nothing(isolated):
    before = snapshot(isolated)
    packet = cli(isolated, "lookup", "demo", "--json")
    assert packet["status"] == "unavailable"
    assert packet["readonly"] and packet["historical_only"]
    assert snapshot(isolated) == before


def test_lookup_old_schema_conflict_without_migration(isolated):
    registry = isolated / "registry.sqlite"
    source = isolated / "source.md"
    source.write_text("historical fixture")
    with sqlite3.connect(registry) as db:
        db.execute("CREATE TABLE cards(card_id TEXT,source TEXT,source_ref TEXT,path TEXT,title TEXT,status TEXT,body TEXT,updated_at TEXT)")
        for card_id, producer, state in [("old.index", "project-wiki", "verified"),
                                          ("old.export", "project-wiki-export", "partial")]:
            db.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?)",
                       (card_id, producer, "service:demo", str(source), "Demo", state, "different claim: " + state, "2099-01-01"))
    before = snapshot(isolated)
    result = cli(isolated, "lookup", "service:demo", "--json")["results"][0]
    assert result["state"] == "conflict" and result["effective_truth"] == "disputed"
    assert result["freshness"] == "unknown"
    assert {v["card_id"] for v in result["variants"]} == {"old.index", "old.export"}
    assert all(v["source_checked_at"] is None for v in result["variants"])
    assert snapshot(isolated) == before
    with sqlite3.connect(registry) as db:
        assert len(db.execute("PRAGMA table_info(cards)").fetchall()) == 8


@pytest.mark.parametrize("sources", [("project-wiki", "project-wiki-export"),
                                    ("issue-tracking", "issue-tracking-export")])
def test_index_export_merge_retains_conflicting_producers(isolated, sources):
    first = card(isolated, source=sources[0])
    second = card(isolated, "export.demo", sources[1], "partial", "a conflicting declaration")
    with connect() as db:
        upsert_card(db, first)
        upsert_card(db, second)
        upsert_card(db, first)
        upsert_card(db, second)
        assert db.execute("SELECT count(*) FROM cards").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM card_provenance").fetchone()[0] == 2
        merged = dict(db.execute("SELECT * FROM cards").fetchone())
        assert merged["status"] == "disputed"
        assert merged["source_checked_at"] is None
        assert len(json.loads(merged["body"])["variants"]) == 2
    before = snapshot(isolated)
    group = lookup("service:demo")["results"][0]
    assert len(group["variants"]) == 2
    assert {v["source"] for v in group["variants"]} == set(sources)
    assert {v["card_id"] for v in group["variants"]} == {"index.demo", "export.demo"}
    assert {v["path"] for v in group["variants"]} == {first["path"], second["path"]}
    assert group["conflict"] and group["freshness"] == "current"
    assert snapshot(isolated) == before


def test_reindex_time_never_refreshes_source_verification(isolated, monkeypatch):
    item = card(isolated, body='{"truth":{"last_checked_at":"2020-01-02"}}')
    with connect() as db:
        monkeypatch.setattr("xmem.provenance.utc_now", lambda: "2026-09-07T01:00:00Z")
        upsert_card(db, item)
        old = dict(db.execute("SELECT * FROM cards").fetchone())
        monkeypatch.setattr("xmem.provenance.utc_now", lambda: "2026-09-08T01:00:00Z")
        upsert_card(db, item)
        new = dict(db.execute("SELECT * FROM cards").fetchone())
    assert new["indexed_at"] != old["indexed_at"]
    assert new["source_checked_at"] == old["source_checked_at"] == "2020-01-02"
    result = lookup("demo")["results"][0]
    assert result["freshness"] == "current" and result["effective_truth"] == "historical"
    Path(item["path"]).write_text("source changed after indexing")
    assert lookup("demo")["results"][0]["state"] == "changed"
    Path(item["path"]).unlink()
    assert lookup("demo")["results"][0]["state"] == "missing"


def test_real_index_export_adapter_and_evidence_use_one_identity(isolated):
    data = isolated / "wiki/data"
    data.mkdir(parents=True)
    (data / "project-hub.index.json").write_text(json.dumps({"entities": [
        {"id": "service:demo", "type": "Service", "name": "Demo", "confidence": 1, "fields": {}}
    ]}))
    (data / "xmem-export.cards.jsonl").write_text(json.dumps({
        "id": "project-wiki.service.demo-export", "source_ref": "service:demo", "type": "wiki.service",
        "title": "Demo", "truth": {"status": "partial"},
        "evidence": [{"kind": "fixture", "path": "source.yaml"}]
    }) + "\n")
    import_project_wiki(isolated / "wiki")
    import_project_wiki(isolated / "wiki")
    with connect() as db:
        rows = db.execute("SELECT * FROM cards").fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "disputed"
        assert {row[0] for row in db.execute("SELECT card_id FROM evidence")} == {rows[0]["card_id"]}
        assert db.execute("SELECT count(*) FROM card_provenance").fetchone()[0] == 2
    group = lookup("service:demo")["results"][0]
    assert group["state"] == "conflict"
    assert {v["card_id"] for v in group["variants"]} == {"project-wiki.service.demo", "project-wiki.service.demo-export"}


def test_bug_pattern_adapter_does_not_invent_checked_at(isolated):
    file = isolated / "bug-patterns.jsonl"
    file.write_text(json.dumps({"id": "pattern.demo", "title": "Demo", "symptom": "fixture failure",
                                "updated_at": "2099-01-01", "status": "verified"}) + "\n")
    import_bug_patterns(file)
    with connect() as db:
        row = db.execute("SELECT * FROM cards").fetchone()
        assert row["source_checked_at"] is None
        assert row["indexed_at"]
    assert lookup("pattern.demo")["results"][0]["effective_truth"] == "historical"
    pending = pending_inbox_to_export_card({"id": "pending.demo", "receivedAt": "2099-01-01", "payload": {}}, file, 1)
    assert pending["truth"]["last_checked_at"] == ""


def test_importing_old_duplicate_rows_retains_history(isolated):
    first = card(isolated, source="project-wiki")
    second = card(isolated, "export.demo", "project-wiki-export", "partial", "legacy conflicting declaration")
    with connect() as db:
        # Simulate pre-migration duplicate rows without provenance.
        for item in (first, second):
            db.execute("INSERT INTO cards(card_id,source,source_ref,path,title,status,body) VALUES(?,?,?,?,?,?,?)",
                       tuple(item[k] for k in ("card_id", "source", "source_ref", "path", "title", "status", "body")))
        upsert_card(db, dict(first))
        assert db.execute("SELECT count(*) FROM cards").fetchone()[0] == 2  # No historical deletion.
    group = lookup("service:demo")["results"][0]
    assert group["state"] == "conflict" and group["effective_truth"] == "disputed"
    assert {v["card_id"] for v in group["variants"]} == {"index.demo", "export.demo"}


def test_wal_database_is_unavailable_without_checkpoint(isolated):
    registry = isolated / "registry.sqlite"
    db = sqlite3.connect(registry)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE cards(card_id TEXT)")
        db.commit()
        assert Path(str(registry) + "-wal").stat().st_size
        before = snapshot(isolated)
        result = lookup("demo")
        assert result["status"] == "unavailable" and "WAL" in result["reason"]
        assert snapshot(isolated) == before
    finally:
        db.close()


@pytest.mark.parametrize("event", ["UserPromptSubmit", "SessionStart", "Stop", "PreCompact", "PostCompact"])
def test_stride_hook_and_gateway_do_not_write_or_inject(isolated, monkeypatch, event):
    monkeypatch.setenv("STRIDE_EXECUTION_CONTEXT", "stride-v1")
    before = snapshot(isolated)
    packet = cli(isolated, "agent-hook", event, "--json", input='{"prompt":"deploy demo previous fix", "transcript_path":"missing.jsonl"}')
    assert packet["action"] == "skip" and packet["readonly"]
    result = cli(isolated, "gateway", "deploy demo previous fix", "--json")
    assert result["decision"] == "skip" and result["packet"] == {}
    assert "can_proceed" not in result
    assert snapshot(isolated) == before


def test_only_exact_stride_context_skips_old_behavior(isolated, monkeypatch):
    calls = []
    def existing(*args, **kwargs):
        calls.append(args)
        return {"existing_behavior": True}
    monkeypatch.setattr(agent_hooks, "build_agent_hook_result", existing)
    for value in [None, "stride", "stride-v1-other"]:
        if value is None:
            monkeypatch.delenv("STRIDE_EXECUTION_CONTEXT", raising=False)
        else:
            monkeypatch.setenv("STRIDE_EXECUTION_CONTEXT", value)
        result = agent_hooks.run_agent_hook("Stop", emit_json=True, stdin_text="{}")
        assert json.loads(result)["existing_behavior"]
    assert len(calls) == 3
    monkeypatch.setenv("STRIDE_EXECUTION_CONTEXT", "stride-v1")
    assert json.loads(agent_hooks.run_agent_hook("Stop", emit_json=True))["action"] == "skip"
    assert len(calls) == 3


def test_dry_run_is_not_consumption_or_savings(isolated):
    gateway.record_gateway_event({"dry_run": True, "decision": "inject"}, [{"card_id": "demo"}])
    assert not (isolated / "home").exists()
    # A previously stored dry_run row is excluded even if it claimed savings.
    home = isolated / "home"
    home.mkdir()
    (home / "gain.jsonl").write_text(json.dumps({"dry_run": True, "event": "gateway.hit", "matches": 99,
                                               "estimated_tokens_saved": 118800, "top_card": "demo"}) + "\n")
    summary = summarize_gain()
    assert summary["rows"] == summary["matches"] == 0
    assert summary["estimated_tokens_saved"] is None
    assert summary["actual_tokens_saved"] is None and summary["benefit_status"] == "unknown"
    assert summarize_card_gain("demo")["rows"] == 0


@pytest.mark.parametrize("query,limit", [("", 8), ("x" * 2001, 8), ("demo", 0), ("demo", True), ("demo", 21)])
def test_lookup_invalid_request_never_creates_registry(isolated, query, limit):
    before = snapshot(isolated)
    with pytest.raises(ValueError):
        lookup(query, limit=limit)
    assert snapshot(isolated) == before
