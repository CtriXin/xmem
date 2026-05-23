from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
XMEM = ROOT / "bin" / "xmem"


def run(cmd, cwd, env, check=True):
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        env = {**os.environ, "XMEM_HOME": str(base / "home"), "XMEM_PROJECT_WIKI": str(base / "project-wiki")}
        repo = base / "repo"
        repo.mkdir()
        run(["git", "init", "-q"], repo, env)
        run(["git", "config", "user.email", "test@example.com"], repo, env)
        run(["git", "config", "user.name", "test"], repo, env)
        (repo / "ad.txt").write_text("lazyload\nIntersectionObserver\n", encoding="utf-8")
        run(["git", "add", "ad.txt"], repo, env)
        run(["git", "commit", "-q", "-m", "init"], repo, env)
        run([str(XMEM), "init", "--project-id", "smoke", "--alias", "smoke ads"], repo, env)
        sources = json.loads((base / "home" / "sources.json").read_text(encoding="utf-8"))
        assert str(repo.resolve()) in [item["root"] for item in sources["local_roots"]]
        status = run([str(XMEM), "status"], repo, env).stdout
        assert "registry_exists: true" in status
        assert "local_sources: 1" in status
        help_text = run([str(XMEM), "help"], repo, env).stdout
        assert "xmem sync" in help_text and "xmem fix" in help_text and "xmem doctor" in help_text
        doctor = run([str(XMEM), "doctor"], repo, env).stdout
        assert "xmem_doctor:" in doctor and "components:" in doctor
        root_help = run([str(XMEM), "-h"], repo, env).stdout
        assert "用法: xmem <命令> [选项]" in root_help
        assert "显示帮助并退出" in root_help
        shim = base / "bin" / "xmem"
        shim.parent.mkdir()
        shim.symlink_to(XMEM)
        linked_status = run([str(shim), "status"], repo, env).stdout
        assert "registry_exists: true" in linked_status
        shutil.copy(ROOT / "examples" / "cards" / "ads.lazyload.yaml", repo / ".xmem" / "cards" / "ads.lazyload.yaml")
        (repo / "ad.txt").write_text("lazyload\n", encoding="utf-8")
        proc = run([str(XMEM), "check"], repo, env, check=False)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "IntersectionObserver" in proc.stdout

        wiki = base / "project-wiki" / "data"
        wiki.mkdir(parents=True)
        (wiki / "project-hub.index.json").write_text(json.dumps({
            "entities": [{
                "id": "service:demo-ads", "type": "Service", "name": "demo-ads",
                "title": "Demo Ads", "status": "active", "aliases": ["car ads"],
                "fields": {"localPath": str(repo), "techStack": "Node", "actualGitBranch": "main"},
                "confidence": 1, "updatedAt": "2026-05-22T00:00:00Z"
            }]
        }), encoding="utf-8")
        run([str(XMEM), "import", "project-wiki", "--path", str(wiki.parent)], repo, env)
        found = run([str(XMEM), "find", "car ads", "--json"], repo, env).stdout
        assert "project-wiki.service.demo-ads" in found
        packet = run([str(XMEM), "context", "car ads"], repo, env).stdout
        assert "xmem_context:" in packet
        assert "resolution:" in packet
        assert "registry_candidates" in packet
        assert "source_path:" in packet
        opened = run([str(XMEM), "open", "project-wiki.service.demo-ads"], repo, env).stdout
        assert "source_ref: service:demo-ads" in opened
        run([str(XMEM), "fix", "car ads", "wrong=old ads", "correct=car ads", "basis=test_confirmed"], repo, env)
        corrected = run([str(XMEM), "context", "old ads"], repo, env).stdout
        assert "corrections[1]" in corrected
        assert "wrong_aliases:" in corrected
        hooked = run([
            str(XMEM), "hook", "finish",
            "Added lazyload invariant for car ads and preserved IntersectionObserver",
            "--dest", "all",
            "--verified",
        ], repo, env).stdout
        assert "hooked: finish" in hooked
        assert "project_wiki: queued" in hooked
        assert "issue_tracking: seeded" in hooked
        assert "xmem_hook_memory" in (wiki / "agent-inbox.jsonl").read_text(encoding="utf-8")
        assert list((base / "home" / "outbox" / "issue-tracking").glob("*.md"))
        hook_context = run([str(XMEM), "context", "IntersectionObserver"], repo, env).stdout
        assert "hook.memory" in hook_context
        rebuilt = run([
            str(XMEM), "rebuild",
            "--project-wiki", str(wiki.parent),
            "--issue-tracking", str(base / "missing-issues"),
            "--cards", str(ROOT / "examples" / "cards"),
            "--local", str(repo),
            "--skip-issue-tracking",
        ], repo, env).stdout
        assert '"project_wiki"' in rebuilt and '"cards"' in rebuilt
        relation = run([str(XMEM), "context", "xmem shared skill"], repo, env).stdout
        assert "relations[" in relation
        assert "xmem.installation" in relation
        gain = run([str(XMEM), "gain", "--json"], repo, env).stdout
        assert "estimated_tokens_saved" in gain
    print("smoke ok")


if __name__ == "__main__":
    main()
