from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .toon import compact
from .source_check import source_freshness
from .sources import audit_local_sources
from .util import field_from_text, list_after_key, normalize_text, query_terms


REGISTRY_TYPES = {"identity", "wiki.service", "wiki.repo", "wiki.domain", "wiki.project"}
RULE_TYPES = {"invariant", "rule", "guard"}
METHOD_TYPES = {"method", "playbook", "howto"}
EVIDENCE_TYPES = {"evidence.issue"}
ALIAS_TYPES = {"alias", "canonical-alias"}
RELATION_TYPES = {"relation", "link"}
CORRECTION_TYPES = {"correction", "alias-correction"}
MEMORY_TYPES = {"hook.memory", "memory"}
SPEC_TYPES = {
    "context.terms",
    "decision.adr",
    "spec.current",
    "spec.change",
    "spec.plan",
    "spec.task",
    "spec.constitution",
}
CODE_TYPES = {"code.index", "code.hotspot"}
TRAFFIC_TYPES = {"traffic.switch", "traffic-switch", "scmp.traffic-switch"}
STATUS_RANK = {"verified": 60, "partial": 40, "inferred": 30, "stale": 20, "unknown": 10, "disputed": 0}
SOURCE_RANK = {
    "local-card": 100,
    "card-file": 95,
    "issue-bug-patterns": 90,
    "context-docs": 88,
    "openspec": 86,
    "speckit": 84,
    "issue-tracking-export": 82,
    "trellis": 80,
    "project-wiki-export": 78,
    "project-wiki": 70,
    "code-index-bridge": 66,
    "project-wiki-pending": 64,
    "xmem-project-wiki-outbox": 62,
    "issue-tracking": 60,
    "xmem-issue-outbox": 58,
}


def build_context(query: str, current: Dict[str, Any] | None, cards: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    cards = fuse_related_cards(cards)
    alias_cards = [c for c in cards if c.get("type") in ALIAS_TYPES]
    corrections = [c for c in cards if c.get("type") in CORRECTION_TYPES]
    registry = [c for c in cards if c.get("type") in REGISTRY_TYPES or str(c.get("type", "")).startswith("wiki.")]
    rules = [c for c in cards if c.get("type") in RULE_TYPES]
    methods = [c for c in cards if c.get("type") in METHOD_TYPES]
    relations = [c for c in cards if c.get("type") in RELATION_TYPES]
    evidence = [c for c in cards if c.get("type") in EVIDENCE_TYPES]
    memories = [c for c in cards if c.get("type") in MEMORY_TYPES]
    specs = [c for c in cards if c.get("type") in SPEC_TYPES]
    code_indexes = [c for c in cards if c.get("type") in CODE_TYPES]
    traffic = compact_traffic_switches([c for c in cards if c.get("type") in TRAFFIC_TYPES])
    strong_alias = [c for c in alias_cards if is_strong_identity_match(c, query, min_score=8)]
    strong_relation = [c for c in relations if is_strong_identity_match(c, query, min_score=12)]
    strong_correction = [c for c in corrections if c.get("status") in {"verified", "disputed"} and float(c.get("score") or 0) >= 8]
    strong_registry = [c for c in registry if is_strong_identity_match(c, query, min_score=8)]
    strong_traffic = [c for c in traffic if is_strong_identity_match(c, query, min_score=8)]
    correction_guidance_items = correction_guidance(query, corrections)
    suggested_queries = canonical_queries_from_corrections(query, corrections)
    allow_high_signal_hints = looks_like_specific_service_or_domain(query)

    dominant_registry = dominant_strong_registry(strong_registry)

    if not cards:
        resolution = "missing"
    elif strong_correction:
        resolution = "guided_by_correction"
    elif len(strong_registry) == 1 or dominant_registry:
        resolution = "resolved"
    elif len(strong_registry) > 1:
        resolution = "ambiguous"
    elif len(strong_traffic) == 1:
        resolution = "resolved"
    elif len(strong_traffic) > 1:
        resolution = "ambiguous"
    elif len(strong_relation) == 1:
        resolution = "resolved"
    elif len(strong_relation) > 1:
        resolution = "ambiguous"
    elif strong_alias:
        resolution = "guided_by_alias_card"
    else:
        resolution = "partial"

    warnings: List[str] = []
    if resolution in {"ambiguous", "guided_by_alias_card", "guided_by_correction"}:
        warnings.append("multiple verified registry candidates matched; do not assume a single project")
    if correction_guidance_items:
        warnings.append("query matched a correction/dispute overlay; prefer canonical aliases and verify before editing")
    if any(c.get("status") in {"inferred", "partial", "stale", "unknown", "disputed"} for c in cards[:5]) and not ((strong_traffic or strong_relation) and not allow_high_signal_hints):
        warnings.append("some top cards are not verified; use as hints only")
    if any(c.get("source") == "code-index-bridge" for c in cards[:8]):
        warnings.append("code index matches are generated refs; verify in source files before editing")
    if any(str(c.get("source") or "").startswith("xmem-") and "outbox" in str(c.get("source") or "") for c in cards[:8]):
        warnings.append("xmem outbox matches are pending writebacks/seeds; verify with Project Wiki or Issue Record before treating as truth")
    freshness = source_freshness()
    if freshness.get("status") != "fresh":
        warnings.append("source exports are newer than registry or registry is missing; run xmem sync before relying on this packet")
    local_source_health = local_source_health_brief(audit_local_sources(), cards)
    if local_source_health.get("local_only_knowledge_cards"):
        warnings.append("some local knowledge cards are not portable through git; treat them as machine-local until tracked or exported")

    registry_anchor = dominant_registry or (strong_registry[0] if len(strong_registry) == 1 else None)
    registry_for_packet = compact_registry_candidates(
        registry,
        bool(strong_registry),
        bool(strong_traffic),
        bool(strong_relation),
        allow_high_signal_hints=allow_high_signal_hints,
        anchor=registry_anchor,
    )
    relations_for_packet = compact_relation_cards(relations, strong_relation, has_identity_anchor=bool(strong_registry or strong_traffic))
    next_reads = unique_paths(traffic[:3] + evidence[:3] + relations_for_packet[:4] + registry_for_packet[:4] + rules[:3] + methods[:3] + specs[:4] + code_indexes[:5] + memories[:3])
    symbolic_sections = {
        "traffic_switch": traffic[:4],
        "registry_candidates": registry_for_packet,
        "rules": rules[:5],
        "methods": methods[:5],
        "memories": memories[:5],
        "specs": specs[:6],
        "code_indexes": code_indexes[:8],
        "relations": relations_for_packet,
        "evidence": evidence[:6],
    }
    packet = {
        "schema": "xmem.context.v1",
        "truth_policy": "files/code/runtime are truth; sqlite is generated index/cache",
        "query": query,
        "symbolic_memory": symbolic_memory_brief(symbolic_sections),
        "resolution": {
            "status": resolution,
            "do_not_assume_single_project": resolution in {"ambiguous", "guided_by_alias_card", "guided_by_correction"},
            "reason": resolution_reason(resolution, [dominant_registry] if dominant_registry else strong_registry, strong_traffic, strong_relation, cards, correction_guidance_items),
        },
        "current": current_brief(current),
        "suggested_queries": suggested_queries,
        "correction_guidance": correction_guidance_items,
        "corrections": [card_brief(c, i + 1) for i, c in enumerate(corrections[:5])],
        "alias_guidance": [card_brief(c, i + 1) for i, c in enumerate(alias_cards[:5])],
        "traffic_switch": [traffic_switch_brief(c, i + 1) for i, c in enumerate(traffic[:4])],
        "gain_hints": gain_hints(traffic, registry, code_indexes),
        "registry_candidates": [card_brief(c, i + 1) for i, c in enumerate(registry_for_packet)],
        "rules": [card_brief(c, i + 1) for i, c in enumerate(rules[:5])],
        "methods": [card_brief(c, i + 1) for i, c in enumerate(methods[:5])],
        "memories": [card_brief(c, i + 1) for i, c in enumerate(memories[:5])],
        "specs": [card_brief(c, i + 1) for i, c in enumerate(specs[:6])],
        "code_indexes": [card_brief(c, i + 1) for i, c in enumerate(code_indexes[:8])],
        "relations": [card_brief(c, i + 1) for i, c in enumerate(relations_for_packet)],
        "evidence": [card_brief(c, i + 1) for i, c in enumerate(evidence[:6])],
        "source_freshness": freshness_brief(freshness),
        "local_source_health": local_source_health,
        "warnings": warnings,
        "next_reads": next_reads[:10],
        "latest_events": event_briefs(events),
    }
    return packet


def compact_registry_candidates(
    registry: List[Dict[str, Any]],
    has_verified_registry_anchor: bool,
    has_verified_traffic_anchor: bool = False,
    has_verified_relation_anchor: bool = False,
    allow_high_signal_hints: bool = False,
    anchor: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    if anchor:
        anchored = registry_family_candidates(anchor, registry)
        if anchored:
            return anchored[:8]
    if (has_verified_traffic_anchor or has_verified_relation_anchor) and not has_verified_registry_anchor:
        # Verified traffic/relation cards are stronger task anchors than weak
        # legacy Project Wiki template matches such as "模版一".
        verified = [c for c in registry if c.get("status") == "verified" and float(c.get("score") or 0) >= 8]
        if not allow_high_signal_hints:
            return verified[:4]
        high_signal_hints = [c for c in registry if c.get("status") != "verified" and float(c.get("score") or 0) >= 8]
        return (verified[:4] + high_signal_hints[:2])[:6]
    if not has_verified_registry_anchor:
        return registry[:8]
    verified = [c for c in registry if c.get("status") == "verified"]
    others = [c for c in registry if c.get("status") != "verified"]
    return (verified[:6] + others[:2])[:8]


def registry_family_candidates(anchor: Dict[str, Any], registry: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anchor_keys = identity_family_keys(anchor)
    if not anchor_keys:
        return [anchor]
    verified: List[Dict[str, Any]] = []
    hints: List[Dict[str, Any]] = []
    for card in registry:
        if card.get("card_id") == anchor.get("card_id"):
            verified.append(card)
            continue
        if not (anchor_keys & identity_family_keys(card)):
            continue
        if card.get("status") == "verified":
            verified.append(card)
        elif float(card.get("score") or 0) >= 8:
            hints.append(card)
    return dedupe_cards(verified + hints[:2])


def identity_family_keys(card: Dict[str, Any]) -> set[str]:
    values: List[str] = [str(card.get("title", "")), str(card.get("card_id", ""))]
    try:
        values.extend(json.loads(card.get("aliases_json") or "[]"))
    except Exception:
        pass
    keys: set[str] = set()
    for value in values:
        if not is_compact_identity_alias(value):
            continue
        norm = normalize_text(value)
        keys.add(norm)
        keys.update(re.findall(r"[\u4e00-\u9fff]+[0-9]+|[a-z]+[0-9]+", norm))
        if looks_like_specific_service_or_domain(value):
            keys.add(norm.replace("gitgitlabadsconfluxxyzptcfe", "").removesuffix("git"))
    return {key for key in keys if is_identity_family_key(key)}


def is_identity_family_key(key: str) -> bool:
    if not key or key in IDENTITY_ALIAS_STOPWORDS:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in key):
        return any(char.isdigit() for char in key) or len(key) >= 4
    return len(key) >= 4


def dedupe_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        cid = str(card.get("card_id") or card.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(card)
    return out


def compact_relation_cards(relations: List[Dict[str, Any]], strong_relations: List[Dict[str, Any]], *, has_identity_anchor: bool = False) -> List[Dict[str, Any]]:
    if has_identity_anchor and not strong_relations:
        # Once a verified service/repo/traffic anchor is found, weak relation
        # cards (for example same-template ad-sheet matches) become noise.
        return []
    if not strong_relations:
        return relations[:5]
    top_score = max(float(c.get("score") or 0) for c in strong_relations)
    threshold = top_score * 0.5
    extras = [
        c for c in relations
        if c not in strong_relations and float(c.get("score") or 0) >= threshold
    ]
    return (strong_relations[:4] + extras[:2])[:5]


def looks_like_specific_service_or_domain(query: str) -> bool:
    text = str(query or "").strip()
    return "." in text or "-" in text or "/" in text or "_" in text


IDENTITY_ALIAS_STOPWORDS = {
    "alias",
    "aliases",
    "repo",
    "service",
    "domain",
    "deployment",
    "branch",
    "project",
    "implements",
    "uses_repo",
    "belongs_to",
    "deployed_as",
    "has_branch",
    "source_of",
    "validation_service",
    "production_service",
    "prod_service",
}


def is_strong_identity_match(card: Dict[str, Any], query: str, *, min_score: float) -> bool:
    if card.get("status") != "verified" or float(card.get("score") or 0) < min_score:
        return False
    why = str(card.get("why") or "")
    if "exact_alias:" in why:
        return True
    qnorm = normalize_text(query)
    if not qnorm:
        return False
    aliases: List[str] = []
    try:
        aliases = json.loads(card.get("aliases_json") or "[]")
    except Exception:
        aliases = []
    for value in [*aliases, card.get("title", ""), card.get("card_id", "")]:
        anorm = normalize_text(value)
        if not (qnorm and anorm):
            continue
        if anorm == qnorm:
            return True
        if anorm in qnorm:
            if looks_like_specific_service_or_domain(value) or len(anorm) >= 8 or is_compact_identity_alias(value):
                return True
        if is_compact_identity_alias(value) and alias_terms_match_query(value, query):
            return True
        if qnorm in anorm:
            if looks_like_specific_service_or_domain(query) or len(qnorm) >= max(8, int(len(anorm) * 0.75)):
                return True
    if "metadata_match:" in why and looks_like_specific_service_or_domain(query):
        return True
    return False


def is_compact_identity_alias(value: Any) -> bool:
    text = str(value or "").strip()
    norm = normalize_text(text)
    if not norm or norm in IDENTITY_ALIAS_STOPWORDS:
        return False
    if len(norm) < 2:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return len(norm) >= 2
    if any(char.isdigit() for char in norm):
        return len(norm) >= 4
    if any(char in text for char in "-_./"):
        return len(norm) >= 5
    return False


def alias_terms_match_query(alias: Any, query: str) -> bool:
    terms = [normalize_text(term) for term in query_terms(str(alias)) if normalize_text(term)]
    meaningful = [term for term in terms if term not in IDENTITY_ALIAS_STOPWORDS and len(term) >= 1]
    if len(meaningful) < 2:
        return False
    qnorm = normalize_text(query)
    return all(term in qnorm for term in meaningful)


def dominant_strong_registry(strong_registry: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if len(strong_registry) < 2:
        return None
    first = strong_registry[0]
    second = strong_registry[1]
    first_score = float(first.get("score") or 0)
    second_score = float(second.get("score") or 0)
    if first_score - second_score >= 2.0:
        return first
    return None


def compact_traffic_switches(traffic: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not traffic:
        return []
    top_score = float(traffic[0].get("score") or 0)
    if top_score < 8:
        return []
    if top_score < 10:
        return traffic[:2]
    threshold = top_score * 0.85
    return [card for card in traffic if float(card.get("score") or 0) >= threshold][:4]


def gain_hints(traffic: List[Dict[str, Any]], registry: List[Dict[str, Any]], code_indexes: List[Dict[str, Any]]) -> List[str]:
    hints: List[str] = []
    if any(c.get("status") == "verified" for c in traffic):
        hints.append("skip broad repo/issue scan for project identity; start from traffic_switch prod/validation service and repo hints")
        hints.append("read traffic_switch source_refs only when facts conflict or mapping must be promoted")
    if registry:
        hints.append("skip Project Wiki full-text search unless registry candidates are partial/conflicting")
    if code_indexes:
        hints.append("skip full repo rg first; use code_indexes/hotspots as directed entry points, then verify in source")
    return hints[:5]


def traffic_switch_brief(card: Dict[str, Any], rank: int) -> Dict[str, Any]:
    body = str(card.get("body") or "")
    brief = card_brief(card, rank)
    brief.update({
        "project": field_from_text(body, "project"),
        "template": field_from_text(body, "template"),
        "repo": field_from_text(body, "repo"),
        "prod_service": field_from_text(body, "prod_service"),
        "validation_service": field_from_text(body, "validation_service"),
        "prod_pipeline_hint": field_from_text(body, "prod_pipeline_hint"),
        "validation_pipeline_hint": field_from_text(body, "validation_pipeline_hint"),
        "prod_branch_hint": field_from_text(body, "prod_branch_hint"),
        "validation_branch_hint": field_from_text(body, "validation_branch_hint"),
        "approval_group": field_from_text(body, "approval_group"),
        "role_semantics": list_after_key(body, "role_semantics")[:6],
        "repo_local_hints": list_after_key(body, "repo_local_hints")[:6],
        "domains": list_after_key(body, "domains")[:20],
        "common_verification": list_after_key(body, "common_verification")[:8],
        "can_skip": list_after_key(body, "can_skip")[:8],
        "stale_policy": list_after_key(body, "stale_policy")[:8],
    })
    return brief


def local_source_health_brief(audit: Dict[str, Any], cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    matched_paths = [str(card.get("path") or card.get("source_ref") or "") for card in cards]
    details = []
    for item in audit.get("details", []):
        root = str(item.get("root") or "")
        if not root or not item.get("local_only_knowledge_cards"):
            continue
        if not any(path.startswith(root + "/") or path == root for path in matched_paths):
            continue
        details.append({
            "root": root,
            "local_only_knowledge_cards": int(item.get("local_only_knowledge_cards") or 0),
            "status": item.get("status", ""),
        })
    local_only = sum(item["local_only_knowledge_cards"] for item in details)
    if not local_only:
        return {}
    return {
        "knowledge_cards": int(audit.get("knowledge_cards") or 0),
        "tracked_cards": int(audit.get("tracked_cards") or 0),
        "local_only_knowledge_cards": local_only,
        "ignored_knowledge_cards": sum(
            int(item.get("ignored_knowledge_cards") or 0)
            for item in audit.get("details", [])
            if any(detail["root"] == item.get("root") for detail in details)
        ),
        "roots": details[:5],
    }


def canonical_queries_from_corrections(query: str, corrections: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    out: List[str] = []
    for item in correction_guidance(query, corrections):
        for alias in item.get("canonical_aliases") or []:
            alias = str(alias or "").strip()
            if alias and alias not in out:
                out.append(alias)
            if len(out) >= limit:
                return out
    return out


def correction_guidance(query: str, corrections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    qnorm = normalize_text(query)
    out: List[Dict[str, Any]] = []
    for card in corrections:
        body = str(card.get("body") or "")
        wrong_aliases = list_after_key(body, "wrong_aliases")
        canonical_aliases = list_after_key(body, "canonical_aliases")
        if not wrong_aliases and not canonical_aliases:
            continue
        matched_wrong = [
            alias for alias in wrong_aliases
            if alias and (normalize_text(alias) in qnorm or qnorm in normalize_text(alias))
        ]
        if not matched_wrong and card.get("status") != "disputed":
            continue
        out.append({
            "id": card.get("card_id", ""),
            "truth": card.get("status", ""),
            "wrong_aliases": wrong_aliases[:6],
            "canonical_aliases": canonical_aliases[:6],
            "matched_wrong_aliases": matched_wrong[:6],
            "source_path": card.get("path") or card.get("source_ref", ""),
        })
    return out[:5]


def resolution_reason(
    resolution: str,
    strong_registry: List[Dict[str, Any]],
    strong_traffic: List[Dict[str, Any]],
    strong_relation: List[Dict[str, Any]],
    cards: List[Dict[str, Any]],
    guidance: List[Dict[str, Any]] | None = None,
) -> str:
    if resolution == "missing":
        return "no indexed card matched query"
    if resolution == "guided_by_correction":
        if guidance:
            aliases = []
            for item in guidance:
                aliases.extend(item.get("canonical_aliases") or [])
            if aliases:
                return "correction/dispute matched; prefer canonical aliases: " + ", ".join(dict.fromkeys(aliases[:5]))
        return f"correction/dispute card matched: {strong_registry_label(strong_registry)}"
    if resolution == "guided_by_alias_card":
        return f"canonical alias guidance matched: {strong_registry_label(strong_registry)}"
    if resolution == "resolved" and strong_registry:
        return f"one verified registry candidate matched strongly: {strong_registry[0].get('card_id')}"
    if resolution == "resolved" and strong_traffic:
        return f"one verified traffic-switch card matched strongly: {strong_traffic[0].get('card_id')}"
    if resolution == "resolved" and strong_relation:
        return f"one verified relation card matched strongly: {strong_relation[0].get('card_id')}"
    if resolution == "ambiguous":
        if strong_registry:
            return f"{len(strong_registry)} verified registry candidates matched strongly"
        if strong_traffic:
            return f"{len(strong_traffic)} verified traffic-switch cards matched strongly"
        if strong_relation:
            return f"{len(strong_relation)} verified relation cards matched strongly"
    return "matched cards exist, but no single verified registry candidate is strong enough"


def strong_registry_label(strong_registry: List[Dict[str, Any]]) -> str:
    if not strong_registry:
        return "no strong registry candidate"
    return f"{len(strong_registry)} verified registry candidates still require disambiguation"


def current_brief(current: Dict[str, Any] | None) -> Dict[str, Any]:
    if not current:
        return {}
    return {
        "project_id": current.get("project_id", ""),
        "root": current.get("root", ""),
        "branch": current.get("branch", ""),
        "git_sha": current.get("git_sha", ""),
        "tech_stack": current.get("tech_stack", ""),
    }


def card_brief(card: Dict[str, Any], rank: int) -> Dict[str, Any]:
    aliases: List[str] = []
    try:
        aliases = json.loads(card.get("aliases_json") or "[]")
    except Exception:
        aliases = []
    brief = {
        "rank": rank,
        "id": card.get("card_id", ""),
        "node_id": node_id_for_card(card),
        "memory_layer": memory_layer_for_card(card),
        "project_id": card.get("project_id", ""),
        "type": card.get("type", ""),
        "truth": card.get("status", ""),
        "confidence": float(card.get("confidence") or 0),
        "score": float(card.get("score") or 0),
        "source": card.get("source", ""),
        "source_ref": card.get("source_ref", ""),
        "source_path": card.get("path", ""),
        "evidence_ref": evidence_ref_for_card(card),
        "title": compact(card.get("title", ""), 96),
        "why": card.get("why", ""),
        "hints": card_hints(card.get("body", "")),
        "aliases": aliases[:8],
    }
    supporting = card.get("_supporting_cards") or []
    if supporting:
        brief["supporting_cards"] = [supporting_card_brief(c) for c in supporting[:4]]
        brief["supporting_count"] = len(supporting)
    return brief


def supporting_card_brief(card: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": card.get("card_id", ""),
        "node_id": node_id_for_card(card),
        "memory_layer": memory_layer_for_card(card),
        "type": card.get("type", ""),
        "truth": card.get("status", ""),
        "confidence": float(card.get("confidence") or 0),
        "score": float(card.get("score") or 0),
        "source": card.get("source", ""),
        "source_ref": card.get("source_ref", ""),
        "source_path": card.get("path", ""),
        "evidence_ref": evidence_ref_for_card(card),
        "title": compact(card.get("title", ""), 96),
    }


def symbolic_memory_brief(sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    counts = {"L3": 0, "L2": 0, "L1": 0, "L0": 0}
    for cards in sections.values():
        for card in cards:
            layer_id = memory_layer_for_card(card).split(":", 1)[0]
            if layer_id in counts:
                counts[layer_id] += 1
    return {
        "mode": "layered_symbolic",
        "top_canvas": "read packet sections first; each item has node_id and evidence_ref for drilldown",
        "drilldown": "use xmem open <card_id> or read evidence_ref/source_path; files/code/runtime remain truth",
        "layers": [
            {"id": "L3", "name": "policy_profile", "purpose": "rules, guardrails, SOP, user/project preferences", "count": counts["L3"]},
            {"id": "L2", "name": "scenario_pattern", "purpose": "methods, specs, traffic/relation patterns, reusable fixes", "count": counts["L2"]},
            {"id": "L1", "name": "atom_card", "purpose": "entity facts, aliases, corrections, compact memory cards", "count": counts["L1"]},
            {"id": "L0", "name": "raw_evidence", "purpose": "issue/code/source refs used to recover ground truth", "count": counts["L0"]},
        ],
    }


def node_id_for_card(card: Dict[str, Any]) -> str:
    raw = str(card.get("card_id") or card.get("id") or card.get("source_ref") or card.get("path") or "")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()
    return f"n_{slug[:80]}" if slug else ""


def evidence_ref_for_card(card: Dict[str, Any]) -> str:
    return str(card.get("source_ref") or card.get("path") or "")


def memory_layer_for_card(card: Dict[str, Any]) -> str:
    card_type = str(card.get("type") or "")
    source = str(card.get("source") or "")
    if card_type in EVIDENCE_TYPES or card_type in CODE_TYPES or "issue" in source or "code-index" in source:
        return "L0:raw_evidence"
    if card_type in METHOD_TYPES or card_type in SPEC_TYPES or card_type in RELATION_TYPES or card_type in TRAFFIC_TYPES:
        return "L2:scenario_pattern"
    if card_type in RULE_TYPES or card_type in {"guard", "policy"}:
        return "L3:policy_profile"
    if card_type in REGISTRY_TYPES or card_type.startswith("wiki.") or card_type in ALIAS_TYPES or card_type in CORRECTION_TYPES or card_type in MEMORY_TYPES:
        return "L1:atom_card"
    return "L1:atom_card"


def freshness_brief(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": data.get("status", ""),
        "stale_exports": data.get("stale_exports", 0),
        "registry": data.get("registry", ""),
        "stale": [
            {"kind": item.get("kind", ""), "path": item.get("path", "")}
            for item in (data.get("stale") or [])[:5]
        ],
    }


def card_hints(body: str) -> List[str]:
    hints: List[str] = []
    if not body:
        return hints
    keys = ("summary:", "do_not_assume:", "resolution:", "must_include:", "forbid:", "wrong_aliases:", "canonical_aliases:", "effect:")
    capture = False
    for line in body.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(key) for key in keys):
            capture = True
            hints.append(stripped)
            continue
        if capture and (stripped.startswith("- ") or line.startswith("  ")):
            hints.append(stripped)
            if len(hints) >= 10:
                break
            continue
        if capture and stripped and not line.startswith(" "):
            capture = False
    return hints[:10]


def unique_paths(cards: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for card in cards:
        for item in [card, *(card.get("_supporting_cards") or [])]:
            path = item.get("path") or item.get("source_ref")
            if path and path not in out:
                out.append(path)
    return out


def fuse_related_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    singles: List[Dict[str, Any]] = []
    for card in cards:
        key = fusion_key(card)
        if not key:
            singles.append(card)
            continue
        groups.setdefault(key, []).append(card)

    fused: List[Dict[str, Any]] = [dict(card) for card in singles]
    for members in groups.values():
        if len(members) == 1:
            fused.append(dict(members[0]))
            continue
        ordered = sorted(members, key=fusion_sort_key, reverse=True)
        primary = dict(ordered[0])
        max_score = max(float(item.get("score") or 0) for item in ordered)
        if max_score > float(primary.get("score") or 0):
            primary["score"] = max_score
        primary["_supporting_cards"] = [dict(item) for item in ordered[1:]]
        fused.append(primary)
    fused.sort(key=lambda item: (float(item.get("score") or 0), fusion_sort_key(item)), reverse=True)
    return fused


def fusion_key(card: Dict[str, Any]) -> str:
    title = normalize_text(card.get("title", ""), loose=False)
    if len(title) < 4:
        return ""
    family = fusion_family(str(card.get("type") or ""))
    if not family:
        return ""
    return f"{family}:{title}"


def fusion_family(card_type: str) -> str:
    if card_type in RULE_TYPES:
        return "rule"
    if card_type in METHOD_TYPES:
        return "method"
    if card_type in RELATION_TYPES:
        return "relation"
    if card_type in EVIDENCE_TYPES:
        return "evidence"
    if card_type in REGISTRY_TYPES or card_type.startswith("wiki."):
        return "registry"
    if card_type in ALIAS_TYPES:
        return "alias"
    if card_type in CORRECTION_TYPES:
        return "correction"
    if card_type in MEMORY_TYPES:
        return "memory"
    if card_type in SPEC_TYPES:
        return "spec"
    if card_type in TRAFFIC_TYPES:
        return "traffic"
    return ""


def fusion_sort_key(card: Dict[str, Any]) -> tuple[float, int, int, float]:
    return (
        float(card.get("score") or 0),
        STATUS_RANK.get(str(card.get("status") or "unknown"), 10),
        SOURCE_RANK.get(str(card.get("source") or ""), 50),
        float(card.get("confidence") or 0),
    )


def event_briefs(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in events:
        out.append({
            "ts": event.get("ts", ""),
            "event": event.get("event", ""),
            "project_id": event.get("project_id", ""),
            "card_id": event.get("card_id", ""),
        })
    return out
