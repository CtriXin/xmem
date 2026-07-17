"""hook.inject 埋点 + gain hook-outcomes 消费率的回归测试。

背景：gain 面板显示 recall.hit 数千次但 outcomes=0，hook 注入从未被证明消费过
（issue #2）。这里锁两件事：注入时 shown card id 必须落 gain.jsonl；统计时
只有 transcript 的 assistant 行提到 shown id 才算消费，注入行自己不算。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from xmem import agent_hooks
from xmem.hook_outcomes import build_hook_outcomes
from xmem.util import utc_now

ROOT = Path(__file__).resolve().parents[1]
XMEM = ROOT / "bin" / "xmem"


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "XMEM_HOME": str(tmp_path / "home"),
        "XMEM_PROJECT_WIKI": str(tmp_path / "missing-wiki"),
        "XMEM_ISSUE_TRACKING": str(tmp_path / "missing-issue"),
    }


def _read_gain_rows(home: Path) -> list[dict]:
    path = home / "gain.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fake_recall_packet(query: str, **_kwargs) -> dict:
    def memory(cid: str) -> dict:
        return {
            "id": cid,
            "type": "bug_pattern",
            "title": "payment retry timeout bug pattern",
            "summary": "payment retry loop hits gateway timeout",
            "truth": "verified",
            "confidence": 0.8,
            "score": 9.0,
            "why": ["alias_term:payment"],
            "path": "",
        }

    return {
        "query": query,
        "scope": {"project_id": ""},
        "memories": [memory("card.payment-a"), memory("card.payment-b"), memory("card.payment-c")],
        "pending": [],
    }


def test_hook_inject_logs_shown_ids_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setenv("XMEM_HOME", str(home))
    monkeypatch.setattr(agent_hooks, "build_recall", _fake_recall_packet)

    payload = json.dumps({
        "prompt": "payment retry 又超时了，查一下历史坑",
        "cwd": str(tmp_path),
        "session_id": "sess-123",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
    })
    output = agent_hooks.run_agent_hook(
        "UserPromptSubmit", host="claude", cwd=tmp_path, limit=6,
        capture=False, stdin_text=payload,
    )
    assert "card.payment-a" in output

    rows = [row for row in _read_gain_rows(home) if row.get("event") == "hook.inject"]
    assert len(rows) == 1
    row = rows[0]
    assert row["host"] == "claude"
    assert row["hook_event"] == "userpromptsubmit"
    assert row["session_id"] == "sess-123"
    assert row["transcript_path"] == str(tmp_path / "transcript.jsonl")
    assert row["card_ids"] == ["card.payment-a", "card.payment-b", "card.payment-c"]
    # compact 格式只展示前 2 张卡，第 3 张没进对话，不能算 shown
    assert row["shown_card_ids"] == ["card.payment-a", "card.payment-b"]


def test_hook_inject_not_logged_when_output_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setenv("XMEM_HOME", str(home))
    monkeypatch.setattr(
        agent_hooks, "build_recall",
        lambda query, **kwargs: {"query": query, "scope": {}, "memories": [], "pending": []},
    )
    output = agent_hooks.run_agent_hook(
        "UserPromptSubmit", host="claude", cwd=tmp_path, limit=6,
        capture=False, stdin_text=json.dumps({"prompt": "payment retry timeout", "cwd": str(tmp_path)}),
    )
    assert output == ""
    assert [row for row in _read_gain_rows(home) if row.get("event") == "hook.inject"] == []


def test_build_hook_outcomes_counts_assistant_mentions_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("XMEM_HOME", str(home))

    transcript = tmp_path / "t1.jsonl"
    transcript.write_text(
        "\n".join([
            # 注入行落在 user 侧，出现两个 id，不允许算消费
            json.dumps({"type": "user", "message": {"role": "user", "content": "hook context: card.alpha card.beta"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "先看 card.alpha 的历史坑"}}),
            "not-json-line",
        ]) + "\n",
        encoding="utf-8",
    )

    def inject_row(shown: list[str], transcript_path: str) -> dict:
        return {
            "ts": utc_now(), "event": "hook.inject", "source": "agent-hook",
            "host": "claude", "hook_event": "userpromptsubmit", "action": "recall",
            "cwd": str(tmp_path), "project_id": "", "session_id": "s",
            "transcript_path": transcript_path,
            "card_ids": shown, "shown_card_ids": shown, "pending_count": 0, "output_chars": 100,
        }

    rows = [
        inject_row(["card.alpha", "card.beta"], str(transcript)),
        inject_row(["card.gamma"], str(tmp_path / "missing.jsonl")),
        inject_row([], str(transcript)),
        {"ts": utc_now(), "event": "recall.hit", "query": "unrelated"},
    ]
    (home / "gain.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8",
    )

    data = build_hook_outcomes(days=14)
    assert data["injects_total"] == 3
    assert data["injects_no_cards"] == 1
    assert data["injects_unmeasured"] == 1
    assert data["injects_measured"] == 1
    assert data["injects_consumed"] == 1
    assert data["consumption_rate"] == 100.0
    by_card = {item["card_id"]: item for item in data["cards"]}
    assert by_card["card.alpha"]["consumed"] == 1
    assert by_card["card.beta"]["consumed"] == 0


def test_gain_hook_outcomes_cli_smoke(tmp_path: Path):
    env = _base_env(tmp_path)
    proc = subprocess.run(
        [str(XMEM), "gain", "hook-outcomes", "--json"],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["schema"] == "xmem.hook_outcomes.v1"
    assert data["injects_total"] == 0
