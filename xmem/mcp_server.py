from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

from . import __version__
from .maintenance import build_memory_maintenance
from .memory import build_recall, capture_memories, compact_text, review_pending, synthesize_profile
from .util import home_dir, slugify


TOOLS = [
    {
        "name": "memory/capture",
        "description": "Capture candidate memory into the local pending queue. Does not promote to truth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "cwd": {"type": "string"},
                "scope": {"type": "string"},
                "type": {"type": "string"},
                "confidence": {"type": "number"},
                "evidence_path": {"type": "string"},
                "ttl": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory/recall",
        "description": "Recall a compact local hybrid memory packet for a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "cwd": {"type": "string"},
                "limit": {"type": "integer"},
                "include_pending": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "context/profile",
        "description": "Synthesize user and project memory profiles.",
        "inputSchema": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}, "write": {"type": "boolean"}},
        },
    },
    {
        "name": "memory/review_pending",
        "description": "Review pending local memories.",
        "inputSchema": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}, "limit": {"type": "integer"}, "all": {"type": "boolean"}},
        },
    },
    {
        "name": "memory/semantic_grep",
        "description": "Run semantic grep-lite over local memory.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "cwd": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "memory/maintain",
        "description": "Report TTL decay, duplicate card candidates, and duplicate pending memories.",
        "inputSchema": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}, "limit": {"type": "integer"}},
        },
    },
]


def run_mcp_server() -> int:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            response = handle_request(request)
        except Exception as exc:
            response = error_response(None, -32603, str(exc))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def handle_request(request: Dict[str, Any]) -> Dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "initialize":
        return result_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xmem", "version": __version__},
            },
        )
    if method == "tools/list":
        return result_response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        return result_response(request_id, {"content": [{"type": "text", "text": call_tool(name, arguments)}]})
    if request_id is None:
        return None
    return error_response(request_id, -32601, f"unknown method: {method}")


def call_tool(name: str, arguments: Dict[str, Any]) -> str:
    cwd = Path(arguments.get("cwd") or ".")
    if name == "memory/capture":
        packet = capture_memories(
            str(arguments.get("text") or ""),
            cwd=cwd,
            scope=str(arguments.get("scope") or "project"),
            memory_type=str(arguments.get("type") or ""),
            confidence=float(arguments.get("confidence") or 0.55),
            evidence_path=str(arguments.get("evidence_path") or ""),
            ttl=str(arguments.get("ttl") or ""),
            source="mcp",
        )
    elif name == "memory/recall":
        packet = build_recall(
            str(arguments.get("query") or ""),
            cwd=cwd,
            limit=int(arguments.get("limit") or 8),
            include_pending=bool(arguments.get("include_pending")),
        )
    elif name == "context/profile":
        packet = synthesize_profile(cwd=cwd, write=bool(arguments.get("write", True)))
    elif name == "memory/review_pending":
        packet = review_pending(
            cwd=cwd,
            include_all=bool(arguments.get("all")),
            limit=int(arguments.get("limit") or 20),
        )
    elif name == "memory/semantic_grep":
        packet = semantic_grep_packet(str(arguments.get("query") or ""), cwd=cwd, limit=int(arguments.get("limit") or 8))
    elif name == "memory/maintain":
        packet = build_memory_maintenance(cwd=cwd, limit=int(arguments.get("limit") or 250))
    else:
        raise ValueError(f"unknown tool: {name}")
    return json.dumps(packet, ensure_ascii=False, indent=2)


def semantic_grep_packet(query: str, *, cwd: Path | None = None, limit: int = 8) -> Dict[str, Any]:
    packet = build_recall(query, cwd=cwd, limit=limit, include_pending=True)
    hits = []
    for item in packet.get("memories") or []:
        hits.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "score": item.get("score"),
                "truth": item.get("truth"),
                "why": item.get("why") or [],
                "source_ref": item.get("source_ref") or item.get("path"),
                "smfs_path": semantic_file_path(item),
                "snippet": compact_text(str(item.get("summary") or ""), 220),
            }
        )
    return {
        "schema": "xmem.smfs_grep.v1",
        "query": query,
        "mode": "semantic_grep_lite",
        "signals": packet.get("ranking", {}).get("signals") or [],
        "hits": hits,
        "pending_hints": packet.get("pending") or [],
        "token_estimate": packet.get("token_estimate", 0),
    }


def semantic_file_path(item: Dict[str, Any]) -> str:
    card_id = str(item.get("id") or "memory")
    source = str(item.get("source_ref") or item.get("path") or "")
    match = re.search(r"/([^/]+)/\.xmem/cards/", source)
    project = match.group(1) if match else "global"
    return str(home_dir() / "smfs" / "cards" / slugify(project, "project") / f"{slugify(card_id, 'memory')}.md")


def result_response(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
