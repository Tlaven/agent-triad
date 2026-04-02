.PHONY: all help setup dev dev_ui lint format test_unit test_v1_auto test_v1_acceptance

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

# V1 自动化验收（当前可自动化的部分）
test_v1_auto:
	uv run pytest tests/unit_tests/supervisor_agent tests/unit_tests/planner_agent -q

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
	@echo "  make test_unit          运行全部单元测试"
	@echo "  make test_v1_auto       运行 V1 自动化验收测试"
	@echo "  make test_v1_acceptance 运行自动化后给出手测步骤"

