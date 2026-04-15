# 测试目录说明

本目录的 pytest 分层见下文；**改完代码想快速确认有没有明显回归**，优先用下面这一条。

---

## 改完代码先跑什么

| 场景 | 命令 | 说明 |
|------|------|------|
| **日常回归（推荐）** | `make test_automated` | 单元 + 集成，Mock LLM，**不调真实 LLM**；跑的是仓库里已有的自动化用例，**不是**代码覆盖率意义上的「全量」或「全覆盖」。 |
| 只跑单元 | `make test_unit` | 更快，不含图级集成。 |
| 只跑集成 | `make test_integration` | 图级 + Mock LLM。 |
| 静态检查 | `make lint`（在项目根） | Ruff + MyPy；与 pytest 互补。 |
| 真实 API 是否通 | `make test_llm_health` | 约十几秒；**跑 E2E 前建议先跑**。 |
| 真实 LLM 全链路 | `make test_e2e` | 分钟级、计费与网络依赖；按需或发版前。 |

`make test_all` 与 `make test_automated` **完全相同**（仅为兼容旧名称与文档习惯）。

等价 pytest（在项目根执行）：

```bash
uv run pytest tests/unit_tests tests/integration -q
```

---

## 文档怎么读

| 文件 | 内容 |
|------|------|
| [`TESTING.md`](TESTING.md) | 目录分层、`.env`、代理、E2E 前置检查、FAQ、**新增测试约定与准入规则**。 |
| [`V2_TESTING.md`](V2_TESTING.md) | V2-a/b/c（Observation、Planner/MCP、Reflection）相关文件与**针对性** pytest 命令。 |

根目录 [`README.md`](../README.md) 的快速上手会与这里对齐；`docs/README.md` 中的测试索引指向上述文件。
