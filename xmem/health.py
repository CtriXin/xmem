from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .sources import load_sources
from .util import git_root, real_user_home


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _iso_from_epoch(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except Exception:
        return None


def _base_home_for_backup() -> Path:
    # Tests and isolated sessions set XMEM_HOME; keep backup health scoped there so
    # local test runs do not accidentally inspect the user's real LaunchAgent state.
    explicit_xmem = os.environ.get("XMEM_HOME")
    if explicit_xmem:
        return Path(explicit_xmem).expanduser().parent
    return real_user_home()


def _git_count(repo: Path, revision: str) -> int | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--count", revision],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip() or "0")
    except ValueError:
        return None


def backup_health(now: int | None = None) -> Dict[str, Any]:
    base_home = _base_home_for_backup()
    state_dir = Path(os.environ.get("XMEM_BACKUP_STATE_DIR", str(base_home / ".xmem-backup"))).expanduser()
    repo = Path(os.environ.get("XMEM_BACKUP_REPO", str(base_home / "auto-skills" / "CtriXin-repo" / "xmem-backup"))).expanduser()
    interval = int(os.environ.get("XMEM_BACKUP_INTERVAL_SECONDS", "21600") or "21600")
    stale_after = int(os.environ.get("XMEM_BACKUP_STALE_AFTER_SECONDS", str(interval * 4)) or str(interval * 4))
    launch_agent = base_home / "Library" / "LaunchAgents" / "com.ctrinxin.xmem-backup.plist"
    pending_file = state_dir / "pending-sync"
    last_success_file = state_dir / "last-success-epoch"
    current = now if now is not None else _now_epoch()

    last_success = _read_int(last_success_file)
    age_seconds = current - last_success if last_success else None
    pending_text = ""
    if pending_file.exists():
        try:
            pending_text = pending_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            pending_text = "pending"

    configured = state_dir.exists() or repo.exists() or launch_agent.exists() or bool(os.environ.get("XMEM_BACKUP_REPO"))
    status = "not_configured"
    severity = "info"
    next_action = ""
    if pending_text:
        status = "pending"
        severity = "warn"
        next_action = "backup sync is pending; it should retry automatically when network is available"
    elif last_success and age_seconds is not None and age_seconds > stale_after:
        status = "stale"
        severity = "warn"
        next_action = "backup mirror is stale; check launchd/network or run backup sync"
    elif last_success:
        status = "ok"
        severity = "ok"
    elif configured:
        status = "unknown"
        severity = "warn"
        next_action = "backup automation exists but has no recorded success yet"

    remote_ahead = _git_count(repo, "origin/main..HEAD") if repo.exists() else None
    if remote_ahead and remote_ahead > 0 and severity == "ok":
        status = "local_ahead"
        severity = "warn"
        next_action = "backup mirror has local commits that still need push"

    return {
        "status": status,
        "severity": severity,
        "state_dir": str(state_dir),
        "repo": str(repo),
        "repo_exists": repo.exists(),
        "launch_agent": str(launch_agent),
        "launch_agent_exists": launch_agent.exists(),
        "interval_seconds": interval,
        "stale_after_seconds": stale_after,
        "last_success_epoch": last_success,
        "last_success_at": _iso_from_epoch(last_success) if last_success else "",
        "age_seconds": age_seconds,
        "pending": bool(pending_text),
        "pending_reason": pending_text,
        "local_commits_ahead": remote_ahead,
        "next_action": next_action,
    }


def local_card_suggestions(audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    for detail in audit.get("details", []) or []:
        count = int(detail.get("local_only_knowledge_cards") or 0)
        if count <= 0:
            continue
        ignored = int(detail.get("ignored_knowledge_cards") or 0)
        untracked = int(detail.get("untracked_knowledge_cards") or 0)
        if ignored:
            reason = "ignored_by_git"
            fix = "allow .xmem/cards/*.yaml in the owning repo .gitignore, then track reusable knowledge cards"
        elif untracked:
            reason = "untracked_by_git"
            fix = "track reusable knowledge cards in the owning repo, or move personal-only knowledge to ~/.xmem/cards"
        else:
            reason = "not_tracked"
            fix = "track or export the knowledge card from its owning source"
        suggestions.append(
            {
                "root": detail.get("root", ""),
                "status": detail.get("status", ""),
                "count": count,
                "reason": reason,
                "sample": detail.get("sample_local_only") or [],
                "suggested_fix": fix,
            }
        )
    return suggestions


def current_repo_registration(cwd: Path | None = None) -> Dict[str, Any]:
    base = (cwd or Path.cwd()).expanduser()
    try:
        root = git_root(base)
    except Exception:
        root = base.resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        proc = None
    if not proc or proc.returncode != 0:
        return {"status": "no_git", "root": str(root), "registered": False}

    git_root_text = str(Path(proc.stdout.strip()).resolve())
    registered = []
    for item in load_sources().get("local_roots", []):
        raw = item.get("root")
        if not raw:
            continue
        try:
            registered.append(str(Path(str(raw)).expanduser().resolve()))
        except Exception:
            registered.append(str(raw))
    is_registered = git_root_text in registered
    return {
        "status": "registered" if is_registered else "unregistered",
        "root": git_root_text,
        "registered": is_registered,
    }


def build_doctor_report(status: Dict[str, Any], cwd: Path | None = None) -> Dict[str, Any]:
    source_exports = status.get("source_exports") or {}
    audit = status.get("local_source_audit") or {}
    outbox = status.get("outbox") or {}
    backup = backup_health()
    current_repo = current_repo_registration(cwd)
    suggestions = local_card_suggestions(audit)

    components: List[Dict[str, Any]] = []

    counts = status.get("counts") or {}
    registry_severity = "ok" if status.get("registry_exists") and int(counts.get("cards") or 0) > 0 else "error"
    components.append(
        {
            "name": "registry",
            "severity": registry_severity,
            "status": "ok" if registry_severity == "ok" else "missing_or_empty",
            "summary": f"cards={counts.get('cards', 0)} projects={counts.get('projects', 0)}",
        }
    )

    source_status = str(source_exports.get("status") or "unknown")
    source_severity = "error" if source_exports.get("errors") else ("warn" if source_status in {"warn", "stale"} else "ok")
    components.append(
        {
            "name": "source_exports",
            "severity": source_severity,
            "status": source_status,
            "summary": f"errors={source_exports.get('errors', 0)} warnings={source_exports.get('warnings', 0)} stale={source_exports.get('stale_exports', 0)}",
        }
    )

    code_indexes = status.get("code_indexes") or {}
    codegraph_binary = code_indexes.get("codegraph_binary") or ""
    code_index_count = int(code_indexes.get("indexes") or 0)
    provider_text = ",".join(f"{k}={v}" for k, v in sorted((code_indexes.get("providers") or {}).items())) or "none"
    components.append(
        {
            "name": "code_index_bridge",
            "severity": "ok" if codegraph_binary or code_index_count else "info",
            "status": "available" if codegraph_binary else ("indexed_refs_only" if code_index_count else "not_configured"),
            "summary": f"indexes={code_index_count} providers={provider_text} codegraph_binary={codegraph_binary or 'missing'}",
        }
    )

    local_count = int(audit.get("local_only_knowledge_cards") or 0)
    components.append(
        {
            "name": "local_cards",
            "severity": "warn" if local_count else "ok",
            "status": "local_only" if local_count else "portable",
            "summary": f"knowledge={audit.get('knowledge_cards', 0)} local_only_knowledge={local_count}",
        }
    )

    outbox_count = int(outbox.get("total") or 0)
    components.append(
        {
            "name": "outbox",
            "severity": "warn" if outbox_count else "ok",
            "status": "pending" if outbox_count else "empty",
            "summary": (
                f"project_wiki={outbox.get('project_wiki', 0)} "
                f"issue_tracking={outbox.get('issue_tracking', 0)} "
                f"gain_feedback={outbox.get('gain_feedback', 0)} "
                f"total={outbox_count}"
            ),
        }
    )

    components.append(
        {
            "name": "backup",
            "severity": backup.get("severity", "info"),
            "status": backup.get("status", "unknown"),
            "summary": backup_summary(backup),
        }
    )

    repo_severity = "warn" if current_repo.get("status") == "unregistered" else "ok"
    components.append(
        {
            "name": "current_repo",
            "severity": repo_severity,
            "status": current_repo.get("status", "unknown"),
            "summary": current_repo.get("root", ""),
        }
    )

    overall = "ok"
    if any(item.get("severity") == "error" for item in components):
        overall = "error"
    elif any(item.get("severity") == "warn" for item in components):
        overall = "warn"

    actions = [a for a in status.get("next_actions", []) or [] if a != "no blocking xmem maintenance action"]
    if backup.get("next_action"):
        actions.append(str(backup["next_action"]))
    if current_repo.get("status") == "unregistered":
        actions.append("run xmem new if this repo should be searchable from other projects")
    for suggestion in suggestions:
        sample = ",".join(suggestion.get("sample") or [])
        actions.append(f"{suggestion.get('root')}: {suggestion.get('suggested_fix')} ({sample})")
    if not actions:
        actions.append("no blocking xmem maintenance action")

    return {
        "status": overall,
        "components": components,
        "backup": backup,
        "current_repo": current_repo,
        "local_card_suggestions": suggestions,
        "next_actions": actions,
    }


def backup_summary(backup: Dict[str, Any]) -> str:
    status = backup.get("status", "unknown")
    if status == "ok":
        return f"last_success_at={backup.get('last_success_at', '')} age_seconds={backup.get('age_seconds')}"
    if status == "pending":
        return f"pending_reason={backup.get('pending_reason', '')}"
    if status == "stale":
        return f"last_success_at={backup.get('last_success_at', '')} age_seconds={backup.get('age_seconds')}"
    if status == "not_configured":
        return "backup automation not configured for this XMEM_HOME"
    return backup.get("next_action") or status
