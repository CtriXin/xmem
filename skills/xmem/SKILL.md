---
name: xmem
description: Use when the user asks for xmem, cross-project memory, project truth index, finding prior similar project work, preserving feature invariants, importing Project Wiki / issue-tracking evidence, or compact agent context for existing projects.
---

# xmem

xmem is a lightweight truth index between wiki and DB. Use it to find durable project facts, methods, invariants, and evidence without scanning every repo.

Truth rule: `.xmem/*.yaml`, source Markdown, code, git, runtime APIs, and human confirmations are truth. `~/.xmem/registry.sqlite` is generated index/cache only.

## Short Commands

```bash
xmem help
xmem status
xmem doctor
xmem sync
xmem preflight "query"
xmem context "query"
xmem why "query"
xmem open "query"
xmem new
xmem check --sources
xmem fix
xmem gain
```

## Workflow

1. Run `xmem preflight "<task>"` before development or bugfix edits to surface historical bug-patterns, invariants, and required checks.
2. Run `xmem context "<task>"` before broad repo traversal or project selection.
3. If `source_freshness.status` is not `fresh`, run `xmem sync` before relying on the packet.
4. Trust only cards marked `verified`; treat `inferred`, `partial`, `stale`, `unknown`, and `disputed` as hints.
5. For edits that hit a feature with invariant cards, run `xmem check` before final response.
6. Add or update a small card when durable knowledge is discovered; avoid long wiki prose.
7. Use `xmem gain` when asked what xmem saved.
8. Use `xmem doctor` when maintenance state is unclear; it aggregates registry, source exports, local card portability, backup health, outbox, and current repo registration.

`xmem context` is LLM-first: use `resolution.status`, `suggested_queries`, `correction_guidance`, `why`, `truth`, `source_ref`, `warnings`, and `next_reads` to decide what to read next. Do not infer a single project when `do_not_assume_single_project` is true. Duplicate cards may be fused; read `supporting_cards` for alternate sources behind the primary card.

For SCMP/domain/service work, also read `traffic_switch` and `gain_hints` when present. A verified `traffic.switch` card can be used as the starting route for prod/validation service, repo, branch hints, approval group, common verification, and skipped lookup guidance. Domain/service binding and latest deploy state still need live verification; Project Wiki pending candidates remain hint-only.

Traffic switch wording rule: `validation_service` is a candidate traffic target for validating new behavior before cutover, not a generic test environment. Do not infer test-environment semantics only because a service name contains `-test`.

`xmem preflight` is the development-start packet. Use `readiness`, `risk_level`, `known_bug_patterns`, `must_keep`, `avoid`, `known_failure_modes`, `required_checks`, and `source_refs` before editing. If `readiness` is `blocked_source_stale`, sync first; if it is `needs_disambiguation`, resolve the project/entity before changing code.

## Agent hooks

When acting as an agent, use xmem hooks without asking the user to remember commands:

- On session/task start: run the `start` hook to register the project and refresh local cards.
- On session/task end: a silent `finish` hook without text may record a close marker; it must not create memory or inject context.
- When durable knowledge is discovered: run a `note` or `finish` hook with a short LLM-written summary.
- For bugfix/release/deploy work: use `fix`, `release`, or `deploy` events so xmem can queue Project Wiki and issue-tracking follow-up.

Hook rule: xmem may create `.xmem/cards/hook.*.yaml`, append a Project Wiki write request, or create an issue seed. It must not silently rewrite Project Wiki Markdown or promote guessed data to a final issue record.

MMS sessions normally inject this skill and run lightweight xmem session-start/session-end hooks automatically. Do not ask the user to install or remember hook commands inside MMS-launched Codex/Claude/OpenCode/agy sessions.

## Sync

Use `xmem sync` as the default refresh. It rebuilds `~/.xmem/registry.sqlite` from Project Wiki, issue-tracking, built-in cards, `~/.xmem/cards`, and known local folders in `~/.xmem/sources.json`.

These imports are read-only. They create searchable cards and evidence pointers in `~/.xmem/registry.sqlite`. If `xmem status` shows `0 cards`, run `xmem sync`; in isolated agent sessions it should still use `/Users/xin/.xmem`.

If present, xmem also consumes compact source exports:

- `project-wiki/data/xmem-export.cards.jsonl`
- `project-wiki/data/agent-inbox.jsonl`
- `issue-tracking/index/xmem-export.cards.jsonl`
- `issue-tracking/index/bug-patterns.jsonl`
- `~/.xmem/outbox/project-wiki/*.json`
- `~/.xmem/outbox/issue-tracking/*.md`

These exports are bridge/index inputs only; Project Wiki and Issue Record remain the source truth.
Project Wiki `agent-inbox.jsonl` rows are pending writebacks only: xmem imports them as `wiki.pending` / `partial` / hint-only cards. They must not override verified Project Wiki truth.
xmem outbox writes are also imported as pending hints only: Project Wiki JSON requests become `wiki.pending`, and Issue Record seeds become `evidence.issue` until the owning source accepts/exports them.

Use `xmem check --sources` when Project Wiki or Issue Record changes its export. Missing optional exports are reported as optional_missing; malformed JSONL rows, duplicate ids, invalid truth status, or invalid confidence are errors.

`xmem sync` rebuilds the generated SQLite registry atomically through a temp file and final swap, so concurrent agents should keep reading the previous complete registry until the new one is ready.

If registered repos already have `.ai/map/map.db` or `.codegraph/codegraph.db`, sync imports only compact `code.index` / `code.hotspot` refs. These refs help route an Agent to likely files/symbols, but generated DBs and source files remain truth; verify in code before editing. `map` is the primary quick code map, while `codegraph` is an optional deep-symbol tool.

## Source Routing

- Project/entity truth goes to Project Wiki: service, repo, domain, branch, deploy target, owner, business name, oral alias.
- Bug truth goes to Issue Record: symptom, root cause, fix pattern, verification, regression guard, evidence paths.
- xmem truth stays compact: cross-project method/invariant/relation cards with source pointers only.
- Code structure truth stays in source files plus generated `.ai/map` / `.codegraph` indexes; xmem stores only routing refs.
- Do not duplicate full Project Wiki or Issue Record truth into xmem; xmem is the control plane and generated index consumer.
- When source exports change, require `xmem check --sources` and `xmem sync`; context should be treated as stale until `source_freshness.status` is `fresh`.

## New folders and corrections

Use `xmem new` in a new folder. It creates `.xmem/`, writes an identity card from git/package/folder evidence, and registers the folder so future `xmem sync` can find it from other projects.

Use `xmem fix` when a match is wrong or ambiguous. It asks for the entity/query, wrong alias, optional correct alias, and basis; then writes a correction/dispute card under `~/.xmem/cards/corrections`.

`xmem check` uses local and indexed invariant/rule/guard cards to inspect the current git diff. Treat warnings as blockers until the invariant is preserved or consciously updated.

## Card schema

Read `references/card-schema.md` when creating or revising cards.
