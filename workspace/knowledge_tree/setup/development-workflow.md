---
title: 开发工作流与常用命令
source: project_seed
created_at: '2026-05-07T00:00:00+08:00'
metadata:
  category: setup
---

AgentTriad 开发工作流基于 `uv` 包管理器和 `make` 命令。

常用命令：
- `make dev` — 启动 LangGraph 开发服务器（端口 2024），支持热重载
- `make dev_ui` — 同上并打开 LangGraph Studio UI
- `make test_unit` — 运行单元测试（tests/unit_tests/）
- `make test_integration` — 运行集成测试（tests/integration/）
- `make test_automated` — 单元 + 集成（无真实 LLM）
- `make test_e2e` — E2E 测试（需 API key，-m live_llm）
- `make test_coverage` — 覆盖率报告（阈值 80%）
- `make lint` — ruff check + mypy --strict src
- `make format` — ruff format + import 排序

交互式测试工具 `chat.py`：
- `uv run chat.py --kt` — 启用知识树交互测试
- `uv run chat.py --kt --script e2e.txt --report e2e.json` — 非交互脚本执行
- `--kt-root` 指定知识树根目录，`--reset-kt-root` 重置测试数据
- `--turn-timeout 120` 设置每轮超时

测试分层：unit（mock，快速）→ integration（组件组合，无 LLM）→ e2e（需 API key）。
