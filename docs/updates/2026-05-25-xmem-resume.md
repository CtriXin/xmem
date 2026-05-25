# xmem Resume Packet Update - 2026-05-25

Status: implemented and covered by smoke/unit tests.

## What's New

- Added `xmem resume <query>` for taking over existing work from an issue slug, domain, service, repo, or task phrase.
- Added `xmem resume --fields issue=... domain=... service=... repo=... project=... task=... mode=...` so hooks can pass clean targets instead of noisy conversation context.
- `resume` combines `xmem context` identity routing with `xmem preflight` guardrails into one compact agent-facing packet.
- `resume` retrieval is counted by `xmem gain` as a token-saving event, alongside `context` and `preflight`.

## Packet Shape

`xmem resume` returns `schema: xmem.resume.v1` with:

- `identity`: current project, traffic-switch or registry candidates, and ambiguity state.
- `current_gate`: readiness, severity, blockers, before-edit checks, before-deploy checks, and completion basis.
- `historical_pitfalls`: matched Issue Record bug patterns.
- `invariants` / `methods`: reusable rules and procedures.
- `must_keep` / `avoid` / `required_checks`: compact instructions for development and closeout.
- `token_savers`: what the next agent can skip, for example broad repo scans, broad issue scans, or raw JSON/log reading.
- `recent_evidence` / `next_reads`: evidence refs to drill into only when needed.
- `next_action`: the shortest safe next step.

## Boundaries

- `resume` is not a new truth owner; it is a read model over indexed owner sources.
- Dynamic facts such as current branch, live deploy status, domain binding, pod convergence, and runtime health still need live verification.
- Project Wiki owns stable project/entity truth; Issue Record owns bug evidence; xmem only routes compact memory and source refs.
- If `source_freshness.status` is not `fresh`, run `xmem sync` and rerun `xmem resume` before relying on the packet.

## Token Waste Addressed

This feature addresses the xmem-side waste reported by agents:

- Avoid reading long handoff files before identifying the project and current gate.
- Avoid broad issue-tracking grep when verified cards already route the issue/domain/service.
- Avoid reading full skill docs when a compact packet can tell the next command/read.
- Avoid repeating context + preflight separately during task takeover.

SCMP-specific wrappers such as compact pod summaries, Feishu closeout automation, scoped git status, or runtime sanitizers remain owned by SCMP/scmp-ops, not xmem.

## Verified Checks

- `python3 -m py_compile xmem/*.py`
- `PYTHONPATH=. pytest -q tests/test_v01.py`

