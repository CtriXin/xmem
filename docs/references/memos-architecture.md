# MemOS 架构参考笔记

> 来源：https://github.com/MemTensor/MemOS
> 记录日期：2026-05-29
> 定位：跨 session 的 LLM 记忆操作系统，非 xmem 直接竞品，但有设计思路可借鉴。

---

## 核心设计

MemOS 把 LLM 记忆分三层，类比 OS 概念：

| 层 | 类比 | 职责 |
|---|---|---|
| L1 Traces | 磁盘块 | 原始交互数据，不可变日志 |
| L2 Policies | 调度器 | 决定什么值得记、何时检索、何时遗忘 |
| L3 World Models | 用户态 | 高阶理解 + 可复用技能（Crystallized Skills） |

关键洞察：**记忆不只是检索，还有写入策略和衰减策略**。xmem 目前只做了 L1（存储）+ 简单检索，L2 是潜在的扩展方向。

---

## 对 xmem 有参考价值的机制

### 1. Memory Feedback / Correction
MemOS 支持用自然语言修正已存储的记忆。适用场景：agent 写了错误的项目结论，下一次 session 可以纠正。

xmem 目前：卡片写完就是静态文件，过时了只能手动改或删。
可借鉴：在 event log 里追加 correction event，卡片有 `corrected_by` 字段。

### 2. 可检索性设计（inspectable by design）
MemOS 声称"structured as a graph, not a black-box embedding"。图结构让记忆可解释、可审计。

xmem 当前：卡片是 yaml，可读性好，但缺少关系表达（card A 引用 card B）。
可借鉴：卡片加 `related_cards` 或 `supersedes` 字段，形成轻量有向关系。

### 3. MemScheduler（异步处理）
生产场景下记忆写入不应阻塞主流程。MemOS 用 Redis Streams 做异步 ingestion。

xmem 当前：同步写 SQLite，对本地 CLI 场景足够。
可借鉴：如果 xmem 未来要服务多 agent 并发，考虑写入队列化。目前不需要。

---

## 不建议借鉴的部分

- **Neo4j 图数据库**：xmem 定位轻量本地工具，引入 Neo4j 是过度依赖。
- **Redis Streams**：同上，本地 CLI 项目不需要消息队列。
- **云部署架构**：MemOS 是 SaaS/自部署服务；xmem 是本地 CLI 工具，架构哲学不同。

---

## 与 Mem0 的对比（备忘）

[Mem0](https://github.com/mem0ai/mem0) 定位类似 MemOS，但更轻量（Python，无图数据库依赖）。
如需进一步调研，可对比两者的检索策略和 memory schema 设计。
