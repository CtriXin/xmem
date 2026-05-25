# xmem COSCLI Isolated Env Memory - 2026-05-25

Status: implemented as a built-in verified guard card.

## Why

A later SCMP/COS deploy session reported that xmem usage was `0%` and the agent spent roughly 10 debug turns on `coscli secretID is missing` before reaching the actual likely cause: isolated agent HOME/config lookup. The useful reusable lesson is not the specific deploy result; it is the failure pattern and shortest safe debug path.

## Added Memory

Card: `examples/cards/scmp/coscli-isolated-home.yaml`

It records:

- `coscli secretID is missing` inside isolated sessions should first be classified as possible isolated HOME / real credential path mismatch.
- Retry with a real user HOME / REAL_PATH env template before assuming credentials are missing.
- Avoid long config/log/inode debugging loops before checking the env boundary.
- Avoid pasting full `deploy.config.json` or raw coscli logs when a compact bucket/region/upload/purge summary is enough.
- Verify bucket, region, branch, and runtime deploy state from owner sources or live evidence before closeout.

## Boundary

xmem does not know the current bucket, region, deploy branch, or live COS state unless an owner source exports those facts. This card only prevents repeated debugging waste and routes the agent toward the correct first checks.

SCMP-side follow-ups still belong in `scmp-ops`:

- a COS upload preflight/retry wrapper;
- compact `deploy.config.json` summary extraction;
- automatic xmem preflight/resume when SCMP detects coscli/rf/lookup failure classes.

## Verified Checks

- `PYTHONPATH=. pytest -q tests/test_v01.py`
- New coverage:
  - `test_preflight_recalls_coscli_isolated_home_guard`
  - `test_resume_surfaces_coscli_token_savers`
