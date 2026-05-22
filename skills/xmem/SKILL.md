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
xmem sync
xmem context "query"
xmem why "query"
xmem open "query"
xmem new
xmem fix
xmem gain
```

## Workflow

1. Run `xmem context "<task>"` before broad repo traversal.
2. Trust only cards marked `verified`; treat `inferred`, `partial`, `stale`, `unknown`, and `disputed` as hints.
3. For edits that hit a feature with invariant cards, run `xmem check` before final response.
4. Add or update a small card when durable knowledge is discovered; avoid long wiki prose.
5. Use `xmem gain` when asked what xmem saved.

`xmem context` is LLM-first: use `resolution.status`, `suggested_queries`, `correction_guidance`, `why`, `truth`, `source_ref`, `warnings`, and `next_reads` to decide what to read next. Do not infer a single project when `do_not_assume_single_project` is true.

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

## New folders and corrections

Use `xmem new` in a new folder. It creates `.xmem/`, writes an identity card from git/package/folder evidence, and registers the folder so future `xmem sync` can find it from other projects.

Use `xmem fix` when a match is wrong or ambiguous. It asks for the entity/query, wrong alias, optional correct alias, and basis; then writes a correction/dispute card under `~/.xmem/cards/corrections`.

`xmem check` uses local and indexed invariant/rule/guard cards to inspect the current git diff. Treat warnings as blockers until the invariant is preserved or consciously updated.

## Card schema

Read `references/card-schema.md` when creating or revising cards.
