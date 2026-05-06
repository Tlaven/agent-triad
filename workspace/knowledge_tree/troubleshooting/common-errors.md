---
title: 常见错误与排错指南
source: project_seed
created_at: '2026-05-07T00:00:00+08:00'
metadata:
  category: troubleshooting
---

AgentTriad 常见错误及诊断方法。

**Executor 子进程问题**：
- "Executor 服务不可达" — 检查子进程是否启动（端口冲突、Python 路径）
- "Executor LLM 调用超时" — 调大 `executor_call_model_timeout`（默认 180s）
- "V3 基础设施启动失败" — 检查 .env 编码（必须是 UTF-8 无 BOM），避免中文字符
- "进程端口已被占用" — 终止残留 Python/uvicorn 进程后重试

**LLM 连接问题**：
- "API key 无效" — 检查 .env 中 `LLM_API_KEY` 是否正确
- "模型不存在" — 检查 `config/agent_models.toml` 模型名称
- "连接超时" — 检查网络代理设置和 `LLM_BASE_URL`

**知识树问题**：
- 检索无结果 — 确认 `ENABLE_KNOWLEDGE_TREE=true`，检查种子文档是否存在
- 语义 embedder 加载失败 — 检查 `sentence-transformers` 是否安装，模型会自动降级到 hash
- hash embedder 检索分数低 — 正常现象（0.15-0.4），阈值默认 0.15

**工具执行问题**：
- "path 超出允许的根目录范围" — 文件操作必须在 `workspace/agent/` 内
- "content 过大" — 文件写入限制 1MB
- "command 过长" — 命令长度限制 2000 字符
- "命令执行超时" — 调大 timeout 参数（上限 3600s）
