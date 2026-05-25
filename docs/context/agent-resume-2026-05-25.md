# xmem Agent Resume Packet - 2026-05-25

Use this as the compact pickup surface after the `xmem resume` feature.

## Current State

```toon
schema: xmem_agent_resume.v1
status: resume_packet_implemented
version: 0.1.38
primary_feature: xmem resume
primary_commands[10]: xmem status,xmem doctor,xmem sync,xmem resume,xmem preflight,xmem context,xmem check --sources,xmem gain,xmem gain card,xmem suppress
truth_model: owner_sources_are_truth__registry_is_cache
```

## What Changed

- `xmem resume` now builds a compact takeover packet from context + preflight.
- Structured fields are supported: `issue`, `domain`, `service`, `repo`, `project`, `task`, and `mode`.
- Resume output includes identity, current_gate, historical_pitfalls, must_keep, avoid, required_checks, token_savers, recent_evidence, next_reads, warnings, source_freshness, and next_action.
- Tests cover traffic-switch takeover and Issue Record bug-pattern takeover.

## Safe Usage

- Start a fresh session or inherited task with `xmem resume "<issue|domain|service|task>"` before reading long handoff material.
- Prefer `--fields` when a hook or previous agent has clean structured fields.
- Treat verified cards as starting truth and partial/pending/stale cards as hints.
- Live-verify dynamic runtime facts in the owner system before deploy or closeout.

## Do Not Do

- Do not turn `resume` into a new Project Wiki, Issue Record, or SCMP Context DB.
- Do not paste raw runtime JSON, broad grep output, or full skill docs into xmem packets.
- Do not silently promote pending Project Wiki rows or single-incident bug hints to verified truth.

