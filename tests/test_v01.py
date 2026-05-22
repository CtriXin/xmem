from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from xmem.util import query_terms


ROOT = Path(__file__).resolve().parents[1]
XMEM = ROOT / "bin" / "xmem"


def run(cmd: list[str], cwd: Path, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def init_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    env = {**os.environ, "XMEM_HOME": str(tmp_path / "home"), "XMEM_PROJECT_WIKI": str(tmp_path / "project-wiki")}
    repo = tmp_path / "repo"
    repo.mkdir()
    run(["git", "init", "-q"], repo, env)
    run(["git", "config", "user.email", "test@example.com"], repo, env)
    run(["git", "config", "user.name", "test"], repo, env)
    (repo / "AdBanner.tsx").write_text("lazyload\nIntersectionObserver\n", encoding="utf-8")
    run(["git", "add", "."], repo, env)
    run(["git", "commit", "-q", "-m", "init"], repo, env)
    run([str(XMEM), "init", "--project-id", "demo-ads", "--alias", "demo ads"], repo, env)
    return repo, env


def write_project_wiki(tmp_path: Path, repo: Path) -> Path:
    wiki = tmp_path / "project-wiki" / "data"
    wiki.mkdir(parents=True)
    (wiki / "project-hub.index.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "service:car-ads",
                        "type": "Service",
                        "name": "car-ads",
                        "title": "Car Ads",
                        "status": "active",
                        "aliases": ["car ads", "automotive ads"],
                        "fields": {"localPath": str(repo), "techStack": "Node", "actualGitBranch": "main"},
                        "confidence": 1,
                        "updatedAt": "2026-05-22T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return wiki.parent


def test_context_expands_wrong_alias_to_canonical_project(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    wiki_root = write_project_wiki(tmp_path, repo)
    run([str(XMEM), "import", "project-wiki", "--path", str(wiki_root)], repo, env)
    run([str(XMEM), "fix", "car ads", "wrong=old ads", "correct=car ads", "basis=test_confirmed"], repo, env)

    packet = json.loads(run([str(XMEM), "context", "old ads", "--json"], repo, env).stdout)

    assert packet["resolution"]["status"] == "guided_by_correction"
    assert "car ads" in packet["suggested_queries"]
    assert packet["correction_guidance"][0]["canonical_aliases"] == ["car ads"]
    assert any(item["id"] == "project-wiki.service.car-ads" for item in packet["registry_candidates"])
    assert packet["resolution"]["do_not_assume_single_project"] is True


def test_multi_word_query_terms_keep_project_and_ads_tokens():
    terms = query_terms("ptc-intention-information ads.txt group_N adstxt")

    assert "ptc-intention-information" in terms
    assert "ads.txt" in terms
    assert "group_n" in terms
    assert "adstxt" in terms
    assert "ptc-intention-informationads.txtgroup_nadstxt" not in terms


def test_check_uses_registry_invariant_cards(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    run([str(XMEM), "import", "cards", str(ROOT / "examples" / "cards")], repo, env)
    (repo / "AdBanner.tsx").write_text("lazyload\n", encoding="utf-8")

    proc = run([str(XMEM), "check", "--json"], repo, env, check=False)
    data = json.loads(proc.stdout)

    assert data["warnings"]
    assert any(item["card"] == "ads.lazyload" and item["term"] == "IntersectionObserver" for item in data["warnings"])
    assert data["matched_cards"] >= 1


def test_gain_reports_queries_and_guardrails(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    run([str(XMEM), "import", "cards", str(ROOT / "examples" / "cards")], repo, env)
    run([str(XMEM), "context", "ad lazyload"], repo, env)
    (repo / "AdBanner.tsx").write_text("lazyload\n", encoding="utf-8")
    run([str(XMEM), "check", "--json"], repo, env, check=False)

    gain = json.loads(run([str(XMEM), "gain", "--json"], repo, env).stdout)

    assert gain["top_queries"][0]["query"] == "ad lazyload"
    assert gain["recent_queries"][0]["top_card"]
    assert gain["recent_guardrails"]


def test_context_next_reads_include_relation_cards(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    run([str(XMEM), "import", "cards", str(ROOT / "examples" / "cards")], repo, env)

    packet = json.loads(run([str(XMEM), "context", "xmem shared skill", "--json"], repo, env).stdout)

    assert any("xmem.installation.yaml" in path for path in packet["next_reads"])


def test_project_wiki_can_import_xmem_export_only(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    wiki = tmp_path / "project-wiki"
    data_dir = wiki / "data"
    data_dir.mkdir(parents=True)
    export = data_dir / "xmem-export.cards.jsonl"
    export.write_text(
        json.dumps(
            {
                "id": "project-wiki.service.demo-export",
                "type": "wiki.service",
                "title": "demo-export",
                "project_id": "demo-export",
                "aliases": ["oral demo", "demo.example.com"],
                "truth": {"status": "verified", "confidence": 0.91, "basis": ["test"], "last_checked_at": "2026-05-22T00:00:00Z"},
                "current": {"repo_path": str(repo), "remote": "git@example.com:demo/export.git", "latest_known_branch": "main"},
                "summary": "oral demo maps to demo-export.",
                "evidence": [{"kind": "project-wiki", "path": "entities/services/demo-export.yaml"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = json.loads(run([str(XMEM), "import", "project-wiki", "--path", str(wiki)], repo, env).stdout)
    packet = json.loads(run([str(XMEM), "context", "oral demo", "--json"], repo, env).stdout)

    assert result["export_cards"] == 1
    assert any(item["id"] == "project-wiki.service.demo-export" for item in packet["registry_candidates"])
    assert any("xmem-export.cards.jsonl" in path for path in packet["next_reads"])


def test_issue_tracking_imports_bug_patterns_as_rules(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    tracking = tmp_path / "issue-tracking"
    index_dir = tracking / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "bug-patterns.jsonl").write_text(
        json.dumps(
            {
                "id": "issue-pattern.ad-lazy-regression",
                "title": "Ad lazy-load regression",
                "symptom": "ad iframe fix removed lazy-load",
                "root_cause": "direct adsbygoogle push bypassed viewport observer",
                "fix_pattern": "restore IntersectionObserver before push",
                "verification": "check filled iframe, unfilled collapse, desktop and mobile",
                "regression_guard": "do not remove lazy-load when fixing ad display",
                "aliases": ["ad iframe lazy regression", "广告没有延迟加载"],
                "status": "verified",
                "confidence": 0.88,
                "evidence": [{"kind": "issue", "path": "issues/demo/ad-lazy/issue.md"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = json.loads(run([str(XMEM), "import", "issue-tracking", "--path", str(tracking)], repo, env).stdout)
    packet = json.loads(run([str(XMEM), "context", "ad iframe lazy regression", "--json"], repo, env).stdout)

    assert result["bug_patterns"] == 1
    assert any(item["id"] == "issue-pattern.ad-lazy-regression" for item in packet["rules"])
