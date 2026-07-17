from __future__ import annotations

import json
import os
import re
import select
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .hook_outcomes import record_hook_inject
from .memory import build_recall, capture_memories, compact_text, estimate_tokens, synthesize_profile
from .util import query_terms, utc_now


RECALL_EVENTS = {"userpromptsubmit", "user_prompt_submit", "prompt", "sessionstart", "session_start", "start"}
STOP_EVENTS = {"stop", "sessionend", "session_end", "precompact", "postcompact"}
PROMPT_KEYS = {"prompt", "user_prompt", "userprompt", "message", "input", "query", "text"}
CWD_KEYS = {"cwd", "working_directory", "workspace", "workspace_dir", "project_dir", "repo_path", "root"}
TRANSCRIPT_KEYS = {"transcript_path", "transcript", "conversation_path", "session_path"}
SESSION_ID_KEYS = {"session_id", "sessionid", "thread_id"}
HOOK_RECALL_QUERY_LIMIT = 1200
HOOK_NOISE_MARKERS = (
    "sessionstart hook (completed)",
    "hook context:",
    "xmem_agent_memory:",
    "caveman mode active",
)
CAPTURE_HINT = re.compile(
    r"^\s*(?:[-*]\s*)?(preference|project_fact|decision|invariant|bug_pattern|rejected_option|workflow_lesson|source_pointer|memory)\s*[:=：-]",
    re.I | re.M,
)
CAPTURE_WORDS = re.compile(
    r"\b(prefer|remember|must|always|never|avoid|decision|decided|rejected|bug|regression|failed|failure|lesson)\b|"
    r"(偏好|记住|必须|不要|别用|禁用|决定|拒绝|失败|报错|教训)",
    re.I,
)


def run_agent_hook(
    event: str,
    *,
    host: str = "codex",
    cwd: Path | None = None,
    limit: int = 6,
    emit_json: bool = False,
    verbosity: str = "compact",
    capture: bool = True,
    stdin_text: str | None = None,
) -> str:
    try:
        result = build_agent_hook_result(
            event,
            host=host,
            cwd=cwd,
            limit=limit,
            capture=capture,
            stdin_text=read_stdin_available() if stdin_text is None else stdin_text,
        )
    except Exception as exc:
        result = {
            "schema": "xmem.agent_hook.v1",
            "ok": False,
            "host": host,
            "event": event,
            "error": str(exc)[:240],
            "generated_at": utc_now(),
        }
    if emit_json:
        output = json.dumps(result, ensure_ascii=False, indent=2)
    elif verbosity == "verbose":
        output = format_agent_hook_result_verbose(result)
    elif verbosity == "silent":
        output = ""
    else:
        output = format_agent_hook_result(result)
    if output and result.get("ok") and result.get("event") in RECALL_EVENTS:
        try:
            record_hook_inject(result, output)
        except Exception:
            pass
    return output


def build_agent_hook_result(
    event: str,
    *,
    host: str,
    cwd: Path | None,
    limit: int,
    capture: bool,
    stdin_text: str,
) -> Dict[str, Any]:
    payload = parse_payload(stdin_text)
    event_norm = normalize_event(event or first_text(payload, {"hook_event_name", "event", "name"}))
    hook_cwd = choose_cwd(payload, cwd)
    prompt = extract_prompt(payload)
    profile = {}
    recall = {}
    captured: Dict[str, Any] = {"created": 0, "pending": []}
    action = "skip"

    if event_norm in RECALL_EVENTS:
        query = hook_recall_query(prompt, hook_cwd) if prompt else project_query(hook_cwd)
        if query:
            recall = build_recall(query, cwd=hook_cwd, limit=limit, include_pending=True)
            recall = filter_hook_recall(recall)
            action = "recall" if recall.get("memories") or recall.get("pending") else "skip"
        if capture and prompt and should_capture_text(prompt):
            captured = capture_memories(
                prompt,
                cwd=hook_cwd,
                scope=infer_capture_scope(prompt, event_norm),
                confidence=0.45,
                evidence_path=evidence_path(payload, hook_cwd),
                source=f"agent-hook:{host}:{event_norm}",
            )
        if event_norm in {"sessionstart", "session_start", "start"}:
            profile = synthesize_profile(cwd=hook_cwd, write=True)
    elif event_norm in STOP_EVENTS:
        session_text = extract_session_text(payload, stdin_text)
        if capture and session_text and should_capture_text(session_text):
            captured = capture_memories(
                session_text,
                cwd=hook_cwd,
                scope="project",
                memory_type="workflow_lesson" if not CAPTURE_HINT.search(session_text) else "",
                confidence=0.35,
                evidence_path=evidence_path(payload, hook_cwd),
                source=f"agent-hook:{host}:{event_norm}",
            )
            action = "capture"
        profile = synthesize_profile(cwd=hook_cwd, write=True)

    return {
        "schema": "xmem.agent_hook.v1",
        "ok": True,
        "host": host,
        "event": event_norm,
        "action": action,
        "cwd": str(hook_cwd),
        "session_id": first_text(payload, SESSION_ID_KEYS),
        "transcript_path": first_text(payload, TRANSCRIPT_KEYS),
        "recall": recall,
        "captured": captured,
        "profile_refs": (profile.get("paths") or {}) if profile else {},
        "generated_at": utc_now(),
    }


def format_agent_hook_result(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return ""
    recall = result.get("recall") or {}
    memories = recall.get("memories") or []
    pending = recall.get("pending") or []
    captured = result.get("captured") or {}
    profiles = result.get("profile_refs") or {}
    event = str(result.get("event") or "")
    if event in STOP_EVENTS:
        return ""
    if not memories and not pending and not captured.get("created") and not profiles:
        return ""

    lines = [f"xmem_agent_memory: compact recall={len(memories)} pending={len(pending)}"]
    if memories:
        for item in memories[:2]:
            title = compact_inline(str(item.get("title") or ""), 72)
            lines.append(
                "  - "
                f"{item.get('id')} "
                f"truth={item.get('truth')} "
                f"title={title}"
            )
        omitted = max(0, len(memories) - 2)
        if omitted:
            lines.append(f"  omitted_recall: {omitted}")
    if captured.get("created"):
        lines.append(f"  pending_captured: {captured.get('created')}")
    if profiles:
        lines.append("  profiles: refreshed")
    return "\n".join(lines)


def format_agent_hook_result_verbose(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return ""
    recall = result.get("recall") or {}
    memories = recall.get("memories") or []
    pending = recall.get("pending") or []
    captured = result.get("captured") or {}
    profiles = result.get("profile_refs") or {}
    event = str(result.get("event") or "")
    if event in STOP_EVENTS:
        return ""
    if not memories and not pending and not captured.get("created") and not profiles:
        return ""

    lines = [
        "xmem_agent_memory:",
        f"  host: {result.get('host')}",
        f"  event: {event}",
        f"  action: {result.get('action')}",
    ]
    if memories:
        lines.append("  recall:")
        for item in memories[:6]:
            lines.append(
                "  - "
                f"id: {item.get('id')} "
                f"truth={item.get('truth')} "
                f"confidence={item.get('confidence')} "
                f"score={item.get('score')} "
                f"title={compact_inline(str(item.get('title') or ''), 90)}"
            )
            summary = item.get("summary")
            if summary:
                lines.append(f"    summary: {compact_inline(str(summary), 180)}")
            if item.get("path"):
                lines.append(f"    evidence: {item.get('path')}")
        next_reads = recall.get("next_reads") or []
        if next_reads:
            lines.append("  next_reads:")
            lines.extend(f"  - {path}" for path in next_reads[:4])
        lines.append(f"  token_estimate: {recall.get('token_estimate') or estimate_tokens(memories)}")
    if pending:
        lines.append("  pending_hints:")
        for item in pending[:3]:
            lines.append(
                "  - "
                f"id: {item.get('id')} "
                f"type={item.get('type')} "
                f"confidence={item.get('confidence')} "
                f"title={compact_inline(str(item.get('title') or ''), 90)}"
            )
            lines.append(f"    truth: unpromoted_pending")
    if captured.get("created"):
        lines.append(f"  pending_captured: {captured.get('created')}")
        for item in (captured.get("pending") or [])[:3]:
            lines.append(f"  - pending: {item.get('id')} type={item.get('type')} path={item.get('path')}")
    if profiles:
        lines.append("  profile_refs:")
        for key, path in profiles.items():
            lines.append(f"    {key}: {path}")
    return "\n".join(lines)


def filter_hook_recall(packet: Dict[str, Any]) -> Dict[str, Any]:
    query = str(packet.get("query") or "")
    project_id = str((packet.get("scope") or {}).get("project_id") or "")
    terms = important_terms(query)
    filtered = dict(packet)
    memories = [
        item
        for item in packet.get("memories") or []
        if is_relevant_memory(item, terms=terms, project_id=project_id)
    ]
    pending = [
        item
        for item in packet.get("pending") or []
        if is_relevant_pending(item, terms=terms, project_id=project_id)
    ]
    filtered["memories"] = memories
    filtered["pending"] = pending
    filtered["next_reads"] = [item.get("path") for item in memories if item.get("path")][:4]
    filtered["token_estimate"] = estimate_tokens(memories)
    filtered.setdefault("ranking", {})["hook_filter"] = {
        "mode": "relevance_gate",
        "before_memories": len(packet.get("memories") or []),
        "after_memories": len(memories),
        "before_pending": len(packet.get("pending") or []),
        "after_pending": len(pending),
    }
    return filtered


def is_relevant_memory(item: Dict[str, Any], *, terms: set[str], project_id: str) -> bool:
    text = memory_display_text(item)
    overlap = term_overlap(terms, text)
    if overlap >= 2:
        return True
    if project_id and source_matches_project(str(item.get("source_ref") or item.get("path") or ""), project_id) and overlap >= project_overlap_threshold(terms):
        return True
    if project_id and "project_match" in {str(reason) for reason in item.get("why") or []} and overlap >= project_overlap_threshold(terms):
        return True
    semantic = max_semantic_reason(item)
    if semantic >= 0.28 and overlap >= project_overlap_threshold(terms):
        return True
    if semantic >= 0.18 and overlap >= project_overlap_threshold(terms) and str(item.get("type") or "") in {"preference", "invariant", "workflow_lesson"}:
        return True
    return False


def is_relevant_pending(item: Dict[str, Any], *, terms: set[str], project_id: str) -> bool:
    text = memory_display_text(item)
    overlap = term_overlap(terms, text)
    scope = item.get("scope") or {}
    same_project = bool(project_id and scope.get("project_id") == project_id)
    if same_project and overlap >= project_overlap_threshold(terms):
        return True
    if overlap >= 2:
        return True
    return False


def important_terms(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "现在",
        "这个",
        "那个",
        "是不是",
        "为什么",
        "怎么",
        "什么",
        "使用",
        "按照",
        "顺序",
        "修复",
        "验证",
        "问题",
        "记录",
        "issue",
        "bug",
        "open",
    }
    return {term for term in query_terms(text) if len(term) >= 2 and term.lower() not in stop}


def project_overlap_threshold(terms: set[str]) -> int:
    return 1 if len(terms) <= 2 else 2


def term_overlap(terms: set[str], text: str) -> int:
    if not terms:
        return 0
    low = text.lower()
    return sum(1 for term in terms if term.lower() in low)


def memory_display_text(item: Dict[str, Any]) -> str:
    fields = [
        item.get("id"),
        item.get("type"),
        item.get("title"),
        item.get("summary"),
        item.get("source_ref"),
        item.get("path"),
    ]
    scope = item.get("scope")
    if scope:
        fields.append(json.dumps(scope, ensure_ascii=False, sort_keys=True))
    return "\n".join(str(value or "") for value in fields)


def source_matches_project(source: str, project_id: str) -> bool:
    if not source or not project_id:
        return False
    needles = [
        f"/issues/{project_id}/",
        f"/local-sources/{project_id}/",
        f"/{project_id}/.xmem/",
        f"/{project_id}/",
    ]
    return any(needle in source for needle in needles)


def max_semantic_reason(item: Dict[str, Any]) -> float:
    best = 0.0
    for reason in item.get("why") or []:
        match = re.match(r"semantic_lite:([0-9.]+)", str(reason))
        if match:
            best = max(best, float(match.group(1)))
    return best


def read_stdin_available() -> str:
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return ""
    if not ready:
        return ""
    try:
        return sys.stdin.read()
    except OSError:
        return ""


def parse_payload(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {"payload": data}
    except json.JSONDecodeError:
        return {"text": raw}


def normalize_event(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", (value or "").strip().lower())


def choose_cwd(payload: Dict[str, Any], cwd: Path | None) -> Path:
    for value in iter_text_values(payload, CWD_KEYS):
        path = Path(os.path.expandvars(value)).expanduser()
        if path.exists():
            return path
    if cwd:
        return cwd
    return Path.cwd()


def extract_prompt(payload: Dict[str, Any]) -> str:
    for value in iter_text_values(payload, PROMPT_KEYS):
        if value and len(value.strip()) >= 2:
            return compact_text(value, 4000)
    return ""


def extract_session_text(payload: Dict[str, Any], stdin_text: str) -> str:
    for value in iter_text_values(payload, TRANSCRIPT_KEYS):
        path = Path(os.path.expandvars(value)).expanduser()
        if path.exists() and path.is_file():
            return transcript_tail(path)
    summary = first_text(payload, {"summary", "session_summary", "final_summary"})
    if summary:
        return compact_text(summary, 4000)
    return compact_text(stdin_text, 4000)


def transcript_tail(path: Path, max_bytes: int = 24000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(max(0, size - max_bytes))
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    texts: List[str] = []
    for line in raw.splitlines()[-80:]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            texts.append(stripped)
            continue
        texts.extend(value for value in iter_text_values(data, PROMPT_KEYS | {"content", "summary"}) if value)
    return compact_text("\n".join(texts), 4000)


def should_capture_text(text: str) -> bool:
    clean = text.strip()
    if len(clean) < 12:
        return False
    return bool(CAPTURE_HINT.search(clean) or CAPTURE_WORDS.search(clean))


def hook_recall_query(prompt: str, cwd: Path) -> str:
    clean = (prompt or "").strip()
    if not clean:
        return project_query(cwd)
    if len(clean) <= HOOK_RECALL_QUERY_LIMIT:
        return clean
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    filtered = [line for line in lines if not is_hook_noise_line(line)]
    if not filtered:
        filtered = lines
    if len(filtered) > 12:
        filtered = filtered[:3] + filtered[-9:]
    filtered.append(project_query(cwd))
    return compact_text("\n".join(filtered), HOOK_RECALL_QUERY_LIMIT)


def is_hook_noise_line(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in HOOK_NOISE_MARKERS)


def infer_capture_scope(text: str, event: str) -> str:
    low = text.lower()
    if event in {"sessionstart", "session_start", "start"}:
        return "project"
    if any(token in low for token in ("user preference", "我希望", "我喜欢", "prefer", "always", "never")):
        return "user"
    return "project"


def evidence_path(payload: Dict[str, Any], cwd: Path) -> str:
    for value in iter_text_values(payload, TRANSCRIPT_KEYS):
        return value
    return str(cwd)


def project_query(cwd: Path) -> str:
    return f"project memory {cwd.name}"


def first_text(data: Any, keys: set[str]) -> str:
    for value in iter_text_values(data, keys):
        if value:
            return value
    return ""


def iter_text_values(data: Any, keys: set[str]) -> Iterable[str]:
    if isinstance(data, dict):
        for key, value in data.items():
            key_norm = str(key).lower()
            if key_norm in keys:
                if isinstance(value, str):
                    yield value
                elif isinstance(value, (int, float)):
                    yield str(value)
            if isinstance(value, (dict, list)):
                yield from iter_text_values(value, keys)
    elif isinstance(data, list):
        for item in data:
            yield from iter_text_values(item, keys)


def compact_inline(text: str, limit: int) -> str:
    return compact_text(text.replace("\n", " "), limit)
