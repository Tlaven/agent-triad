.PHONY: all help setup dev dev_ui lint format test_unit test_integration test_e2e test_e2e_parallel test_llm_health test_all test_v1_auto test_v1_acceptance

all: help

setup:
	uv sync --dev

dev:
	uv run langgraph dev --config langgraph.json --no-browser

dev_ui:
	uv run langgraph dev --config langgraph.json

lint:
	uv run ruff check src tests
	uv run mypy --strict src

format:
	uv run ruff format src tests
	uv run ruff check --select I --fix src tests

test_unit:
	uv run pytest tests/unit_tests -q

test_integration:
	uv run pytest tests/integration -q

test_llm_health:
	uv run pytest tests/e2e/test_llm_health.py -q -s --tb=line

test_e2e:
	uv run pytest tests/e2e -m live_llm -q

# 并行跑 E2E（多进程；适合「不同场景」互不共享状态时用）。注意 API 速率限制与账单。
# 工作进程数：默认 auto（CPU 核数）；可覆盖，例如: make test_e2e_parallel E2E_WORKERS=4
E2E_WORKERS ?= auto
test_e2e_parallel:
	uv run pytest tests/e2e -m live_llm -q -n $(E2E_WORKERS)

test_all:
	uv run pytest tests/unit_tests tests/integration -q

# V1 自动化验收（当前可自动化的部分）
test_v1_auto:
	uv run pytest tests/unit_tests -q

# V1 验收入口：先跑自动化，再提示手测步骤
test_v1_acceptance: test_v1_auto
	@echo "V1 自动化检查已完成。"
	@echo "接下来执行手测："
	@echo "1) make dev_ui 启动 LangGraph Studio（读取 langgraph.json）"
	@echo "2) 场景A：简单问答，期望不调用工具或快速结束"
	@echo "3) 场景B：短流程任务，期望调用 execute_plan 并返回结构化结果"
	@echo "4) 场景C：故意失败任务，期望触发重规划并在 MAX_REPLAN 后收敛停止"

help:
	@echo "Development:"
	@echo "  make setup              安装依赖（uv sync --dev）"
	@echo "  make dev                启动 langgraph dev（无 UI）"
	@echo "  make dev_ui             启动 langgraph dev（Studio UI）"
	@echo ""
	@echo "Quality:"
	@echo "  make lint               运行 ruff + mypy"
	@echo "  make format             自动格式化并整理 import"
	@echo ""
	@echo "Tests:"
	@echo "  make test_unit          运行全部单元测试（无 LLM）"
	@echo "  make test_integration   运行图级集成测试（Mock LLM）"
	@echo "  make test_llm_health    运行 LLM 连通性 + 延迟诊断（快速，建议 E2E 前先跑）"
	@echo "  make test_e2e           运行 E2E 验收测试（真实 LLM，需 API Key，串行）"
	@echo "  make test_e2e_parallel 同上，但用 pytest-xdist 并行（可用 E2E_WORKERS=4）"
	@echo "                          单测超时：E2E_TEST_TIMEOUT（秒，默认 600；0=关闭）"
	@echo "  make test_all           运行单元测试 + 集成测试"
	@echo "  make test_v1_auto       运行 V1 自动化验收（等同 test_unit）"
	@echo "  make test_v1_acceptance 运行自动化后给出手测步骤"

