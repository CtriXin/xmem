from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, List

from . import __version__
from .agent_hooks import run_agent_hook
from .checks import check_diff
from .code_index import code_index_status, import_code_indexes
from .context import build_context, canonical_queries_from_corrections
from .gain import format_card_gain_dashboard, format_gain_dashboard, record_gain_confirmation, summarize_card_gain, summarize_gain
from .gateway import run_gateway
from .health import backup_health, build_doctor_report
from .hooks import outbox_counts, run_hook
from .maintenance import build_memory_maintenance, format_memory_maintenance
from .memory import (
    build_recall,
    capture_memories,
    forget_memory,
    format_pending_review,
    format_recall,
    promote_memory,
    promote_trellis_memory,
    review_pending,
    supersede_memory,
    synthesize_profile,
)
from .mcp_server import run_mcp_server
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
    import_xmem_outbox,
    import_xmem_export,
)
from .project import detect_project, index_local, init_project
from .preflight import build_preflight
from .resume import build_resume
from .search import latest_events, record_suppression, search_cards
from .setup import setup_workspace
from .source_check import check_source_exports, compact_source_health
from .sources import audit_local_sources, index_registered_sources, load_sources, register_local_root, registered_roots, sources_path
from .store import connect, rows
from .toon import context_packet, gateway_packet, llm_packet, preflight_packet, resume_packet
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

    def _check_value(self, action, value):
        hidden = getattr(action, "_xmem_hidden_choices", set())
        if hidden and value not in action.choices:
            visible_choices = {key: val for key, val in action.choices.items() if key not in hidden}
            original_choices = action.choices
            action.choices = visible_choices
            try:
                return super()._check_value(action, value)
            finally:
                action.choices = original_choices
        return super()._check_value(action, value)


def add_hidden_parser(subparsers, name: str, **kwargs):
    parser = subparsers.add_parser(name, help=argparse.SUPPRESS, **kwargs)
    hidden = getattr(subparsers, "_xmem_hidden_choices", set())
    hidden.add(name)
    subparsers._xmem_hidden_choices = hidden
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if getattr(action, "dest", None) != name
    ]
    return parser


def build_parser() -> argparse.ArgumentParser:
    p = XmemArgumentParser(
        prog="xmem",
        usage="xmem <命令> [选项]",
        description="xmem：给 Agent 用的轻量跨项目 memory router / truth index。",
        epilog="常用入口：xmem status / doctor / sync / context / preflight / check / gain / help",
    )
    p.add_argument("--version", action="version", version=f"xmem {__version__}", help="显示版本号并退出")
    p._positionals.title = "命令"
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<命令>", prog="xmem", parser_class=XmemArgumentParser)

    sub.add_parser("help", help="显示最常用命令卡片")

    status = sub.add_parser("status", help="查看索引位置、数量和 source 状态")
    status.add_argument("--json", action="store_true", help="输出 JSON")

    doctor = sub.add_parser("doctor", help="综合检查 registry、source、backup、outbox 和当前 repo")
    doctor.add_argument("--json", action="store_true", help="输出 JSON")

    sync = sub.add_parser("sync", help="从文件 truth sources 刷新 SQLite index")
    sync.add_argument("--json", action="store_true", help="输出 JSON")

    setup = sub.add_parser("setup", help="泛化初始化：创建 ~/.xmem 工作区并注册项目 roots")
    setup.add_argument("paths", nargs="*", help="项目 repo 或 workspace root；默认当前目录")
    setup.add_argument("--root", action="append", default=[], help="额外项目/工作区 root，可重复")
    setup.add_argument("--scan-depth", type=int, default=2, help="扫描 workspace 内 git repo 的深度，默认 2")
    setup.add_argument("--register-only", action="store_true", help="只注册 roots，不写 repo-local .xmem")
    setup.add_argument("--memory-repo", default="", help="可选：创建一个共享 xmem memory repo")
    setup.add_argument("--max-roots", type=int, default=80, help="最多注册/初始化多少个 root，默认 80")
    setup.add_argument("--no-sync", action="store_true", help="setup 后不自动 sync")
    setup.add_argument("--dry-run", action="store_true", help="只预览会发现哪些 roots，不写文件")
    setup.add_argument("--yes", action="store_true", help="兼容 installer；setup 默认非交互执行")
    setup.add_argument("--json", action="store_true", help="输出 JSON")

    new = sub.add_parser("new", help="给当前/指定文件夹创建或刷新 .xmem")
    new.add_argument("path", nargs="?", default=".")
    new.add_argument("--json", action="store_true", help="输出 JSON")

    init = sub.add_parser("init", help="初始化当前 repo 的 .xmem")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--project-id", default="")
    init.add_argument("--alias", action="append", default=[])
    init.add_argument("--force", action="store_true", help="允许覆盖已有 .xmem")

    index_p = sub.add_parser("index", help="把本地 .xmem cards 写入全局 index")
    index_p.add_argument("path", nargs="?", default=None, help="repo 路径；默认当前目录")
    index_p.add_argument("--cwd", default="", help="兼容 Agent 调用习惯；等同于指定 repo 路径")

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
    openspec_imp = add_hidden_parser(imp_sub, "openspec")
    openspec_imp.add_argument("path", nargs="?", default=".")
    speckit_imp = add_hidden_parser(imp_sub, "speckit")
    speckit_imp.add_argument("path", nargs="?", default=".")
    trellis_imp = add_hidden_parser(imp_sub, "trellis")
    trellis_imp.add_argument("path", nargs="?", default=".")
    memory_imp = imp_sub.add_parser("project-memory", help="导入已知 project memory / spec sources")
    memory_imp.add_argument("path", nargs="?", default=".")
    code_imp = imp_sub.add_parser("code-index", help="导入 map/codegraph 生成索引的轻量 ref")
    code_imp.add_argument("path", nargs="?", default=".")

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
    preflight.add_argument("query", nargs="?", default="")
    preflight.add_argument("--limit", type=int, default=8)
    preflight.add_argument("--fields", nargs="*", default=[], metavar="KEY=VALUE", help="结构化 query 字段，如 domain=... service=... repo=... task=... mode=...")
    for name in ("domain", "service", "repo", "project", "task", "mode"):
        preflight.add_argument(f"--{name}", default="", help=argparse.SUPPRESS)
    preflight.add_argument("--json", action="store_true", help="输出 JSON")

    resume = sub.add_parser("resume", help="接手已有任务：按 issue/domain/service 生成紧凑 task memory")
    resume.add_argument("query", nargs="?", default="")
    resume.add_argument("--limit", type=int, default=8)
    resume.add_argument("--fields", nargs="*", default=[], metavar="KEY=VALUE", help="结构化 query 字段，如 issue=... domain=... service=... repo=... task=...")
    for name in ("issue", "domain", "service", "repo", "project", "task", "mode"):
        resume.add_argument(f"--{name}", default="", help=argparse.SUPPRESS)
    resume.add_argument("--json", action="store_true", help="输出 JSON")

    gateway = sub.add_parser("gateway", help="Agent 入口层：自动判断是否注入 xmem compact memory")
    gateway.add_argument("query", nargs="?", default="")
    gateway.add_argument("--cwd", default=".", help="任务所在工作目录，默认当前目录")
    gateway.add_argument("--event", default="pre-task", help="事件：session-start/pre-task/pre-tool/tool-error/closeout/manual")
    gateway.add_argument("--limit", type=int, default=8)
    gateway.add_argument("--budget", type=int, default=700, help="输出预算提示，默认 700")
    gateway.add_argument("--fields", nargs="*", default=[], metavar="KEY=VALUE", help="结构化字段，如 issue=... domain=... service=... task=...")
    for name in ("issue", "domain", "service", "repo", "project", "task", "mode"):
        gateway.add_argument(f"--{name}", default="", help=argparse.SUPPRESS)
    gateway.add_argument("--format", choices=["toon", "text", "json"], default="toon", help="输出格式，默认 toon")
    gateway.add_argument("--json", action="store_true", help="输出 JSON（等同 --format json）")
    gateway.add_argument("--dry-run", action="store_true", help="只展示 would inject/skip，不代表已注入")

    suppress = add_hidden_parser(sub, "suppress")
    suppress.add_argument("--card", required=True, help="card id")
    suppress.add_argument("--for-query", required=True, help="原 query 或 query_hash")
    suppress.add_argument("--reason", default="irrelevant", help="原因，默认 irrelevant")
    suppress.add_argument("--json", action="store_true", help="输出 JSON")

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
    chk.add_argument("--strict", action="store_true", help="把 warning 也作为失败退出")
    chk.add_argument("--json", action="store_true", help="输出 JSON")

    gain = sub.add_parser("gain", help="查看 xmem telemetry / 收益口径 / guardrail 统计")
    gain.add_argument("--json", action="store_true", help="输出 JSON")
    gain.add_argument("--no-color", action="store_true", help="关闭 dashboard ANSI 颜色")
    gain.add_argument("--summary", action="store_true", help="只显示关键摘要")
    gain.add_argument("--detail", action="store_true", help="兼容选项；gain 默认已显示完整面板")
    gain.add_argument("--limit", type=int, default=None, help="只读取最近 N 条 gain log；默认读取全部")
    gain_sub = gain.add_subparsers(dest="gain_cmd", metavar="<操作>", parser_class=XmemArgumentParser)
    gain_sub.title = "操作"
    gain_show = gain_sub.add_parser("show", help="显示 xmem telemetry 和粗估收益")
    gain_show.add_argument("--json", action="store_true", help="输出 JSON")
    gain_show.add_argument("--no-color", action="store_true", help="关闭 dashboard ANSI 颜色")
    gain_show.add_argument("--summary", action="store_true", help="只显示关键摘要")
    gain_show.add_argument("--detail", action="store_true", help="兼容选项；gain 默认已显示完整面板")
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
    gain_card = gain_sub.add_parser("card", help="解释某个 top_card 为什么高频命中")
    gain_card.add_argument("card_id")
    gain_card.add_argument("--json", action="store_true", help="输出 JSON")
    gain_card.add_argument("--no-color", action="store_true", help="关闭 dashboard ANSI 颜色")
    gain_card.add_argument("--limit", type=int, default=None, help="只读取最近 N 条 gain log；默认读取全部")

    capture = sub.add_parser("capture", help="捕获候选 memory 到 pending queue")
    capture.add_argument("text", nargs="*", help="要捕获的简短内容；也可配合 --from-session")
    capture.add_argument("--from-session", nargs="?", const="-", help="从文件或 stdin 读取 session/artifact 文本")
    capture.add_argument("--type", default="", help="preference/project_fact/decision/invariant/bug_pattern/rejected_option/workflow_lesson/source_pointer")
    capture.add_argument("--scope", default="project", help="user/project/repo/task/workflow/tool/code_style")
    capture.add_argument("--cwd", default=".", help="用于推断 project scope 的目录")
    capture.add_argument("--confidence", type=float, default=0.55)
    capture.add_argument("--evidence-path", default="")
    capture.add_argument("--ttl", default="")
    capture.add_argument("--supersedes", action="append", default=[])
    capture.add_argument("--alias", action="append", default=[])
    capture.add_argument("--json", action="store_true", help="输出 JSON")

    recall = sub.add_parser("recall", help="本地 hybrid recall，输出小 memory packet")
    recall.add_argument("query")
    recall.add_argument("--cwd", default=".")
    recall.add_argument("--limit", type=int, default=8)
    recall.add_argument("--include-pending", action="store_true")
    recall.add_argument("--json", action="store_true", help="输出 JSON")

    profile = sub.add_parser("profile", help="生成 user/project memory profile")
    profile.add_argument("--cwd", default=".")
    profile.add_argument("--no-write", action="store_true")
    profile.add_argument("--json", action="store_true", help="输出 JSON")

    review = sub.add_parser("review-pending", help="查看 pending memories")
    review.add_argument("--cwd", default=".")
    review.add_argument("--all", action="store_true", help="包含全局 pending")
    review.add_argument("--limit", type=int, default=20)
    review.add_argument("--json", action="store_true", help="输出 JSON")

    promote = sub.add_parser("promote", help="把 pending memory 提升为 card")
    promote.add_argument("memory_id")
    promote.add_argument("--cwd", default=".")
    promote.add_argument("--verified", action="store_true", help="显式标记 verified；默认 partial")
    promote.add_argument("--scope", default="", help="覆盖 pending scope")
    promote.add_argument("--json", action="store_true", help="输出 JSON")

    promote_trellis = add_hidden_parser(sub, "promote-trellis")
    promote_trellis.add_argument("--source-card", required=True, help="Trellis card id")
    promote_trellis.add_argument("--decision", choices=["distill", "reject", "keep-pointer"], required=True)
    promote_trellis.add_argument("--decided-by", required=True, help="human:<name>|review:<path>|agent:<model>@<session>")
    promote_trellis.add_argument("--basis", default="", help="裁决依据简述")
    promote_trellis.add_argument("--basis-file", default="", help="裁决依据文件")
    promote_trellis.add_argument("--output-type", default="source_pointer", help="distill 输出 memory type，默认 source_pointer")
    promote_trellis.add_argument("--summary", default="", help="distill 后的新记忆摘要")
    promote_trellis.add_argument("--evidence", default="", help="distill evidence path，默认 Trellis source path")
    promote_trellis.add_argument("--cwd", default=".")
    promote_trellis.add_argument("--json", action="store_true", help="输出 JSON")

    forget = add_hidden_parser(sub, "forget")
    forget.add_argument("memory_id")
    forget.add_argument("--cwd", default=".")
    forget.add_argument("--reason", default="")
    forget.add_argument("--json", action="store_true", help="输出 JSON")

    supersede = add_hidden_parser(sub, "supersede")
    supersede.add_argument("old_id")
    supersede.add_argument("new_id")
    supersede.add_argument("--cwd", default=".")
    supersede.add_argument("--reason", default="")
    supersede.add_argument("--json", action="store_true", help="输出 JSON")

    agent_hook = sub.add_parser("agent-hook", help="Claude/Codex hook: automatic recall/capture/profile, fail-open")
    agent_hook.add_argument("event", help="UserPromptSubmit, SessionStart, Stop, PreCompact, PostCompact")
    agent_hook.add_argument("--host", default="codex", help="codex/claude/opencode")
    agent_hook.add_argument("--cwd", default=".", help="fallback working directory")
    agent_hook.add_argument("--limit", type=int, default=6)
    agent_hook.add_argument("--no-capture", action="store_true", help="只 recall/profile，不写 pending")
    agent_hook.add_argument("--verbose", action="store_true", help="显示完整 recall summaries/evidence；默认 compact")
    agent_hook.add_argument("--json", action="store_true", help="输出 JSON")

    maintain = add_hidden_parser(sub, "maintain")
    maintain.add_argument("--cwd", default=".")
    maintain.add_argument("--limit", type=int, default=250)
    maintain.add_argument("--json", action="store_true", help="输出 JSON")

    add_hidden_parser(sub, "mcp")

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

    why_last = sub.add_parser("why-last", help="显示最近 N 次检索/hook 实际注入了什么(读 gain.jsonl)")
    why_last.add_argument("limit", nargs="?", type=int, default=10, help="最近多少条，默认 10")
    why_last.add_argument("--json", action="store_true", help="输出 JSON")

    fix = add_hidden_parser(sub, "fix")
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
    if args.cmd == "doctor":
        return doctor_cmd(args)
    if args.cmd == "sync":
        return sync_cmd(args)
    if args.cmd == "setup":
        return setup_cmd(args)
    if args.cmd == "new":
        return new_cmd(args)
    if args.cmd == "init":
        project = init_project(Path(args.path), args.project_id, args.alias, args.force)
        print(f"initialized {project['project_id']} at {project['root']}")
        print(f"local: {Path(project['root']) / '.xmem'}")
        print(f"global: {home_dir()}")
        return 0
    if args.cmd == "index":
        if args.cwd and args.path:
            print("xmem index: use either positional path or --cwd, not both", file=sys.stderr)
            return 2
        count = index_local(Path(args.cwd or args.path or "."))
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
        elif args.source == "code-index":
            print(json.dumps(import_code_indexes([Path(args.path)]), ensure_ascii=False, indent=2))
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
        structured_fields = collect_preflight_fields(args)
        query = preflight_search_query(args.query, structured_fields)
        cards = search_cards(query, max(args.limit * 4, 20), gain_event="preflight")
        for expanded_query in canonical_queries_from_corrections(query, cards):
            cards = merge_cards(cards, search_cards(expanded_query, max(args.limit * 2, 10), record_gain=False))
        events = latest_events(3)
        packet = build_preflight(query, current, cards, events, structured_fields=structured_fields, raw_query=args.query)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(preflight_packet(packet))
        return 0
    if args.cmd == "resume":
        current = None
        try:
            root = git_root(Path.cwd())
            current = detect_project(root)
        except Exception:
            pass
        structured_fields = collect_resume_fields(args)
        query = resume_search_query(args.query, structured_fields)
        cards = search_cards(query, max(args.limit * 4, 20), gain_event="resume")
        for expanded_query in canonical_queries_from_corrections(query, cards):
            cards = merge_cards(cards, search_cards(expanded_query, max(args.limit * 2, 10), record_gain=False))
        events = latest_events(3)
        packet = build_resume(query, current, cards, events, structured_fields=structured_fields, raw_query=args.query)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(resume_packet(packet))
        return 0
    if args.cmd == "gateway":
        structured_fields = collect_gateway_fields(args)
        packet = run_gateway(
            args.query,
            fields=structured_fields,
            cwd=Path(args.cwd),
            event=args.event,
            limit=args.limit,
            budget=args.budget,
            dry_run=args.dry_run,
        )
        if args.json or args.format == "json":
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(gateway_packet(packet))
        return 0
    if args.cmd == "capture":
        text = read_capture_text(args)
        result = capture_memories(
            text,
            cwd=Path(args.cwd),
            scope=args.scope,
            memory_type=args.type,
            confidence=args.confidence,
            evidence_path=args.evidence_path,
            ttl=args.ttl,
            supersedes=args.supersedes,
            aliases=args.alias,
            source="session" if args.from_session is not None else "manual",
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"captured: {result['created']}")
            for item in result["pending"]:
                print(f"- {item['id']} [{item['type']}] {item['path']}")
        return 0
    if args.cmd == "recall":
        packet = build_recall(args.query, cwd=Path(args.cwd), limit=args.limit, include_pending=args.include_pending)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(format_recall(packet))
        return 0
    if args.cmd == "profile":
        packet = synthesize_profile(cwd=Path(args.cwd), write=not args.no_write)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            paths = packet.get("paths") or {}
            print("profile:")
            for key, value in paths.items():
                print(f"- {key}: {value}")
            if not paths:
                print(packet.get("user_profile", "").strip())
        return 0
    if args.cmd == "review-pending":
        packet = review_pending(cwd=Path(args.cwd), include_all=args.all, limit=args.limit)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(format_pending_review(packet))
        return 0
    if args.cmd == "promote":
        result = promote_memory(args.memory_id, cwd=Path(args.cwd), verified=args.verified, target_scope=args.scope)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"promoted: {result['card_id']} -> {result['path']}")
        return 0
    if args.cmd == "promote-trellis":
        result = promote_trellis_memory(
            source_card=args.source_card,
            decision=args.decision,
            decided_by=args.decided_by,
            basis=args.basis,
            basis_file=args.basis_file,
            output_type=args.output_type,
            summary=args.summary,
            evidence=args.evidence,
            cwd=Path(args.cwd),
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"trellis decision: {result['decision']} audit={result['audit_path']}")
            if result.get("output_pending_id"):
                print(f"pending: {result['output_pending_id']} -> {result.get('output_path')}")
        return 0
    if args.cmd == "forget":
        result = forget_memory(args.memory_id, cwd=Path(args.cwd), reason=args.reason)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"forgotten: {result['id']} ({result['scope']})")
        return 0
    if args.cmd == "supersede":
        result = supersede_memory(args.old_id, args.new_id, cwd=Path(args.cwd), reason=args.reason)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"superseded: {result['old_id']} -> {result['new_id']}")
        return 0
    if args.cmd == "agent-hook":
        output = run_agent_hook(
            args.event,
            host=args.host,
            cwd=Path(args.cwd),
            limit=args.limit,
            emit_json=args.json,
            verbosity="verbose" if args.verbose else "compact",
            capture=not args.no_capture,
        )
        if output:
            print(output)
        return 0
    if args.cmd == "maintain":
        packet = build_memory_maintenance(cwd=Path(args.cwd), limit=args.limit)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(format_memory_maintenance(packet))
        return 0
    if args.cmd == "mcp":
        return run_mcp_server()
    if args.cmd == "suppress":
        row = record_suppression(args.card, args.for_query, args.reason)
        if args.json:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            print(f"suppressed: {row['card_id']} for query_hash={row['query_hash']} reason={row['reason']}")
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
            return 2 if result.get("errors") or (args.strict and result.get("warnings")) else 0
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
    if args.cmd == "why-last":
        return why_last_cmd(args)
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


def read_capture_text(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.from_session is not None:
        if args.from_session == "-":
            parts.append(sys.stdin.read())
        else:
            parts.append(Path(args.from_session).read_text(encoding="utf-8"))
    if args.text:
        parts.append(" ".join(args.text))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise SystemExit("capture requires text or --from-session")
    return text


def help_cmd() -> int:
    print(
        "\n".join(
            [
                "xmem 常用命令：",
                "- xmem status              # 查看索引状态、source 健康度、outbox",
                "- xmem doctor              # 综合诊断 registry / source / backup / 当前 repo",
                "- xmem setup               # 泛化初始化 ~/.xmem，并注册当前 repo / workspace",
                "- xmem sync                # 刷新索引；从 Project Wiki / Issue Record / 本地 cards 重建",
                "- xmem context <query>     # 查历史项目、方法、证据，返回 LLM 好读 packet",
                "- xmem preflight <query>   # 开发/修 bug 前查历史坑、must_keep、required checks",
                "- xmem preflight --fields domain=... task=...  # Agent 用结构化字段，避免旧上下文污染",
                "- xmem resume <query>      # 接手已有任务：身份、历史坑、gate、证据、下一步一包返回",
                "- xmem resume --fields issue=... domain=... task=...  # fresh session / hook 推荐入口",
                "- xmem gateway <query>     # Agent 入口层自动判断 skip/inject；给 MMS/hook 内部用",
                "- xmem check               # 改完前检查 invariant / rule / guardrail",
                "- xmem gain                # 查看完整 telemetry / Top 查询 / Top Cards 面板",
                "- xmem gain --summary      # 只看关键摘要",
                "- xmem gain card <id>      # 解释某个 card 的命中来源和最近 query",
                "- xmem capture --type preference \"...\"  # 捕获候选 memory 到 pending",
                "- xmem review-pending       # 查看待审核 memory",
                "- xmem promote <pending-id> # 提升 pending 为 compact card",
                "- xmem recall <query>       # 本地 hybrid recall 小包",
                "- xmem profile --cwd .      # 生成 user/project profile",
                "- xmem agent-hook UserPromptSubmit --host codex  # Agent 自动 recall/capture",
                "- xmem why <query>         # 解释为什么匹配",
                "- xmem open <id|query>     # 打开 card / evidence 摘要",
                "- xmem new                 # 新项目/新文件夹初始化并注册",
                "",
                "代码索引：sync 会读取已存在的 .ai/map/map.db / .codegraph/codegraph.db，只写轻量 ref；代码文件仍是真相。",
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


def generic_cards_path() -> Path:
    return package_root() / "examples" / "generic-cards"


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
        code_indexes = data.get("code_indexes") or {}
        if code_indexes:
            print(
                "code_indexes: "
                f"{code_indexes.get('indexes', 0)} indexes, "
                f"{code_indexes.get('cards', 0)} cards, "
                f"errors={len(code_indexes.get('errors') or [])}"
            )
        xmem_outbox = data.get("xmem_outbox") or {}
        if xmem_outbox:
            print(
                "xmem_outbox: "
                f"cards={xmem_outbox.get('cards', 0)} "
                f"evidence={xmem_outbox.get('evidence', 0)} "
                f"errors={xmem_outbox.get('errors', 0)}"
            )
        status = data.get("status", {})
        source_exports = status.get("source_exports") or {}
        if source_exports:
            print(
                "source_exports: "
                f"{source_exports.get('status', 'unknown')} "
                f"errors={source_exports.get('errors', 0)} "
                f"warnings={source_exports.get('warnings', 0)} "
                f"stale_exports={source_exports.get('stale_exports', 0)}"
            )
        actions = status.get("next_actions") or []
        if actions:
            print("next_actions:")
            for action in actions:
                print(f"- {action}")
    return 0


def sync_sources(cards_path: Path | str | None = None) -> dict[str, Any]:
    class RebuildArgs:
        project_wiki = os.environ.get("XMEM_PROJECT_WIKI", "/Users/xin/project-wiki")
        issue_tracking = os.environ.get("XMEM_ISSUE_TRACKING", "/Users/xin/issue-tracking")
        cards = str(cards_path or default_cards_path())
        local = "."
        skip_project_wiki = False
        skip_issue_tracking = False
        skip_cards = False
        skip_local = False

    result = rebuild_data(RebuildArgs())
    result["status"] = registry_status()
    return result


def setup_cmd(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in [*(args.paths or []), *(args.root or [])]]
    memory_repo = Path(args.memory_repo).expanduser() if args.memory_repo else None
    data = setup_workspace(
        paths,
        scan_depth=max(0, int(args.scan_depth)),
        init_projects=not args.register_only,
        memory_repo=memory_repo,
        dry_run=bool(args.dry_run),
        max_roots=max(1, int(args.max_roots)),
    )
    if not args.no_sync and not args.dry_run:
        data["sync"] = sync_sources(cards_path=generic_cards_path())
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("xmem setup")
        print(f"xmem_home: {data.get('xmem_home')}")
        print(f"mode: {data.get('mode')}")
        print(f"discovered_roots: {len(data.get('discovered_roots') or [])}")
        for root in (data.get("discovered_roots") or [])[:10]:
            print(f"- {root}")
        if len(data.get("discovered_roots") or []) > 10:
            print(f"- ... {len(data.get('discovered_roots') or []) - 10} more")
        print(f"initialized_projects: {len(data.get('initialized_projects') or [])}")
        print(f"registered_roots: {len(data.get('registered_roots') or [])}")
        if data.get("memory_repo"):
            print(f"memory_repo: {data['memory_repo'].get('root')}")
        if data.get("files_written"):
            print("files_written:")
            for path in data["files_written"]:
                print(f"- {path}")
        if data.get("sync"):
            counts = (data.get("sync") or {}).get("status", {}).get("counts", {})
            print(f"synced: cards={counts.get('cards', 0)} projects={counts.get('projects', 0)}")
        if data.get("skipped"):
            print("skipped:")
            for item in data["skipped"][:5]:
                print(f"- {item.get('path')}: {item.get('reason')}")
        print("next_steps:")
        for step in data.get("next_steps") or []:
            print(f"- {step}")
    return 0


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
    data = {
        "xmem_home": str(home_dir()),
        "registry": str(db),
        "registry_exists": db.exists(),
        "real_user_home": str(real_user_home()),
        "sources": str(sources_path()),
        "source_exports": check_source_exports(),
        "local_source_count": len(load_sources().get("local_roots", [])),
        "local_source_audit": audit_local_sources(),
        "backup": backup_health(),
        "outbox": outbox_counts(),
        "counts": counts,
    }
    data["code_indexes"] = code_index_status(registered_roots())
    data["next_actions"] = status_next_actions(data)
    return data


def status_next_actions(data: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    counts = data.get("counts") or {}
    source_exports = data.get("source_exports") or {}
    audit = data.get("local_source_audit") or {}
    outbox = data.get("outbox") or {}
    if not data.get("registry_exists") or int(counts.get("cards") or 0) == 0:
        actions.append("run xmem sync to rebuild the generated registry")
    if source_exports.get("errors"):
        actions.append("fix Project Wiki / Issue Record export errors before relying on context")
    elif source_exports.get("stale_exports"):
        actions.append("run xmem sync because source exports are newer than registry")
    if audit.get("local_only_knowledge_cards"):
        actions.append("decide whether local-only .xmem/cards should be tracked by git or exported by their source project")
    backup = data.get("backup") or {}
    if backup.get("next_action"):
        actions.append(str(backup["next_action"]))
    if int(outbox.get("total") or 0):
        actions.append("review xmem outbox and promote accepted Project Wiki / Issue Record writes")
    if not actions:
        actions.append("no blocking xmem maintenance action")
    return actions


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
        print(
            "outbox: "
            f"project_wiki={outbox.get('project_wiki', 0)} "
            f"issue_tracking={outbox.get('issue_tracking', 0)} "
            f"gain_feedback={outbox.get('gain_feedback', 0)} "
            f"total={outbox.get('total', 0)}"
        )
        backup = data.get("backup") or {}
        print(
            "backup: "
            f"{backup.get('status', 'unknown')} "
            f"last_success_at={backup.get('last_success_at', '') or 'none'} "
            f"pending={str(bool(backup.get('pending'))).lower()} "
            f"age_seconds={backup.get('age_seconds')}"
        )
        source_exports = data.get("source_exports") or {}
        print(
            "source_exports: "
            f"{source_exports.get('status', 'unknown')} "
            f"errors={source_exports.get('errors', 0)} "
            f"warnings={source_exports.get('warnings', 0)} "
            f"optional_missing={source_exports.get('optional_missing', 0)} "
            f"stale_exports={source_exports.get('stale_exports', 0)}"
        )
        code_indexes = data.get("code_indexes") or {}
        providers = code_indexes.get("providers") or {}
        provider_text = ",".join(f"{key}={value}" for key, value in sorted(providers.items())) or "none"
        print(
            "code_indexes: "
            f"indexes={code_indexes.get('indexes', 0)} "
            f"roots_checked={code_indexes.get('roots_checked', 0)} "
            f"providers={provider_text} "
            f"codegraph_binary={code_indexes.get('codegraph_binary') or 'missing'}"
        )
        counts = data.get("counts", {})
        if counts:
            print("counts:")
            for key, value in counts.items():
                print(f"- {key}: {value}")
        actions = data.get("next_actions") or []
        if actions:
            print("next_actions:")
            for action in actions:
                print(f"- {action}")
    return 0


def doctor_cmd(args: argparse.Namespace) -> int:
    data = build_doctor_report(registry_status(), Path.cwd())
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"xmem_doctor: {data.get('status', 'unknown')}")
        print("components:")
        for item in data.get("components", []):
            print(f"- {item.get('name')}: {item.get('severity')} {item.get('status')} ({item.get('summary')})")
        suggestions = data.get("local_card_suggestions") or []
        if suggestions:
            print("local_card_fixes:")
            for item in suggestions[:5]:
                sample = ",".join(item.get("sample") or [])
                print(f"- {item.get('root')}: {item.get('reason')} -> {item.get('suggested_fix')} sample={sample}")
        actions = data.get("next_actions") or []
        if actions:
            print("next_actions:")
            for action in actions:
                print(f"- {action}")
    return 2 if data.get("status") == "error" else 0


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


def why_last_cmd(args: argparse.Namespace) -> int:
    """Show what the last N retrievals/hooks actually surfaced, from gain.jsonl.

    Makes the otherwise-invisible auto-injection observable: each line is one
    real recall event with the query, the top card it returned, and why.
    """
    from .util import home_dir, load_jsonl

    relevant_sources = {"recall", "context", "preflight", "gateway", "resume", "why", "search"}
    rows = [
        row
        for row in load_jsonl(home_dir() / "gain.jsonl")
        if str(row.get("event", "")).rsplit(".", 1)[-1] in {"hit", "miss"}
        and str(row.get("source", "")) in relevant_sources
    ]
    recent = rows[-max(1, args.limit):]
    if args.json:
        print(json.dumps(recent, ensure_ascii=False, indent=2))
        return 0
    if not recent:
        print("(gain.jsonl 还没有检索事件)")
        return 0
    print(f"最近 {len(recent)} 次检索注入(query → top card → why):")
    for row in recent:
        ts = str(row.get("ts", ""))[:16].replace("T", " ")
        event = str(row.get("event", ""))
        query = str(row.get("query", "")).replace("\n", " ").strip()
        if len(query) > 56:
            query = query[:55] + "…"
        top = str(row.get("top_card", "")) or "—"
        status = str(row.get("top_status", "")) or "?"
        score = row.get("top_score", 0)
        matches = row.get("matches", 0)
        why = str(row.get("top_why", "")) or "—"
        print(f"  {ts}  {event:<14} q=\"{query}\"")
        print(f"      → {top}  [{status} score={score} matches={matches}]  why={why}")
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


def collect_preflight_fields(args: argparse.Namespace) -> dict[str, str]:
    fields = parse_key_items(list(getattr(args, "fields", []) or []))
    for key in ("domain", "service", "repo", "project", "task", "mode"):
        value = str(getattr(args, key, "") or "").strip()
        if value:
            fields[key] = value
    return {key: value for key, value in fields.items() if value}


def preflight_search_query(raw_query: str, fields: dict[str, str]) -> str:
    if not fields:
        return raw_query
    parts: list[str] = []
    for key in ("domain", "service", "repo", "project", "task", "mode"):
        value = fields.get(key)
        if value:
            parts.append(value)
    if not fields.get("task") and raw_query:
        parts.append(raw_query)
    return " ".join(parts).strip() or raw_query


def collect_resume_fields(args: argparse.Namespace) -> dict[str, str]:
    fields = parse_key_items(list(getattr(args, "fields", []) or []))
    for key in ("issue", "domain", "service", "repo", "project", "task", "mode"):
        value = str(getattr(args, key, "") or "").strip()
        if value:
            fields[key] = value
    return {key: value for key, value in fields.items() if value}


def resume_search_query(raw_query: str, fields: dict[str, str]) -> str:
    if not fields:
        return raw_query
    parts: list[str] = []
    for key in ("issue", "domain", "service", "repo", "project", "task", "mode"):
        value = fields.get(key)
        if value:
            parts.append(value)
    if not fields.get("task") and raw_query:
        parts.append(raw_query)
    return " ".join(parts).strip() or raw_query


def collect_gateway_fields(args: argparse.Namespace) -> dict[str, str]:
    fields = parse_key_items(list(getattr(args, "fields", []) or []))
    for key in ("issue", "domain", "service", "repo", "project", "task", "mode"):
        value = str(getattr(args, key, "") or "").strip()
        if value:
            fields[key] = value
    return {key: value for key, value in fields.items() if value}


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
            print(
                "outbox: "
                f"project_wiki={counts.get('project_wiki', 0)} "
                f"issue_tracking={counts.get('issue_tracking', 0)} "
                f"gain_feedback={counts.get('gain_feedback', 0)} "
                f"total={counts.get('total', 0)}"
            )
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
    if gain_cmd_name == "card":
        data = summarize_card_gain(args.card_id, limit=args.limit)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            use_color = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")
            print(format_card_gain_dashboard(data, color=use_color))
        return 0
    data = summarize_gain(limit=args.limit)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        use_color = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")
        print(format_gain_dashboard(data, color=use_color, detail=not getattr(args, "summary", False)))
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
            roots = registered_roots([local_root])
            result["project_memory"] = import_project_memory_roots(roots)
            result["code_indexes"] = import_code_indexes(roots)
        if not args.skip_cards:
            result["cards"] = import_cards(Path(args.cards))
            result["global_cards"] = import_cards(home_dir() / "cards")
        result["xmem_outbox"] = import_xmem_outbox()
        if not args.skip_project_wiki:
            pw = Path(args.project_wiki)
            has_project_wiki_source = (
                (pw / "data" / "project-hub.index.json").exists()
                or (pw / "data" / "xmem-export.cards.jsonl").exists()
                or (pw / "data" / "agent-inbox.jsonl").exists()
            )
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
