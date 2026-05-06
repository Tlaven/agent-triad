---
title: V4 知识树两层存储架构
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: patterns
---

V4 知识树是 AgentTriad 的上下文自管理核心，目标是让 Agent 管理自己的记忆。

架构：两层存储 + Overlay：
- 文件系统（Source of Truth）：Markdown 文件 + YAML frontmatter，目录层级即树结构
- 向量索引：内存中 cosine similarity 索引，存储 content_embedding 和目录锚点
- Overlay JSON：跨目录关联边（如"测试"关联"编码规范"）

向量-结构互塑闭环：
- 向量→结构：新信息 embed 后找最近目录锚点，自动放入对应目录（零 LLM）
- 结构→向量：文件变更自动重算目录锚点（质心），实时校准向量空间

检索（RAG）使用三路 RRF 融合（k=60）：
- Path 1：content embedding（内容语义匹配）
- Path 2：title embedding（标题匹配）
- Path 3：anchor expansion（命中目录锚点后扩展到同目录其他节点）

知识摄入两个入口：
- Entry A（自动）：Executor 完成后自动提取有价值的执行知识
- Entry B（主动）：Supervisor 调用 knowledge_tree_ingest 工具

语义 embedder：bge-small-zh-v1.5（512 维），不可用时降级到 n-gram hash embedder。
kt_retrieve 图节点在用户消息时自动 RAG 注入，threshold 0.4，top-3 结果。
