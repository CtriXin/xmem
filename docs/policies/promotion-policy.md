# xmem Promotion Policy

This policy defines how discoveries move from agent observations into durable sources without turning xmem into a heavy DB.

## Source Owners

- Project Wiki owns stable project/entity truth: domain, service, repo, owner, deploy target, business name, oral alias.
- Issue Record owns bug truth: symptom, root cause, fix pattern, verification, regression guard, evidence paths.
- xmem owns compact routing memory: methods, invariants, corrections, relation cards, and refs to owner sources.
- Code/source plus generated map/codegraph own code structure; xmem stores only routing refs.
- Runtime APIs own current state: pipeline, pod, image, branch, DNS/binding, SCMP current.

Other systems should store refs to owner truth, not duplicate long content.

## Candidate To Promotion Flow

1. Agent observes a durable fact, bug, or correction.
2. Write a compact candidate to Project Wiki `agent-inbox`, Issue Record, or xmem card based on owner.
3. Keep candidate `partial` unless it already meets the truth-level policy.
4. Owner source reviews/promotes/merges/rejects the candidate.
5. Owner export updates.
6. Agent runs `xmem check --sources` and `xmem sync`.
7. xmem context/preflight may then treat the accepted export as source truth.

## Project Wiki Pending

Promote only stable mappings and aliases. Do not promote dynamic fields like current branch, latest deploy, current pod, SCMP current, or temporary pipeline status as permanent truth. Those may be stored as generated index/evidence with `last_checked_at`, TTL, and source refs.

Project Wiki pending is always hint-only inside xmem until Project Wiki exports it. Verified xmem cards can outrank pending rows for routing, but Project Wiki accepted export outranks xmem when they conflict.

## Issue Pattern Promotion

Create a new bug pattern when the root cause and prevention recipe are reusable. Merge into an existing pattern when the symptom differs but root cause, fix pattern, and guard are the same. Append evidence instead of creating a new pattern when only another occurrence is discovered.

A pattern should include symptom, root cause, fix pattern, regression guard, deterministic verification, affected scope, evidence refs, and status. Promote from `partial` to `verified` only under the truth-level policy.

If the same pattern appears at least twice or a high-impact production incident occurs, self-improve should remind the human/owner to review promotion or add a stronger preflight blocker.

## xmem Card Promotion

Use xmem cards for compact reusable relations, rules, methods, and corrections. Prefer one small card over a wiki page. A card must include truth status, confidence, basis, last_checked_at, summary, and evidence refs.

Do not write full issue logs, full Project Wiki pages, raw SCMP JSON, secrets, or large Feishu payloads into xmem cards. Store links/paths and compact summaries only.

## Reject And Dispute

Reject candidates that are duplicates, dynamic state masquerading as stable truth, unsupported guesses, or contradicted by owner source/runtime evidence. Mark `disputed` when the wrong value is still present in a source and add correction guidance so future agents do not repeat it.
