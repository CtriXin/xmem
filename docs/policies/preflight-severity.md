# xmem Preflight Severity Policy

This policy defines how agents should act on `xmem preflight` output before coding, debugging, deploy, or traffic work.

## Severity Levels

- `hint`: helps route search or remember context; may proceed after noting it.
- `warn`: proceed only after preserving the invariant or adding the required check.
- `block`: stop the task action until the missing fact/check/source is resolved.

`risk_level` is a summary; individual guardrails decide whether to hint, warn, or block.

## Default Actions

- `blocked_source_stale`: block; run `xmem sync` and `xmem check --sources` before relying on context.
- `needs_disambiguation`: block edits/deploy; resolve project/domain/service first.
- `ready_with_guards`: proceed, but execute required checks and keep known invariants.
- `no_prior_memory`: proceed with normal source/code/runtime verification; do not invent memory.

## Blockers

Block implementation or deploy when any of these apply:

- Project/service/domain identity is ambiguous and the task could affect production.
- A verified invariant would be removed without an explicit replacement.
- Required deploy checks cannot be run or are failing.
- Runtime owner source contradicts the planned action.
- Domain binding does not point to the intended service for a traffic-switch task.
- SCMP deploy payload contains stale path or unexpected path fields.
- Pipeline succeeded but pod image/version has not converged.
- Pod health check path is missing in code/static files for Docker/PM2/static services.
- Safe-access or live verification fails and no classifier explains a safe next step.
- Approval/reporting is required but the message or read-back evidence is missing.

## Warnings

Warn, but do not necessarily block, when:

- A bug pattern is `partial` and has only one issue, but matches the task strongly.
- A Project Wiki pending row matches; use it only as hint until exported.
- A generated code index or map match suggests a file; verify in source before editing.
- A sheet/domain group is majority-backed but branch/deploy data is older than the task.

## Hints

Hints include low-score body matches, old issue evidence, single-domain sheet groups, folder-name identity, and generated index refs. Hints may guide the next read, but they must not be cited as final truth.

## Required Closeout

If preflight raised a guard, closeout must state which required checks passed or why a guard was intentionally not applicable. For deploy work, closeout should include deploy run, pipeline state, pod image/version, health path when relevant, safe-access/live verification, and issue/Project Wiki writeback refs.
