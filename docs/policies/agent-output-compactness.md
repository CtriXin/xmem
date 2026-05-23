# xmem Agent Output Compactness Policy

This policy defines how agents should avoid wasting context on human-facing raw logs, large JSON, broad grep output, and repeated status text. It is a routing policy, not an implementation for SCMP, lark-cli, or Issue Record.

## Default Rule

Agents should ask tools for agent-facing compact output first. If a tool can return both raw payload and summary, use the summary in chat/context and store the raw payload as an evidence file path. Do not paste full raw JSON, full grep logs, full skill docs, secrets, env blocks, or duplicated cards unless the user explicitly asks for them.

For structured packets, prefer compact JSON or TOON-style summaries with only the fields needed for the next decision. Full raw output belongs in evidence files.

## Common Waste Patterns

Avoid these by default:

- SCMP pod API raw JSON with env/container/lifecycle data when only pod name, status, image tag, restart count, and health check are needed.
- `rg /Users/xin` or broad `rg issue-tracking` before checking current issue/progress, Project Wiki index, xmem context, or SCMP context.
- safe-access full card JSON plus summary when the agent only needs pass counts and evidence path.
- Feishu message read-back raw JSON when only message_id, position, mentions, sender, and content digest are needed.
- Repeated lark-cli `_notice` / version warnings in every command output.
- Full `SKILL.md` or long reference docs when only one workflow section is needed.
- `rf getcf` raw account/DNS records for many domains when only domain -> feServer is needed.
- Rewriting the same conclusion manually into progress, issue timeline, handoff, and Project Wiki payload instead of generating one closeout card.

## Preferred Compact Shapes

Use or request outputs like these:

- pods: `ok`, `pod_count`, `running_new`, `old_remaining`, `restart_count`, `image_tag`, `health_check`.
- binding: `domain`, `expected_service`, `actual_service`, `status`.
- safe-access: `passed`, `domains_passed/total`, `assets_passed/total`, `evidence_path`, `failures`.
- deploy: `run_id`, `pipeline_status`, `branch`, `version`, `image`, `path_guard`, `evidence_path`.
- Feishu message: `message_id`, `position`, `mentions`, `sender`, `content_digest`.
- issue summary: `status`, `gates`, `evidence`, `open_risks`, `next_action`.

## Search Order

Before broad grep, use this order:

1. Current issue/progress/handoff for the active task.
2. `xmem preflight` / `xmem context` for known guards and routing.
3. Project Wiki accepted index for service/domain/repo truth.
4. SCMP compact lookup/current/profile helpers.
5. Directed `rg` in the specific repo or issue folder.
6. Broad issue-tracking/global search only with a small line limit and explicit reason.

## Raw Output Policy

Raw output is allowed when it is the evidence itself or needed to debug a parser, but it should be redirected to a file and referenced by path. If raw output may include secrets, env values, cookies, tokens, or full pod env, sanitize before storing or displaying.

## Closeout Policy

One compact closeout card should feed progress, Issue Record, Project Wiki writeback, and handoff. Agents should not handwrite the same status paragraph three times. If the tool cannot generate a unified closeout yet, write the compact card once and reuse it by path.
