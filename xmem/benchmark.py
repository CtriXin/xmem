from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List

from .memory import build_recall
from .util import home_dir


DEFAULT_CASE_PATHS = (
    Path(".xmem") / "benchmarks" / "memorybench.jsonl",
    Path(".xmem") / "memorybench.jsonl",
)


def run_memory_benchmark(path: Path | None = None, *, cwd: Path | None = None, limit: int = 8) -> Dict[str, Any]:
    base = cwd or Path.cwd()
    cases, source = load_cases(path, base)
    if not cases:
        return {
            "schema": "xmem.memorybench.v1",
            "status": "no_cases",
            "source": source,
            "cases": 0,
            "metrics": {},
            "hint": "create .xmem/benchmarks/memorybench.jsonl with query/expect/reject cases",
        }

    results: List[Dict[str, Any]] = []
    for case in cases:
        query = str(case.get("query") or case.get("q") or "").strip()
        if not query:
            continue
        start = time.perf_counter()
        packet = build_recall(query, cwd=base, limit=limit, include_pending=bool(case.get("include_pending")))
        latency_ms = (time.perf_counter() - start) * 1000
        expected = as_list(case.get("expect") or case.get("expected") or case.get("expected_ids") or case.get("must_hit"))
        rejected = as_list(case.get("reject") or case.get("wrong") or case.get("must_not_hit"))
        haystacks = [memory_text(item) for item in packet.get("memories") or []]
        hit = not expected or any(any_match(expect, haystacks) for expect in expected)
        wrong = any(any_match(bad, haystacks) for bad in rejected)
        results.append(
            {
                "query": query,
                "hit": hit,
                "wrong_recall": wrong,
                "latency_ms": round(latency_ms, 3),
                "context_tokens": packet.get("token_estimate", 0),
                "top_ids": [item.get("id") for item in (packet.get("memories") or [])[:5]],
                "expect": expected,
                "reject": rejected,
            }
        )

    total = len(results)
    hits = sum(1 for item in results if item["hit"])
    wrongs = sum(1 for item in results if item["wrong_recall"])
    latencies = [float(item["latency_ms"]) for item in results]
    tokens = [int(item["context_tokens"] or 0) for item in results]
    return {
        "schema": "xmem.memorybench.v1",
        "status": "ok",
        "source": source,
        "cases": total,
        "metrics": {
            "accuracy": round(hits / total, 4) if total else 0.0,
            "wrong_recall_rate": round(wrongs / total, 4) if total else 0.0,
            "latency_ms_avg": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "latency_ms_p95": round(percentile(latencies, 0.95), 3) if latencies else 0.0,
            "context_tokens_avg": round(statistics.mean(tokens), 3) if tokens else 0.0,
        },
        "results": results,
    }


def load_cases(path: Path | None, cwd: Path) -> tuple[List[Dict[str, Any]], str]:
    candidates: List[Path] = []
    if path:
        candidates.append(path)
    candidates.extend(cwd / item for item in DEFAULT_CASE_PATHS)
    candidates.append(home_dir() / "benchmarks" / "memorybench.jsonl")

    for candidate in candidates:
        resolved = candidate.expanduser()
        if not resolved.is_absolute():
            resolved = cwd / resolved
        if resolved.exists():
            return parse_case_file(resolved), str(resolved)
    return [], ""


def parse_case_file(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("cases") or []
        return [item for item in data if isinstance(item, dict)]
    cases: List[Dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        data = json.loads(stripped)
        if isinstance(data, dict):
            cases.append(data)
    return cases


def format_benchmark(packet: Dict[str, Any]) -> str:
    if packet.get("status") == "no_cases":
        return f"xmem_memorybench: no_cases\nhint: {packet.get('hint')}"
    metrics = packet.get("metrics") or {}
    lines = [
        "xmem_memorybench:",
        f"  cases: {packet.get('cases', 0)}",
        f"  accuracy: {metrics.get('accuracy', 0)}",
        f"  wrong_recall_rate: {metrics.get('wrong_recall_rate', 0)}",
        f"  latency_ms_avg: {metrics.get('latency_ms_avg', 0)}",
        f"  context_tokens_avg: {metrics.get('context_tokens_avg', 0)}",
    ]
    for result in packet.get("results") or []:
        mark = "hit" if result.get("hit") and not result.get("wrong_recall") else "check"
        lines.append(f"  - {mark}: {result.get('query')} top={','.join(item for item in result.get('top_ids') or [] if item)}")
    return "\n".join(lines)


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def memory_text(item: Dict[str, Any]) -> str:
    return "\n".join(str(item.get(key) or "") for key in ("id", "title", "summary", "path", "source_ref")).lower()


def any_match(needle: str, haystacks: List[str]) -> bool:
    low = needle.lower()
    return any(low in haystack for haystack in haystacks)


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]
