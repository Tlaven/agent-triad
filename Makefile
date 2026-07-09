.PHONY: all help setup dev dev_ui dev_probe lint format test_unit test_integration test_e2e test_e2e_parallel test_llm_health test_automated test_coverage test_lint_coverage test_full test_all test_everything test_v1_auto test_v1_acceptance test_kt_hardening test_kt_benchmarks test_filter_recall

all: help

DEV_PORT ?= 2024
TEST_COVERAGE_FAIL_UNDER ?= 80

setup:
	uv sync --dev

dev:
	uv run langgraph dev --config langgraph.json --port $(DEV_PORT) --no-browser

dev_ui:
	uv run langgraph dev --config langgraph.json --port $(DEV_PORT)

# 探测专用：禁用 watchfiles 热重载，避免 kt_probe 写盘 / 日志轮转触发 reload 杀掉 run
# 详见 docs/probe-analysis-2026-07-01.md N1
dev_probe:
	uv run langgraph dev --config langgraph.json --port $(DEV_PORT) --no-browser --no-reload

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

# 单元 + 集成（Mock LLM），不含 E2E / live_llm。适合改代码后的快速回归，≠「覆盖率全量」。
test_automated:
	uv run pytest tests/unit_tests tests/integration -q

# 覆盖率统计（单元 + 集成，Mock LLM）。用于衡量代码行覆盖，不等于真实场景“行为全覆盖”。
test_coverage:
	uv run pytest tests/unit_tests tests/integration -q --cov=src --cov-report=term-missing --cov-report=xml --cov-fail-under=$(TEST_COVERAGE_FAIL_UNDER)

# 合并前 / 发版前推荐：静态检查（ruff + mypy）+ 单元与集成测试的 src 行覆盖率（Mock LLM，不含 E2E）。
test_lint_coverage: lint test_coverage

# 在 test_lint_coverage 基础上再跑真实 LLM E2E。需 API Key，慢且受配额/网络影响；
# 适合发版前本地或夜间流水线，不适合无密钥的默认 PR CI。E2E 前可先 make test_llm_health。
test_everything: test_lint_coverage test_e2e

# V1 自动化验收（当前可自动化的部分）
test_v1_auto:
	uv run pytest tests/unit_tests -q

# V1 验收入口：先跑自动化，再提示手测步骤
test_v1_acceptance: test_v1_auto
	@echo "V1 自动化检查已完成。"
	@echo "接下来执行手测："
	@echo "1) make dev_ui 启动 LangGraph Studio（读取 langgraph.json）"
	@echo "2) 场景A：简单问答，期望不调用工具或快速结束"
	@echo "3) 场景B：短流程任务，期望调用 call_executor 并返回结构化结果"
	@echo "4) 场景C：故意失败任务，期望触发重规划并在 MAX_REPLAN 后收敛停止"

# ─── KT 加固回归 ───────────────────────────────────────────
# 详见 docs/superpowers/plans/2026-07-04-kt-cleanup-and-filter-hardening.md
# 与 plan: C:\Users\TL\.claude\plans\rippling-wobbling-pretzel.md

# Entry A 加固真 LLM 回归（5 场景）。前置：make dev 已启动 + .env 已配 API key。
# 走 langgraph dev SDK，每场景 30-60s，总 ~5min。标 live_llm，PR CI 不跑。
test_kt_hardening:
	uv run pytest tests/e2e/test_kt_entry_a_hardening_live.py -v -m live_llm -s

# dedup_benchmark 阈值门禁（PR CI 用）。读 workspace/knowledge_tree/.vector_index.json。
# 对生产阈值 0.95 行断言 precision/recall。当前数据集无重复节点（ground truth=0），
# precision/recall=nan 会被跳过；如未来出现真合并，断言生效。
test_kt_benchmarks:
	uv run python scripts/dedup_benchmark.py --dataset index --min-precision 0.90 --min-recall 0.85

# filter_recall_benchmark 用 fixture（CI 用，不依赖 logs/probes/）。
# fixture 是难样本集中版（NEGATIVE 占 43%），precision 看起来比全量低（0.53 vs 0.86）。
# 阈值留 headroom，能抓大幅退化。
test_filter_recall:
	uv run python scripts/filter_recall_benchmark.py \
		--fixture tests/fixtures/probes/turns_sample.jsonl \
		--min-precision 0.45 --min-recall 0.65

help:
	@echo "Development:"
	@echo "  make setup              安装依赖（uv sync --dev）"
	@echo "  make dev                启动 langgraph dev（无 UI，默认端口 2024）"
	@echo "  make dev_ui             启动 langgraph dev（Studio UI，默认端口 2024）"
	@echo "  make dev_probe          同 dev 但禁用热重载（探测专用，避免 kt_probe/log 触发 reload）"
	@echo "                          可覆盖端口：make dev DEV_PORT=2025"
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
	@echo "  make test_automated     单元 + 集成（Mock LLM，改代码后推荐烟测）"
	@echo "  make test_coverage      仅 pytest：单元 + 集成 + 覆盖率（不含 lint）"
	@echo "  make test_lint_coverage lint + test_coverage（合并前/发版前；Mock LLM，不含 E2E）"
	@echo "  make test_everything    test_lint_coverage + test_e2e（真实 LLM；见 Makefile 注释）"
	@echo "  make test_v1_auto       运行 V1 自动化验收（等同 test_unit）"
	@echo "  make test_v1_acceptance 运行自动化后给出手测步骤"
	@echo ""
	@echo "KT Hardening:"
	@echo "  make test_kt_hardening  Entry A 真 LLM 回归（5 场景；前置 make dev + API key）"
	@echo "  make test_kt_benchmarks dedup_benchmark 阈值门禁（PR CI）"
	@echo "  make test_filter_recall filter_recall_benchmark fixture 阈值门禁（PR CI）"

