# xmem Agent Resume Packet - 2026-05-24

Use this as the compact pickup surface for future LLM sessions.

## Read First

- `README.md` for the current public contract.
- `docs/closeouts/2026-05-24-xmem-v0.1-closeout.md` for the latest closed state.
- `docs/policies/truth-level.md`, `docs/policies/preflight-severity.md`, and `docs/policies/promotion-policy.md` before changing truth or blocker behavior.
- `docs/policies/agent-output-compactness.md` before adding helper output or logs.

## Current State

```toon
schema: xmem_agent_resume.v1
status: stable_v0.1
version: 0.1.35
source_freshness: fresh
backup: pushed
registry: "1929 cards / 2245 evidence / 9856 aliases"
control_plane: "xmem routes memory; owners keep truth"
primary_commands[9]: xmem status,xmem doctor,xmem sync,xmem context,xmem preflight,xmem check --sources,xmem gain,xmem gain card,xmem suppress
```

## Do Next Only If Asked

- Implement new xmem features.
- Promote pending Project Wiki rows.
- Push the unrelated Issue Record local stack.
- Change SCMP helper behavior directly from this repo.

## Safe Maintenance

- Run `xmem doctor` for health.
- Run `xmem sync` after Project Wiki or Issue Record exports change.
- Run `python3 scripts/sync_backup.py --fail-on-secret` in `/Users/xin/auto-skills/CtriXin-repo/xmem-backup` after important memory changes.
- Commit xmem repo changes with the required agent identity trailers from `/Users/xin/.agents/rules/commit-identity.md`.
