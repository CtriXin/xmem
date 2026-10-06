from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from xmem.util import slugify, stable_slug

ROOT = Path(__file__).resolve().parents[1]
XMEM = ROOT / "bin" / "xmem"


def run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(f"command failed {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def setup_env(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    env = {
        **os.environ,
        "XMEM_HOME": str(tmp_path / "home"),
        "XMEM_PROJECT_WIKI": str(tmp_path / "project-wiki"),
        "XMEM_ISSUE_TRACKING": str(tmp_path / "issue-tracking"),
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    run(["git", "init", "-q"], repo, env)
    run(["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], repo, env)
    run([str(XMEM), "init", "--project-id", "demo"], repo, env)
    return repo, env


def query(env: dict[str, str], sql: str) -> list[tuple]:
    conn = sqlite3.connect(Path(env["XMEM_HOME"]) / "registry.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_stable_slug_keeps_ascii_ids_and_separates_non_ascii():
    for value in ["car ads", "issue-pattern.ad-lazy-regression", "demo-ads", "ptc_v5", ""]:
        assert stable_slug(value) == slugify(value)
    assert slugify("广告位") == slugify("小说模板") == "project"
    assert stable_slug("广告位") != stable_slug("小说模板")
    assert stable_slug("广告位").startswith("project-")
    assert stable_slug(stable_slug("广告位")) == stable_slug("广告位")
    assert stable_slug("ads广告") != stable_slug("ads")


def test_fix_does_not_overwrite_other_non_ascii_correction(tmp_path: Path):
    repo, env = setup_env(tmp_path)
    run([str(XMEM), "fix", "广告位", "wrong=旧广告", "correct=新广告", "basis=test"], repo, env)
    run([str(XMEM), "fix", "小说模板", "wrong=旧模板", "correct=新模板", "basis=test"], repo, env)

    files = sorted((Path(env["XMEM_HOME"]) / "cards" / "corrections").glob("*.yaml"))
    assert len(files) == 2
    titles = sorted(row[0] for row in query(env, "SELECT title FROM cards WHERE type='correction'"))
    assert titles == ["小说模板 alias correction", "广告位 alias correction"]


def test_bug_patterns_with_chinese_titles_keep_separate_cards(tmp_path: Path):
    repo, env = setup_env(tmp_path)
    patterns = tmp_path / "bug-patterns.jsonl"
    patterns.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in [
            {"title": "广告懒加载回归", "symptom": "iframe 修复移除了懒加载", "status": "verified"},
            {"title": "支付回调丢单", "symptom": "回调重试时订单丢失", "status": "verified"},
        ]) + "\n",
        encoding="utf-8",
    )
    result = json.loads(run([str(XMEM), "import", "bug-patterns", str(patterns)], repo, env).stdout)

    assert result["cards"] == 2
    titles = sorted(row[0] for row in query(env, "SELECT title FROM cards WHERE card_id LIKE 'issue-pattern.%'"))
    assert titles == ["广告懒加载回归", "支付回调丢单"]


def test_issue_tracking_chinese_dirs_keep_separate_issue_and_project(tmp_path: Path):
    repo, env = setup_env(tmp_path)
    tracking = tmp_path / "issue-tracking"
    for project, issue, title in [("项目甲", "广告修复", "广告修复"), ("项目乙", "支付修复", "支付修复")]:
        d = tracking / "issues" / project / issue
        d.mkdir(parents=True)
        (d / "issue.md").write_text(f"Task name: {title}\nStatus: done\n", encoding="utf-8")
    run([str(XMEM), "import", "issue-tracking", "--path", str(tracking)], repo, env)

    issues = sorted(row[0] for row in query(env, "SELECT title FROM cards WHERE type='evidence.issue'"))
    assert issues == ["广告修复", "支付修复"]
    projects = sorted(row[0] for row in query(env, "SELECT name FROM projects WHERE name LIKE '项目%'"))
    assert projects == ["项目乙", "项目甲"]
