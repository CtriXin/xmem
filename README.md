# xmem

Lightweight cross-project memory for agents. xmem is a truth index, not a heavy wiki or RAG platform. It stores small cards with truth status, evidence pointers, and fast search metadata.

## Goals

- Resolve project identity across aliases, folders, services, domains, and branches.
- Store durable method cards, for example how ads are added.
- Store invariant cards, for example ad lazy-load must not regress.
- Import read-only sources such as Project Wiki and issue-tracking.
- Return compact agent context with freshness and evidence status.
- Track rough token savings and prevented regressions with `xmem gain`.

## Quick start

```bash
./bin/xmem init
./bin/xmem status
./bin/xmem import project-wiki --path /Users/xin/project-wiki
./bin/xmem import issue-tracking --path /Users/xin/issue-tracking
./bin/xmem find "car ads lazyload"
./bin/xmem context "how did we add ads before"
./bin/xmem open ads.lazyload
./bin/xmem check
./bin/xmem rebuild
./bin/xmem gain
```

Truth files live with the project:

```text
.xmem/
  project.yaml
  cards/
  events.jsonl
  snapshot.toon
```

Generated index/cache lives globally:

```text
~/.xmem/
  registry.sqlite
  events.jsonl
  gain.jsonl
```

Rule: files/code/runtime are truth; SQLite is only a generated search index. If `registry.sqlite` is wrong, delete and rebuild it from `.xmem/`, Project Wiki, issue-tracking, git, and runtime sources.

## Truth status

- `verified`: backed by code, tests, runtime API, human confirmation, or durable evidence.
- `inferred`: guessed or imported from a partially trusted source.
- `partial`: useful but incomplete.
- `stale`: was true for an older git sha or outdated source.
- `disputed`: conflicting records exist.
- `unknown`: not enough evidence.

## Card types

- `identity`: what this project is.
- `method`: how to do a durable feature.
- `invariant`: behavior that must not regress.
- `evidence.issue`: imported issue record.
- `wiki.service`, `wiki.repo`, `wiki.domain`: imported Project Wiki entity cards.

## Design rule

xmem should stay small. A repo starts with at most a few cards. Full text search and RAG can be added later, but cards and evidence pointers remain the source of truth.

## LLM packet

`xmem context` defaults to an LLM-first packet:

```text
xmem_context:
  resolution:
    status: resolved | ambiguous | partial | missing
    do_not_assume_single_project: true | false
  registry_candidates: ...
  rules: ...
  methods: ...
  evidence: ...
  next_reads: ...
```

Use `xmem find` for human/debug candidate lists and `xmem context --json` for exact structured output.

## Useful commands

```bash
xmem import cards examples/cards       # import reusable cards such as rules/aliases
xmem open <card-id-or-query>           # show source-backed card excerpt
xmem open <card-id> --body             # print full source body
xmem rebuild                           # rebuild SQLite from file truth sources
```

`xmem status` should show the stable registry at `/Users/xin/.xmem` on this machine. In isolated agent sessions, xmem detects the real user home so agents do not accidentally create an empty per-session registry.

## Installed local links

On this machine the CLI points at this repo, while agent skill directories point at the shared skill copy:

```text
/Users/xin/.local/bin/xmem -> /Users/xin/auto-skills/CtriXin-repo/xmem/bin/xmem
/Users/xin/auto-skills/shared-skills/xmem
/Users/xin/.codex/skills/xmem -> /Users/xin/auto-skills/shared-skills/xmem
/Users/xin/.claude/skills/xmem -> /Users/xin/auto-skills/shared-skills/xmem
/Users/xin/.opencode/skills/xmem -> /Users/xin/auto-skills/shared-skills/xmem
/Users/xin/.agents/skills/xmem -> /Users/xin/auto-skills/shared-skills/xmem
```
