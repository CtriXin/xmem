"""Temporary SQLite task rows verify scope selection, never action permission."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from xmem.agent_hooks import build_agent_hook_result
from xmem.memory_scope import memory_scope

ROOT = Path(__file__).resolve().parents[1]
TASK = "1234567890abcdef"


@pytest.fixture
def task(tmp_path, monkeypatch):
    home = tmp_path / "stride-home"
    workspace = home / "tasks" / TASK / "workspace"
    (workspace / "src").mkdir(parents=True)
    monkeypatch.setenv("STRIDE_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XMEM_HOME", str(tmp_path / "xmem"))
    monkeypatch.setenv("XMEM_REGISTRY_PATH", str(tmp_path / "xmem" / "registry.sqlite"))
    monkeypatch.delenv("STRIDE_EXECUTION_CONTEXT", raising=False)
    for key in ("MMS_HOST_HOME", "HOST_HOME", "REAL_HOME", "MMS_REAL_HOME"):
        monkeypatch.delenv(key, raising=False)
    db = sqlite3.connect(home / "stride.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT, title TEXT)")
    db.execute("INSERT INTO tasks VALUES(?,?,?)", (TASK, "running", "Title grants no authority"))
    db.commit()
    yield home, workspace, db
    db.close()


def application_snapshot(home, db):
    # SHM reader marks are SQLite coordination, explicitly outside this claim.
    return (db.execute("SELECT * FROM tasks").fetchall(),
            {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in home.glob("stride.db*") if not p.name.endswith("-shm")})


def test_wal_current_task_cwd_and_subdirectory_are_memory_only(task):
    home, workspace, db = task
    before = application_snapshot(home, db)
    for cwd in (workspace, workspace / "src"):
        result = memory_scope(cwd)
        assert result["context"] == "stride-v1" and result["basis"] == "task_workspace"
        assert result["task_id"] == TASK and result["workspace"] == str(workspace)
        assert result["authority"] == "none" and "can_proceed" not in result
    assert application_snapshot(home, db) == before


def test_path_shape_title_and_symlink_are_insufficient(task, tmp_path):
    home, workspace, db = task
    unknown = home / "tasks" / "fedcba0987654321" / "workspace"
    unknown.mkdir(parents=True)
    foreign = tmp_path / "arbitrary/tasks" / TASK / "workspace"
    foreign.mkdir(parents=True)
    (workspace / "outside").symlink_to(tmp_path)
    for cwd in (unknown, foreign, workspace.parent, home, workspace / "outside", tmp_path / "missing"):
        assert memory_scope(cwd)["context"] == "legacy"
    db.execute("DELETE FROM tasks")
    db.execute("INSERT INTO tasks VALUES(?,?,?)", ("other", "running", str(workspace)))
    db.commit()
    assert memory_scope(workspace)["context"] == "legacy"


@pytest.mark.parametrize("name", ["stride", "stride-v1", "stride-v3"])
def test_default_and_legacy_homes_require_real_task(tmp_path, monkeypatch, name):
    home = tmp_path / ".local/share" / name
    workspace = home / "tasks" / TASK / "workspace"
    workspace.mkdir(parents=True)
    with sqlite3.connect(home / "stride.db") as db:
        db.execute("CREATE TABLE tasks(id TEXT,status TEXT)")
        db.execute("INSERT INTO tasks VALUES(?,?)", (TASK, "paused"))
    result = memory_scope(workspace, environ={"HOME": str(tmp_path)})
    assert result["context"] == "stride-v1" and result["task_status"] == "paused"


def test_home_alias_resolves_same_database(task, tmp_path, monkeypatch):
    home, workspace, _ = task
    alias = tmp_path / "alias"
    alias.symlink_to(home)
    monkeypatch.setenv("STRIDE_HOME", str(alias))
    result = memory_scope(alias / "tasks" / TASK / "workspace/src")
    assert result["home"] == str(home) and result["context"] == "stride-v1"


def test_missing_and_invalid_db_are_not_created_or_migrated(tmp_path):
    workspace = tmp_path / "tasks" / TASK / "workspace"
    workspace.mkdir(parents=True)
    env = {"STRIDE_HOME": str(tmp_path)}
    assert memory_scope(workspace, env)["context"] == "legacy"
    database = tmp_path / "stride.db"
    assert not database.exists()
    database.write_text("not SQLite")
    before = database.read_bytes()
    assert memory_scope(workspace, env)["context"] == "legacy"
    assert database.read_bytes() == before


def test_explicit_marker_is_only_memory_selection(tmp_path):
    result = memory_scope(tmp_path, {"STRIDE_EXECUTION_CONTEXT": "stride-v1"})
    assert result["context"] == "stride-v1" and result["basis"] == "explicit_context"
    assert result["authority"] == "none" and "task_id" not in result and "can_proceed" not in result
    assert memory_scope(tmp_path, {"STRIDE_EXECUTION_CONTEXT": "stride-v1-other"})["context"] == "legacy"


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "PostCompact"])
def test_native_hook_cwd_skips_all_legacy_memory_consumers(task, monkeypatch, tmp_path, event):
    _, workspace, _ = task
    def forbidden(*args, **kwargs):
        raise AssertionError("Stride scope must not consume legacy recall/capture/profile")
    for name in ("build_recall", "capture_memories", "synthesize_profile"):
        monkeypatch.setattr("xmem.agent_hooks." + name, forbidden)
    result = build_agent_hook_result(event, host="codex", cwd=tmp_path, limit=6, capture=True,
                                    stdin_text=json.dumps({"cwd": str(workspace), "prompt": "remember failed fix"}))
    assert result["action"] == "skip" and result["memory_scope"]["basis"] == "task_workspace"
    assert not (tmp_path / "xmem").exists()


def test_nested_tool_cwd_is_not_scope_authority(task, monkeypatch, tmp_path):
    _, workspace, _ = task
    calls = []
    monkeypatch.setattr("xmem.agent_hooks.synthesize_profile", lambda **kwargs: calls.append(kwargs) or {})
    result = build_agent_hook_result("Stop", host="codex", cwd=tmp_path, limit=6, capture=False,
                                    stdin_text=json.dumps({"cwd": str(tmp_path), "tool_input": {"cwd": str(workspace)}}))
    assert "memory_scope" not in result and calls  # Legacy behavior still selected.


def test_cli_scope_and_gateway_native_cwd_do_not_touch_xmem(task, tmp_path):
    home, workspace, db = task
    before = application_snapshot(home, db)
    for args in (["memory-scope", "--cwd", str(workspace), "--json"],
                 ["gateway", "deploy demo", "--cwd", str(workspace), "--json"]):
        proc = subprocess.run([str(ROOT / "bin/xmem"), *args], cwd=tmp_path,
                              capture_output=True, text=True, env=dict(os.environ))
        assert proc.returncode == 0, proc.stderr
        packet = json.loads(proc.stdout)
        assert packet.get("context") == "stride-v1" or packet.get("decision") == "skip"
    assert application_snapshot(home, db) == before
    assert not (tmp_path / "xmem").exists()
