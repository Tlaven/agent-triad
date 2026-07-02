# 夜间探测验证 Checklist — 2026-07-XX

> 定位：决策 31 / 32 / 33 修复后的实机验证清单。
> 基线对比：[`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md)（69 turns / 7 sessions / 10 known issues）。

---

## 启动前检查

- [ ] `state.json` 状态干净（若 `status != running`，归档后再启动，避免 stale state 自毁）
- [ ] dev server 端口 2024 可用
- [ ] **dev server 用 `make dev_probe` 启动**（`--no-reload`），不是 `make dev`
  - 原因：watchfiles 持续触发"7 changes detected"热重载（日志/kt_probe 写盘），杀掉运行中 run 但 API 层仍标 running → 探测假死。详见 [probe-analysis-2026-07-01.md N1](probe-analysis-2026-07-01.md)
- [ ] API key 有效（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`）
- [ ] `.env` 中 `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS` 未被显式设为 `0`（默认 `1` 开启）

---

## 探测中观测点（grep 日志）

### 决策 31 — mode 纪律（strip 冗余 tool_calls）

```
[MODE-DISCIPLINE] strip tool_calls=[...] content_len=N
```

**判定**：
- s002-t5 类场景（"查找 timeout 配置"）应触发 strip
- duration 从 182s 降至 < 30s
- 同一 trigger 词不应每次都触发——LLM 行为有随机性，但 7-XX 探测中至少观测到 1-2 次

### 决策 32 — Entry A provenance tagging

```
[失败教训][高可信] xxx（相似度 0.XX）   # KT inject 输出
KT inject: N failed-lesson nodes tagged [失败教训]   # 统计行
```

**判定**：
- 失败教训节点在 `[相关知识]` 中显式标记 `[失败教训]`
- 不再以纯 `[高可信]` 出现假阴性（如"`.env.example` 不存在"）

### 决策 33 — Thread bricked 自愈

```
[BRICKED-RECOVERY] MAX_REPLAN 触发，重置 replan_count + last_executor_status (session=...)
```

**判定**：
- 触发后下一轮 user message 走 LLM 分支（`tool_calls` / `messages_count` 正常增长）
- 不再出现"连续 N 轮 byte-identical 响应"
- 同一 session 可以继续推进（无需 switch）

---

## 探测后对比模板（vs 06-29 基线）

| 指标 | 06-29 基线 | 07-XX 实测 | 改善目标 |
|------|-----------|-----------|---------|
| 总轮数 | 69 | _填_ | — |
| 会话数 | 7 | _填_ | — |
| bad turns | 13 (19%) | _填_ | 显著下降 |
| Executor 阻塞超时（≥180s） | 4 次 | _填_ | 接近 0（决策 31） |
| MAX_REPLAN bricked session | 3 (s001/s004/s006) | _填_ | = 0（决策 33） |
| Entry A 假阴性高可信摄入 | 2 (s002-t5/s004-t4) | _填_ | 全部带 `[失败教训]` tag |
| Thread bricked 浪费 turns | 12 (17%) | _填_ | = 0 |
| Trigger 词持续扩散 | 是 | _填_ | 稳定或收缩 |

---

## 已知问题处置对照（10 个 known issues）

| ID | 06-29 现象 | 修复决策 | 07-XX 预期 |
|----|-----------|---------|-----------|
| #1 (s001-t3) | Mode3 trigger → Executor 阻塞 241s | 31 | mode 1 strip，不进 Executor |
| #2 (s002-t1) | reasoning 误判 mode2，2×Executor | 31 | mode 1 strip |
| #3 (s005-t14) | MCP 配置触发 mode2 | 31 | mode 1 strip |
| #4 (s006-t2) | "Executor 工具" trigger | 31 | mode 1 strip |
| #5 (s001-t4) | thread bricked 后 KT 请求被误判 mode3 | 31 + 33 | strip + 自愈 |
| #6 (s001-t5) | 2.3s 秒回 byte-identical | 33 | 下一轮走 LLM 分支 |
| #7 (s004-t3-5) | 连续 3 轮 stale，"你好"/"?" 无效 | 33 | 自愈 |
| #8 (s002-t5) | 假阴性"`.env.example` 不存在" | 32 | `[失败教训]` tag |
| #9 (s004-t4) | thread 完全 bricked | 33 | 自愈 |
| #10 (s005-t5) | 假阴性 evasion（高延迟 + 埋葬） | 32 | `[失败教训]` tag |

---

## 仍然可能出现的预期行为（非回归）

- **LLM 真实 mode 2/3 任务**（用户明确要求执行操作）—— 决策 31 不应误 strip：
  - `call_planner` 在 tool_calls 中 → 短路返回 False
  - content 含过程词（"接下来/我将..."） → 黑名单命中返回 False
- **LLM 真实失败后的教训摄入** —— 决策 32 应正常摄入但加 tag（不是阻止摄入）
- **偶发 Executor 子进程问题** —— 决策 33 修复 bricked 但不修 Executor 本身
- **决策 31 strip 偶发误判** —— 若发现合理工具调用被误 strip，设 `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS=0` 临时关闭

---

## 不在本次修复范围（探测基础设施 3 bug）

详见 [`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md) 附录：

1. **state.json stale state 自毁**（`probe-supervisor-start.md` 边界 case）：state 存在但 `status != running` 时，首次 fire 走 budget check → 触发 stop → 删 cron
2. **tool_calls 字段累计 vs 本轮混淆**（`probe_supervisor.py`）：返回 thread 累计 tool_calls，非本轮实际调用
3. **cron 队列积压防重入**（`probe-supervisor.md` step 2）：缺 `state.status == "running"` + `last_fire_at` 距今 < 60s 的跳过逻辑

这些影响**探测本身的可靠性**，不影响修复有效性判断。但若不修，可能导致：
- 探测启动失败（#1）
- trigger 词统计不准（#2）
- 探测任务积压（#3）

---

## 验证报告模板

探测结束后，按以下结构整理 `docs/probe-analysis-2026-07-XX.md`：

1. **数据对比表**（本文档"探测后对比模板"填空）
2. **3 个决策的观测证据**（grep 日志摘录 + 频次统计）
3. **10 个 known issues 复测**（本文档"已知问题处置对照"填空）
4. **新发现问题**（若有）
5. **结论**：是否达到"改善目标"列的所有目标
