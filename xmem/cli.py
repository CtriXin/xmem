from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, List

from . import __version__
from .checks import check_diff
from .context import build_context, canonical_queries_from_corrections
from .gain import format_gain_dashboard, record_gain_confirmation, summarize_gain
from .hooks import outbox_counts, run_hook
from .importers import (
    import_bug_patterns,
    import_context_docs,
    import_issue_tracking,
    import_openspec,
    import_project_memory_roots,
    import_project_memory_sources,
    import_project_wiki,
    import_speckit,
    import_trellis,
    import_xmem_export,
)
from .project import detect_project, index_local, init_project
from .preflight import build_preflight
from .search import latest_events, search_cards
from .source_check import check_source_exports, compact_source_health
from .sources import audit_local_sources, index_registered_sources, load_sources, register_local_root, registered_roots, sources_path
from .store import connect, rows
from .toon import context_packet, llm_packet, preflight_packet
from .util import emit_yaml, git_root, home_dir, real_user_home, utc_now


class ChineseHelpFormatter(argparse.RawTextHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, prefix or "用法: ")


class XmemArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", ChineseHelpFormatter)
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = "参数"
        self._optionals.title = "选项"
        if add_help:
            self.add_argument("-h", "--help", action="help", help="显示帮助并退出")


def build_parser() -> argparse.ArgumentParser:
    p = XmemArgumentParser(
        prog="xmem",
        usage="xmem <命令> [选项]",
        description="xmem：给 Agent 用的轻量跨项目 memory router / truth index。",
        epilog="常用入口：xmem status / sync / context / preflight / check / gain / help",
    )
    p.add_argument("--version", action="version", version=f"xmem {__version__}", help="显示版本号并退出")
    p._positionals.title = "命令"
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<命令>", prog="xmem", parser_class=XmemArgumentParser)

    sub.add_parser("help", help="显示最常用命令卡片")

    status = sub.add_parser("status", help="查看索引位置、数量和 source 状态")
    status.add_argument("--json", action="store_true", help="输出 JSON")

    sync = sub.add_parser("sync", help="从文件 truth sources 刷新 SQLite index")
    sync.add_argument("--json", action="store_true", help="输出 JSON")

    new = sub.add_parser("new", help="给当前/指定文件夹创建或刷新 .xmem")
    new.add_argument("path", nargs="?", default=".")
    new.add_argument("--json", action="store_true", help="输出 JSON")

    init = sub.add_parser("init", help="初始化当前 repo 的 .xmem")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--project-id", default="")
    init.add_argument("--alias", action="append", default=[])
    init.add_argument("--force", action="store_true", help="允许覆盖已有 .xmem")

    sub.add_parser("index", help="把本地 .xmem cards 写入全局 index").add_argument("path", nargs="?", default=".")

    imp = sub.add_parser("import", help="导入 read-only sources")
    imp_sub = imp.add_subparsers(dest="source", required=True, metavar="<source>", parser_class=XmemArgumentParser)
    imp_sub.title = "source"
    pw = imp_sub.add_parser("project-wiki", help="导入 /Users/xin/project-wiki index")
    pw.add_argument("--path", default="/Users/xin/project-wiki")
    it = imp_sub.add_parser("issue-tracking", help="导入 /Users/xin/issue-tracking issue records")
    it.add_argument("--path", default="/Users/xin/issue-tracking")
    cards_imp = imp_sub.add_parser("cards", help="导入 card YAML 文件")
    cards_imp.add_argument("path", nargs="?", default="examples/cards")
    export_imp = imp_sub.add_parser("export", help="导入 xmem-export.cards.jsonl")
    export_imp.add_argument("path", nargs="?", default="xmem-export.cards.jsonl")
    patterns_imp = imp_sub.add_parser("bug-patterns", help="导入 issue bug-patterns.jsonl")
    patterns_imp.add_argument("path", nargs="?", default="bug-patterns.jsonl")
    ctx_imp = imp_sub.add_parser("context-docs", help="导入 CONTEXT.md 和 ADR Markdown")
    ctx_imp.add_argument("path", nargs="?", default=".")
    openspec_imp = imp_sub.add_parser("openspec", help="导入 OpenSpec specs / changes")
    openspec_imp.add_argument("path", nargs="?", default=".")
    speckit_imp = imp_sub.add_parser("speckit", help="导入 Spec Kit specs / plans / tasks / constitution")
    speckit_imp.add_argument("path", nargs="?", default=".")
    trellis_imp = imp_sub.add_parser("trellis", help="导入 Trellis specs / tasks / workspace memory")
    trellis_imp.add_argument("path", nargs="?", default=".")
    memory_imp = imp_sub.add_parser("project-memory", help="导入已知 project memory / spec sources")
    memory_imp.add_argument("path", nargs="?", default=".")

    find = sub.add_parser("find", help="搜索 cards / projects / evidence")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--json", action="store_true", help="输出 JSON")

    ctx = sub.add_parser("context", help="返回给 LLM 读的紧凑 context packet")
    ctx.add_argument("query")
    ctx.add_argument("--limit", type=int, default=8)
    ctx.add_argument("--json", action="store_true", help="输出 JSON")
    ctx.add_argument("--legacy-toon", action="store_true", help="输出旧版 flat TOON table")

    preflight = sub.add_parser("preflight", help="开发/修 bug 前读取历史坑、invariant 和 required checks")
    preflight.add_argument("query")
    preflight.add_argument("--limit", type=int, default=8)
    preflight.add_argument("--json", action="store_true", help="输出 JSON")

    card = sub.add_parser("card", help="管理本地 cards")
    card_sub = card.add_subparsers(dest="card_cmd", required=True, metavar="<操作>", parser_class=XmemArgumentParser)
    card_sub.title = "操作"
    cl = card_sub.add_parser("list", help="列出本地 cards")
    cl.add_argument("path", nargs="?", default=".")
    cs = card_sub.add_parser("show", help="查看本地 card")
    cs.add_argument("id")
    cs.add_argument("path", nargs="?", default=".")
    cn = card_sub.add_parser("new", help="创建本地 card 模板")
    cn.add_argument("id")
    cn.add_argument("--type", default="method")
    cn.add_argument("--title", default="")
    cn.add_argument("--feature", default="")
    cn.add_argument("--path", default=".")

    chk = sub.add_parser("check", help="用本地/索引 invariant 检查当前 diff")
    chk.add_argument("path", nargs="?", default=".")
    chk.add_argument("--sources", action="store_true", help="校验 Project Wiki / Issue Record xmem exports")
    chk.add_argument("--json", action="store_true", help="输出 JSON")

    gain = sub.add_parser("gain", help="查看 xmem telemetry / 收益口径 / guardrail 统计")
    gain.add_argument("--json", action="store_true", help="输出 JSON")
    gain.add_argument("--no-color", action="store_true", help="关闭 dashboard ANSI 颜色")
    gain.add_argument("--limit", type=int, default=None, help="只读取最近 N 条 gain log；默认读取全部")
    gain_sub = gain.add_subparsers(dest="gain_cmd", metavar="<操作>", parser_class=XmemArgumentParser)
    gain_sub.title = "操作"
    gain_show = gain_sub.add_parser("show", help="显示 xmem telemetry 和粗估收益")
    gain_show.add_argument("--json", action="store_true", help="输出 JSON")
    gain_show.add_argument("--no-color", action="store_true", help="关闭 dashboard ANSI 颜色")
    gain_show.add_argument("--limit", type=int, default=None, help="只读取最近 N 条 gain log；默认读取全部")
    gain_confirm = gain_sub.add_parser("confirm", help="确认一次 gain/outcome 信号")
    gain_confirm.add_argument("query")
    gain_confirm.add_argument("--note", default="")
    gain_confirm.add_argument("--task", default="")
    gain_confirm.add_argument("--actual-tokens-saved", type=int, default=0)
    gain_confirm.add_argument("--bug-prevented", action="store_true", help="标记这次避免了 bug")
    gain_confirm.add_argument("--json", action="store_true", help="输出 JSON")
    gain_reject = gain_sub.add_parser("reject", help="标记一次被高估/错误的 gain 信号")
    gain_reject.add_argument("query")
    gain_reject.add_argument("--note", default="")
    gain_reject.add_argument("--task", default="")
    gain_reject.add_argument("--json", action="store_true", help="输出 JSON")

    tail = sub.add_parser("tail", help="查看最近 registry events")
    tail.add_argument("--limit", type=int, default=10)
    tail.add_argument("--json", action="store_true", help="输出 JSON")

    opn = sub.add_parser("open", help="按 id 或 query 打开一个 card / evidence 摘要")
    opn.add_argument("id_or_query")
    opn.add_argument("--json", action="store_true", help="输出 JSON")
    opn.add_argument("--body", action="store_true", help="输出完整 card body")

    why = sub.add_parser("why", help="解释为什么 xmem 匹配这个 query")
    why.add_argument("query")
    why.add_argument("--json", action="store_true", help="输出 JSON")

    fix = sub.add_parser("fix", help="记录 alias 纠错或争议")
    fix.add_argument("entity", nargs="?")
    fix.add_argument("items", nargs="*", help="可写 wrong=... correct=... basis=...，否则按提示回答")
    fix.add_argument("--json", action="store_true", help="输出 JSON")

    hook = sub.add_parser("hook", help="Agent hook：捕获/同步 durable work memory")
    hook.add_argument("event", help="start, note, finish, fix, bug, release, deploy, decision, status")
    hook.add_argument("text", nargs="*", help="Agent 写的短摘要")
    hook.add_argument("--path", default=".")
    hook.add_argument("--dest", action="append", default=[], choices=["auto", "xmem", "project-wiki", "issue-tracking", "all"])
    hook.add_argument("--target", default="", help="已知时填写 Project Wiki target entity id")
    hook.add_argument("--verified", action="store_true", help="标记为 verified outcome")
    hook.add_argument("--json", action="store_true", help="输出 JSON")

    rebuild = sub.add_parser("rebuild", help="从文件 truth sources 重建 generated SQLite index")
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
        elif args.source == "export":
            print(json.dumps(import_xmem_export(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "bug-patterns":
            print(json.dumps(import_bug_patterns(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "context-docs":
            print(json.dumps(import_context_docs(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "openspec":
            print(json.dumps(import_openspec(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "speckit":
            print(json.dumps(import_speckit(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "trellis":
            print(json.dumps(import_trellis(Path(args.path)), ensure_ascii=False, indent=2))
        elif args.source == "project-memory":
            print(json.dumps(import_project_memory_sources(Path(args.path)), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "find":
        cards = search_cards(args.query, args.limit, gain_event="find")
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
        cards = search_cards(args.query, max(args.limit * 4, 20), gain_event="context")
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
    if args.cmd == "preflight":
        current = None
        try:
            root = git_root(Path.cwd())
            current = detect_project(root)
        except Exception:
            pass
        cards = search_cards(args.query, max(args.limit * 4, 20), gain_event="preflight")
        for expanded_query in canonical_queries_from_corrections(args.query, cards):
            cards = merge_cards(cards, search_cards(expanded_query, max(args.limit * 2, 10), record_gain=False))
        events = latest_events(3)
        packet = build_preflight(args.query, current, cards, events)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(preflight_packet(packet))
        return 0
    if args.cmd == "card":
        return card_cmd(args)
    if args.cmd == "check":
        if args.sources:
            result = check_source_exports()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("\n".join(compact_source_health(result)))
            return 2 if result.get("errors") else 0
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
        return gain_cmd(args)
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
                "xmem 常用命令：",
                "- xmem status              # 查看索引状态、source 健康度、outbox",
                "- xmem sync                # 刷新索引；从 Project Wiki / Issue Record / 本地 cards 重建",
                "- xmem context <query>     # 查历史项目、方法、证据，返回 LLM 好读 packet",
                "- xmem preflight <query>   # 开发/修 bug 前查历史坑、must_keep、required checks",
                "- xmem check               # 改完前检查 invariant / rule / guardrail",
                "- xmem gain                # 查看 telemetry、粗估收益、确认收益口径",
                "- xmem why <query>         # 解释为什么匹配",
                "- xmem open <id|query>     # 打开 card / evidence 摘要",
                "- xmem new                 # 新项目/新文件夹初始化并注册",
                "- xmem fix                 # 记录 alias 纠错或争议",
                "",
                "Agent 内部：hook / gain confirm / gain reject 会自动记录 outcome 或 outbox，不需要日常记。",
                "",
                "truth 规则：Project Wiki / Issue Record / code / files 是 source truth；SQLite 只是 index/cache。",
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
        "source_exports": check_source_exports(),
        "local_source_count": len(load_sources().get("local_roots", [])),
        "local_source_audit": audit_local_sources(),
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
        audit = data.get("local_source_audit") or {}
        if audit:
            print(
                "local_cards: "
                f"cards={audit.get('cards', 0)} "
                f"knowledge={audit.get('knowledge_cards', 0)} "
                f"tracked={audit.get('tracked_cards', 0)} "
                f"local_only={audit.get('local_only_cards', 0)} "
                f"local_only_knowledge={audit.get('local_only_knowledge_cards', 0)} "
                f"ignored={audit.get('ignored_cards', 0)} "
                f"untracked={audit.get('untracked_cards', 0)}"
            )
            if audit.get("local_only_knowledge_cards"):
                print("local_card_warning: some non-identity .xmem/cards are local-only and not portable via git")
                for item in [d for d in audit.get("details", []) if d.get("local_only_knowledge_cards")][:3]:
                    print(f"- {item.get('root')}: {item.get('local_only_knowledge_cards')} local-only knowledge cards ({item.get('status')})")
        outbox = data.get("outbox", {})
        print(f"outbox: project_wiki={outbox.get('project_wiki', 0)} issue_tracking={outbox.get('issue_tracking', 0)}")
        source_exports = data.get("source_exports") or {}
        print(
            "source_exports: "
            f"{source_exports.get('status', 'unknown')} "
            f"errors={source_exports.get('errors', 0)} "
            f"warnings={source_exports.get('warnings', 0)} "
            f"optional_missing={source_exports.get('optional_missing', 0)} "
            f"stale_exports={source_exports.get('stale_exports', 0)}"
        )
        counts = data.get("counts", {})
        if counts:
            print("counts:")
            for key, value in counts.items():
                print(f"- {key}: {value}")
    return 0


def why_cmd(args: argparse.Namespace) -> int:
    cards = search_cards(args.query, 5, gain_event="why")
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
    matches = [] if found else search_cards(args.id_or_query, 1, gain_event="open")
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


def gain_cmd(args: argparse.Namespace) -> int:
    gain_cmd_name = args.gain_cmd or "show"
    if gain_cmd_name == "confirm":
        row = record_gain_confirmation(
            "confirmed",
            args.query,
            note=args.note,
            task=args.task,
            actual_tokens_saved=args.actual_tokens_saved,
            bug_prevented=args.bug_prevented,
        )
        if args.json:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            print(f"gain confirmed: {args.query}")
        return 0
    if gain_cmd_name == "reject":
        row = record_gain_confirmation("rejected", args.query, note=args.note, task=args.task)
        if args.json:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            print(f"gain rejected: {args.query}")
        return 0
    data = summarize_gain(limit=args.limit)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        use_color = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")
        print(format_gain_dashboard(data, color=use_color))
    return 0


def rebuild_data(args: argparse.Namespace) -> dict[str, Any]:
    from .store import db_path

    db = db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    temp = db.with_name(f"{db.name}.tmp-{os.getpid()}")
    if temp.exists():
        temp.unlink()
    result: dict[str, Any] = {"rebuilt": str(db), "temp": str(temp)}
    previous = os.environ.get("XMEM_REGISTRY_PATH")
    os.environ["XMEM_REGISTRY_PATH"] = str(temp)
    try:
        if not args.skip_local:
            local_root = git_root(Path(args.local))
            if (local_root / ".xmem").exists():
                register_local_root(local_root, "xmem.sync")
                result["local_sources"] = index_registered_sources([local_root])
            else:
                result["local_sources"] = index_registered_sources()
            result["project_memory"] = import_project_memory_roots(registered_roots([local_root]))
        if not args.skip_cards:
            result["cards"] = import_cards(Path(args.cards))
            result["global_cards"] = import_cards(home_dir() / "cards")
        if not args.skip_project_wiki:
            pw = Path(args.project_wiki)
            has_project_wiki_source = (pw / "data" / "project-hub.index.json").exists() or (pw / "data" / "xmem-export.cards.jsonl").exists()
            result["project_wiki"] = import_project_wiki(pw) if has_project_wiki_source else {"skipped": str(pw)}
        if not args.skip_issue_tracking:
            it = Path(args.issue_tracking)
            has_issue_source = (
                (it / "issues").exists()
                or (it / "index" / "xmem-export.cards.jsonl").exists()
                or (it / "index" / "bug-patterns.jsonl").exists()
            )
            result["issue_tracking"] = import_issue_tracking(it) if has_issue_source else {"skipped": str(it)}
        os.replace(temp, db)
        result["atomic_swap"] = True
        return result
    finally:
        if previous is None:
            os.environ.pop("XMEM_REGISTRY_PATH", None)
        else:
            os.environ["XMEM_REGISTRY_PATH"] = previous
        if temp.exists():
            temp.unlink()


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
