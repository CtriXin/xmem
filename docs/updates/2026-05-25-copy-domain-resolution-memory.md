# xmem Copy-Domain Resolution Memory - 2026-05-25

Status: implemented as built-in xmem cards and covered by tests.

## Why

The t102746 retro showed a repeated routing failure: new copied domains may not exist in `lookup` / `rf getcf` yet. Treating that miss as "project not found" wastes time and pushes the user to provide service identity manually. The durable lesson belongs in xmem because it is a cross-task resolution strategy and should work even when a task is not launched through `scmp-ops`.

## Added Cards

- `scmp.rule.copy-domain-resolve-by-sibling-template`
  - New copied domains can miss lookup/rf.
  - Resolve through template name, old sibling domains, Project Wiki, Issue Record history, or repo web-configs.
  - Do not ask the user for service name before checking indexed memory.
  - Do not claim online safe-access success before binding is live-verified.

- `scmp.identity.crypto-template1-adx-copy-domain`
  - Compact routing identity for `t102746 / crypto模版一 / ADX-4638`.
  - Route: `ptc_ssr_crypto` -> `ptc-temp-crypto-adx` -> repo `git@gitlab.adsconflux.xyz:ptc/fe/ptc_ssr_crypto.git`.
  - Old sibling examples: `coindomax.com`, `cointomay.com`, `cryptimx.com`, `coinhubtime.com`, `newstechni.com`, `cryptmaxy.com`.
  - Target domains observed in t102746 are included as evidence, not permanent current binding truth.

## Non-SCMP Usage

Any agent can run:

```bash
xmem resume "t102746 crypto模版一 复制域名 4638"
xmem preflight "新建复制域名 crypto模版一 新域名查不到"
```

This avoids relying only on `scmp-ops`. `scmp-ops` can improve the experience by calling these automatically, but the xmem card itself is available to Codex/Claude/OpenCode/MMS sessions through the normal xmem CLI and skill.

## Boundary

- This is routing memory, not current runtime truth.
- Domain binding, branch, pipeline, pod state, cache, and live ad behavior still need owner-source or live verification.
- Project Wiki / Issue Record should own accepted stable mappings and evidence; xmem stores compact recall cards and source refs.

## Verified Checks

- `PYTHONPATH=. pytest -q tests/test_v01.py`
- New coverage:
  - `test_preflight_recalls_copy_domain_resolution_guard`
  - `test_resume_routes_crypto_template_copy_domain_task`
