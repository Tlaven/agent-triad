# 测试目录说明

本目录的 pytest 分层见下文；**改完代码想快速确认有没有明显回归**，优先用下面这一条。

---

## 改完代码先跑什么

| 场景 | 命令 | 说明 |
|------|------|------|
| **日常回归（推荐）** | `make test_automated` | 单元 + 集成，Mock LLM，**不调真实 LLM**；跑的是仓库里已有的自动化用例，适合改代码后的快速烟测。 |
| **合并前检查（推荐）** | `make test_lint_coverage` | `lint`（ruff + mypy）+ `tests/unit_tests` 与 `tests/integration` 的 `src` 行覆盖率；**不含** `tests/e2e` 与真实 LLM。别名：`make test_full` / `make test_all`。 |
| 只跑单元 | `make test_unit` | 更快，不含图级集成。 |
| 只跑集成 | `make test_integration` | 图级 + Mock LLM。 |
| 静态检查 | `make lint`（在项目根） | Ruff + MyPy；与 pytest 互补。 |
| 真实 API 是否通 | `make test_llm_health` | 约十几秒；**跑 E2E 前建议先跑**。 |
| 真实 LLM 全链路 | `make test_e2e` | 分钟级、计费与网络依赖；按需或发版前。 |
| **E2E Server 测试** | `uv run python -u tests/e2e/test_comprehensive_server.py` | 需 `make dev` 运行中；20 用例 × 全工具覆盖 × 三级验证；~15min。 |
| E2E Server 全工具 | `uv run python -u tests/e2e/test_all_tools_server.py` | 需 `make dev`；10 用例快速覆盖；~10min。 |
| E2E Server 知识树 | `uv run python -u tests/e2e/test_kt_via_server.py` | 需 `make dev`；5 用例 KT 专项；~3min。 |
| 自动化 + E2E 串联 | `make test_everything` | 先 `test_lint_coverage`，再 `test_e2e`；需 API Key，适合发版前本地。 |

`make test_coverage` 会执行 `unit + integration` 并输出覆盖率报告（含 `coverage.xml`）；默认覆盖率门槛由 `TEST_COVERAGE_FAIL_UNDER` 控制（默认 80）。

等价 pytest（在项目根执行）：

```bash
uv run pytest tests/unit_tests tests/integration -q
# 或查看覆盖率：
uv run pytest tests/unit_tests tests/integration -q --cov=src --cov-report=term-missing --cov-report=xml
```

---

## 文档怎么读

| 文件 | 内容 |
|------|------|
| [`TESTING.md`](TESTING.md) | 目录分层、`.env`、代理、E2E 前置检查、**E2E Server 测试方法论与三级验证**、已知 LLM 行为问题、FAQ。 |
| [`V2_TESTING.md`](V2_TESTING.md) | V2-a/b/c（Observation、Planner/MCP、Reflection）相关文件与**针对性** pytest 命令。 |

根目录 [`README.md`](../README.md) 的快速上手会与这里对齐；`docs/README.md` 中的测试索引指向上述文件。
