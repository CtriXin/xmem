from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, List

from . import __version__
from .checks import check_diff
from .context import build_context, canonical_queries_from_corrections
from .gain import summarize_gain
from .hooks import outbox_counts, run_hook
from .importers import import_issue_tracking, import_project_wiki
from .project import detect_project, index_local, init_project
from .search import latest_events, search_cards
from .sources import index_registered_sources, load_sources, register_local_root, sources_path
from .store import connect, rows
from .toon import context_packet, llm_packet
from .util import emit_yaml, git_root, home_dir, real_user_home, utc_now


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xmem", description="Lightweight cross-project truth index for agents")
    p.add_argument("--version", action="version", version=f"xmem {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("help", help="Show the short xmem command card")

    status = sub.add_parser("status", help="Show registry path and index counts")
    status.add_argument("--json", action="store_true")

    sync = sub.add_parser("sync", help="Sync/rebuild from file truth sources")
    sync.add_argument("--json", action="store_true")

    new = sub.add_parser("new", help="Create or refresh xmem files for the current/new folder")
    new.add_argument("path", nargs="?", default=".")
    new.add_argument("--json", action="store_true")

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

    why = sub.add_parser("why", help="Explain why xmem matched a query")
    why.add_argument("query")
    why.add_argument("--json", action="store_true")

    fix = sub.add_parser("fix", help="Record a simple alias correction/dispute")
    fix.add_argument("entity", nargs="?")
    fix.add_argument("items", nargs="*", help="Use wrong=... correct=... basis=... or answer prompts")
    fix.add_argument("--json", action="store_true")

    hook = sub.add_parser("hook", help="Agent hook: capture/sync durable work memory")
    hook.add_argument("event", help="start, note, finish, fix, bug, release, deploy, decision, status")
    hook.add_argument("text", nargs="*", help="Short agent-created summary")
    hook.add_argument("--path", default=".")
    hook.add_argument("--dest", action="append", default=[], choices=["auto", "xmem", "project-wiki", "issue-tracking", "all"])
    hook.add_argument("--target", default="", help="Project Wiki target entity id when known")
    hook.add_argument("--verified", action="store_true")
    hook.add_argument("--json", action="store_true")

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
    if args.cmd == "help":
        return help_cmd()
    if args.cmd == "status":
        return status_cmd(args)
    if args.cmd == "sync":
        return sync_cmd(args)
    if args.cmd == "new":
        return new_cmd(args)
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
        for expanded_query in canonical_queries_from_corrections(args.query, cards):
            cards = merge_cards(cards, search_cards(expanded_query, max(args.limit * 2, 10), record_gain=False))
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
            top_queries = data.get("top_queries") or []
            if top_queries:
                print("top_queries:")
                for item in top_queries[:5]:
                    print(f"- {item.get('query')}: {item.get('count')}")
            guardrails = data.get("recent_guardrails") or []
            if guardrails:
                print("recent_guardrails:")
                for item in guardrails[-5:]:
                    print(f"- {item.get('event')}: warnings={item.get('warnings')} matched_cards={item.get('matched_cards')}")
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
    if args.cmd == "why":
        return why_cmd(args)
    if args.cmd == "fix":
        return fix_cmd(args)
    if args.cmd == "hook":
        return hook_cmd(args)
    if args.cmd == "rebuild":
        return rebuild_cmd(args)
    return 1


def merge_cards(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in [*primary, *extra]:
        card_id = str(card.get("card_id") or "")
        key = card_id or str(card.get("path") or id(card))
        if key in seen:
            continue
        seen.add(key)
        merged.append(card)
    merged.sort(key=lambda item: (float(item.get("score") or 0), float(item.get("confidence") or 0)), reverse=True)
    return merged


def help_cmd() -> int:
    print(
        "\n".join(
            [
                "xmem quick commands:",
                "- xmem status              # registry path + counts",
                "- xmem sync                # rebuild from Project Wiki, issue records, known folders",
                "- xmem context <words>     # LLM packet",
                "- xmem why <words>         # why it matched",
                "- xmem open <id|words>     # card/evidence excerpt",
                "- xmem new                 # create/register .xmem for this folder",
                "- xmem fix                 # prompted alias correction/dispute",
                "- xmem gain                # savings stats",
                "- agent hooks              # auto-managed: start/finish/fix -> xmem/wiki/issue queues",
                "",
                "truth: files/cards/wiki/issues/code are source; SQLite is only cache/index",
            ]
        )
    )
    return 0


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_cards_path() -> Path:
    return package_root() / "examples" / "cards"


def sync_cmd(args: argparse.Namespace) -> int:
    data = sync_sources()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("synced")
        print(f"registry: {data.get('status', {}).get('registry', '')}")
        counts = data.get("status", {}).get("counts", {})
        for key in ("projects", "cards", "evidence", "aliases", "events"):
            if key in counts:
                print(f"- {key}: {counts[key]}")
        local_sources = data.get("local_sources", {})
        if local_sources:
            print(f"local_sources: {local_sources.get('roots', 0)} roots, {local_sources.get('cards', 0)} cards")
    return 0


def sync_sources() -> dict[str, Any]:
    class RebuildArgs:
        project_wiki = "/Users/xin/project-wiki"
        issue_tracking = "/Users/xin/issue-tracking"
        cards = str(default_cards_path())
        local = "."
        skip_project_wiki = False
        skip_issue_tracking = False
        skip_cards = False
        skip_local = False

    result = rebuild_data(RebuildArgs())
    result["status"] = registry_status()
    return result


def new_cmd(args: argparse.Namespace) -> int:
    project = init_project(Path(args.path))
    count = index_local(Path(args.path))
    data = {"project": project, "indexed_cards": count, "status": registry_status()}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"project: {project['project_id']}")
        print(f"root: {project['root']}")
        print(f"truth: {Path(project['root']) / '.xmem'}")
        print(f"indexed_cards: {count}")
        print("registered: yes")
        print("basis: git/package/folder evidence; add small cards when durable facts are known")
    return 0


def import_cards(path: Path) -> dict[str, Any]:
    from .project import card_from_file
    from .store import log_event, upsert_card

    base = path.expanduser()
    if not base.is_absolute():
        base = Path.cwd() / base
    if not base.exists():
        return {"cards": 0, "skipped": str(base)}
    files = [base] if base.is_file() else sorted(base.glob("**/*.yaml"))
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


def registry_status() -> dict[str, Any]:
    from .store import db_path

    db = db_path()
    counts: dict[str, Any] = {}
    if db.exists():
        with connect() as conn:
            for table in ("projects", "cards", "evidence", "aliases", "events"):
                counts[table] = rows(conn, f"SELECT COUNT(*) AS count FROM {table}")[0]["count"]
    return {
        "xmem_home": str(home_dir()),
        "registry": str(db),
        "registry_exists": db.exists(),
        "real_user_home": str(real_user_home()),
        "sources": str(sources_path()),
        "local_source_count": len(load_sources().get("local_roots", [])),
        "outbox": outbox_counts(),
        "counts": counts,
    }


def status_cmd(args: argparse.Namespace) -> int:
    data = registry_status()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"xmem_home: {data['xmem_home']}")
        print(f"registry: {data['registry']}")
        print(f"registry_exists: {str(data['registry_exists']).lower()}")
        print(f"real_user_home: {data['real_user_home']}")
        print(f"sources: {data['sources']}")
        print(f"local_sources: {data['local_source_count']}")
        outbox = data.get("outbox", {})
        print(f"outbox: project_wiki={outbox.get('project_wiki', 0)} issue_tracking={outbox.get('issue_tracking', 0)}")
        counts = data.get("counts", {})
        if counts:
            print("counts:")
            for key, value in counts.items():
                print(f"- {key}: {value}")
    return 0


def why_cmd(args: argparse.Namespace) -> int:
    cards = search_cards(args.query, 5)
    data = {"query": args.query, "matches": explain_cards(cards)}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"query: {args.query}")
        for i, item in enumerate(data["matches"], 1):
            print(f"{i}. {item['id']} [{item['truth']}] score={item['score']}")
            print(f"   why: {item['why']}")
            print(f"   source: {item['source_ref']}")
    return 0


def explain_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for card in cards:
        out.append(
            {
                "id": card.get("card_id", ""),
                "title": card.get("title", ""),
                "type": card.get("type", ""),
                "truth": card.get("status", ""),
                "score": card.get("score", 0),
                "why": card.get("why", ""),
                "source_ref": card.get("source_ref") or card.get("path", ""),
            }
        )
    return out


def fix_cmd(args: argparse.Namespace) -> int:
    values = parse_key_items(args.items)
    entity = args.entity or values.get("entity") or prompt("entity/query")
    wrong = values["wrong"] if "wrong" in values else prompt("wrong alias (blank if unknown)", allow_blank=True)
    correct = values["correct"] if "correct" in values else prompt("correct alias (blank if unknown)", allow_blank=True)
    basis = values.get("basis") or prompt("basis", default="human_confirmed" if correct else "user_reported_dispute")
    note = values.get("note") or ""
    card_path = write_fix_card(entity, wrong, correct, basis, note)
    imported = import_cards(card_path)
    data = {"card": str(card_path), "imported": imported, "status": registry_status()}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"wrote: {card_path}")
        print(f"status: {'verified correction' if correct else 'dispute recorded'}")
        print("synced: yes")
    return 0


def parse_key_items(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" in item:
            key, value = item.split("=", 1)
            out[key.strip().lstrip("-")] = value.strip()
    return out


def prompt(label: str, default: str = "", allow_blank: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default:
            return default
        if value or allow_blank:
            return value


def write_fix_card(entity: str, wrong: str, correct: str, basis: str, note: str = "") -> Path:
    from .util import slugify

    status = "verified" if correct else "disputed"
    cid = f"alias-correction.{slugify(entity)}"
    path = home_dir() / "cards" / "corrections" / f"{cid}.yaml"
    data: dict[str, Any] = {
        "id": cid,
        "type": "correction",
        "title": f"{entity} alias correction",
        "scope": {"entity": entity},
        "aliases": [x for x in [entity, wrong, correct] if x],
        "truth": {
            "status": status,
            "confidence": 0.95 if status == "verified" else 0.55,
            "basis": [basis],
            "last_checked_at": utc_now(),
        },
        "summary": "Alias correction/dispute captured by xmem.",
        "wrong_aliases": [wrong] if wrong else [],
        "canonical_aliases": [correct] if correct else [],
        "effect": [
            "Warn when source still contains wrong alias.",
            "Prefer canonical aliases when present.",
            "Do not silently edit upstream Project Wiki; keep this card as truth overlay until source is corrected.",
        ],
        "evidence": [{"kind": basis, "ref": note or "xmem fix"}],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(emit_yaml(data) + "\n", encoding="utf-8")
    return path


def hook_cmd(args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip()
    data = run_hook(args.event, text=text, path=Path(args.path), destinations=args.dest, verified=args.verified, target=args.target)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"hooked: {data.get('event')}")
        project = data.get("project") or {}
        if project:
            print(f"project: {project.get('project_id', '')}")
        if data.get("card"):
            print(f"card: {data['card']}")
        if data.get("destinations"):
            print(f"destinations: {', '.join(data['destinations'])}")
        outbox = data.get("outbox") or {}
        for name, item in outbox.items():
            if isinstance(item, dict):
                print(f"{name}: {item.get('status')} {item.get('path')}")
        counts = data.get("outbox_counts") or data.get("outbox") or {}
        if "project_wiki" in counts or "issue_tracking" in counts:
            print(f"outbox: project_wiki={counts.get('project_wiki', 0)} issue_tracking={counts.get('issue_tracking', 0)}")
        if data.get("matches"):
            print(f"matches: {len(data['matches'])}")
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


def rebuild_data(args: argparse.Namespace) -> dict[str, Any]:
    from .store import db_path

    db = db_path()
    if db.exists():
        db.unlink()
    result: dict[str, Any] = {"reset": str(db)}
    if not args.skip_local:
        local_root = git_root(Path(args.local))
        if (local_root / ".xmem").exists():
            register_local_root(local_root, "xmem.sync")
            result["local_sources"] = index_registered_sources([local_root])
        else:
            result["local_sources"] = index_registered_sources()
    if not args.skip_cards:
        result["cards"] = import_cards(Path(args.cards))
        result["global_cards"] = import_cards(home_dir() / "cards")
    if not args.skip_project_wiki:
        pw = Path(args.project_wiki)
        result["project_wiki"] = import_project_wiki(pw) if (pw / "data" / "project-hub.index.json").exists() else {"skipped": str(pw)}
    if not args.skip_issue_tracking:
        it = Path(args.issue_tracking)
        result["issue_tracking"] = import_issue_tracking(it) if (it / "issues").exists() else {"skipped": str(it)}
    return result


def rebuild_cmd(args: argparse.Namespace) -> int:
    result = rebuild_data(args)
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
