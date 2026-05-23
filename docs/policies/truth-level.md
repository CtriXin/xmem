# xmem Truth Level Policy

This policy defines how agents should read, promote, and demote xmem truth states. xmem is a memory router: source files, Project Wiki, Issue Record, code, git, and runtime APIs own truth; SQLite is only a generated index.

## Owner Precedence

When facts conflict, prefer sources in this order:

1. Human-confirmed owner source or accepted Project Wiki export.
2. Runtime/API/code evidence observed during the task.
3. Verified xmem card with source refs.
4. Issue Record evidence or bug-pattern export.
5. Project Wiki pending / xmem outbox / generated index.
6. Folder names, oral guesses, stale branch names, or body-only text matches.

If two same-rank sources conflict, mark the result `disputed` and ask for human or owner-source resolution before editing or deploying.

## Status Meanings

- `verified`: evidence-backed and owned by a durable source, or cross-checked by current runtime/code plus source refs.
- `partial`: useful but incomplete; good as a routing hint, not final truth.
- `inferred`: guessed from weak evidence such as names, old notes, or one non-owner source.
- `stale`: previously true but tied to an old source state, old git sha, old deploy, or expired TTL.
- `disputed`: conflicting evidence exists and no owner source has resolved it.
- `unknown`: no reliable basis yet.

## Promotion Rules

Project/entity mapping can become `verified` only when an owner source accepts it, or when human confirmation plus code/runtime/source refs agree. Project Wiki `agent-inbox.jsonl` remains `partial` until Project Wiki exports it.

Bug patterns become `verified` when they have a reusable root cause, regression guard, verification recipe, and either two independent issues or one high-confidence production incident with human/owner confirmation. A single real incident without repeat confirmation stays `partial`.

Sheet/domain group relation can become `verified` when current `lookup` and `rf getcf` show a strong majority: at least 80% of resolved domains and at least 5 domains point to the same service/repo. Single-domain sheets stay `partial`; mixed groups stay `partial` or `disputed` until reviewed.

xmem cards can be `verified` only when they include source refs. Cards without code/runtime/source evidence should stay `partial` or `inferred`.

## Stale And TTL Rules

Dynamic facts must not become permanent truth:

- Current branch, latest deploy run, pod image, pod status, pipeline state: task scoped; live verify again after 6 hours or before any deploy/switch.
- Domain-to-service binding: live verify before deploy, traffic switch, or approval message.
- Repo-to-service relation and canonical oral alias: can remain verified after Project Wiki/human confirmation, but still check owner source when facts conflict.
- Generated code index refs: `partial` routing hints; source files remain truth.

When TTL expires, xmem may still return the fact as a hint, but agents must label it stale and verify before acting.

## Demotion Rules

Demote to `partial` when source refs are missing, owner export is pending only, or sample size is too small. Demote to `stale` when TTL expires or source mtime is newer than registry. Mark `disputed` when a verified card conflicts with owner source, runtime API, or user correction.
