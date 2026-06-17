# xmem TODO

## 有价值的改进（待评估优先级）

### 记忆可编辑与反馈循环
xmem 当前卡片是静态 yaml，没有"agent 写错后修正"的机制。
MemOS 的做法：自然语言纠错 → 更新存储 → 下次检索用修正后版本。
**价值**：agent 跨 session 上下文容易过时，有纠错能力后记忆衰减可控。
**参考**：[MemOS 架构笔记](docs/references/memos-architecture.md)

### 向量检索索引
卡片数量增长后，纯文件名/关键字匹配召回率不够。
MemOS 用 Qdrant 做语义检索；xmem 可以考虑轻量方案（sqlite-vec / lancedb）。
**价值**：卡片 >50 张时检索效率和相关性明显下降。
**前提**：需要先有明确的检索 query 场景，避免过早优化。

### 记忆分层：Policy 层分离
MemOS 区分"什么值得记"（Policy）和"怎么检索"（Retrieval），目前 xmem 这两个关注点混在 card schema 里。
**价值**：当规则复杂化时（不同项目类型、不同 agent 行为），分离有助于扩展。
**前提**：当前项目规模不需要，等实际需求出现再拆。

---

## 记录在案（低优先级，有空再看）

- [ ] 调研 sqlite-vec 是否满足 xmem 的向量检索需求
- [ ] 评估 MemOS feedback loop 机制在卡片场景的适用性
- [ ] 考虑给 card 加 `last_verified_at` 字段，过期卡片自动降权
