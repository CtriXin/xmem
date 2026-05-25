# xmem

Lightweight cross-project memory for agents. xmem is a truth index, not a heavy wiki or RAG platform. It stores small cards with truth status, evidence pointers, and fast search metadata.

Current package version: `0.1.38`.

## What's New in 0.1.38

This release adds `xmem resume`, a compact takeover packet for fresh sessions or existing tasks. It is the xmem-side answer to token waste from long handoffs, long skill reads, broad issue scans, raw runtime JSON, and repeated closeout readbacks.

- `xmem resume <issue|domain|service|query>` combines `context` routing and `preflight` guardrails into one agent-facing packet.
- `xmem resume --fields issue=... domain=... service=... task=...` lets hooks/agents pass clean structured targets and avoid old-context pollution.
- The packet returns identity, current gate, historical pitfalls, invariants, must_keep/avoid, required checks, recent evidence refs, token_savers, next_reads, and next_action.
- `resume` is a read model only: it does not verify live runtime state, does not own Project Wiki / Issue Record truth, and does not replace tests or deploy checks.
- `resume` events now count as token-saving retrieval in `xmem gain`, so future reports can show whether takeover packets were actually used.

## What's New in 0.1.37

This release is the public-onboarding cut of xmem. The main update is not a private SCMP/wiki integration; it is the generic path any user can start with.

- `xmem setup` is now the first-run entry: it creates `~/.xmem`, writes generic docs/config/schema examples, discovers/registers project roots, and can create a shared memory repo with `--memory-repo`.
- Public MMS installs can opt into xmem with `bash install.sh --install-xmem`; MMS also supports `--dry-run` to preview the xmem install/setup plan without writing files.
- `--register-only` lets installers register roots without writing repo-local `.xmem` files, keeping first install low-touch.
- Host-home handling is explicit for isolated sessions and temp-HOME installs, so `xmem status` reports the intended xmem home instead of accidentally reading a real-user registry.
- The supported feature set is now documented as a lightweight memory router: compact cards, source imports, context/preflight packets, guard checks, gain telemetry, suppression feedback, and optional map/codegraph refs.

Public boundary: xmem does not require or bundle SCMP, Project Wiki, Issue Record, Feishu/Lark, Jira, Linear, or cloud backup. Those can be adapters later, but the default install stays generic and user-owned.

## Current closeout

The latest stable pickup point is:

- `docs/updates/2026-05-25-xmem-resume.md`
- `docs/context/agent-resume-2026-05-25.md`
- `docs/updates/2026-05-24-xmem-public-onboarding.md`
- `docs/closeouts/2026-05-24-xmem-v0.1-closeout.md`
- `docs/context/agent-resume-2026-05-24.md`

Read these first when a future agent needs to resume xmem work without transcript context.

## Goals

- Resolve project identity across aliases, folders, services, domains, and branches.
- Store durable method cards, for example how ads are added.
- Store invariant cards, for example ad lazy-load must not regress.
- Import read-only sources such as Project Wiki and issue-tracking.
- Import lightweight project memory/spec sources such as `CONTEXT.md`, ADRs, OpenSpec, Spec Kit, and Trellis.
- Return compact agent context with freshness and evidence status.
- Return layered symbolic packets: compact top sections plus `node_id`, `memory_layer`, and evidence refs for drilldown.
- Return SCMP traffic-switch packets when a verified relation card matches a domain/service/template query.
- Return development preflight packets that surface historical bug patterns before edits.
- Return resume packets for taking over existing tasks without reading long handoffs first.
- Track xmem telemetry and rough, uncalibrated savings hints with `xmem gain`.

## Quick start

```bash
xmem help
xmem setup
xmem status
xmem doctor
xmem sync
xmem preflight "ads lazyload change"
xmem preflight --fields domain=example.com task="ads lazyload change"
xmem resume "issue slug or domain"
xmem resume --fields issue=demo-issue domain=example.com task="ads lazyload change"
xmem context "how did we add ads before"
xmem why "car ads lazyload"
xmem open ads.lazyload
xmem new
xmem check --sources
xmem fix
xmem suppress --card ads.lazyload --for-query "ads lazyload change" --reason irrelevant
xmem gain
xmem gain confirm "ads lazyload"
```

For a first-time generic install, start with:

```bash
xmem setup
```

`xmem setup` creates a user-owned `~/.xmem` workspace, registers the current repo or workspace roots, writes generic owner-model docs, and runs `xmem sync` unless `--no-sync` is passed. It does not require SCMP, Project Wiki, Issue Record, Feishu, Jira, Linear, or any private adapter.

Useful setup variants:

```bash
xmem setup --root ~/projects --scan-depth 2
xmem setup --root ~/projects --register-only
xmem setup --memory-repo ~/xmem-memory
xmem setup --dry-run --json
```

MMS users can install the generic CLI and skill globally with:

```bash
bash install.sh --install-xmem
bash install.sh --install-xmem --dry-run
```

The MMS installer uses the low-touch path: install the CLI/skill, create `~/.xmem`, register shallow HOME git roots, and avoid writing repo-local `.xmem` files until a user or agent runs `xmem setup` inside a specific project.

## Supported Features

| Area | Supported | Not the default |
| --- | --- | --- |
| Onboarding | `xmem setup`, `--root`, `--register-only`, `--memory-repo`, `--dry-run --json` | deep automatic repo rewrites |
| Memory shape | compact YAML cards, aliases, truth status, evidence pointers | long wiki pages inside xmem |
| Retrieval | `xmem context`, `xmem resume`, `xmem why`, `xmem open`, correction-guided search | guarantee that unknown/unindexed facts can be found |
| Development gate | `xmem preflight`, `xmem resume` current_gate, structured `--fields`, invariant `xmem check` | replacing tests, code review, or live verification |
| Source imports | Project Wiki/Issue exports when present, generic project memory/spec docs, local cards, outbox hints | requiring any private adapter |
| Code routing | compact `.ai/map` / `.codegraph` refs when present | treating generated indexes as code truth |
| Feedback | `xmem gain`, confirm/reject outcomes, suppress true-but-irrelevant hits | billing-accurate token savings |
| Portability | user-owned files and optional git memory repo | silent cloud backup or upload |

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

`xmem sync` is the normal refresh path. It rebuilds the generated SQLite index and prints source health plus `next_actions` from the rebuilt status. It imports from:

- Project Wiki at `/Users/xin/project-wiki`.
- Issue records at `/Users/xin/issue-tracking`.
- Pending xmem outbox writes under `~/.xmem/outbox/project-wiki` and `~/.xmem/outbox/issue-tracking`.
- Optional compact source exports:
  - `/Users/xin/project-wiki/data/xmem-export.cards.jsonl`
  - `/Users/xin/project-wiki/data/agent-inbox.jsonl`
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

Project Wiki `agent-inbox.jsonl` rows are imported only as `wiki.pending` cards with `truth.status=partial`, confidence capped at `0.6`, and `hint_only_until_project_wiki_accepts` policy. They make pending writebacks searchable, but they never override verified Project Wiki exports.

xmem outbox rows are imported the same way: Project Wiki outbox JSON becomes `wiki.pending`, and Issue Record seed Markdown becomes `evidence.issue` with `partial` truth. These records are only routing hints until Project Wiki / Issue Record accepts and exports them.

Use `xmem check --sources` to validate export shape before or after another tool generates it. Missing exports are reported as optional_missing; malformed rows, invalid truth status, duplicate ids, and bad confidence values are errors. It also reports local `.xmem/cards` portability warnings so ignored/untracked knowledge cards are visible before another machine or agent misses them. Use `xmem check --sources --strict` when an agent gate should fail on warnings, not only errors.

Registry rebuilds are atomic: `xmem sync` builds a temporary SQLite index and swaps it into place at the end, so concurrent `xmem context` readers should not see a half-empty registry during sync.

`xmem status` also reports `next_actions`, xmem-backup health, and registered local `.xmem/cards`. Generated identity cards may remain local, but non-identity rule/method/correction cards are flagged as `local_only_knowledge` when they are ignored or untracked by git. That warning means xmem can read them on this machine, but they are not portable through git until the owning project decides to track or export them. Context and preflight packets include compact `local_source_health` when matched cards come from a non-portable local source, so agents know when the evidence is machine-local without adding noise to unrelated queries.

`xmem doctor` is the single maintenance view. It combines registry state, source export health, local-card portability, outbox counts, xmem-backup state, and current repo registration. Use it when `status` says there is a warning or before handing work to another agent.

## Resume packets

Use `xmem resume` when taking over an existing issue, domain, service, or long-running task. It is designed for fresh sessions before reading a long handoff.

```bash
xmem resume "ptc-v5-novabeats1 action.readoxa.com traffic switch"
xmem resume --fields issue=t102748 domain=action.readoxa.com task="traffic switch"
xmem resume "ad iframe lazy regression" --json
```

The packet is agent-facing and compact. It contains:

- `identity`: resolved project/service/repo/traffic-switch hints from verified cards first.
- `current_gate`: preflight readiness, blockers, required-before-edit/deploy checks, and completion basis.
- `historical_pitfalls`: matched issue bug-patterns and regressions.
- `must_keep` / `avoid` / `required_checks`: rules to preserve before editing or closing out.
- `recent_evidence` / `next_reads`: source refs to drill into only when needed.
- `token_savers`: what the agent can skip, such as broad repo/issue scans or raw JSON/log reads.
- `next_action`: the shortest safe next step.

`resume` is not a new truth owner. It starts from indexed cards and owner exports, then tells the agent where truth lives. Dynamic facts such as current deploy state, domain binding, branch, or pod status still require live verification in their owner systems.

## Control Plane Contract

xmem is the single agent entry and control plane:

- Project Wiki owns entity truth: service, repo, domain, branch, deploy target, owner, business name, and oral aliases.
- Issue Record owns bug truth: symptom, root cause, fix pattern, verification, regression guard, and evidence paths.
- xmem owns compact cross-project memory: method, invariant, relation, correction, source freshness, and generated registry/cache.

Agents should route new durable facts to the owning source, run `xmem check --sources`, then `xmem sync`. `xmem context` includes `source_freshness`; if it is not `fresh`, sync before relying on the packet.

## New folders

`xmem new` creates `.xmem/` for the current folder and registers that folder in `~/.xmem/sources.json`, so future `xmem sync` can index it from any other project.

`xmem setup` is the broader onboarding command for a new machine or workspace. It can discover git repos under one or more roots, initialize repo-local `.xmem` identity files, create a generic `~/.xmem/config.toml`, and optionally create a shared memory repo with `--memory-repo`. Use `--register-only` when you want xmem to remember roots without writing `.xmem` into every repo yet.

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

Detailed governance lives in `docs/policies/`:

- `docs/policies/truth-level.md`: source precedence, promotion/demotion, dynamic-fact TTL, and conflict rules.
- `docs/policies/preflight-severity.md`: how agents interpret `xmem preflight` as `hint`, `warn`, or `block`.
- `docs/policies/promotion-policy.md`: how Project Wiki pending rows, Issue patterns, and xmem cards are promoted, merged, or rejected.
- `docs/policies/agent-output-compactness.md`: how agents avoid raw JSON, broad grep, long docs, repeated notices, and duplicated closeout text.
- `docs/policies/layered-symbolic-memory.md`: how context/preflight packets keep a compact symbolic top layer with evidence drilldown.

Short rule cards for these policies live under `examples/cards/policy/`, so `xmem context "truth level policy"` and `xmem preflight "deploy blocker"` can surface the policy without reading long docs first.

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
  symbolic_memory:
    mode: layered_symbolic
    layers: L3 policy_profile / L2 scenario_pattern / L1 atom_card / L0 raw_evidence
  resolution:
    status: resolved | ambiguous | partial | missing
    do_not_assume_single_project: true | false
  traffic_switch:
    - prod_service / validation_service / repo / branch hints / domains / stale_policy
  gain_hints: ...
  suggested_queries: ...
  correction_guidance: ...
  registry_candidates: ...
  rules: ...
  methods: ...
  memories: ...
  evidence: ...
  next_reads: ...
```

Each card item includes `node_id`, `memory_layer`, `evidence_ref`, `source_ref`, and `source_path` when available. Agent context should reason from the compact top layer, then drill down with `xmem open <card_id>` or source paths when facts matter. Use `xmem why` for human/debug match reasons and `xmem context --json` for exact structured output.

`xmem preflight` is the development-start packet. It is narrower than `context`: it pulls matched Issue Record bug-patterns, invariant/rule cards, must-keep behavior, known failure modes, and required verification checks before code is edited. Preflight keeps an actionable relevance gate: alias/metadata matches are trusted as direct signals, while weak body-only matches must clear a score threshold before they can become guardrails.

```text
xmem_preflight:
  readiness: ready_with_guards | needs_disambiguation | blocked_source_stale | no_prior_memory
  severity: hint | warn | block
  can_proceed: true | false
  risk_level: high | medium | low | unknown
  blockers: ...
  required_before_edit: ...
  required_before_deploy: ...
  known_bug_patterns: ...
  invariants: ...
  must_keep: ...
  avoid: ...
  known_failure_modes: ...
  required_checks: ...
  source_refs: ...
```

Agents should run `xmem preflight "<task>"` before implementation/bugfix work, then obey `severity`, `can_proceed`, `blockers`, `required_before_edit`, and `required_before_deploy` before editing/deploying. Preserve `must_keep`, avoid known failure modes, and run `required_checks`. If source freshness is stale, sync first; if project identity is ambiguous, disambiguate before editing.

For hook-generated or long/noisy prompts, agents should prefer structured preflight fields: `xmem preflight --fields domain=... service=... repo=... task=... mode=...`. When domain/service/repo fields do not resolve to a verified target anchor, xmem returns `readiness: needs_clarification`, `can_proceed: false`, and clears guardrail sections instead of emitting a misleading rules packet.

Preflight severity policy: hints only route the next read; warnings require preserving the invariant or running the check; blockers stop edits/deploy until resolved. Blockers include stale source exports, ambiguous production target, verified invariant removal, SCMP deploy payload/path mismatch, unconverged pods, failed safe-access/live verification, and missing domain binding for traffic-switch work. SCMP/Feishu/issue/rg/log queries also activate compact-output guardrails: summarize for agents and store bulky raw output as evidence paths.

`xmem context` conservatively fuses duplicate cards with the same title and type family. The best card stays in `rules` / `methods` / `relations`, and matching duplicates appear as `supporting_cards`, keeping LLM packets compact while preserving source traceability.

If a query hits a correction card, xmem expands the canonical alias as an extra search internally and marks the packet as `guided_by_correction` instead of pretending the wrong alias is reliable truth.

If a card is true but irrelevant for the current query, use `xmem suppress --card <id> --for-query <query-or-hash> --reason irrelevant`. This writes a local feedback row and downranks that card for the exact query hash only; it does not change card truth, Project Wiki, or Issue Record.

When a query hits a verified `traffic.switch` card, `xmem context` surfaces a `traffic_switch` packet before lower-confidence Project Wiki pending rows. This gives agents prod/validation service, repo, branch hints, approval group, common verification, stale policy, and "what lookup can be skipped" guidance without scanning issue-tracking. Domain/service binding and latest deploy state still require live verification.

Traffic switch wording matters: `validation_service` is a candidate traffic target used to verify new behavior before cutover. It is not a generic test environment, even when the service name contains `-test`.

When a longer query contains a verified compact alias such as `网文二 repo validation_service`, xmem first locks the verified identity family, then treats `repo` / `validation_service` as requested fields. Weak same-template relation cards are hidden once a verified identity anchor exists, so agents do not get noisy unrelated sheet candidates.

## Guardrails and gain

`xmem check` inspects the current git diff against local and indexed `invariant` / `rule` / `guard` cards. It is intentionally lightweight: it looks for explicit `diff_guard.warn_if_removed`, `warn_if_added`, and `forbid` terms and exits non-zero for human-visible warnings.

`xmem gain` summarizes lookup, `context`, `preflight`, and `check` telemetry from `~/.xmem/gain.jsonl`. The default view is now the full event/query/card dashboard. Use `xmem gain --summary` for the short key summary with real confidence result, confirmed-vs-rough tokens, hit overview, risk signals, top query order, and the few queries that most need review. Top queries are sorted by calls desc, then matches desc, then rough tokens desc. In detail view, `Top 查询` aggregates query text, while `Top Cards` aggregates `top_card` ids from retrieval logs. Top card status/confidence is hydrated from the current registry when old gain rows did not record it. The `Top Card 解释` section shows common/recent queries, sources, avg score, and top why for noisy cards. Use `xmem gain card <id>` to inspect one card's common queries and recent hits. The bar column is `粗估占比`: relative rough-token share inside that section, not progress or confirmed savings. `xmem gain --detail` remains as a compatibility alias for the default full dashboard.

Hit/miss/pass/prevented are log counts; `hit` only means candidates were returned. Token savings are rough, uncalibrated estimates for context/preflight matches only, not billing truth. Risk hints come from rule warnings, not confirmed production bugs. By default, `xmem gain` reads all gain rows; use `--limit N` only when you want a recent slice.

The gain dashboard self-calibrates its own confidence labels. It reports whether the current data is only telemetry/proxy or partially calibrated, surfaces high rough estimates that need review, and records future match quality fields such as `top_score`, `top_status`, and `top_why`.

Outcome signals improve gain calibration over time. `xmem hook finish|fix|bug --verified` appends an `outcome.*` row to `gain.jsonl`, and `xmem gain confirm <query>` / `xmem gain reject <query>` can record explicit human calibration. These signals still do not turn rough token estimates into billing truth, but they let the dashboard distinguish pure proxy data from partially calibrated outcomes.

Confirmed/rejected gain outcomes also create review outbox items under `~/.xmem/outbox/gain-feedback`. Confirmed outcomes queue a Project Wiki review request; rejected or bug-prevented outcomes queue an Issue Record seed. This keeps upstream truth human-reviewed instead of silently mutating Project Wiki or issue records.

Agent-facing output should be compact by default. Prefer compact JSON or TOON-style summaries with evidence paths over full SCMP pod JSON, broad issue-tracking grep, raw safe-access/Feishu payloads, repeated lark-cli notices, or long skill/reference dumps. Raw payloads should be redirected to files and cited by path unless the user explicitly asks to see them.

## Useful commands

```bash
xmem setup                  # first-time generic workspace/project onboarding
xmem status                 # registry health and counts
xmem doctor                 # registry/source/backup/current repo diagnosis
xmem sync                   # rebuild from truth sources
xmem preflight <query>      # dev-start bug guards and required checks
xmem preflight --fields domain=... task=...  # structured preflight to avoid context pollution
xmem check --sources        # validate Project Wiki / Issue Record exports
xmem import project-memory  # import CONTEXT/ADR/OpenSpec/Spec Kit/Trellis from this folder
xmem import openspec        # import OpenSpec files only
xmem import speckit         # import Spec Kit files only
xmem import trellis         # import Trellis files only
xmem new                    # create/register .xmem for this folder
xmem why <query>            # explain matches
xmem open <card-id-or-query>
xmem fix                    # record alias correction/dispute
xmem suppress --card <id> --for-query <query/hash>  # mark true-but-irrelevant match for ranking only
xmem gain                   # full gain event/query/card dashboard
xmem gain --summary         # key gain summary only
xmem gain --detail          # compatibility alias for the full dashboard
xmem gain card <id>         # explain one top_card's common/recent query hits
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
