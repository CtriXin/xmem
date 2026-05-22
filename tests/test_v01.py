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
    env = {
        **os.environ,
        "XMEM_HOME": str(tmp_path / "home"),
        "XMEM_PROJECT_WIKI": str(tmp_path / "project-wiki"),
        "XMEM_ISSUE_TRACKING": str(tmp_path / "issue-tracking"),
    }
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


def test_check_honors_guard_path_scope_before_terms(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    card_dir = repo / ".xmem" / "cards"
    card_dir.mkdir(parents=True, exist_ok=True)
    (card_dir / "ads-scope.yaml").write_text(
        "\n".join(
            [
                "id: ads.scope",
                "type: invariant",
                "title: Scoped ad guard",
                "scope:",
                "  paths:",
                "    - \"**/*Ad*\"",
                "diff_guard:",
                "  warn_if_removed:",
                "    - observe",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "notes.md").write_text("observe\n", encoding="utf-8")
    run(["git", "add", "."], repo, env)
    run(["git", "commit", "-q", "-m", "add scoped guard"], repo, env)
    (repo / "notes.md").write_text("done\n", encoding="utf-8")

    data = json.loads(run([str(XMEM), "check", "--json"], repo, env).stdout)

    assert data["warnings"] == []
    assert data["matched_cards"] == 0


def test_gain_reports_queries_and_guardrails(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    run([str(XMEM), "import", "cards", str(ROOT / "examples" / "cards")], repo, env)
    run([str(XMEM), "context", "ad lazyload"], repo, env)
    (repo / "AdBanner.tsx").write_text("lazyload\n", encoding="utf-8")
    run([str(XMEM), "check", "--json"], repo, env, check=False)

    gain = json.loads(run([str(XMEM), "gain", "--json"], repo, env).stdout)
    text_gain = run([str(XMEM), "gain"], repo, env).stdout

    assert gain["top_queries"][0]["query"] == "ad lazyload"
    assert gain["top_queries"][0]["estimated_tokens_saved"] > 0
    assert gain["observed"]["context_hits"] == 1
    assert gain["recent_queries"][0]["top_card"]
    assert gain["recent_guardrails"]
    assert "XMEM Gain 收益面板" in text_gain
    assert "真实字段:" in text_gain
    assert "按事件" in text_gain


def test_gain_distinguishes_lookup_from_context_savings(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    run([str(XMEM), "import", "cards", str(ROOT / "examples" / "cards")], repo, env)
    run([str(XMEM), "find", "ad lazyload"], repo, env)

    lookup_gain = json.loads(run([str(XMEM), "gain", "--json"], repo, env).stdout)
    assert lookup_gain["observed"]["context_queries"] == 0
    assert lookup_gain["estimated_tokens_saved"] == 0
    assert any(item["event"] == "find.hit" for item in lookup_gain["by_event"])

    run([str(XMEM), "context", "ad lazyload"], repo, env)
    context_gain = json.loads(run([str(XMEM), "gain", "--json"], repo, env).stdout)
    assert context_gain["observed"]["context_hits"] == 1
    assert context_gain["estimated_tokens_saved"] > 0


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


def test_preflight_returns_issue_patterns_before_development(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    tracking = tmp_path / "issue-tracking"
    index_dir = tracking / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "bug-patterns.jsonl").write_text(
        json.dumps(
            {
                "id": "issue-pattern.ad-lazy-regression",
                "title": "Ad lazy-load regression",
                "symptom": "ad iframe display was fixed but lazy-load stopped working",
                "root_cause": "display fix bypassed the lazy-load initialization path",
                "fix_pattern": "keep lazy-load wrapper when changing ad display",
                "verification": "check filled iframe, unfilled collapse, desktop and mobile",
                "regression_guard": "do not remove lazy-load when fixing ad display",
                "aliases": ["ad iframe lazy regression", "广告没有延迟加载"],
                "truth": {"status": "verified", "confidence": 0.9},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    run([str(XMEM), "import", "issue-tracking", "--path", str(tracking)], repo, env)

    packet = json.loads(run([str(XMEM), "preflight", "ad iframe lazy regression", "--json"], repo, env).stdout)
    text_packet = run([str(XMEM), "preflight", "ad iframe lazy regression"], repo, env).stdout

    assert packet["schema"] == "xmem.preflight.v1"
    assert packet["readiness"] == "ready_with_guards"
    assert packet["risk_level"] == "high"
    assert any(item["id"] == "issue-pattern.ad-lazy-regression" for item in packet["known_bug_patterns"])
    assert any("lazy-load" in item["text"] for item in packet["must_keep"])
    assert any("iframe" in item["text"] for item in packet["required_checks"])
    assert "xmem_preflight:" in text_packet
    assert "must_keep" in text_packet


def test_check_sources_reports_export_shape_errors(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    export = tmp_path / "project-wiki" / "data" / "xmem-export.cards.jsonl"
    export.parent.mkdir(parents=True)
    export.write_text(
        json.dumps({"id": "bad.card", "title": "Bad Card", "truth": {"status": "certain", "confidence": 1.2}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    proc = run([str(XMEM), "check", "--sources", "--json"], repo, env, check=False)
    data = json.loads(proc.stdout)

    assert proc.returncode == 2
    assert data["errors"] >= 2
    assert data["exports"][0]["errors"]


def test_check_sources_accepts_valid_exports_with_optional_missing(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    export = tmp_path / "project-wiki" / "data" / "xmem-export.cards.jsonl"
    export.parent.mkdir(parents=True)
    export.write_text(
        json.dumps(
            {
                "id": "project-wiki.service.ok",
                "type": "wiki.service",
                "title": "ok",
                "truth": {"status": "verified", "confidence": 0.9},
                "summary": "ok service",
                "evidence": [{"kind": "test", "ref": "unit"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    proc = run([str(XMEM), "check", "--sources", "--json"], repo, env)
    data = json.loads(proc.stdout)

    assert proc.returncode == 0
    assert data["errors"] == 0
    assert data["exports"][0]["rows"] == 1


def test_context_fuses_duplicate_cards_by_title(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    export = tmp_path / "dupe-export.cards.jsonl"
    rows = [
        {
            "id": "rule.dupe.partial",
            "type": "rule",
            "title": "Duplicate Ad Rule",
            "aliases": ["duplicate ad rule"],
            "truth": {"status": "partial", "confidence": 0.5},
            "summary": "Older partial rule.",
            "evidence": [{"kind": "test", "ref": "partial"}],
        },
        {
            "id": "rule.dupe.verified",
            "type": "rule",
            "title": "Duplicate Ad Rule",
            "aliases": ["duplicate ad rule"],
            "truth": {"status": "verified", "confidence": 0.9},
            "summary": "Verified rule.",
            "evidence": [{"kind": "test", "ref": "verified"}],
        },
    ]
    export.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    run([str(XMEM), "import", "export", str(export)], repo, env)
    packet = json.loads(run([str(XMEM), "context", "duplicate ad rule", "--json"], repo, env).stdout)
    matches = [item for item in packet["rules"] if item["title"] == "Duplicate Ad Rule"]

    assert len(matches) == 1
    assert matches[0]["id"] == "rule.dupe.verified"
    assert matches[0]["supporting_count"] == 1
    assert matches[0]["supporting_cards"][0]["id"] == "rule.dupe.partial"


def test_context_warns_when_source_export_is_newer_than_registry(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    export = tmp_path / "project-wiki" / "data" / "xmem-export.cards.jsonl"
    export.parent.mkdir(parents=True)
    export.write_text(
        json.dumps(
            {
                "id": "project-wiki.service.freshness",
                "type": "wiki.service",
                "title": "freshness-service",
                "truth": {"status": "verified", "confidence": 0.9},
                "summary": "freshness test",
                "evidence": [{"kind": "test", "ref": "initial"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    run([str(XMEM), "sync", "--json"], repo, env)
    export.write_text(export.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    registry = Path(env["XMEM_HOME"]) / "registry.sqlite"
    newer_than_registry = registry.stat().st_mtime + 2
    os.utime(export, (newer_than_registry, newer_than_registry))

    packet = json.loads(run([str(XMEM), "context", "freshness-service", "--json"], repo, env).stdout)

    assert packet["source_freshness"]["status"] == "stale"
    assert packet["source_freshness"]["stale_exports"] >= 1
    assert any("run xmem sync" in warning for warning in packet["warnings"])

    text_packet = run([str(XMEM), "context", "freshness-service"], repo, env).stdout
    assert "source_freshness:" in text_packet
    assert "stale_exports:" in text_packet
