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
    assert gain["calibration"]["status"] == "proxy_only"
    assert gain["calibration"]["confidence"] == "low"
    assert gain["calibration"]["needs_review"]
    assert gain["recent_queries"][0]["top_card"]
    assert gain["recent_guardrails"]
    assert "XMEM Gain 收益面板" in text_gain
    assert "日志计数字段:" in text_gain
    assert "命中口径:" in text_gain
    assert "收益口径:" in text_gain
    assert "自校准状态:" in text_gain
    assert "待校准高估项" in text_gain
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


def test_gain_confirm_and_hook_outcome_calibrate_dashboard(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    run([str(XMEM), "import", "cards", str(ROOT / "examples" / "cards")], repo, env)
    run([str(XMEM), "context", "ad lazyload"], repo, env)
    confirmed = json.loads(
        run(
            [
                str(XMEM),
                "gain",
                "confirm",
                "ad lazyload",
                "--note",
                "confirmed useful",
                "--actual-tokens-saved",
                "120",
                "--bug-prevented",
                "--json",
            ],
            repo,
            env,
        ).stdout
    )
    hooked = json.loads(
        run(
            [
                str(XMEM),
                "hook",
                "finish",
                "ad lazyload outcome verified",
                "--verified",
                "--json",
            ],
            repo,
            env,
        ).stdout
    )

    gain = json.loads(run([str(XMEM), "gain", "--json"], repo, env).stdout)
    text_gain = run([str(XMEM), "gain"], repo, env).stdout

    assert confirmed["event"] == "gain.confirmed"
    assert {item["target"] for item in confirmed["feedback"]} == {"gain-feedback", "project-wiki", "issue-tracking"}
    assert hooked["outcome"]["event"] == "outcome.finish"
    assert hooked["outcome"]["feedback"][0]["target"] == "gain-feedback"
    assert gain["calibration"]["status"] == "partially_calibrated"
    assert gain["calibration"]["confirmed"] == 1
    assert gain["calibration"]["confirmed_actual_tokens_saved"] == 120
    assert gain["actual_tokens_saved"] == 120
    assert gain["calibration"]["outcomes"] == 1
    assert "outcomes=1" in text_gain
    assert list((Path(env["XMEM_HOME"]) / "outbox" / "gain-feedback").glob("*.json"))
    assert list((Path(env["XMEM_HOME"]) / "outbox" / "project-wiki").glob("*.json"))
    assert list((Path(env["XMEM_HOME"]) / "outbox" / "issue-tracking").glob("gain_*.md"))


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


def test_imports_context_specs_and_trellis_sources(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    (repo / "CONTEXT.md").write_text(
        "# Ads Context\n\ncanonical term: aurora ads means car ads lazyload platform\n",
        encoding="utf-8",
    )
    (repo / "docs" / "adr").mkdir(parents=True)
    (repo / "docs" / "adr" / "0001-lazyload.md").write_text(
        "# Keep lazyload\n\nStatus: Accepted\n\nDecision: preserve IntersectionObserver for ad display fixes.\n",
        encoding="utf-8",
    )
    (repo / "openspec" / "specs" / "ads").mkdir(parents=True)
    (repo / "openspec" / "specs" / "ads" / "spec.md").write_text(
        "# Ads Lazyload Spec\n\nRequirement: ads keep lazyload and collapse empty slots.\n",
        encoding="utf-8",
    )
    (repo / ".specify" / "memory").mkdir(parents=True)
    (repo / ".specify" / "memory" / "constitution.md").write_text(
        "# Project Constitution\n\nInvariant: never remove lazyload guards.\n",
        encoding="utf-8",
    )
    (repo / ".trellis" / "tasks").mkdir(parents=True)
    (repo / ".trellis" / "tasks" / "lazyload.md").write_text(
        "# Trellis Lazyload Task\n\nTask: verify ad lazyload after bug fixes.\n",
        encoding="utf-8",
    )

    context_docs = json.loads(run([str(XMEM), "import", "context-docs", str(repo)], repo, env).stdout)
    openspec = json.loads(run([str(XMEM), "import", "openspec", str(repo)], repo, env).stdout)
    speckit = json.loads(run([str(XMEM), "import", "speckit", str(repo)], repo, env).stdout)
    trellis = json.loads(run([str(XMEM), "import", "trellis", str(repo)], repo, env).stdout)
    packet = json.loads(run([str(XMEM), "context", "aurora ads lazyload constitution trellis", "--json"], repo, env).stdout)

    assert context_docs["cards"] == 2
    assert openspec["cards"] == 1
    assert speckit["cards"] == 1
    assert trellis["cards"] == 1
    assert any(item["source"] == "context-docs" for item in packet["specs"])
    assert any(item["source"] == "openspec" for item in packet["specs"])
    assert any(item["source"] == "speckit" for item in packet["specs"])
    assert any(item["source"] == "trellis" for item in packet["specs"])
    assert any("CONTEXT.md" in path for path in packet["next_reads"])


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


def test_preflight_filters_weak_body_only_guards(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "xmem-control.yaml").write_text(
        "\n".join(
            [
                "id: xmem.control",
                "type: rule",
                "title: xmem control",
                "aliases:",
                "  - xmem memory router",
                "truth:",
                "  status: verified",
                "  confidence: 0.95",
                "summary: xmem routes memory to source truth.",
                "must_include:",
                "  - Keep source truth pointers.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (cards / "ads-noise.yaml").write_text(
        "\n".join(
            [
                "id: ads.noise",
                "type: invariant",
                "title: Ads noise",
                "aliases:",
                "  - unrelated ads",
                "truth:",
                "  status: verified",
                "  confidence: 0.9",
                "summary: This card mentions xmem memory only as an example, but it is about ads.",
                "must_include:",
                "  - Preserve ad lazyload.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run([str(XMEM), "import", "cards", str(cards)], repo, env)

    packet = json.loads(run([str(XMEM), "preflight", "xmem memory router", "--json"], repo, env).stdout)

    ids = {item["id"] for item in packet["invariants"]}
    assert "xmem.control" in ids
    assert "ads.noise" not in ids
    assert all("ads.noise" not in ref for ref in packet["source_refs"])


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


def test_status_reports_local_only_non_identity_cards(tmp_path: Path):
    repo, env = init_repo(tmp_path)
    (repo / ".gitignore").write_text(".xmem/\n", encoding="utf-8")
    (repo / ".xmem" / "cards" / "local.rule.yaml").write_text(
        "\n".join(
            [
                "id: local.rule",
                "type: rule",
                "title: Local Rule",
                "truth:",
                "  status: verified",
                "  confidence: 0.9",
                "summary: Local-only knowledge should be visible in status.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    data = json.loads(run([str(XMEM), "status", "--json"], repo, env).stdout)
    audit = data["local_source_audit"]
    detail = next(item for item in audit["details"] if item["root"] == str(repo))
    text = run([str(XMEM), "status"], repo, env).stdout

    assert audit["local_only_knowledge_cards"] >= 1
    assert detail["ignored_knowledge_cards"] >= 1
    assert any("local-only .xmem/cards" in action for action in data["next_actions"])
    assert ".xmem/cards/local.rule.yaml" in detail["sample_local_only"]
    assert "local_card_warning" in text
    assert "local_only_knowledge=" in text
    assert "next_actions:" in text

    sources = json.loads(run([str(XMEM), "check", "--sources", "--json"], repo, env).stdout)
    sources_text = run([str(XMEM), "check", "--sources"], repo, env).stdout
    assert sources["status"] == "warn"
    assert sources["local_card_warnings"] >= 1
    assert sources["local_source_audit"]["local_only_knowledge_cards"] >= 1
    assert "local_only_knowledge" in sources_text
    strict = run([str(XMEM), "check", "--sources", "--strict"], repo, env, check=False)
    assert strict.returncode == 2
    sync_text = run([str(XMEM), "sync"], repo, env).stdout
    assert "source_exports: warn" in sync_text
    assert "next_actions:" in sync_text
    packet = json.loads(run([str(XMEM), "context", "local rule", "--json"], repo, env).stdout)
    assert packet["local_source_health"]["local_only_knowledge_cards"] >= 1
    assert any("not portable through git" in warning for warning in packet["warnings"])
    assert "local_source_health:" in run([str(XMEM), "preflight", "local rule"], repo, env).stdout
    unrelated = json.loads(run([str(XMEM), "context", "definitely-no-local-card-match", "--json"], repo, env).stdout)
    assert unrelated["local_source_health"] == {}


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
