# xmem

Lightweight cross-project memory for agents. xmem is a truth index, not a heavy wiki or RAG platform. It stores small cards with truth status, evidence pointers, and fast search metadata.

Current package version: `0.1.10`.

## Goals

- Resolve project identity across aliases, folders, services, domains, and branches.
- Store durable method cards, for example how ads are added.
- Store invariant cards, for example ad lazy-load must not regress.
- Import read-only sources such as Project Wiki and issue-tracking.
- Import lightweight project memory/spec sources such as `CONTEXT.md`, ADRs, OpenSpec, Spec Kit, and Trellis.
- Return compact agent context with freshness and evidence status.
- Return development preflight packets that surface historical bug patterns before edits.
- Track xmem telemetry and rough, uncalibrated savings hints with `xmem gain`.

## Quick start

```bash
xmem help
xmem status
xmem sync
xmem preflight "ads lazyload change"
xmem context "how did we add ads before"
xmem why "car ads lazyload"
xmem open ads.lazyload
xmem new
xmem check --sources
xmem fix
xmem gain
xmem gain confirm "ads lazyload"
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
- Optional compact source exports:
  - `/Users/xin/project-wiki/data/xmem-export.cards.jsonl`
  - `/Users/xin/issue-tracking/index/xmem-export.cards.jsonl`
  - `/Users/xin/issue-tracking/index/bug-patterns.jsonl`
- Built-in reusable cards in this repo.
- User overlay cards in `~/.xmem/cards`, for example alias corrections.
- Known local project folders recorded in `~/.xmem/sources.json`.
- Read-only project memory/spec files from known local project folders:
  - `CONTEXT.md`, `CONTEXT-MAP.md`, `docs/adr/*.md`, `adr/*.md`
  - `openspec/specs/**`, `openspec/changes/**`
  - `.specify/memory/**`, `.specify/specs/**`, `specs/*/{spec,plan,tasks}.md`
  - `.trellis/spec/**`, `.trellis/tasks/**`, `.trellis/workspace/**`

Imports are read-only. xmem does not silently rewrite Project Wiki or issue records; corrections are stored as small overlay cards until the upstream source is fixed.

These project-memory adapters are source routers, not workflow dependencies. xmem reads their Markdown outputs as evidence pointers and compact cards; it does not require OpenSpec, Spec Kit, Trellis, or grill-with-docs to be installed.

`xmem-export.cards.jsonl` is the preferred bridge format for other truth systems. Project Wiki can export entity cards, and Issue Record can export verified bug-pattern/rule cards; xmem imports them as generated index rows while keeping the source files as truth.

Use `xmem check --sources` to validate export shape before or after another tool generates it. Missing exports are reported as optional_missing; malformed rows, invalid truth status, duplicate ids, and bad confidence values are errors.

Registry rebuilds are atomic: `xmem sync` builds a temporary SQLite index and swaps it into place at the end, so concurrent `xmem context` readers should not see a half-empty registry during sync.

`xmem status` also audits registered local `.xmem/cards`. Generated identity cards may remain local, but non-identity rule/method/correction cards are flagged as `local_only_knowledge` when they are ignored or untracked by git. That warning means xmem can read them on this machine, but they are not portable through git until the owning project decides to track or export them.

## Control Plane Contract

xmem is the single agent entry and control plane:

- Project Wiki owns entity truth: service, repo, domain, branch, deploy target, owner, business name, and oral aliases.
- Issue Record owns bug truth: symptom, root cause, fix pattern, verification, regression guard, and evidence paths.
- xmem owns compact cross-project memory: method, invariant, relation, correction, source freshness, and generated registry/cache.

Agents should route new durable facts to the owning source, run `xmem check --sources`, then `xmem sync`. `xmem context` includes `source_freshness`; if it is not `fresh`, sync before relying on the packet.

## New folders

`xmem new` creates `.xmem/` for the current folder and registers that folder in `~/.xmem/sources.json`, so future `xmem sync` can index it from any other project.

Creation basis is intentionally narrow:

- Git remote, branch, and sha when present.
- Package or project manifests when present.
- Folder name and detected tech stack as fallback.
- Human confirmation or runtime/code evidence only when explicitly captured in cards.

If only the folder name is known, the identity starts as `inferred`. Add small cards when durable facts are known; do not inflate it into a long wiki page.

## Agent hooks

Agents can call `xmem hook` at start/finish/fix/release boundaries. This is intentionally managed by agents, not something the user has to remember.

Hook behavior:

- `start` registers a real project folder and refreshes local xmem cards.
- `finish` without text is a lightweight close marker only; it must not create a memory card or inject context.
- `note` / `finish` / `fix` / `release` can create a small `.xmem/cards/hook.*.yaml` memory card.
- When a change looks like service/domain/repo/deploy knowledge, xmem queues an append-only Project Wiki write request in `project-wiki/data/agent-inbox.jsonl`.
- When a change looks like bug/fix/release work, xmem writes an issue-tracking seed under `~/.xmem/outbox/issue-tracking`.
- xmem never silently edits Project Wiki source Markdown and never creates a full issue record from guessed data; review/issue-recorder promotes queued records.

This gives an automatic path: LLM observes work -> hook captures compact truth/evidence -> Project Wiki/issue queues receive durable candidates -> `xmem sync` imports the approved sources back into the registry.

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
- `hook.memory`: agent hook memory waiting to become durable project knowledge.
- `evidence.issue`: imported issue record.
- `wiki.service`, `wiki.repo`, `wiki.domain`: imported Project Wiki entity cards.

## Design rule

xmem should stay small. A repo starts with at most a few cards. Full text search and RAG can be added later, but cards and evidence pointers remain the source of truth.

## LLM packets

`xmem context` defaults to an LLM-first packet:

```text
xmem_context:
  resolution:
    status: resolved | ambiguous | partial | missing
    do_not_assume_single_project: true | false
  suggested_queries: ...
  correction_guidance: ...
  registry_candidates: ...
  rules: ...
  methods: ...
  memories: ...
  evidence: ...
  next_reads: ...
```

Use `xmem why` for human/debug match reasons and `xmem context --json` for exact structured output.

`xmem preflight` is the development-start packet. It is narrower than `context`: it pulls matched Issue Record bug-patterns, invariant/rule cards, must-keep behavior, known failure modes, and required verification checks before code is edited. Preflight keeps an actionable relevance gate: alias/metadata matches are trusted as direct signals, while weak body-only matches must clear a score threshold before they can become guardrails.

```text
xmem_preflight:
  readiness: ready_with_guards | needs_disambiguation | blocked_source_stale | no_prior_memory
  risk_level: high | medium | low | unknown
  known_bug_patterns: ...
  invariants: ...
  must_keep: ...
  avoid: ...
  known_failure_modes: ...
  required_checks: ...
  source_refs: ...
```

Agents should run `xmem preflight "<task>"` before implementation/bugfix work, then preserve `must_keep`, avoid known failure modes, and run `required_checks`. If source freshness is stale, sync first; if project identity is ambiguous, disambiguate before editing.

`xmem context` conservatively fuses duplicate cards with the same title and type family. The best card stays in `rules` / `methods` / `relations`, and matching duplicates appear as `supporting_cards`, keeping LLM packets compact while preserving source traceability.

If a query hits a correction card, xmem expands the canonical alias as an extra search internally and marks the packet as `guided_by_correction` instead of pretending the wrong alias is reliable truth.

## Guardrails and gain

`xmem check` inspects the current git diff against local and indexed `invariant` / `rule` / `guard` cards. It is intentionally lightweight: it looks for explicit `diff_guard.warn_if_removed`, `warn_if_added`, and `forbid` terms and exits non-zero for human-visible warnings.

`xmem gain` summarizes lookup, `context`, `preflight`, and `check` telemetry from `~/.xmem/gain.jsonl`. Hit/miss/pass/prevented are log counts; `hit` only means candidates were returned. Token savings are rough, uncalibrated estimates for context/preflight matches only, not billing truth. Risk hints come from rule warnings, not confirmed production bugs. By default, `xmem gain` reads all gain rows; use `--limit N` only when you want a recent slice.

The gain dashboard self-calibrates its own confidence labels. It reports whether the current data is only telemetry/proxy or partially calibrated, surfaces high rough estimates that need review, and records future match quality fields such as `top_score`, `top_status`, and `top_why`.

Outcome signals improve gain calibration over time. `xmem hook finish|fix|bug --verified` appends an `outcome.*` row to `gain.jsonl`, and `xmem gain confirm <query>` / `xmem gain reject <query>` can record explicit human calibration. These signals still do not turn rough token estimates into billing truth, but they let the dashboard distinguish pure proxy data from partially calibrated outcomes.

Confirmed/rejected gain outcomes also create review outbox items under `~/.xmem/outbox/gain-feedback`. Confirmed outcomes queue a Project Wiki review request; rejected or bug-prevented outcomes queue an Issue Record seed. This keeps upstream truth human-reviewed instead of silently mutating Project Wiki or issue records.

## Useful commands

```bash
xmem status                 # registry health and counts
xmem sync                   # rebuild from truth sources
xmem preflight <query>      # dev-start bug guards and required checks
xmem check --sources        # validate Project Wiki / Issue Record exports
xmem import project-memory  # import CONTEXT/ADR/OpenSpec/Spec Kit/Trellis from this folder
xmem import openspec        # import OpenSpec files only
xmem import speckit         # import Spec Kit files only
xmem import trellis         # import Trellis files only
xmem new                    # create/register .xmem for this folder
xmem why <query>            # explain matches
xmem open <card-id-or-query>
xmem fix                    # record alias correction/dispute
xmem gain confirm <query>   # confirm a useful xmem hit/outcome
xmem gain reject <query>    # mark a rough gain estimate as overestimated/wrong
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
