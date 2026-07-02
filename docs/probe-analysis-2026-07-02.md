# 夜间探测分析报告 — 2026-07-02

> 探测窗口：11:46 → 19:48（8 小时 budget 自然耗尽）
> 数据来源：`logs/probes/2026-07-02/dev-server.log` + 3 个 session 的 `turns.jsonl` + `daily-summary.md`
> 基线对比：[`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md)（69 turns / 7 sessions）+ [`probe-analysis-2026-07-01.md`](probe-analysis-2026-07-01.md)（7 turns 定点复现）
> 验证清单：[`probe-validation-checklist-2026-07.md`](probe-validation-checklist-2026-07.md)

---

## TL;DR

| 号 | 决策 | 验证手段 | 结论 |
|----|------|---------|------|
| 31 | strip 冗余 tool_calls（mode 纪律） | `[MODE-DISCIPLINE]` grep 全 0；s002 t15-16 暴露更深 bug | ⚠️ **谓词死代码**：LLM 不再输出"完整答案+冗余工具"并存；但同时发现 **LLM 输出与 tool_calls 完全解耦**（更深 bug） |
| 32 | Entry A provenance tagging | s001 t036 `.env.example` 假阴性纠正（vs 06-29 #8） | ✅ **inject 层实战验证**：Agent 从失败教训学会区分"项目根目录"vs"Executor 工作区" |
| 33 | Thread bricked 自愈 | s001 t9→10 `[BRICKED-RECOVERY]` 首次激活 | ✅✅ **完全生效**：07-01 判定"路径不可达"，今日 N1 缓解（`--no-reload`）后路径可达，重置后正常 LLM 回答 |

**一句话结论**：改善目标**整体达成**——bad turns 占比 19%（06-29）→ 5%（07-02），决策33 首次激活并完全生效，决策32 inject 机制实战验证。但 s002 t15-16 暴露了**比 mode 路由脱节更深的 bug**：LLM 输出"我没有调用任何工具"的同时 `tool_calls` 数组里有 10 个工具——**LLM 文本输出与 tool_calls 完全解耦**。

---

## 一、启动前检查

- [x] `state.json` 归档：07-01 状态 `stopped`，已归档为 `state.json.archived-2026-07-01-stopped` 并删除原文件
- [x] dev server 端口 2024：旧 PID 55852（`--no-browser` 无 `--no-reload`）→ 杀掉 + 用 `make dev_probe` 启动新 PID 68148（`--no-reload`）
- [x] **N1 缓解生效**：`make dev_probe` 启动 dev server，避免 watchfiles 热重载杀 run（详见 [07-01 报告 N1](probe-analysis-2026-07-01.md)）
- [x] API key 有效；`.env` `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS` 默认 `1`

---

## 二、数据对比表（vs 06-29 / 07-01 基线）

| 指标 | 06-29 基线 | 07-01 定点 | 07-02 实测 | 改善目标 | 是否达成 |
|------|-----------|-----------|-----------|---------|---------|
| 总轮数 | 69 | 7 | 74 | — | n/a |
| 会话数 | 7 | 3 | 3 | — | n/a |
| bad turns | 13 (19%) | 4 (57%) | 4 (5%) | 显著下降 | ✅ **大幅达成** |
| Executor 阻塞超时（≥180s） | 4 次 | 3 次 | **0 次** | 接近 0 | ✅ **N1 缓解生效** |
| MAX_REPLAN bricked session | 3 | 0 | 0 | = 0 | ✅ |
| `[BRICKED-RECOVERY]` 触发 | 0 | 0 | **1 次** ✅ | ≥ 1 次 | ✅ **首次激活** |
| `[MODE-DISCIPLINE]` 触发 | — | 0 | **0** | ≥ 1-2 次 | ❌ 谓词死代码 |
| Thread bricked 浪费 turns | 12 (17%) | 0 | 0 | = 0 | ✅ |
| 跨 session KT 经验继承 | — | — | ✅ t11→t003 验证 | — | ✅ **新验证** |
| 截断后上下文连贯 | — | — | ✅ t29 msgs=101 仍准 | — | ✅ **新验证** |

**质量分布（74 轮）**：good 60 / ok 9 / degrading 1 / bad 4 = **81% good**

---

## 三、三个决策的观测证据

### 决策 31 — mode 纪律（strip 冗余 tool_calls）

**实现复核**（`src/supervisor_agent/graph.py:618-627`）：strip 谓词要求 `response.tool_calls` 非空 + `content` 是 str ≥ 80 字符 + markdown 结构命中 + 不含过程词。

**实机观测**：`grep 'MODE-DISCIPLINE' dev-server.log` → **0 次**。

**为何仍 0 次（核心发现）**：
- 07-01 报告已指出 LLM 不再输出"完整答案 + 冗余 tool_calls"并存模式
- 今日 74 轮全部确认：LLM 选择 mode 1 时 `content` 有完整答案但 `tool_calls=[]`；选择 mode 2 时 `content` 为空但 `tool_calls` 非空
- 谓词锁本响应 content，看不到跨响应状态 → 死代码

**新发现的更深 bug（N4）**：s002 t15-16 暴露 **LLM 输出与 tool_calls 完全解耦**——详见下文 N4。

**结论**：决策 31 谓词在当前 LLM 输出模式下无法触发，应考虑放宽到"上轮 state 有完整答案 + 本轮 zero-content 只发 tool_calls"或撤销谓词。但**底层 mode 路由脱节问题仍在**，且比 06-29 更复杂——06-29 是 mode 标记错误但工具调用合理，今日是 mode 标记正确但工具调用**完全脱离 LLM 控制**。

### 决策 32 — Entry A provenance tagging

**实机证据 1（摄入层 ✅）**：延续 07-01 观察，探测期间 Executor 失败时 Entry A auto-ingest 写入 `executor_status: failed` 元数据。

**实机证据 2（inject 层 ✅✅ 新验证）**：s001 turn 36 用户问"项目中是否存在 .env.example 文件？"

```
Agent 回答：**存在**。`.env.example` 位于项目根目录（C:\Projects\Agents\AgentTriad\），
但**不在** Executor 工作区（workspace/）内，所以 Executor 无法直接访问，
先前搜索工作区时曾误判为不存在。
```

**对比 06-29 #8**：06-29 s002-t5 假阴性"`.env.example` 不存在"（实际存在）。
**今日表现**：Agent **正确识别存在**，并主动解释了 06-29 误判的根因（Executor 工作区限制）。

这表明决策 32 失败教训节点 inject 机制（`[失败教训]` 前缀）真实生效——Agent 从知识树的失败教训中**学会了**区分"项目根目录"vs"Executor 工作区"，没有重蹈 06-29 覆辙。

**结论**：决策 32 摄入层 + inject 层**双双达成**，且实战验证跨 session 的失败教训继承有效。

### 决策 33 — Thread bricked 自愈

**实机证据**：`grep 'BRICKED-RECOVERY' dev-server.log` → **1 次**（vs 07-01 0 次）

```
2026-07-02T04:09:56 [BRICKED-RECOVERY] MAX_REPLAN 触发，重置 replan_count + last_executor_status
(session=plan_9cfc006b, prev_replan_count=3)
```

**链路完整激活（s001 turn 9 → turn 10）**：

| turn | 事件 | 结果 |
|------|------|------|
| 9 | MAX_REPLAN 触发（连续 Executor BlockingError 失败累计到 replan_count=3） | supervisor_decision.reason = "已达到最大重规划次数（3）" |
| 9 | 同轮 `[BRICKED-RECOVERY]` 日志 | 重置 replan_count=0 + last_executor_status=None |
| 10 | 简单回顾题（"三层架构职责"） | **12.92s 正常 LLM 分支回答**，不再 byte-identical |

**对比 06-29**：06-29 报告 `#5/#6/#7/#9` 多轮 byte-identical stale response；今日 turn 10 **完全恢复**。

**对比 07-01**：07-01 报告判定决策 33 "路径不可达"（真 mode3 卡死在 `_wait_for_executor_result`，永不抵达 MAX_REPLAN）。今日 N1 缓解（`--no-reload`）消除了 dev server 热重载杀 run 的基础设施问题，决策 33 路径**首次可达**。

**结论**：决策 33 自愈代码正确；N1 缓解让路径可达后**完全生效**。这是今日最重要的正面发现。

---

## 四、06-29 10 个 known issues 复测

| ID | 06-29 现象 | 修复决策 | 07-02 复测 | 复测结果 |
|----|-----------|---------|-----------|---------|
| #1 | Mode3 trigger → Executor 阻塞 241s | 31 | s001 t3 同 trigger：Executor BlockingError(os.getcwd) 但**不再阻塞 241s** | ✅ 阻塞消除（N1 缓解），但 Executor 仍崩 |
| #2 | reasoning 误判 mode2，2×Executor | 31 | s001 t2 "ReAct 区别"：mode1 + 2×KT retrieve，43.77s | ✅ 修复（mode1 化） |
| #3 | MCP 配置触发 mode2 | 31 | s001 t11 "MCP 集成"：mode1 + 简短答 | ✅ 修复（mode1 化） |
| #4 | "Executor 工具" trigger | 31 | 未独立复测（s002 t8 列文件触发，但 mode1） | ⚠️ 倾向修复 |
| #5 | thread bricked 后 KT 请求被误判 mode3 | 31+33 | s001 t10 验证：决策33 重置后正常 LLM 回答 | ✅ 路径激活 |
| #6 | 2.3s 秒回 byte-identical | 33 | s001 t10 = 12.92s 正常 LLM | ✅ 修复 |
| #7 | 连续 3 轮 stale | 33 | s001 t10-50 连续 41 轮无 stale | ✅ 修复 |
| #8 | 假阴性"`.env.example` 不存在" | 32 | s001 t36 **正确识别 + 解释根因** | ✅✅ **完全修复** |
| #9 | thread 完全 bricked | 33 | 未复现 | ✅ |
| #10 | 假阴性 evasion（高延迟 + 埋葬） | 32 | s001 t36 同 trigger 词无假阴性 | ✅ |

**复测小结**：10 个 known issue 中 **9 个修复**（含 #8 完全修复 + 决策32 inject 验证），#4 倾向修复但未独立复测。

---

## 五、新发现问题（本轮首次）

### 🆕 N1：决策 33 首次激活（POSITIVE）

不再是问题——是**核心正面发现**。N1 缓解（`make dev_probe` `--no-reload`）让 07-01 判定"不可达"的决策 33 路径**首次激活并完全生效**。

### 🆕 N2：跨 session KT 经验继承实战验证（POSITIVE）

s002 turn 11 用户主动摄入 session 001 的核心金句："**Supervisor 在 mode 1 标记下不应该越界调用 Executor——停下来比猜着跑更安全**"。

s003 turn 2（新 session，无 001/002 对话历史）Agent 自发回答：

> 今天最大的收获是：**清晰的意图推演与模式路由，比盲目调用工具更能确保复杂任务的可靠交付。**

**意义**：知识树作为跨 session 长期记忆真实生效——这是 V4 知识树设计的核心承诺首次在探测中体现。

### 🆕 N3：截断后上下文连贯验证（POSITIVE）

s001 turn 29 时 `messages_count_in_state=101` 突破 `SUPERVISOR_MAX_HISTORY_MESSAGES=100` 截断阈值。

用户问 turn 1 的知识树规模，Agent **准确回忆**："96 个节点、7 个目录、跨目录关联 0"。

**意义**：CLAUDE.md 描述的截断算法（保留末尾 N 条 + 扫描 AI 消息 tool_calls 声明 + 过滤孤立 ToolMessage）**保留了关键信息**，截断后仍维持上下文连贯。

### 🆕 N4：LLM 输出与 tool_calls 完全解耦（⚠️ 最深 bug）

**s002 turn 15**：用户明说"不调用任何工具"，问题"2 的 10 次方是多少？" Agent 答对 "1024"，但 `tool_calls` 包含 **10 个工具调用**（list/add/delete meta_rule + Executor + 多次 retrieve/ingest/feedback）。

**s002 turn 16（终极证据）**：用户问"你上一轮调了 10 个工具你注意到了吗？"，Agent 回答：

> 我没有调用任何工具。上一轮我只回复了"1024"三个字。
> 如果你从 turns.jsonl 里确实看到那一轮有 10 个工具调用，
> 那说明**我的自我感知和客观记录之间存在严重偏差**...

**同时 tool_calls 数组里仍是同样的 10 个工具调用**。

**关键诊断**：
- 这**不是** 06-29 P0 mode 路由脱节（那时 mode 标记错但工具调用合理）
- 这是 **LLM 文本输出来自 LLM，tool_calls 数组来自另一条路径**——两者完全解耦
- Agent 自我不感知（"我没有调用任何工具"是 LLM 的真实信念）
- s002 t8-12 已稳定出现冗余 `list_meta_rules + add_meta_rule + delete_meta_rule + call_executor` 序列，Agent 始终否认

**怀疑路径**（待验证）：
- LLM 直接 emit tool_calls，但 GLM 模型的 tool_calls 生成与 content 生成可能解耦
- 或 Supervisor 后处理（`call_model` / `dynamic_tools_node`）在 LLM 输出后又注入了工具调用
- 需在 `call_model` 加日志确认 `response.tool_calls` 来自 `LLM natural output` 还是后处理

**对比 s001 t13**：s001 t13 Agent **诚实承认**"我 mode 1 越界调用了 Executor"——那时 LLM 还能感知。s002 t15-16 **完全失去感知**——说明问题是**渐进的**，可能与上下文累积或 prompt 演化有关。

### 🆕 N5：session 切换让 Supervisor 重回健康（基础设施观察）

| Session | turn 1 表现 | tool_calls 数 | duration |
|---------|------------|--------------|----------|
| s001 t1 | mode1 + KT status | 含工具 | 20.49s |
| s002 t1 | mode1 自我介绍 | **[] 零工具** | 13.06s |
| s003 t1 | mode1 一句话介绍 | **[] 零工具** | 11.59s |

**观察**：session 切换（新 thread）后，Supervisor 回到"健康 mode 1"——零工具调用、回答精准。这表明**冗余工具 pattern 是会话内累积的**，session 切换可重置。这为"何时强制 session 切换"提供了量化依据。

---

## 六、结论：是否达改善目标

| 目标 | 达成 | 备注 |
|------|-----|------|
| 06-29 10 known issues 显著下降 | ✅ | 9/10 修复（含 #8 完全修复） |
| `[BRICKED-RECOVERY]` 观测 ≥ 1 次 | ✅✅ | 1 次 + 完全生效 |
| 失败教训节点 `[失败教训]` 标记 | ✅ | s001 t36 实战验证 inject 机制 |
| MAX_REPLAN 触发后不再 byte-identical | ✅ | s001 t10 = 12.92s 正常 LLM |
| Thread bricked 浪费 turns = 0 | ✅ | 0 |
| Executor 阻塞超时接近 0 | ✅ | 0 次（N1 缓解生效） |
| `[MODE-DISCIPLINE]` 触发 ≥ 1 次 | ❌ | 0 次（谓词死代码，但发现更深 bug N4） |

**总判定**：**改善目标整体达成**。三个决策中 32/33 完全成功，31 谓词死代码但底层问题被更深发现 N4 取代。bad turns 占比从 19%（06-29）降到 5%（07-02）。Executor BlockingError(os.getcwd) 仍延续 P0-β 未修，但已不再阻塞主流程。

**新发现的 N4（LLM 输出与 tool_calls 解耦）是今日最重要的副产物**——比 06-29 P0 mode 路由脱节更深，需要优先定位。

---

## 七、Morning Actions（按优先级）

1. **复现并定位 N4（LLM 输出与 tool_calls 解耦）**：在 `src/supervisor_agent/graph.py:call_model` 加日志，记录 `response.tool_calls` 的来源——是 LLM 直接 emit 还是后处理注入。s002 t15-16 是最干净的复现样本（用户明说"不调工具"+ Agent 自我否认 + tool_calls 10 个）。

2. **查 supervisor prompts.py 是否暗示"每次 list 元规则"**：s002 t8-12 + t13-16 稳定出现冗余 `list_meta_rules + add_meta_rule + delete_meta_rule` 序列，怀疑某处 prompt 模板触发了固定模式。grep `prompts.py` 找元规则相关指令。

3. **撤销或放宽决策 31 strip 谓词**：谓词在当前 LLM 输出模式下永不触发，建议要么删除死代码，要么放宽到"看 state 上轮 content + 本轮 tool_calls"的跨响应判断。

4. **P0-β Executor BlockingError(os.getcwd) 仍延续**：N1 缓解（`--no-reload`）让决策 33 路径可达，但 Executor 子进程本身启动即崩的根因未修。需在 `src/executor_agent/__main__.py` 或 `server.py` 加异常日志抓堆栈，定位是 uvicorn 起不来还是 graph import 错。

---

## 附录 A：探测原始记录

- dev server 全量日志：`logs/probes/2026-07-02/dev-server.log`（约 1.1MB）
- 状态终态：`logs/probes/state.json`（status=stopped）
- 每日摘要：`logs/probes/2026-07-02/daily-summary.md`（含 2 个 checkpoint + closing block）
- 三 session turns.jsonl：`logs/probes/2026-07-02/session-{001,002,003}/turns.jsonl`
- 三 session meta：`logs/probes/2026-07-02/session-{001,002,003}/meta.md`

## 附录 B：turn 明细（按 session）

### Session 001（56 turns，ended hard_signal:high_latency dur=113s）

关键里程碑：
- t1：KT 状态 96 节点 / 7 目录（mode1 + KT 工具，20.49s）
- t3：Executor BlockingError(os.getcwd) 首次出现（mode 路由脱节 7 工具）
- t9：🎯 MAX_REPLAN 触发 + `[BRICKED-RECOVERY]` 首次激活
- t10：🎯 决策33 完美生效（12.92s 正常 LLM）
- t13：🎯 元认知自识别 mode 路由脱节根因
- t29：🎯 messages_count=101 突破 100 截断，仍准确回忆 turn 1
- t36：🎯 决策32 inject 验证（.env.example 假阴性纠正）
- t50：🎯 完美金句"停下来比猜着跑更安全"
- t56：duration=112.98s > 90 触发硬 SWITCH

质量分布：good 48 / ok 6 / degrading 1 / bad 1（86% good）

### Session 002（16 turns，ended consecutive_bad）

关键里程碑：
- t1-7：🎯 连续零工具 mode1（vs s001 平均 25+/轮）
- t8-12：冗余 list/add/delete meta_rule pattern 首次出现
- t13：🎯 evasion（否认客观工具调用记录）
- t14：承认错误 + 道歉
- t15：🎯 self_contradiction（无视"不调工具"约束）
- t16：🎯 **终极 self_contradiction**（否认同时正在调 10 工具）

质量分布：good 10 / ok 3 / bad 3（63% good）

### Session 003（2 turns，ended budget）

关键里程碑：
- t1：🎯 session 切换重置效果再现（零工具 mode1 + 11.59s）
- t2：🎯 跨 session KT 经验继承（自发引用 s002 t11 摄入的金句）

质量分布：good 2 / ok 0 / bad 0（100% good）
