from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, List

from . import __version__
from .checks import check_diff
from .context import build_context
from .gain import summarize_gain
from .importers import import_issue_tracking, import_project_wiki
from .project import detect_project, index_local, init_project
from .search import latest_events, search_cards
from .store import connect, rows
from .toon import context_packet, llm_packet
from .util import emit_yaml, git_root, home_dir, real_user_home, utc_now


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xmem", description="Lightweight cross-project truth index for agents")
    p.add_argument("--version", action="version", version=f"xmem {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="Show registry path and index counts")
    status.add_argument("--json", action="store_true")

    init = sub.add_parser("init", help="Initialize .xmem in the current repo")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--project-id", default="")
    init.add_argument("--alias", action="append", default=[])
    init.add_argument("--force", action="store_true")

    sub.add_parser("index", help="Index local .xmem cards into the global registry").add_argument("path", nargs="?", default=".")

    imp = sub.add_parser("import", help="Import read-only sources")
    imp_sub = imp.add_subparsers(dest="source", required=True)
    pw = imp_sub.add_parser("project-wiki", help="Import /Users/xin/project-wiki index")
    pw.add_argument("--path", default="/Users/xin/project-wiki")
    it = imp_sub.add_parser("issue-tracking", help="Import /Users/xin/issue-tracking issue records")
    it.add_argument("--path", default="/Users/xin/issue-tracking")
    cards_imp = imp_sub.add_parser("cards", help="Import card YAML files")
    cards_imp.add_argument("path", nargs="?", default="examples/cards")

    find = sub.add_parser("find", help="Search cards/projects/evidence")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--json", action="store_true")

    ctx = sub.add_parser("context", help="Return compact agent context packet")
    ctx.add_argument("query")
    ctx.add_argument("--limit", type=int, default=8)
    ctx.add_argument("--json", action="store_true")
    ctx.add_argument("--legacy-toon", action="store_true", help="Print the old flat TOON table")

    card = sub.add_parser("card", help="Manage local cards")
    card_sub = card.add_subparsers(dest="card_cmd", required=True)
    cl = card_sub.add_parser("list", help="List local cards")
    cl.add_argument("path", nargs="?", default=".")
    cs = card_sub.add_parser("show", help="Show a local card")
    cs.add_argument("id")
    cs.add_argument("path", nargs="?", default=".")
    cn = card_sub.add_parser("new", help="Create a local card template")
    cn.add_argument("id")
    cn.add_argument("--type", default="method")
    cn.add_argument("--title", default="")
    cn.add_argument("--feature", default="")
    cn.add_argument("--path", default=".")

    chk = sub.add_parser("check", help="Check current diff against local invariants")
    chk.add_argument("path", nargs="?", default=".")
    chk.add_argument("--json", action="store_true")

    gain = sub.add_parser("gain", help="Show xmem savings/guardrail stats")
    gain.add_argument("--json", action="store_true")

    tail = sub.add_parser("tail", help="Show recent registry events")
    tail.add_argument("--limit", type=int, default=10)
    tail.add_argument("--json", action="store_true")

    opn = sub.add_parser("open", help="Open one card by id or query")
    opn.add_argument("id_or_query")
    opn.add_argument("--json", action="store_true")
    opn.add_argument("--body", action="store_true", help="Print full card body")

    rebuild = sub.add_parser("rebuild", help="Rebuild generated SQLite index from file truth sources")
    rebuild.add_argument("--project-wiki", default="/Users/xin/project-wiki")
    rebuild.add_argument("--issue-tracking", default="/Users/xin/issue-tracking")
    rebuild.add_argument("--cards", default="examples/cards")
    rebuild.add_argument("--local", default=".")
    rebuild.add_argument("--skip-project-wiki", action="store_true")
    rebuild.add_argument("--skip-issue-tracking", action="store_true")
    rebuild.add_argument("--skip-cards", action="store_true")
    rebuild.add_argument("--skip-local", action="store_true")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "status":
        return status_cmd(args)
    if args.cmd == "init":
        project = init_project(Path(args.path), args.project_id, args.alias, args.force)
        print(f"initialized {project['project_id']} at {project['root']}")
        print(f"local: {Path(project['root']) / '.xmem'}")
        print(f"global: {home_dir()}")
        return 0
    if args.cmd == "index":
        count = index_local(Path(args.path))
        print(f"indexed {count} local cards")
        return 0
    if args.cmd == "import":
        if args.source == "project-wiki":
            print(json.dumps(import_project_wiki(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "issue-tracking":
            print(json.dumps(import_issue_tracking(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "cards":
            print(json.dumps(import_cards(Path(args.path)), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "find":
        cards = search_cards(args.query, args.limit)
        if args.json:
            print(json.dumps(cards, ensure_ascii=False, indent=2))
        else:
            for i, c in enumerate(cards, 1):
                print(f"{i}. {c['card_id']} [{c['status']}] score={c['score']} source={c['source']}")
                print(f"   {c['title']}")
                if c.get("path"):
                    print(f"   {c['path']}")
        return 0
    if args.cmd == "context":
        current = None
        try:
            root = git_root(Path.cwd())
            current = detect_project(root)
        except Exception:
            pass
        cards = search_cards(args.query, max(args.limit * 4, 20))
        events = latest_events(3)
        if args.json:
            print(json.dumps(build_context(args.query, current, cards, events), ensure_ascii=False, indent=2))
        elif args.legacy_toon:
            print(context_packet(args.query, current, cards, events))
        else:
            print(llm_packet(build_context(args.query, current, cards, events)))
        return 0
    if args.cmd == "card":
        return card_cmd(args)
    if args.cmd == "check":
        result = check_diff(Path(args.path))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"checked {result['checked_cards']} cards at {result['root']}")
            warnings = result.get("warnings") or []
            if warnings:
                print("warnings:")
                for w in warnings:
                    print(f"- {w['card']}: removed {w['term']} ({w['reason']})")
                return 2
            print("ok")
        return 0
    if args.cmd == "gain":
        data = summarize_gain()
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"estimated_tokens_saved: {data['estimated_tokens_saved']}")
            print(f"estimated_bug_prevented: {data['estimated_bug_prevented']}")
            print(f"matches: {data['matches']}")
            print("events:")
            for key, value in sorted(data["events"].items()):
                print(f"- {key}: {value}")
        return 0
    if args.cmd == "tail":
        events = latest_events(args.limit)
        if args.json:
            print(json.dumps(events, ensure_ascii=False, indent=2))
        else:
            for event in events:
                print(f"{event.get('ts')} {event.get('event')} {event.get('project_id')} {event.get('card_id')}")
        return 0
    if args.cmd == "open":
        return open_cmd(args)
    if args.cmd == "rebuild":
        return rebuild_cmd(args)
    return 1


def import_cards(path: Path) -> dict[str, int]:
    from .project import card_from_file
    from .store import log_event, upsert_card

    base = path.expanduser()
    if not base.is_absolute():
        base = Path.cwd() / base
    files = [base] if base.is_file() else sorted(base.glob("*.yaml"))
    count = 0
    with connect() as conn:
        for card_path in files:
            card = card_from_file(card_path, "")
            card["source"] = "card-file"
            card["source_ref"] = str(card_path)
            upsert_card(conn, card)
            count += 1
        log_event(conn, "import.cards", payload={"path": str(base), "cards": count})
        conn.commit()
    return {"cards": count}


def status_cmd(args: argparse.Namespace) -> int:
    from .store import db_path

    db = db_path()
    counts: dict[str, Any] = {}
    if db.exists():
        with connect() as conn:
            for table in ("projects", "cards", "evidence", "aliases", "events"):
                counts[table] = rows(conn, f"SELECT COUNT(*) AS count FROM {table}")[0]["count"]
    data = {
        "xmem_home": str(home_dir()),
        "registry": str(db),
        "registry_exists": db.exists(),
        "real_user_home": str(real_user_home()),
        "counts": counts,
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"xmem_home: {data['xmem_home']}")
        print(f"registry: {data['registry']}")
        print(f"registry_exists: {str(data['registry_exists']).lower()}")
        print(f"real_user_home: {data['real_user_home']}")
        if counts:
            print("counts:")
            for key, value in counts.items():
                print(f"- {key}: {value}")
    return 0


def open_cmd(args: argparse.Namespace) -> int:
    with connect() as conn:
        found = rows(conn, "SELECT * FROM cards WHERE card_id = ?", (args.id_or_query,))
    matches = [] if found else search_cards(args.id_or_query, 1)
    card = found[0] if found else (matches[0] if matches else None)
    if not card:
        raise SystemExit(f"card not found: {args.id_or_query}")
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0
    if args.body:
        print(card.get("body", ""), end="" if str(card.get("body", "")).endswith("\n") else "\n")
        return 0
    print(f"id: {card.get('card_id','')}")
    print(f"type: {card.get('type','')}")
    print(f"title: {card.get('title','')}")
    print(f"truth: {card.get('status','')} confidence={card.get('confidence','')}")
    print(f"source: {card.get('source','')}")
    print(f"source_ref: {card.get('source_ref') or card.get('path','')}")
    body = str(card.get("body", ""))
    excerpt = "\n".join(body.splitlines()[:80])
    print("body_excerpt:")
    print(excerpt)
    return 0


def rebuild_cmd(args: argparse.Namespace) -> int:
    from .store import db_path

    db = db_path()
    if db.exists():
        db.unlink()
    result: dict[str, Any] = {"reset": str(db)}
    if not args.skip_local:
        result["local_cards"] = index_local(Path(args.local))
    if not args.skip_cards:
        result["cards"] = import_cards(Path(args.cards))
    if not args.skip_project_wiki:
        pw = Path(args.project_wiki)
        result["project_wiki"] = import_project_wiki(pw) if (pw / "data" / "project-hub.index.json").exists() else {"skipped": str(pw)}
    if not args.skip_issue_tracking:
        it = Path(args.issue_tracking)
        result["issue_tracking"] = import_issue_tracking(it) if (it / "issues").exists() else {"skipped": str(it)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def card_cmd(args: argparse.Namespace) -> int:
    root = git_root(Path(args.path))
    cards_dir = root / ".xmem" / "cards"
    if args.card_cmd == "list":
        for path in sorted(cards_dir.glob("*.yaml")):
            print(path.name)
        return 0
    if args.card_cmd == "show":
        candidates = [cards_dir / f"{args.id}.yaml", cards_dir / args.id]
        for path in candidates:
            if path.exists():
                print(path.read_text(encoding="utf-8"), end="")
                return 0
        raise SystemExit(f"card not found: {args.id}")
    if args.card_cmd == "new":
        cards_dir.mkdir(parents=True, exist_ok=True)
        cid = args.id
        path = cards_dir / f"{cid}.yaml"
        if path.exists():
            raise SystemExit(f"card exists: {path}")
        data: dict[str, Any] = {
            "id": cid,
            "type": args.type,
            "title": args.title or cid,
            "scope": {"feature": args.feature, "project": root.name},
            "aliases": [],
            "truth": {"status": "inferred", "confidence": 0.4, "basis": [], "last_checked_at": utc_now()},
            "summary": "Fill the durable method/rule here.",
            "must_include": [],
            "checks": [],
            "evidence": [],
        }
        path.write_text(emit_yaml(data) + "\n", encoding="utf-8")
        print(path)
        return 0
    return 1
