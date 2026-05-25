# xmem Public Onboarding Update - 2026-05-24

Status: documented and ready for public optional install through MMS.

## What's New

- `xmem setup` is the generic first-run flow for new users and workspaces.
- MMS can install xmem as an optional global pack with `bash install.sh --install-xmem`.
- MMS `--dry-run` previews the xmem install/setup plan without writing files.
- Installer-style onboarding uses `--register-only` so the first install creates `~/.xmem` and registers shallow roots without writing repo-local `.xmem` files everywhere.
- xmem now honors explicit host-home env values when reporting `real_user_home` and choosing the registry in isolated or temp-HOME installs.

## Supported Public Features

- Compact project/method/invariant/correction cards with truth status and evidence refs.
- User-owned global workspace under `~/.xmem` plus optional repo-local `.xmem` cards.
- `xmem context` for compact retrieval before broad repo search.
- `xmem preflight` for historical bug-patterns, invariants, required checks, and blockers before edits.
- `xmem check` for lightweight invariant checks against the current diff.
- `xmem sync` as a generated-registry rebuild from owned sources.
- `xmem gain` for retrieval telemetry, rough saved-token hints, and outcome calibration.
- `xmem suppress` for true-but-irrelevant ranking feedback.
- Optional imports from generic project memory docs, OpenSpec, Spec Kit, Trellis, map, and codegraph refs when those files exist.

## Public Boundaries

- No private SCMP / Project Wiki / Issue Record dependency in the default install.
- No silent cloud backup or upload.
- No claim that every unknown fact is searchable; xmem can only route from indexed sources, cards, aliases, and evidence refs.
- No replacement for tests, code review, runtime verification, or a real project wiki/issue tracker.
- Generated `registry.sqlite` remains cache; source files, code, git, runtime checks, and human-confirmed cards remain truth.

## Verified Smoke

A temp-HOME MMS install smoke was run with local xmem source:

```bash
bash install.sh --lang en --install-xmem --dry-run
bash install.sh --lang en --install-xmem
~/.local/bin/xmem --version
~/.local/bin/xmem status
```

Expected result: xmem CLI/skill files are installed in the temp HOME, `~/.xmem` is created there, and `xmem status` reports the temp HOME as both `xmem_home` base and `real_user_home` when explicit host-home env is set.
