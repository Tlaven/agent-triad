---
title: 测试分层与执行方式
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: conventions
---

测试命令统一使用 uv run pytest。包管理器为 uv（所有命令前缀 uv run）。

测试分层：
- unit_tests/：无外部依赖，mock 所有 IO，快速执行
- integration/：模拟组件交互，测试模块间协作
- e2e/：需真实 API key，使用 -m live_llm marker 标记

常用命令：
- make test_unit：单元测试
- make test_integration：集成测试
- make test_automated：单元 + 集成（无真实 LLM）
- make test_coverage：覆盖率报告（阈值 80%）
- make lint：ruff check + mypy --strict

运行单个测试：uv run pytest tests/unit_tests/path/test_file.py::test_name -q

知识树测试：
- 默认使用 mock_embedder（避免下载 sentence-transformers 模型）
- 需要真实语义模型的测试使用 conftest_semantic.py 的 requires_semantic marker
- 无 sentence-transformers 环境下自动 skip 语义测试

配置：测试环境变量见 .env / .env.example，TESTING.md 有详细分层说明。
