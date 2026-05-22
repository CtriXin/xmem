from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .store import connect, log_event, upsert_card, upsert_project
from .toon import project_snapshot
from .util import append_jsonl, emit_yaml, field_from_text, front_value, git_root, git_value, list_after_key, read_json, slugify, utc_now


def detect_tech_stack(root: Path) -> str:
    parts: List[str] = []
    if (root / "package.json").exists():
        pkg = read_json(root / "package.json", {}) or {}
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        if "next" in deps:
            parts.append("Next.js")
        if "nuxt" in deps or "@nuxt/kit" in deps:
            parts.append("Nuxt")
        if "react" in deps:
            parts.append("React")
        if "vue" in deps:
            parts.append("Vue")
        parts.append("Node")
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        parts.append("Python")
    if (root / "Cargo.toml").exists():
        parts.append("Rust")
    if (root / "go.mod").exists():
        parts.append("Go")
    return " / ".join(dict.fromkeys(parts)) or "unknown"


def package_name(root: Path) -> str:
    pkg = read_json(root / "package.json", {}) if (root / "package.json").exists() else {}
    if isinstance(pkg, dict) and pkg.get("name"):
        return str(pkg["name"])
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="ignore")
        for key in ("name",):
            value = front_value(text, key)
            if value:
                return value
    return ""


def detect_project(root: Path) -> Dict[str, Any]:
    root = git_root(root)
    remote = git_value(root, "remote", "get-url", "origin")
    branch = git_value(root, "branch", "--show-current")
    sha = git_value(root, "rev-parse", "--short", "HEAD")
    pkg_name = package_name(root)
    base = pkg_name or (remote.split(":")[-1].split("/")[-1].removesuffix(".git") if remote else root.name)
    project_id = slugify(base)
    aliases = [root.name]
    if pkg_name and pkg_name not in aliases:
        aliases.append(pkg_name)
    if remote:
        aliases.append(remote.split("/")[-1].removesuffix(".git"))
    return {
        "project_id": project_id,
        "name": base,
        "root": str(root),
        "remote": remote,
        "branch": branch,
        "git_sha": sha,
        "tech_stack": detect_tech_stack(root),
        "aliases": list(dict.fromkeys([a for a in aliases if a])),
        "status": "verified" if remote or sha else "inferred",
        "updated_at": utc_now(),
        "source": "local-init",
    }


def init_project(path: Path, project_id: str = "", aliases: List[str] | None = None, force: bool = False) -> Dict[str, Any]:
    root = git_root(path)
    xdir = root / ".xmem"
    cards_dir = xdir / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    (xdir / "events.jsonl").touch(exist_ok=True)
    project = detect_project(root)
    if project_id:
        project["project_id"] = slugify(project_id)
    if aliases:
        project["aliases"] = list(dict.fromkeys(project.get("aliases", []) + aliases))
    pfile = xdir / "project.yaml"
    if force or not pfile.exists():
        pfile.write_text(emit_yaml(project) + "\n", encoding="utf-8")
    identity = cards_dir / "project.identity.yaml"
    if force or not identity.exists():
        card = default_identity_card(project)
        identity.write_text(emit_yaml(card) + "\n", encoding="utf-8")
    snap = project_snapshot(project, [identity])
    (xdir / "snapshot.toon").write_text(snap + "\n", encoding="utf-8")
    with connect() as conn:
        upsert_project(conn, project)
        upsert_card(conn, card_from_file(identity, project["project_id"]))
        payload = {"root": str(root)}
        log_event(conn, "project.init", project_id=project["project_id"], payload=payload)
        append_jsonl(xdir / "events.jsonl", {"ts": utc_now(), "actor": "xmem", "event": "project.init", "project_id": project["project_id"], "payload": payload})
        conn.commit()
    return project


def default_identity_card(project: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": "project.identity",
        "type": "identity",
        "title": f"{project['name']} identity",
        "scope": {"project": project["project_id"], "repo": project.get("root", "")},
        "aliases": project.get("aliases", []),
        "truth": {
            "status": project.get("status", "inferred"),
            "confidence": 0.9 if project.get("status") == "verified" else 0.5,
            "basis": ["git_observed"],
            "git_sha": project.get("git_sha", ""),
            "last_checked_at": utc_now(),
        },
        "evidence": [{"kind": "repo", "path": project.get("root", ""), "ref": project.get("remote", "")}],
    }


def card_from_file(path: Path, default_project: str = "") -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    cid = front_value(text, "id") or path.stem
    title = front_value(text, "title") or cid
    ctype = front_value(text, "type") or "fact"
    status = front_value(text, "status") or field_from_text(text, "status")
    if not status:
        if "status: verified" in text:
            status = "verified"
        elif "status: inferred" in text:
            status = "inferred"
        else:
            status = "unknown"
    confidence = 0.5
    raw_conf = front_value(text, "confidence") or field_from_text(text, "confidence")
    try:
        confidence = float(raw_conf) if raw_conf else (0.95 if status == "verified" else 0.5)
    except ValueError:
        confidence = 0.5
    aliases = list_after_key(text, "aliases")[:30]
    return {
        "card_id": cid,
        "project_id": default_project or field_from_text(text, "project"),
        "type": ctype,
        "title": title,
        "path": str(path),
        "status": status,
        "confidence": confidence,
        "aliases": aliases,
        "body": text,
        "updated_at": utc_now(),
        "source": "local-card",
        "source_ref": str(path),
    }


def index_local(path: Path) -> int:
    root = git_root(path)
    xdir = root / ".xmem"
    if not xdir.exists():
        return 0
    project = detect_project(root)
    count = 0
    with connect() as conn:
        upsert_project(conn, project)
        for card_path in sorted((xdir / "cards").glob("*.yaml")):
            upsert_card(conn, card_from_file(card_path, project["project_id"]))
            count += 1
        payload = {"cards": count}
        log_event(conn, "project.index", project_id=project["project_id"], payload=payload)
        append_jsonl(xdir / "events.jsonl", {"ts": utc_now(), "actor": "xmem", "event": "project.index", "project_id": project["project_id"], "payload": payload})
        conn.commit()
    return count
