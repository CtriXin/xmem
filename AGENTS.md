# xmem Agent Rules

- Always keep xmem lightweight: file cards + SQLite index + event log.
- Do not add vector DB, web UI, background daemon, or non-stdlib dependencies without explicit approval.
- Files are truth; SQLite is index/cache only. Any DB fact must point back to a file, code path, git sha, runtime API, or human/source evidence.
- Treat `verified` cards as evidence-backed and `inferred` cards as untrusted hints.
- Prefer adding a small card over writing long wiki prose.
- Keep generated data in `.xmem/` or `~/.xmem/`; do not commit generated SQLite unless requested.
- Use `./bin/xmem` for local smoke tests.
- Optimize `context` output for LLM correctness, not terminal prettiness: include resolution status, why, truth state, evidence path, and next reads.
