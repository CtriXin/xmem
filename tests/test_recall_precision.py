"""Regression guard for recall precision + observability.

Covers the phantom-match bug where bare digits / stopwords substring-matched the
card alias blob and produced confident-looking noise, plus the ``why-last``
observability command and the ``index --cwd`` agent-call ergonomics.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from xmem.search import digit_boundary_match, is_weak_term

ROOT = Path(__file__).resolve().parents[1]
XMEM = ROOT / "bin" / "xmem"


def _run(cmd, cwd, env, check=True):
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


# --- pure unit tests: the exact phantom-match the fix removes ---

def test_pure_digit_term_does_not_match_inside_longer_number():
    # "3+2 吧" used to hit deploy-0602 via "2" inside "0602"; must not anymore.
    assert digit_boundary_match("2", "scmp deploy 0602 v5") is False
    assert digit_boundary_match("3", "ptc-ai-inflation 0602") is False


def test_real_numeric_id_still_matches_at_boundary():
    assert digit_boundary_match("4638", "adx-4638 crypto") is True
    assert digit_boundary_match("102746", "t102746 traffic") is True


def test_weak_terms_are_dropped():
    assert is_weak_term("3") is True       # single digit
    assert is_weak_term("a") is True       # single char
    assert is_weak_term("需要") is True     # cjk stopword
    assert is_weak_term("没有") is True
    assert is_weak_term("image") is True   # pasted-screenshot noise


def test_meaningful_terms_are_kept():
    assert is_weak_term("网文") is False
    assert is_weak_term("coscli") is False
    assert is_weak_term("assign") is False
    assert is_weak_term("4638") is False   # numeric id kept (boundary-checked at match time)


# --- integration: observability + agent-call ergonomics ---

def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "XMEM_HOME": str(tmp_path / "home"),
        "XMEM_PROJECT_WIKI": str(tmp_path / "missing-wiki"),
        "XMEM_ISSUE_TRACKING": str(tmp_path / "missing-issue"),
    }


def test_why_last_reads_recent_gain_events(tmp_path: Path):
    env = _base_env(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    rows = [
        {"ts": "2026-06-17T10:00:00Z", "event": "recall.hit", "source": "recall",
         "query": "coscli secretID missing", "matches": 3,
         "top_card": "scmp.coscli.isolated-home-env", "top_status": "verified",
         "top_score": 18.4, "top_why": "alias_term:coscli"},
        {"ts": "2026-06-17T10:01:00Z", "event": "search.miss", "source": "search",
         "query": "nothing here", "matches": 0, "top_card": "", "top_status": "",
         "top_score": 0, "top_why": ""},
    ]
    (home / "gain.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    out = _run([str(XMEM), "why-last", "5"], tmp_path, env).stdout
    assert "scmp.coscli.isolated-home-env" in out
    assert "alias_term:coscli" in out

    parsed = json.loads(_run([str(XMEM), "why-last", "5", "--json"], tmp_path, env).stdout)
    assert any(r["top_card"] == "scmp.coscli.isolated-home-env" for r in parsed)


def test_index_accepts_cwd_option_for_agent_calls(tmp_path: Path):
    env = _base_env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo, env)
    _run(["git", "config", "user.email", "t@example.com"], repo, env)
    _run(["git", "config", "user.name", "t"], repo, env)
    _run([str(XMEM), "init", "--project-id", "demo", "--alias", "demo"], repo, env)

    # --cwd behaves like the positional path (agents call it this way).
    assert "indexed" in _run([str(XMEM), "index", "--cwd", str(repo)], tmp_path, env).stdout

    # Supplying both positional path and --cwd is rejected with exit code 2.
    clash = _run([str(XMEM), "index", str(repo), "--cwd", str(repo)], tmp_path, env, check=False)
    assert clash.returncode == 2
