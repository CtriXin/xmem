# 2026-05-25 xmem gateway

`xmem gateway` is the thin entry-layer command for launchers and hooks. It makes xmem automatic without editing every skill.

## Commands

```bash
xmem gateway "user request" --cwd "$PWD"
xmem gateway --fields issue=t102746 task="crypto模版一 复制域名 4638" --format toon --budget 700
xmem gateway "coscli secretID is missing COS deploy" --event tool-error --json
```

## Behavior

- Returns `schema: xmem.gateway.v1`.
- Returns `decision: skip` for simple local tasks and avoids registry search when the task shape has no memory signal.
- Returns `decision: inject` for domain/service/deploy/COS/copy-domain/history/bugfix-shaped tasks when indexed candidates exist.
- Uses `resume` and/or `preflight` internally, then emits only the compact subset needed by a launcher.
- Fails open: empty registry, no match, stale source, or low-value task should not block normal repo inspection.
- Redacts common secret syntaxes at the gateway output/log boundary.

## Output sections

- `query_input`: redacted raw/search query, structured fields, cwd.
- `search`: match count, top card, score/status/confidence/source.
- `packet.identity`: route hints and current project, when available.
- `packet.current_gate`: readiness/severity/risk/action.
- `packet.historical_pitfalls`, `invariants`, `methods`.
- `packet.must_keep`, `avoid`, `required_checks`.
- `packet.token_savers`, `recent_evidence`, `next_action`.

## Boundary

Gateway is not a new truth owner. It only decides whether to inject compact memory. Project Wiki, Issue Record, code/files, and live runtime systems remain the owner sources.

## Validation

- `PYTHONPATH=. pytest -q tests/test_v01.py`
- `python3 tests/smoke.py`
- `python3 -m py_compile xmem/*.py`
