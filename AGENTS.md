# xmem Agent Rules

- Always keep xmem lightweight: file cards + SQLite index + event log.
- Do not add vector DB, web UI, background daemon, or non-stdlib dependencies without explicit approval.
- Files are truth; SQLite is index/cache only. Any DB fact must point back to a file, code path, git sha, runtime API, or human/source evidence.
- Treat `verified` cards as evidence-backed and `inferred` cards as untrusted hints.
- Prefer adding a small card over writing long wiki prose.
- Keep generated data in `.xmem/` or `~/.xmem/`; do not commit generated SQLite unless requested.
- Use `./bin/xmem` for local smoke tests.
- Optimize `context` output for LLM correctness, not terminal prettiness: include resolution status, why, truth state, evidence path, and next reads.
- Use `xmem preflight "<task>"` before implementation/bugfix work to surface bug-patterns, invariants, and required checks.
- xmem is the control plane only: route entity truth to Project Wiki exports, bug truth to Issue Record exports, and keep xmem cards compact with source pointers.
- Treat stale source exports as a blocker for reliable context; run `xmem check --sources` and `xmem sync` before relying on changed source exports.
- Follow `docs/policies/truth-level.md`, `docs/policies/preflight-severity.md`, and `docs/policies/promotion-policy.md` when changing truth status, blockers, or promotion behavior.
- Do not silently promote Project Wiki pending rows, single-incident bug patterns, generated indexes, or dynamic runtime state to `verified`.
- Follow `docs/policies/agent-output-compactness.md`: prefer compact summaries plus evidence paths over raw JSON/logs, broad grep, long docs, repeated notices, or duplicated closeout text.
- Keep context/preflight layered and symbolic: agent reads compact top sections first; every card item must preserve `node_id`, `memory_layer`, and evidence refs for drilldown.
