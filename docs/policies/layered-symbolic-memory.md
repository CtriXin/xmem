# Layered Symbolic Memory Policy

This policy defines how xmem absorbs layered/symbolic memory ideas without becoming a heavy memory platform.

## Rule

xmem packets use progressive disclosure:

1. Top layer: compact `xmem_context` / `xmem_preflight` sections for agent reasoning.
2. Middle layer: card items with stable `node_id`, `memory_layer`, `source_ref`, `source_path`, and `evidence_ref`.
3. Bottom layer: source files, code, Issue Record, Project Wiki, runtime APIs, or human evidence.

The top layer is a symbolic task canvas, not truth. Truth stays in files/code/runtime and owner systems.

## Layers

- `L3:policy_profile`: rules, invariants, guardrails, SOPs, and user/project preferences.
- `L2:scenario_pattern`: methods, specs, traffic/relation patterns, and reusable fix patterns.
- `L1:atom_card`: entity facts, aliases, corrections, Project Wiki cards, and compact hook memories.
- `L0:raw_evidence`: issue records, code-index refs, source paths, logs stored by path, and runtime evidence.

## Guardrails

- Do not paste raw logs or long source records into xmem cards.
- Do not treat generated SQLite rows, code indexes, or pending Project Wiki rows as final truth.
- Do not create lossy summaries without a drilldown path.
- Prefer one compact node plus an evidence path over duplicating full content.

## Checks

- `xmem context "<task>"` should show `symbolic_memory`.
- Card items in context/preflight sections should show `node_id` and `memory_layer`.
- Items that cite truth should keep `source_ref`, `source_path`, or `evidence_ref`.
- `xmem open <card_id>` or direct source paths should recover the underlying evidence.
