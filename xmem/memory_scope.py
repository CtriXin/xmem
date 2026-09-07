"""Read-only selection of memory behaviour, never execution authorization."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3


def memory_scope(cwd=None, environ=None) -> dict:
    env = os.environ if environ is None else environ
    result = {"schema": "xmem.memory_scope.v1", "context": "legacy", "readonly": True,
              "basis": "no_stride_task", "authority": "none"}
    if env.get("STRIDE_EXECUTION_CONTEXT") == "stride-v1":
        return {**result, "context": "stride-v1", "basis": "explicit_context"}
    try:
        current = Path(cwd or Path.cwd()).expanduser().resolve(strict=True)
        if not current.is_dir():
            return result
        host = next((env.get(key) for key in ("MMS_HOST_HOME", "HOST_HOME", "REAL_HOME", "MMS_REAL_HOME")
                     if env.get(key)), env.get("HOME") or str(Path.home()))
        # MMS may isolate HOME; no config is opened or changed to recover it.
        host = re.sub(r"^(/Users/[^/]+)/\.config/mms/.*$", r"\1", host)
        homes = [Path(host).expanduser() / ".local/share" / name for name in ("stride", "stride-v1", "stride-v3")]
        if env.get("STRIDE_HOME"):
            homes.append(Path(env["STRIDE_HOME"]).expanduser())
        allowed = {home.resolve() for home in homes}
        for workspace in (current, *current.parents):
            if workspace.name != "workspace" or workspace.parent.parent.name != "tasks":
                continue
            task = workspace.parent.name
            home = workspace.parent.parent.parent
            if not re.fullmatch(r"[0-9a-f]{16}", task) or home not in allowed:
                continue
            # Resolved containment + a real task row, not a path/title guess.
            expected = (home / "tasks" / task / "workspace").resolve(strict=True)
            if expected != workspace or not current.is_relative_to(expected):
                continue
            database = home / "stride.db"
            if not database.is_file():
                return {**result, "basis": "stride_database_missing"}
            # WAL must be read, not silently ignored. SQLite can update SHM
            # reader coordination; no Store/schema/business mutation occurs.
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.execute("PRAGMA query_only=ON")
                row = db.execute("SELECT id,status FROM tasks WHERE id=?", (task,)).fetchone()
            if row is not None:
                return {**result, "context": "stride-v1", "basis": "task_workspace",
                        "task_id": row[0], "task_status": row[1], "home": str(home), "workspace": str(expected)}
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error):
        return {**result, "basis": "stride_scope_unavailable"}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    print(json.dumps(memory_scope(args.cwd), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
