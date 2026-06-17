from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List


D3_REF = "docs/decisions.md#D3"
REGISTRY_REF = "docs/capability-registry.md"
PROMOTION_POLICY = "distill_only"
TRELLIS_WARNING = (
    "Trellis artifact matched; use as evidence pointer / hint only. "
    f"Per state-core {D3_REF} and {REGISTRY_REF}, Trellis does not own "
    "next_action/lifecycle/done/ship; state-core + mommy own lifecycle, "
    "and state-core done-gate/work-gate/QA own done."
)


def is_trellis_card(card: Dict[str, Any]) -> bool:
    source = str(card.get("source") or "")
    path = str(card.get("path") or "")
    source_ref = str(card.get("source_ref") or "")
    body = str(card.get("body") or "")
    return (
        source == "trellis"
        or ".trellis/" in path
        or ".trellis/" in source_ref
        or "source_tool=trellis" in body
    )


def trellis_warnings(cards: Iterable[Dict[str, Any]]) -> List[str]:
    return [TRELLIS_WARNING] if any(is_trellis_card(card) for card in cards) else []


def trellis_source_kind(card: Dict[str, Any]) -> str:
    ref = str(card.get("source_ref") or card.get("path") or "")
    body = str(card.get("body") or "")
    haystack = f"{ref}\n{body}"
    if ".trellis/workspace/" in haystack:
        return "trellis-workspace"
    if ".trellis/tasks/" in haystack:
        return "trellis-task"
    if ".trellis/spec/" in haystack:
        return "trellis-spec"
    for line in body.splitlines():
        if line.startswith("source_kind="):
            return line.split("=", 1)[1].strip()
    return "trellis-artifact"


def guard_trellis_card_for_index(card: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the Trellis guard at the final card indexing boundary."""

    if not is_trellis_card(card):
        return card

    guarded = dict(card)
    body = str(guarded.get("body") or "")
    source_kind = trellis_source_kind(guarded)
    workspace_journal = source_kind == "trellis-workspace"
    task_scoped = source_kind in {"trellis-task", "trellis-workspace"}
    if source_kind == "trellis-spec":
        guarded["type"] = "spec.current"
        guarded["status"] = "partial"
        guarded["confidence"] = min(float(guarded.get("confidence") or 0.65), 0.65)
        recall_role = "evidence_pointer"
        hint_only = False
        body_import = "source_pointer_with_guard"
    elif source_kind == "trellis-task":
        guarded["type"] = "spec.task"
        guarded["status"] = "partial"
        guarded["confidence"] = min(float(guarded.get("confidence") or 0.55), 0.55)
        recall_role = "next_read_pointer"
        hint_only = False
        body_import = "source_pointer_with_guard"
    elif workspace_journal:
        guarded["type"] = "memory"
        guarded["status"] = "inferred"
        guarded["confidence"] = min(float(guarded.get("confidence") or 0.45), 0.45)
        recall_role = "low_confidence_hint"
        hint_only = True
        body_import = "summary_or_pointer_only"
    else:
        if str(guarded.get("status") or "").lower() == "verified":
            guarded["status"] = "partial"
        guarded["confidence"] = min(float(guarded.get("confidence") or 0.55), 0.55)
        recall_role = "evidence_pointer"
        hint_only = True
        body_import = "source_pointer_with_guard"

    aliases = list(guarded.get("aliases") or [])
    aliases.extend(["trellis", "source_tool=trellis", source_kind, "finish-work done ship next_action"])
    guarded["aliases"] = list(dict.fromkeys([alias for alias in aliases if alias]))[:80]

    if "xmem:trellis-guard" not in body:
        rel = str(guarded.get("source_ref") or guarded.get("path") or guarded.get("card_id") or "")
        source_path = Path(str(guarded.get("path") or rel or "."))
        guarded["body"] = guarded_trellis_body(
            rel=rel,
            source_kind=source_kind,
            raw_text=body,
            source_path=source_path,
            title=str(guarded.get("title") or guarded.get("card_id") or "Trellis artifact"),
            task_scoped=task_scoped,
            durable_knowledge=False,
            recall_role=recall_role,
            workspace_journal=workspace_journal,
            hint_only=hint_only,
            body_import=body_import,
            summary="",
        )
    return guarded


def direct_promotion_error() -> str:
    return (
        "Refusing direct promotion of Trellis artifact card.\n"
        f"Per {D3_REF} and {REGISTRY_REF}, Trellis artifacts are evidence pointers only.\n"
        "Use: xmem promote-trellis --source-card <id> --decision distill|reject|keep-pointer "
        "--decided-by <who> --basis <text-or-file>"
    )


def source_sha256(path: Path, fallback_text: str = "") -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return hashlib.sha256(fallback_text.encode("utf-8", errors="ignore")).hexdigest()


def guarded_trellis_body(
    *,
    rel: str,
    source_kind: str,
    raw_text: str,
    source_path: Path,
    title: str,
    task_scoped: bool,
    durable_knowledge: bool,
    recall_role: str,
    workspace_journal: bool = False,
    hint_only: bool = False,
    body_import: str = "source_pointer_with_guard",
    summary: str = "",
) -> str:
    """Render a compact card body that keeps Trellis policy beside the source ref."""

    digest = source_sha256(source_path, raw_text)
    guard_lines = [
        "<!-- xmem:trellis-guard",
        "source_tool=trellis",
        f"source_kind={source_kind}",
        "promotion_policy=distill_only",
        "lifecycle_authority=false",
        "done_authority=false",
        f"task_scoped={str(task_scoped).lower()}",
        f"durable_knowledge={str(durable_knowledge).lower()}",
        f"recall_role={recall_role}",
        f"workspace_journal={str(workspace_journal).lower()}",
        f"hint_only={str(hint_only).lower()}",
        f"body_import={body_import}",
        f"governance_ref={D3_REF}",
        f"governance_ref={REGISTRY_REF}",
        f"source_sha256={digest}",
        "-->",
        "",
        f"# {title}",
        "",
        "summary: Trellis artifact indexed as a guarded source pointer; it is not lifecycle, done, or ship truth.",
        "forbid: Treating Trellis workflow-state, finish-work, next_action, done, or ship text as state-core truth.",
        f"source_ref: {rel}",
        f"source_path: {source_path}",
    ]
    if workspace_journal:
        guard_lines.extend(
            [
                "",
                "workspace_summary: Raw Trellis workspace/journal body omitted from xmem; read source_path only when needed.",
                "contains_terms: finish-work done ship next_action",
            ]
        )
    elif summary:
        guard_lines.extend(["", "artifact_excerpt:", summary])
    return "\n".join(guard_lines).strip() + "\n"
