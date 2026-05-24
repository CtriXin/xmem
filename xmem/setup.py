from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from .project import init_project
from .sources import register_local_root
from .util import git_root, git_value, home_dir, slugify, utc_now

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".xmem",
    ".codegraph",
    ".ai",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
}


def setup_workspace(
    paths: Iterable[Path],
    *,
    scan_depth: int = 2,
    init_projects: bool = True,
    memory_repo: Path | None = None,
    dry_run: bool = False,
    max_roots: int = 80,
) -> dict[str, Any]:
    """Create a generic xmem workspace without assuming private adapters."""
    raw_paths = list(paths) or [Path.cwd()]
    discovered, skipped = discover_project_roots(raw_paths, scan_depth=scan_depth, max_roots=max_roots)
    result: dict[str, Any] = {
        "schema": "xmem.setup.v1",
        "xmem_home": str(home_dir()),
        "input_paths": [str(p.expanduser()) for p in raw_paths],
        "scan_depth": scan_depth,
        "dry_run": dry_run,
        "mode": "init_projects" if init_projects else "register_only",
        "discovered_roots": [str(p) for p in discovered],
        "initialized_projects": [],
        "registered_roots": [],
        "memory_repo": None,
        "files_written": [],
        "skipped": skipped,
        "next_steps": [],
    }

    if dry_run:
        result["next_steps"] = setup_next_steps(result)
        return result

    result["files_written"].extend(write_home_workspace_files())

    for root in discovered:
        if init_projects:
            project = init_project(root)
            result["initialized_projects"].append(
                {"project_id": project.get("project_id", ""), "root": project.get("root", str(root)), "truth": str(Path(project.get("root", str(root))) / ".xmem")}
            )
        else:
            registered = register_local_root(root, "xmem.setup")
            result["registered_roots"].append(str(registered))

    if memory_repo:
        result["memory_repo"] = create_memory_repo(memory_repo, dry_run=False)
        if result["memory_repo"].get("project"):
            result["initialized_projects"].append(result["memory_repo"]["project"])

    result["next_steps"] = setup_next_steps(result)
    return result


def discover_project_roots(paths: Iterable[Path], *, scan_depth: int = 2, max_roots: int = 80) -> tuple[list[Path], list[dict[str, str]]]:
    roots: list[Path] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw in paths:
        path = raw.expanduser()
        if not path.exists():
            skipped.append({"path": str(path), "reason": "missing"})
            continue
        repo_root = git_repo_root(path)
        candidates = [repo_root] if repo_root else scan_for_git_roots(path, scan_depth=scan_depth)
        if not candidates and path.is_dir():
            candidates = [path.resolve()]
        for candidate in candidates:
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            roots.append(candidate.resolve())
            if len(roots) >= max_roots:
                skipped.append({"path": str(path), "reason": f"max_roots_reached:{max_roots}"})
                return roots, skipped
    return roots, skipped


def git_repo_root(path: Path) -> Path | None:
    try:
        root = git_root(path)
    except Exception:
        return None
    if (root / ".git").exists() or git_value(root, "rev-parse", "--show-toplevel"):
        return root.resolve()
    return None


def scan_for_git_roots(path: Path, *, scan_depth: int) -> list[Path]:
    found: list[Path] = []
    base = path.resolve()
    if not base.is_dir():
        return found
    for current, dirs, _files in os.walk(base):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(base).parts)
        except ValueError:
            depth = 0
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if depth > scan_depth:
            dirs[:] = []
            continue
        if (current_path / ".git").exists():
            found.append(current_path.resolve())
            dirs[:] = []
    return found


def write_home_workspace_files() -> list[str]:
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    readme = home / "README.md"
    if not readme.exists():
        readme.write_text(home_readme(), encoding="utf-8")
        written.append(str(readme))

    config = home / "config.toml"
    if not config.exists():
        config.write_text(default_config_toml(), encoding="utf-8")
        written.append(str(config))

    schema_dir = home / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    export_example = schema_dir / "xmem-export.cards.example.jsonl"
    if not export_example.exists():
        export_example.write_text(export_example_jsonl(), encoding="utf-8")
        written.append(str(export_example))

    cards_readme = home / "cards" / "README.md"
    if not cards_readme.exists():
        cards_readme.parent.mkdir(parents=True, exist_ok=True)
        cards_readme.write_text(cards_readme_text(), encoding="utf-8")
        written.append(str(cards_readme))

    return written


def create_memory_repo(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    root = path.expanduser().resolve()
    data: dict[str, Any] = {"root": str(root), "files_written": [], "project": None}
    if dry_run:
        return data

    root.mkdir(parents=True, exist_ok=True)
    files = {
        root / "README.md": memory_repo_readme(),
        root / "cards" / "README.md": cards_readme_text(),
        root / "exports" / "xmem-export.cards.example.jsonl": export_example_jsonl(),
        root / "AGENTS.md": memory_repo_agents(),
    }
    for target, text in files.items():
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        data["files_written"].append(str(target))

    project = init_project(root, project_id=slugify(root.name or "xmem-memory"), aliases=["xmem memory", "project memory"])
    data["project"] = {"project_id": project.get("project_id", ""), "root": project.get("root", str(root)), "truth": str(root / ".xmem")}
    return data


def setup_next_steps(result: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    if not result.get("discovered_roots"):
        steps.append("run xmem setup inside a repo, or pass project roots with xmem setup --root <path>")
    elif result.get("dry_run"):
        steps.append("rerun without --dry-run to write ~/.xmem and register discovered roots")
    else:
        steps.append("run xmem sync to rebuild the generated registry after source changes")
        steps.append("use xmem context <query> before broad repo search")
        steps.append("add small .xmem/cards/*.yaml only for durable rules, methods, relations, or corrections")
    if not result.get("memory_repo"):
        steps.append("optional: create a shared memory repo with xmem setup --memory-repo ~/xmem-memory")
    return steps


def home_readme() -> str:
    return """# xmem workspace\n\nThis folder is the local xmem control plane. It is not the only source of truth.\n\nTruth can live in:\n\n- repo-local `.xmem/project.yaml` and `.xmem/cards/*.yaml`;\n- source code, git history, README, ADR, CONTEXT, OpenSpec, Spec Kit, or Trellis docs;\n- local bug/release notes or any issue tracker that can export compact cards;\n- explicit human confirmations and runtime checks.\n\n`registry.sqlite` is generated cache. If it looks wrong, rebuild with `xmem sync`.\n\nCommon flow:\n\n1. `xmem setup` in a repo or workspace root.\n2. `xmem sync` after source changes.\n3. `xmem context <query>` before broad search.\n4. `xmem preflight <task>` before implementation or bugfix edits.\n5. Add compact cards only for durable knowledge that should survive future sessions.\n"""


def default_config_toml() -> str:
    return f"""# xmem generic config\nschema = "xmem.config.v1"\ncreated_at = "{utc_now()}"\n\n[truth_owners]\nproject = "repo .xmem/project.yaml, README, docs, or your own wiki export"\nbug = "repo notes, issue tracker export, or .xmem/cards bug-pattern cards"\ncode = "source files, git history, map/codegraph generated indexes"\nruntime = "live checks and runtime APIs"\nxmem = "compact cards, routing refs, freshness, corrections, and generated registry"\n\n[adapters]\n# Optional adapters can write xmem-export.cards.jsonl. None are required.\nproject_wiki = "optional"\nissue_tracker = "optional"\ncode_index = "optional map/codegraph"\n"""


def export_example_jsonl() -> str:
    return (
        '{"id":"project.demo","type":"project.identity","title":"Demo project","status":"verified","confidence":0.9,'
        '"aliases":["demo"],"summary":"Compact project identity exported from a user-owned source.",'
        '"evidence":[{"kind":"repo","path":"/path/to/demo"}]}\n'
        '{"id":"bug-pattern.demo-redaction","type":"rule","title":"Redact secrets before compact output","status":"verified","confidence":0.9,'
        '"aliases":["redact","secret","compact output"],"regression_guard":"Apply redaction before printing agent-facing summaries.",'
        '"evidence":[{"kind":"issue","path":"issues/demo-redaction.md"}]}\n'
    )


def cards_readme_text() -> str:
    return """# xmem cards\n\nUse cards for compact, durable knowledge only. Keep long prose in the owning docs and reference it from `evidence`.\n\nGood cards:\n\n- project identity and aliases;\n- invariant / must-keep behavior;\n- recurring bug pattern and regression guard;\n- method / recipe that future agents should reuse;\n- correction when a previous alias or mapping was wrong.\n\nAvoid copying full wiki pages, raw logs, screenshots, secrets, or large command output into cards.\n"""


def memory_repo_readme() -> str:
    return """# xmem memory repo\n\nThis optional repo can hold shared xmem cards and compact exports for a team or one user across machines.\n\nIt is not required for xmem. Start small:\n\n- `cards/` for durable compact rules and methods;\n- `exports/` for generated `xmem-export.cards.jsonl` files from your own systems;\n- evidence stays in the owning repo, issue tracker, wiki, or runtime system.\n\nRun `xmem sync` after changing cards or exports.\n"""


def memory_repo_agents() -> str:
    return """# Agent rules for this xmem memory repo\n\n- Keep cards compact and evidence-backed.\n- Do not store secrets, raw logs, full wiki pages, or bulky artifacts here.\n- Prefer refs to source files, issues, docs, git commits, or runtime evidence.\n- Treat generated indexes as cache, not truth.\n"""
