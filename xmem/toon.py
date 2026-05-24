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
    freshness = packet.get("source_freshness") or {}
    if freshness:
        lines.append("  source_freshness:")
        for key in ("status", "stale_exports", "registry"):
            lines.append(f"    {key}: {quote_scalar(freshness.get(key, ''))}")
        stale = freshness.get("stale") or []
        lines.append(f"    stale[{len(stale)}]:")
        for item in stale[:5]:
            lines.append(f"      - kind: {quote_scalar(item.get('kind', ''))}; path: {quote_scalar(item.get('path', ''))}")
    symbolic = packet.get("symbolic_memory") or {}
    if symbolic:
        lines.extend(symbolic_memory_item(symbolic, 2))
    local_health = packet.get("local_source_health") or {}
    if local_health:
        lines.append("  local_source_health:")
        for key in ("knowledge_cards", "tracked_cards", "local_only_knowledge_cards", "ignored_knowledge_cards"):
            lines.append(f"    {key}: {quote_scalar(local_health.get(key, ''))}")
    suggested_queries = packet.get("suggested_queries") or []
    lines.append(f"  suggested_queries[{len(suggested_queries)}]:")
    for query in suggested_queries:
        lines.append(f"    - {quote_scalar(query)}")
    guidance = packet.get("correction_guidance") or []
    lines.append(f"  correction_guidance[{len(guidance)}]:")
    for item in guidance:
        lines.append(f"    - id: {quote_scalar(item.get('id', ''))}")
        lines.append(f"      truth: {quote_scalar(item.get('truth', ''))}")
        if item.get("wrong_aliases"):
            lines.append(f"      wrong_aliases: {quote_scalar(', '.join(map(str, item.get('wrong_aliases') or [])))}")
        if item.get("canonical_aliases"):
            lines.append(f"      canonical_aliases: {quote_scalar(', '.join(map(str, item.get('canonical_aliases') or [])))}")
    traffic = packet.get("traffic_switch") or []
    lines.append(f"  traffic_switch[{len(traffic)}]:")
    for item in traffic:
        lines.extend(traffic_item(item, 4))
    gain_hints = packet.get("gain_hints") or []
    lines.append(f"  gain_hints[{len(gain_hints)}]:")
    for hint in gain_hints:
        lines.append(f"    - {quote_scalar(hint)}")
    for section in ("corrections", "alias_guidance", "relations", "registry_candidates", "rules", "methods", "memories", "specs", "code_indexes", "evidence"):
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


def preflight_packet(packet: Dict[str, Any]) -> str:
    """Emit a compact development-preflight packet for agents."""
    lines = ["xmem_preflight:"]
    for key in ("schema", "truth_policy", "query", "intent", "readiness", "risk_level", "action"):
        lines.append(f"  {key}: {quote_scalar(packet.get(key, ''))}")
    lines.append(f"  severity: {quote_scalar(packet.get('severity', ''))}")
    lines.append(f"  can_proceed: {quote_scalar(packet.get('can_proceed', ''))}")
    symbolic = packet.get("symbolic_memory") or {}
    if symbolic:
        lines.extend(symbolic_memory_item(symbolic, 2))
    blockers = packet.get("blockers") or []
    lines.append(f"  blockers[{len(blockers)}]:")
    for item in blockers:
        lines.append(f"    - code: {quote_scalar(item.get('code', ''))}")
        lines.append(f"      text: {quote_scalar(compact(item.get('text', ''), 180))}")
        lines.append(f"      severity: {quote_scalar(item.get('severity', ''))}")
    for section in ("required_before_edit", "required_before_deploy"):
        items = packet.get(section) or []
        lines.append(f"  {section}[{len(items)}]:")
        for item in items:
            lines.append(f"    - {quote_scalar(compact(item, 200))}")
    freshness = packet.get("source_freshness") or {}
    lines.append("  source_freshness:")
    for key in ("status", "stale_exports", "registry"):
        lines.append(f"    {key}: {quote_scalar(freshness.get(key, ''))}")
    local_health = packet.get("local_source_health") or {}
    if local_health:
        lines.append("  local_source_health:")
        for key in ("knowledge_cards", "tracked_cards", "local_only_knowledge_cards", "ignored_knowledge_cards"):
            lines.append(f"    {key}: {quote_scalar(local_health.get(key, ''))}")
    resolution = packet.get("resolution") or {}
    lines.append("  resolution:")
    for key in ("status", "do_not_assume_single_project", "reason"):
        lines.append(f"    {key}: {quote_scalar(resolution.get(key, ''))}")
    current = packet.get("current") or {}
    if current:
        lines.append("  current:")
        for key in ("project_id", "root", "branch", "git_sha", "tech_stack"):
            lines.append(f"    {key}: {quote_scalar(current.get(key, ''))}")
    for section in ("matched_projects", "known_bug_patterns", "invariants", "methods", "specs"):
        items = packet.get(section) or []
        lines.append(f"  {section}[{len(items)}]:")
        for item in items:
            lines.extend(brief_item(item, 4))
    for section in ("must_keep", "avoid", "known_failure_modes", "required_checks"):
        items = packet.get(section) or []
        lines.append(f"  {section}[{len(items)}]:")
        for item in items:
            lines.append(f"    - text: {quote_scalar(item.get('text', ''))}")
            lines.append(f"      field: {quote_scalar(item.get('field', ''))}")
            lines.append(f"      card_id: {quote_scalar(item.get('card_id', ''))}")
            lines.append(f"      truth: {quote_scalar(item.get('truth', ''))}")
            lines.append(f"      source_ref: {quote_scalar(compact(item.get('source_ref', ''), 160))}")
    source_refs = packet.get("source_refs") or []
    lines.append(f"  source_refs[{len(source_refs)}]:")
    for path in source_refs:
        lines.append(f"    - {quote_scalar(path)}")
    warnings = packet.get("warnings") or []
    lines.append(f"  warnings[{len(warnings)}]:")
    for warning in warnings:
        lines.append(f"    - {quote_scalar(warning)}")
    next_reads = packet.get("next_reads") or []
    lines.append(f"  next_reads[{len(next_reads)}]:")
    for path in next_reads:
        lines.append(f"    - {quote_scalar(path)}")
    return "\n".join(lines)


def brief_item(item: Dict[str, Any], indent: int) -> List[str]:
    pad = " " * indent
    sub = " " * (indent + 2)
    lines = [f"{pad}- id: {quote_scalar(compact(item.get('id', ''), 96))}"]
    keys = ["node_id", "memory_layer", "rank", "score", "type", "truth", "confidence", "project_id", "source", "title", "why", "evidence_ref", "source_ref", "source_path"]
    for key in keys:
        value = item.get(key, "")
        if key in {"evidence_ref", "source_ref", "source_path"}:
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


def symbolic_memory_item(item: Dict[str, Any], indent: int) -> List[str]:
    pad = " " * indent
    sub = " " * (indent + 2)
    lines = [f"{pad}symbolic_memory:"]
    for key in ("mode", "top_canvas", "drilldown"):
        lines.append(f"{sub}{key}: {quote_scalar(compact(item.get(key, ''), 200))}")
    layers = item.get("layers") or []
    lines.append(f"{sub}layers[{len(layers)}]:")
    for layer in layers:
        lines.append(
            "{pad}- id: {id}; name: {name}; count: {count}; purpose: {purpose}".format(
                pad=" " * (indent + 4),
                id=quote_scalar(layer.get("id", "")),
                name=quote_scalar(layer.get("name", "")),
                count=quote_scalar(layer.get("count", "")),
                purpose=quote_scalar(compact(layer.get("purpose", ""), 160)),
            )
        )
    return lines


def traffic_item(item: Dict[str, Any], indent: int) -> List[str]:
    lines = brief_item(item, indent)
    sub = " " * (indent + 2)
    for key in (
        "project",
        "template",
        "repo",
        "prod_service",
        "validation_service",
        "prod_pipeline_hint",
        "validation_pipeline_hint",
        "prod_branch_hint",
        "validation_branch_hint",
        "approval_group",
    ):
        value = item.get(key, "")
        if value:
            lines.append(f"{sub}{key}: {quote_scalar(compact(value, 160))}")
    for key in ("role_semantics", "repo_local_hints", "domains", "common_verification", "can_skip", "stale_policy"):
        values = item.get(key) or []
        if not values:
            continue
        lines.append(f"{sub}{key}[{len(values)}]:")
        for value in values[:12]:
            lines.append(f"{sub}  - {quote_scalar(compact(value, 180))}")
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
