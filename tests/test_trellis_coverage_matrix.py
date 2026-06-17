from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
XMEM = ROOT / "bin" / "xmem"


def run(cmd: list[str], cwd: Path, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def init_repo(tmp_path: Path, name: str = "repo") -> tuple[Path, dict[str, str]]:
    env = {
        **os.environ,
        "XMEM_HOME": str(tmp_path / "home"),
        "XMEM_PROJECT_WIKI": str(tmp_path / "project-wiki"),
        "XMEM_ISSUE_TRACKING": str(tmp_path / "issue-tracking"),
    }
    repo = tmp_path / name
    repo.mkdir(parents=True)
    run(["git", "init", "-q"], repo, env)
    run(["git", "config", "user.email", "test@example.com"], repo, env)
    run(["git", "config", "user.name", "test"], repo, env)
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    run(["git", "add", "."], repo, env)
    run(["git", "commit", "-q", "-m", "init"], repo, env)
    run([str(XMEM), "init", "--project-id", name, "--alias", name], repo, env)
    return repo, env


def write_trellis_fixture(repo: Path) -> None:
    for rel in (".trellis/spec", ".trellis/tasks", ".trellis/workspace", ".claude/hooks", ".codex"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / ".trellis/spec/site.md").write_text(
        "# Trellis Site Spec\n\nReusable information architecture pointer.\n",
        encoding="utf-8",
    )
    (repo / ".trellis/tasks/task.md").write_text(
        "# Trellis Task\n\nTask says finish-work can mark done and next_action is ship.\n",
        encoding="utf-8",
    )
    (repo / ".trellis/workspace/journal.md").write_text(
        "# Trellis Journal\n\ndone=true ship=true finish-work next_action=deploy.\n",
        encoding="utf-8",
    )
    (repo / ".trellis/workspace/transcript.md").write_text(
        "# Trellis Transcript\n\n" + "PROMPT TRANSCRIPT RAW LINE SHOULD NOT BE STORED.\n" * 20,
        encoding="utf-8",
    )
    (repo / "AGENTS.md").write_text("Prefer Trellis command over manual steps.\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("Use trellis-finish-work when done.\n", encoding="utf-8")
    (repo / ".claude/hooks/settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": ["trellis"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (repo / ".codex/config.toml").write_text("[hooks]\nfinish = 'trellis-finish-work'\n", encoding="utf-8")
    (repo / "state-core-task-state.json").write_text(
        json.dumps({"phase": "executing", "done": False, "next_action": "state-core-owned"}, sort_keys=True),
        encoding="utf-8",
    )


def db_cards(env: dict[str, str], where: str = "1=1") -> list[sqlite3.Row]:
    conn = sqlite3.connect(Path(env["XMEM_HOME"]) / "registry.sqlite")
    conn.row_factory = sqlite3.Row
    return list(conn.execute(f"SELECT * FROM cards WHERE {where}"))


def warning_has_d3(packet: dict) -> bool:
    return any("Trellis artifact matched" in w and "docs/decisions.md#D3" in w and "docs/capability-registry.md" in w for w in packet.get("warnings", []))


def test_trellis_artifact_and_authority_matrix(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    write_trellis_fixture(repo)
    before_state = (repo / "state-core-task-state.json").read_text(encoding="utf-8")

    result = json.loads(run([str(XMEM), "import", "trellis", str(repo)], repo, env).stdout)
    assert result["cards"] == 4

    rows = {row["source_ref"]: row for row in db_cards(env, "source='trellis'")}
    assert set(rows) == {
        ".trellis/spec/site.md",
        ".trellis/tasks/task.md",
        ".trellis/workspace/journal.md",
        ".trellis/workspace/transcript.md",
    }
    assert rows[".trellis/spec/site.md"]["type"] == "spec.current"
    assert rows[".trellis/spec/site.md"]["status"] == "partial"
    assert rows[".trellis/spec/site.md"]["confidence"] <= 0.65
    assert rows[".trellis/tasks/task.md"]["type"] == "spec.task"
    assert "task_scoped=true" in rows[".trellis/tasks/task.md"]["body"]
    assert "durable_knowledge=false" in rows[".trellis/tasks/task.md"]["body"]
    for ref in (".trellis/workspace/journal.md", ".trellis/workspace/transcript.md"):
        assert rows[ref]["type"] == "memory"
        assert rows[ref]["status"] == "inferred"
        assert rows[ref]["confidence"] <= 0.45
        assert "body_import=summary_or_pointer_only" in rows[ref]["body"]
        assert "PROMPT TRANSCRIPT RAW LINE SHOULD NOT BE STORED" not in rows[ref]["body"]
    assert not any(row["status"] == "verified" for row in rows.values())
    assert (repo / "state-core-task-state.json").read_text(encoding="utf-8") == before_state

    blocked = run([str(XMEM), "promote", rows[".trellis/workspace/journal.md"]["card_id"], "--verified"], repo, env, check=False)
    assert blocked.returncode != 0
    assert "Refusing direct promotion of Trellis artifact card" in (blocked.stdout + blocked.stderr)


def test_trellis_bypass_ingress_paths_are_guarded(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    write_trellis_fixture(repo)
    cards_dir = repo / ".xmem" / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    rogue = cards_dir / "trellis-rogue.yaml"
    rogue.write_text(
        "\n".join(
            [
                "id: trellis.rogue.workspace",
                "type: memory",
                "title: Rogue Trellis workspace card",
                "status: verified",
                "confidence: 0.99",
                "aliases:",
                "  - source_tool=trellis",
                "  - finish-work done ship next_action",
                "summary: source_tool=trellis .trellis/workspace/journal.md RAW ROGUE BODY SHOULD NOT SURVIVE",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    run([str(XMEM), "new", str(repo), "--json"], repo, env)
    indexed = db_cards(env, "card_id='trellis.rogue.workspace'")[0]
    assert indexed["status"] != "verified"
    assert indexed["confidence"] <= 0.55
    assert "promotion_policy=distill_only" in indexed["body"]
    assert "RAW ROGUE BODY SHOULD NOT SURVIVE" not in indexed["body"]

    export = tmp_path / "xmem-export.cards.jsonl"
    export.write_text(
        json.dumps(
            {
                "id": "trellis.exported.task",
                "type": "memory",
                "title": "Exported Trellis task",
                "status": "verified",
                "confidence": 0.99,
                "source_ref": ".trellis/tasks/task.md",
                "aliases": ["source_tool=trellis", "finish-work done ship next_action"],
                "summary": "RAW EXPORTED TASK BODY SHOULD BE GUARDED",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    run([str(XMEM), "import", "export", str(export)], repo, env)
    exported = db_cards(env, "card_id='trellis.exported.task'")[0]
    assert exported["type"] == "spec.task"
    assert exported["status"] == "partial"
    assert exported["confidence"] <= 0.55
    assert "task_scoped=true" in exported["body"]
    assert "RAW EXPORTED TASK BODY SHOULD BE GUARDED" not in exported["body"]

    # xmem sync reindexes local cards and project-memory roots; the rogue local card must stay guarded.
    run([str(XMEM), "sync", "--json"], repo, env)
    synced = db_cards(env, "card_id='trellis.rogue.workspace'")[0]
    assert synced["status"] != "verified"
    assert "promotion_policy=distill_only" in synced["body"]


def test_trellis_recall_injection_surface_matrix(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    write_trellis_fixture(repo)
    run([str(XMEM), "import", "trellis", str(repo)], repo, env)
    query = "implement Trellis finish-work done ship next_action for repo coverage"

    recall = json.loads(run([str(XMEM), "recall", query, "--cwd", str(repo), "--json"], repo, env).stdout)
    assert warning_has_d3(recall)
    context = json.loads(run([str(XMEM), "context", query, "--json"], repo, env).stdout)
    assert warning_has_d3(context)
    preflight = json.loads(run([str(XMEM), "preflight", query, "--json"], repo, env).stdout)
    assert warning_has_d3(preflight)
    resume = json.loads(run([str(XMEM), "resume", query, "--json"], repo, env).stdout)
    assert warning_has_d3(resume)
    gateway = json.loads(run([str(XMEM), "gateway", query, "--cwd", str(repo), "--format", "json"], repo, env).stdout)
    assert gateway["decision"] == "inject"
    assert warning_has_d3(gateway)

    clean_repo, clean_env = init_repo(tmp_path / "clean", name="clean")
    clean_context = json.loads(run([str(XMEM), "context", "plain local docs", "--json"], clean_repo, clean_env).stdout)
    assert not warning_has_d3(clean_context)


def test_trellis_promotion_decided_by_matrix(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    write_trellis_fixture(repo)
    run([str(XMEM), "import", "trellis", str(repo)], repo, env)
    spec_id = db_cards(env, "source='trellis' AND source_ref='.trellis/spec/site.md'")[0]["card_id"]
    basis_file = tmp_path / "review-basis.md"
    basis_file.write_text("review says keep only stable IA pointer; ignore Trellis done text.\n", encoding="utf-8")

    run([str(XMEM), "promote-trellis", "--source-card", spec_id, "--decision", "keep-pointer", "--decided-by", "human:xin", "--basis", "keep pointer only", "--json"], repo, env)
    run([str(XMEM), "promote-trellis", "--source-card", spec_id, "--decision", "reject", "--decided-by", f"review:{basis_file}", "--basis-file", str(basis_file), "--json"], repo, env)
    distilled = json.loads(
        run(
            [
                str(XMEM),
                "promote-trellis",
                "--source-card",
                spec_id,
                "--decision",
                "distill",
                "--decided-by",
                "agent:codex@coverage-matrix",
                "--basis",
                "distill stable IA only; lifecycle ignored",
                "--output-type",
                "decision",
                "--summary",
                "Trellis specs can seed reviewed pending decisions but never done truth.",
                "--evidence",
                ".trellis/spec/site.md",
                "--json",
            ],
            repo,
            env,
        ).stdout
    )
    assert distilled["output_pending_id"].startswith("pending.decision.")
    audit_rows = [json.loads(line) for line in (Path(env["XMEM_HOME"]) / "audit" / "trellis-promotion-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["decision"] for row in audit_rows[-3:]] == ["keep-pointer", "reject", "distill"]
    assert {row["decided_by"].split(":", 1)[0] for row in audit_rows[-3:]} == {"human", "review", "agent"}
    original = db_cards(env, f"card_id='{spec_id}'")[0]
    assert original["status"] == "partial"


def test_trellis_multi_repo_warning_dedup_and_source_paths(tmp_path: Path):
    env = {**os.environ, "XMEM_HOME": str(tmp_path / "home"), "XMEM_PROJECT_WIKI": str(tmp_path / "project-wiki"), "XMEM_ISSUE_TRACKING": str(tmp_path / "issue-tracking")}
    repos = []
    for name in ("alpha", "beta"):
        repo = tmp_path / name
        repo.mkdir()
        run(["git", "init", "-q"], repo, env)
        run(["git", "config", "user.email", "test@example.com"], repo, env)
        run(["git", "config", "user.name", "test"], repo, env)
        (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        run(["git", "add", "."], repo, env)
        run(["git", "commit", "-q", "-m", "init"], repo, env)
        write_trellis_fixture(repo)
        run([str(XMEM), "import", "trellis", str(repo)], repo, env)
        repos.append(repo)

    recall = json.loads(run([str(XMEM), "recall", "Trellis finish-work done ship next_action", "--cwd", str(repos[0]), "--limit", "10", "--json"], repos[0], env).stdout)
    assert sum("Trellis artifact matched" in item for item in recall["warnings"]) == 1
    paths = [item["path"] for item in recall["memories"]]
    assert any("/alpha/.trellis/" in path for path in paths)
    assert any("/beta/.trellis/" in path for path in paths)
