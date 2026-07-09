# CI 配置参考（GitHub Actions）

> 定位：`.github/workflows/ci.yml` 的设计与配置指南。运行时环境变量见 [`environment-variables.md`](environment-variables.md)；本文只讲 CI。
>
> 状态：首次引入 CI（2026-07-08）。PR/push 跑 lint+test+benchmark 门禁；nightly 跑真 LLM Entry A 回归。

---

## 1. 设计

**3 个 job**：

| Job | 触发 | 跑什么 | 需要 secrets |
|-----|------|--------|-------------|
| `lint-test` | push / PR | ruff + mypy(过渡) + 单元/集成测试 + `dedup_benchmark` 阈值门禁 | 否 |
| `coverage` | push / PR | `test_coverage`（阈值 80%）+ 上传 `coverage.xml` artifact | 否 |
| `nightly` | cron `0 2 * * *` + `workflow_dispatch` | 上述 + `filter_recall` + 真 LLM Entry A 回归（5 场景） | **是** |

**关键决策**：

- **PR CI 永不调用真实 LLM**：成本 + 速率 + LLM 非确定性。`live_llm` 标记的测试只在 nightly 跑。
- **mypy --strict 过渡**：项目历史 mypy 状态未清零，首次引入 CI 用 `continue-on-error: true`；清零后改硬门禁。
- **dedup_benchmark 阈值**：对生产阈值 `0.95` 行断言 `precision/recall ≥ 0.90/0.85`；当前数据集无重复节点（precision/recall=nan）时跳过。
- **filter_recall fixture**：30 turn 难样本集中版（NEGATIVE 占 43%），阈值 `0.45/0.65` 留 headroom。全量 180 turn 表现 `precision=0.860`。

---

## 2. Secrets 配置（一次性）

仅 nightly job 需要这 4 个 secret。PR CI 不依赖。

**路径**：GitHub repo → Settings → Secrets and variables → Actions → New repository secret

| Secret 名 | 用途 | 获取方式 |
|-----------|------|---------|
| `OPENAI_API_KEY` | Supervisor + Executor LLM 调用 | 你的 provider（如 opencode.ai zen go）API key |
| `OPENAI_BASE_URL` | OpenAI 兼容接口 base | 如 `https://opencode.ai/zen/go/v1` |
| `ANTHROPIC_API_KEY` | Planner LLM 调用 | 同上 provider（通常是同一密钥） |
| `ANTHROPIC_BASE_URL` | Anthropic 兼容接口 base | 如 `https://opencode.ai/zen/go` |

**验证**：配好后跑 `workflow_dispatch`（GitHub Actions 页面 → CI → Run workflow → 勾选 "跑真 LLM"）。如果 secrets 缺失，nightly job 会自动跳过 live_llm 步骤并打 warning。

---

## 3. 本地复现 CI 行为

PR CI 跑的命令（不需要 API key）：

```bash
make lint                  # ruff + mypy --strict
make test_automated        # 单元 + 集成（Mock LLM）
make test_kt_benchmarks    # dedup_benchmark 阈值门禁
make test_coverage         # 覆盖率（阈值 80%）
make test_filter_recall    # filter_recall fixture 阈值门禁
```

Nightly live_llm 部分（需要 server + API key）：

```bash
make dev &                 # 后台启动 langgraph dev server
sleep 5                    # 等 health check
curl -sf http://localhost:2024/ok || exit 1
make test_kt_hardening     # 5 个真 LLM 场景
kill %1                    # 停 server
```

---

## 4. 失败诊断

### lint-test job 失败

- **ruff check 失败**：跑 `make format` 修复格式 + 手动修逻辑错误
- **mypy --strict 失败**：首次 CI 跑 `continue-on-error: true` 不会阻塞；但应在 issue 跟踪清零
- **单元/集成测试失败**：本地 `make test_automated` 复现
- **dedup_benchmark FAIL**：检查 `workspace/knowledge_tree/.vector_index.json` 是否被 commit；阈值扫描输出看哪个 threshold 行退化

### coverage job 失败

- 跑 `make test_coverage` 看缺失行（`--cov-report=term-missing`）
- 阈值在 `Makefile:6` 的 `TEST_COVERAGE_FAIL_UNDER`（默认 80）

### nightly job 失败

- **server 启动失败**：看 step "Dump server log on failure" 输出的 `server.log` 末 200 行
- **filter_recall FAIL**：fixture 是难样本集中版，precision 0.53 是当前基线；如跌到 <0.45 才 FAIL，需查 `filter.py` 是否退化
- **live_llm 测试失败**：测试本身的 `diagnostic_dump` 输出在 step "KT Entry A hardening" log 里；区分 LLM 非确定性 flaky vs 真实加固退化

---

## 5. 维护

**新增 PR CI 检查**：编辑 `.github/workflows/ci.yml` 的 `lint-test` job，加新 step。

**调整 benchmark 阈值**：编辑 `Makefile` 的 `test_kt_benchmarks` / `test_filter_recall` target。建议阈值变更前先跑 `scripts/dedup_benchmark.py` / `scripts/filter_recall_benchmark.py` 看当前基线。

**更新 probe fixture**：跑 `uv run python scripts/build_probes_fixture.py` 重新选样 + 脱敏。脚本读 `logs/probes/` 全量，按 verdict/signal/模式分布选 30 条。

**关闭 nightly**：删除 `ci.yml` 的 `schedule:` 块即可（保留 `workflow_dispatch` 手动触发能力）。

---

## 6. 已知限制

- **live_llm 测试 LLM 非确定性**：相同输入可能不同输出。测试用 Δ 区间 + tool_calls 验证容忍；如 nightly 失败率 >20%，需重新设计断言。详见 `tests/e2e/test_kt_entry_a_hardening_live.py` 头注释。
- **mypy --strict 过渡**：项目历史 mypy 错误未清零，PR-C 首次引入时 `continue-on-error: true`；待 issue 跟踪清零后改硬门禁。
- **fixture 不代表生产分布**：30 条 fixture 是难样本集中版，precision 看起来比全量低（0.53 vs 0.86）。这是设计选择（覆盖关键场景），不代表 filter 真实表现。
- **dev server 端口 2024 占用**：本地同时跑 dev server + live_llm 测试会冲突；测试用独立 thread_id + tmp_path KT root 隔离数据，但端口必须空闲。
