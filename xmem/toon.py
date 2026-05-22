from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def project_snapshot(project: Dict[str, Any], card_paths: Iterable[Path]) -> str:
    cards = [p.stem for p in card_paths]
    lines = [
        "TOON:",
        f"project_id: {project.get('project_id','')}",
        f"name: {project.get('name','')}",
        f"root: {project.get('root','')}",
        f"branch: {project.get('branch','')}",
        f"git_sha: {project.get('git_sha','')}",
        f"tech_stack: {project.get('tech_stack','')}",
        f"status: {project.get('status','')}",
        "aliases[{}]: {}".format(len(project.get("aliases") or []), ",".join(project.get("aliases") or [])),
        "cards[{}]: {}".format(len(cards), ",".join(cards)),
    ]
    return "\n".join(lines)


def context_packet(query: str, current: Dict[str, Any] | None, cards: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> str:
    lines = ["TOON:", f"query: {query}"]
    if current:
        lines.extend([
            f"current_project: {current.get('project_id','')}",
            f"current_root: {current.get('root','')}",
            f"current_branch: {current.get('branch','')}",
        ])
    if events:
        lines.append(f"latest_event: {events[-1].get('ts','')} {events[-1].get('event','')}")
    lines.append("cards[{}]{{rank,score,card_id,project_id,type,status,source,title}}:".format(len(cards)))
    for idx, card in enumerate(cards, 1):
        row = [
            str(idx), str(card.get("score", "")), compact(card.get("card_id", ""), 56),
            compact(card.get("project_id", ""), 32), card.get("type", ""), card.get("status", ""),
            card.get("source", ""), compact(card.get("title", ""), 64),
        ]
        lines.append("  " + ",".join(quote_cell(x) for x in row))
    return "\n".join(lines)


def llm_packet(packet: Dict[str, Any]) -> str:
    """Emit compact YAML-ish text optimized for LLM parsing, not terminal layout."""
    lines = ["xmem_context:"]
    lines.append(f"  schema: {packet.get('schema', '')}")
    lines.append(f"  truth_policy: {quote_scalar(packet.get('truth_policy', ''))}")
    lines.append(f"  query: {quote_scalar(packet.get('query', ''))}")
    resolution = packet.get("resolution") or {}
    lines.append("  resolution:")
    for key in ("status", "do_not_assume_single_project", "reason"):
        lines.append(f"    {key}: {quote_scalar(resolution.get(key, ''))}")
    current = packet.get("current") or {}
    if current:
        lines.append("  current:")
        for key in ("project_id", "root", "branch", "git_sha", "tech_stack"):
            lines.append(f"    {key}: {quote_scalar(current.get(key, ''))}")
    for section in ("alias_guidance", "registry_candidates", "rules", "methods", "evidence"):
        items = packet.get(section) or []
        lines.append(f"  {section}[{len(items)}]:")
        for item in items:
            lines.extend(brief_item(item, 4))
    warnings = packet.get("warnings") or []
    lines.append(f"  warnings[{len(warnings)}]:")
    for warning in warnings:
        lines.append(f"    - {quote_scalar(warning)}")
    next_reads = packet.get("next_reads") or []
    lines.append(f"  next_reads[{len(next_reads)}]:")
    for path in next_reads:
        lines.append(f"    - {quote_scalar(path)}")
    events = packet.get("latest_events") or []
    lines.append(f"  latest_events[{len(events)}]:")
    for event in events:
        lines.append(
            "    - ts: {ts}; event: {event}; project_id: {project_id}; card_id: {card_id}".format(
                ts=quote_scalar(event.get("ts", "")),
                event=quote_scalar(event.get("event", "")),
                project_id=quote_scalar(event.get("project_id", "")),
                card_id=quote_scalar(event.get("card_id", "")),
            )
        )
    return "\n".join(lines)


def brief_item(item: Dict[str, Any], indent: int) -> List[str]:
    pad = " " * indent
    sub = " " * (indent + 2)
    lines = [f"{pad}- id: {quote_scalar(compact(item.get('id', ''), 96))}"]
    keys = ["rank", "score", "type", "truth", "confidence", "project_id", "source", "title", "why", "source_ref", "source_path"]
    for key in keys:
        value = item.get(key, "")
        if key in {"source_ref", "source_path"}:
            value = compact(value, 160)
        elif key == "title":
            value = compact(value, 96)
        elif key == "why":
            value = compact(value, 160)
        lines.append(f"{sub}{key}: {quote_scalar(value)}")
    aliases = item.get("aliases") or []
    if aliases:
        lines.append(f"{sub}aliases[{len(aliases)}]: {quote_scalar(', '.join(map(str, aliases[:8])))}")
    hints = item.get("hints") or []
    if hints:
        lines.append(f"{sub}hints[{len(hints)}]:")
        for hint in hints[:10]:
            lines.append(f"{sub}  - {quote_scalar(compact(hint, 160))}")
    return lines


def quote_cell(value: Any) -> str:
    text = str(value).replace("\n", " ").strip()
    if "," in text or '"' in text:
        return json.dumps(text, ensure_ascii=False)
    return text


def quote_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\n", " ").strip()
    if text == "":
        return '""'
    if text.startswith("-") or any(ch in text for ch in [":", "#", "{", "}", "[", "]", ",", ";", '"']):
        return json.dumps(text, ensure_ascii=False)
    return text


def compact(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
