# 2026-05-25 SCMP branch deploy tag memory

This update adds a compact xmem rule for a repeated SCMP deploy payload pitfall.

## New card

- `examples/cards/scmp/deploy-tag-empty-for-branch.yaml`
- id: `scmp.rule.deploy-tag-empty-for-branch`
- truth: `verified`

## Rule

For SCMP branch deploy:

- use `--branch` for the target branch;
- use `--version` for the commit/version identifier;
- leave `--tag` empty unless deploying an actual git/release tag;
- do not pass a commit hash to `--tag`.

## Gateway tuning

`xmem gateway` now filters ad-specific cards when a prompt is clearly about log/raw-output text that merely contains `ads.txt` or advertising config snippets. This reduces false advertising gates for log/closeout tasks while preserving project/service identity cards.

## Validation

- `PYTHONPATH=. pytest -q tests/test_v01.py`
- `python3 tests/smoke.py`
- `python3 -m py_compile xmem/*.py`
