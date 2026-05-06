---
title: 环境配置与变量参考
source: project_seed
created_at: '2026-05-07T00:00:00+08:00'
metadata:
  category: setup
---

AgentTriad 通过 `.env` 文件和 `config/agent_models.toml` 管理配置。

环境变量分四类：
1. **LLM 连接**：`LLM_API_KEY`（必须）、`LLM_BASE_URL`（可选，默认 OpenAI）、`LLM_MODEL`（默认由 agent_models.toml 决定）
2. **分 Agent 参数**：`SUPERVISOR_TEMPERATURE`/`PLANNER_TEMPERATURE`/`EXECUTOR_TEMPERATURE`（默认 0.1）、
   `SUPERVISOR_MAX_TOKENS`、`SUPERVISOR_TOP_P`、`EXECUTOR_SEED` 等，每个 Agent 可独立调节
3. **功能开关**：`ENABLE_KNOWLEDGE_TREE=true`（启用 V4 知识树）、`ENABLE_IMPLICIT_THINKING`（启用推理模式）、
   `ENABLE_DEEPWIKI`（MCP DeepWiki）、`ENABLE_FILESYSTEM_MCP`（文件系统 MCP）
4. **超时保护**：`executor_call_model_timeout=180`（LLM 调用超时）、`executor_tool_timeout=300`（工具超时）、
   `executor_wait_timeout=300`（Supervisor 等待超时，须大于 call_model_timeout）

模型配置文件 `config/agent_models.toml` 定义每个 Agent 的默认模型和参数。
使用 `uv run` 前缀运行所有命令。`make setup` 安装依赖。
