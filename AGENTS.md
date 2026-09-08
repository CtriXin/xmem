# xmem Agent Rules

- Current agent-facing entry is `skills/xmem/SKILL.md`: historical read-only lookup. New Stride work does not default to gateway/preflight/sync/capture/profile/pending writeback or card creation.
- Preserve existing legacy CLI/history compatibility unless a change is explicitly scoped to it. Compatibility is not a requirement to invoke OII for new tasks.
- Keep xmem lightweight: file cards and a generated SQLite index. Do not add vector DB, UI, daemon or dependencies without authorization.
- `bin/xmem lookup` does not create or migrate a registry, mutate sources, or write telemetry. Missing/unavailable history is nonblocking; do not sync as a fallback.
- `STRIDE_EXECUTION_CONTEXT=stride-v1` disables hook/gateway memory side effects only and grants no action authority. Native task cwd selection uses `memory_scope` with a resolved workspace and an existing task row, not a path name or title alone.
- Scope SQLite WAL reader coordination is permitted; task schema/data/status writes are not. Lookup separately uses immutable read-only SQLite and rejects uncheckpointed WAL.
- Source evidence owns facts; the index only preserves pointers and competing claims. Keep `indexed_at` separate from source-declared `source_checked_at`; re-indexing is not verification. Keep all conflicting producer provenance under a shared `source_ref` identity.
- Unchanged bytes and a card's declared `verified` status do not verify present runtime truth. Read the current owner source when a decision depends on it.
- Retrieval hits and dry-run would-inject results are not adoption or token savings. Unmeasured benefit is `unknown`.
- Preserve pending, inferred, stale and disputed states; never promote them silently. New evidence belongs in the current task; reusable rules need a verified correction and applicability scope before Stride learning.
- Use `./bin/xmem` for bounded local smoke tests with temporary registries. Keep generated data and SQLite out of commits.
- Keep output compact and preserve evidence paths; do not expose raw private logs or credentials.
- Historical policy documents apply only to explicitly scoped legacy maintenance. They do not add new task blockers or mandatory memory writes.
