---
name: xmem
description: Read-only historical lookup when prior project evidence or a similar past issue would help the current task. Returns source pointers, freshness and conflicts; it does not own current truth or task state.
---

# xmem

用现有历史索引找来源线索，继续在原 Stride task 工作。默认唯一检索入口：

```bash
xmem lookup "具体项目或问题" --limit 8 --json
```

可显式用 `--registry /absolute/path/registry.sqlite` 指定已有库。返回 `xmem.lookup.v1`、`readonly=true`、`historical_only=true`；入口不建库、不迁移、不 sync、不 capture/profile/pending injection、不记录 telemetry。

## 读取结果

- `status=unavailable` 或无匹配时继续直接读当前项目来源，不自动 setup、index 或 sync。
- `state=conflict` 保留双方 producer 的 `variants` 和路径；不要静默选一方为正确。
- `current` 只表示 source bytes 与索引指纹相同；`effective_truth=historical` 仍不是当前 runtime、发布、权限或业务验收。
- `changed` / `missing` / `unknown` 是来源核验边界。直接读取需要的真实 source；不要通过重新索引冒充事实复核。
- `indexed_at` 是索引维护时间；`source_checked_at` 是 source 声明的核验时间。mtime、receipt 时间和最近索引时间不能补成事实已验证。
- 命中次数、dry-run、would-inject 不证明实际采用或节省 token。没有可归因 measurement 的收益写 `unknown`。

## 当前边界

新任务不自动运行 gateway、resume、preflight、hook、sync、capture、profile 或写卡。历史索引里的 next_action/required_checks/blocker 是待核实线索，不能直接恢复 OII、阻断原任务或授予生产权限。经验先保存在原 task；只有经过修复验证且具有适用范围的规则才考虑进入 Stride learning，不另建一份真值库。

已实现的 `STRIDE_EXECUTION_CONTEXT=stride-v1` 或经过真实 task-owned cwd 核对的 memory scope，仅让 hook/gateway 跳过 memory 副作用，绝不是动作授权。缺标识的旧 CLI 行为仍兼容；不要主动调用旧入口来填补 lookup 缺口。显式 mutation 命令未被这个 marker 禁用。

旧能力和历史库不删除。只有明确维护旧 xmem/OII 时才读 [历史完整说明](references/history/skill-before-stride-20260908.md) 并确认所选命令的真实副作用；它不是新任务默认工作流。
