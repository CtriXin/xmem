---
name: xmem
description: Use when the user asks for xmem, cross-project memory, project truth index, finding prior similar project work, preserving feature invariants, importing Project Wiki / issue-tracking evidence, or compact agent context for existing projects.
---

# xmem

xmem is a lightweight truth index between wiki and DB. Use it to find durable project facts, methods, invariants, and evidence without scanning every repo.

Truth rule: `.xmem/*.yaml`, source Markdown, code, git, runtime APIs, and human confirmations are truth. `~/.xmem/registry.sqlite` is generated index/cache only.

## Commands

From the xmem repo:

```bash
./bin/xmem init
./bin/xmem find "ads lazyload"
./bin/xmem context "previous car ads work"
./bin/xmem open ads.lazyload
./bin/xmem check
./bin/xmem rebuild
./bin/xmem gain
```

Installed package equivalent:

```bash
xmem init
xmem context "query"
```

## Workflow

1. Run `xmem context "<task>"` before broad repo traversal.
2. Trust only cards marked `verified`; treat `inferred`, `partial`, `stale`, `unknown`, and `disputed` as hints.
3. For edits that hit a feature with invariant cards, run `xmem check` before final response.
4. Add or update a small card when durable knowledge is discovered; avoid long wiki prose.
5. Use `xmem gain` when asked what xmem saved.

`xmem context` is LLM-first: use `resolution.status`, `why`, `truth`, `source_ref`, `warnings`, and `next_reads` to decide what to read next. Do not infer a single project when `do_not_assume_single_project` is true.

## Source import

```bash
xmem import project-wiki --path /Users/xin/project-wiki
xmem import issue-tracking --path /Users/xin/issue-tracking
xmem import cards /Users/xin/auto-skills/CtriXin-repo/xmem/examples/cards
```

These imports are read-only. They create searchable cards and evidence pointers in `~/.xmem/registry.sqlite`.

Use `xmem rebuild` when the SQLite cache looks stale; it deletes and regenerates the index from file truth sources.

## Card schema

Read `references/card-schema.md` when creating or revising cards.
