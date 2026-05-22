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
xmem help
xmem status
xmem sync
xmem context "how did we add ads before"
xmem why "car ads lazyload"
xmem open ads.lazyload
xmem new
xmem fix
xmem gain
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
  sources.json
  cards/
  events.jsonl
  gain.jsonl
```

Rule: files/code/runtime are truth; SQLite is only a generated search index. If `registry.sqlite` is wrong, delete and rebuild it from `.xmem/`, Project Wiki, issue-tracking, git, and runtime sources.

## Sync model

`xmem sync` is the normal refresh path. It rebuilds the generated SQLite index from:

- Project Wiki at `/Users/xin/project-wiki`.
- Issue records at `/Users/xin/issue-tracking`.
- Built-in reusable cards in this repo.
- User overlay cards in `~/.xmem/cards`, for example alias corrections.
- Known local project folders recorded in `~/.xmem/sources.json`.

Imports are read-only. xmem does not silently rewrite Project Wiki or issue records; corrections are stored as small overlay cards until the upstream source is fixed.

## New folders

`xmem new` creates `.xmem/` for the current folder and registers that folder in `~/.xmem/sources.json`, so future `xmem sync` can index it from any other project.

Creation basis is intentionally narrow:

- Git remote, branch, and sha when present.
- Package or project manifests when present.
- Folder name and detected tech stack as fallback.
- Human confirmation or runtime/code evidence only when explicitly captured in cards.

If only the folder name is known, the identity starts as `inferred`. Add small cards when durable facts are known; do not inflate it into a long wiki page.

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
- `correction`: alias correction or dispute overlay.
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

Use `xmem why` for human/debug match reasons and `xmem context --json` for exact structured output.

## Useful commands

```bash
xmem status                 # registry health and counts
xmem sync                   # rebuild from truth sources
xmem new                    # create/register .xmem for this folder
xmem why <query>            # explain matches
xmem open <card-id-or-query>
xmem fix                    # record alias correction/dispute
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
