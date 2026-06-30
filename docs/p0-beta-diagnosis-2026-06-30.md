# P0-β 诊断报告 — Executor 路径阻塞与 mode 路由脱节

> 诊断日期：2026-06-30
> 触发来源：`docs/probe-analysis-2026-06-29.md` P0-β 改进建议
> 方法：代码考古 + 一次实机复现（probe 客户端 + instrumented 日志）

---

## TL;DR

**报告 P0-β 描述需要修正，真实 P0 是另一个问题。**

- ❌ 原描述："Executor 240s 超时不 fire"
- ✅ 实测：`executor_wait_timeout=200s` **能 fire**（实测 duration=181s）。fix A（commit `47121a2`）的数值调整生效了，只是把 240s 降到了 181s。
- ✅ 真正的 P0：**mode 决策与路由完全解耦**——Supervisor LLM 在中间轮输出 mode A 语义的同时仍输出 `call_executor` tool_calls，`route_model_output` 只看 `tool_calls` 是否为空，无条件路由到 tools 执行。

---

## 一、Git 证据

```
git diff 47121a2..HEAD --stat  (限 src/supervisor_agent/ src/common/ src/executor_agent/)
→ (no output)
```

**自 2026-06-27 02:04 的"修复" commit `47121a2` 起，src/ 零改动。** 两个最新 commit（`b0341aa` 06-28 11:23 / `f10f0a9` 06-28 23:11）都在 probe 运行（06-29 01:55）之前，且只动 `.claude/commands/probe-*.md` 和 `.gitignore`，没碰任何 P0/P1 代码路径。

`47121a2` 三处改动的实际覆盖：

| Fix | 改动 | 覆盖度 |
|-----|------|--------|
| A. `context.py:283` 300→200s + probe `--timeout` 150→240s | 数值调整 | ✅ 超时能 fire，但只改了时间，没改机制 |
| B. `prompts.py:34` 追加"事实核实例外"一行 | 单向补丁 | ❌ 只补 config-query→Mode B，未补 concept→Mode A 反方向 |
| C. `filter.py:71-75` `_NEGATIVE_FACT_PATTERNS` 正则 | 症状层 | ❌ 只挡 KT 摄入，不挡上游假阴性生成；正则措辞覆盖窄 |

---

## 二、复现实测

**环境**：dev server port 2024，新 thread `7a98f91e-...`，probe 客户端 `--timeout 280`。

**触发句**（取自报告 s006-t2）："Executor 都有哪些内置工具？请帮我查一下。"

**probe 客户端返回**：

```json
{
  "status": "success",
  "duration_s": 181.06,
  "tool_calls": ["call_executor", "call_executor", "call_planner"],
  "supervisor_decision": {"mode": 1, "reason": "无需工具即可回答"},
  "messages_count_in_state": 8,
  "ai_message": "根据 Planner Agent 从项目知识树中获取的信息，Executor Agent 当前共有 4 个内置工具..."
}
```

### 证据解读

1. **duration=181s 而非 240s** → `executor_wait_timeout=200s` fire 了（fix A 生效）。
2. **`supervisor_decision.mode=1, reason="无需工具即可回答"`** → LLM 最终判定这是 Mode 1。
3. **`tool_calls=["call_executor","call_executor","call_planner"]`** → 但工具循环里实际调了 3 次 Executor/Planner。
4. **`messages_count=8`** → 还原 ReAct 轮次：

```
msg[0]: user "Executor 都有哪些内置工具？"
msg[1]: ai  tool_calls=[call_executor]      ← 第1轮：LLM 调 Executor
msg[2]: tool (结果)
msg[3]: ai  tool_calls=[call_executor]      ← 第2轮：LLM 再次调 Executor
msg[4]: tool (结果)
msg[5]: ai  tool_calls=[call_planner]       ← 第3轮：LLM 改调 Planner
msg[6]: tool (结果)
msg[7]: ai  content="根据 Planner Agent..."  ← 第4轮：无 tool_calls，mode=1
```

**`supervisor_decision.mode=1` 是 `_infer_supervisor_decision` 对 msg[7]（最终响应，无 tool_calls）的推断——但 msg[1]/[3]/[5] 三轮都已经实际执行了工具。**

---

## 三、根因机制（代码定位）

### 3.1 路由条件只看 tool_calls

`src/supervisor_agent/graph.py:1408`

```python
def route_model_output(state: State) -> Literal["__end__", "tools"]:
    last_message = state.messages[-1]
    if not last_message.tool_calls:
        return "__end__"
    return "tools"
```

**只要 LLM 输出里有 tool_calls，就路由到 tools 节点执行。** 没有任何 mode 信号参与路由。

### 3.2 mode 推断只事后打标签

`src/supervisor_agent/graph.py:602`（在 `call_model` 内）

```python
decision = _infer_supervisor_decision(response)
...
return {"messages": [response], "supervisor_decision": decision}
```

`_infer_supervisor_decision`（`graph.py:1360`）的逻辑：

```python
if not tool_names:
    return SupervisorDecision(mode=1, reason="无需工具即可回答", confidence=0.85)
if "call_planner" in tool_names:
    return SupervisorDecision(mode=3, ...)
if "call_executor" in tool_names or "manage_executor" in tool_names:
    return SupervisorDecision(mode=2, ...)
```

**mode 推断基于 tool_calls——有 tool_calls 就是 mode 2/3，没有就是 mode 1。** 它写入 `state.supervisor_decision`，但这个字段**从不参与路由决策**，只给 state 留记录。

### 3.3 解耦的后果

- LLM 在思考中判定"mode A，无需工具"（符合 prompt `prompts.py:25` 规则）
- 但 LLM **同时**输出了 `call_executor` tool_calls（违反规则）
- `route_model_output` 看到 tool_calls → 路由到 tools → 执行
- 执行完回到 `call_model`，LLM 可能再调一次工具
- ... 直到某轮 LLM 终于不输出 tool_calls → `__end__`
- 最终轮 `_infer_supervisor_decision` 看到 no tool_calls → 标 mode=1

**mode 决策与路由完全解耦。** prompt 规则再清楚，LLM 在任何中间轮输出 tool_calls 都会被无条件执行。

### 3.4 为什么 LLM 会"想 mode A 却调 Executor"

`prompts.py:26`："Executor 是执行工具，不是信息查询工具。不要用 call_executor 来'查资料'或'了解项目'。"

但 LLM 面对"Executor 都有哪些内置工具？"时，**句子里有"Executor"和"工具"两个词**，trigger 词覆盖了规则。LLM 在思考阶段可能推演"这是询问概念 → mode A"，但生成阶段看到"Executor"就条件反射式 bind_tools 调用。

**这是 LLM 行为纪律问题，prompt 治不了——必须在路由层硬约束。**

---

## 四、修正后的优先级

| 原报告 | 实测修正 |
|--------|---------|
| P0-β：Executor 240s 超时不 fire | **降级**：超时能 fire（181s），只是数值偏高 |
| P0-α：mode 分类器 trigger 敏感度 | **升级为 P0**：LLM 推断 mode=1 但工具循环仍调 Executor——是**路由层缺陷**，不是 prompt 规则问题 |
| P1-α：MAX_REPLAN 状态污染 | 不变（仍然是下游症状） |
| P1-β：Entry A 假阴性 | 不变（仍然是下游症状） |

**核心结论**：P0-α（mode 路由脱节）是 P1-α/P1-β 的**上游放大器**。修了 P0-α，下游的 MAX_REPLAN 污染和假阴性链路大部分会消失。

---

## 五、修复方案

### 方案 1：`route_model_output` 增加 mode 短路（最小改动）

在 `route_model_output` 里，如果 `_infer_supervisor_decision` 返回 mode=1 但 response 仍有 tool_calls，强制 `__end__`，丢弃 tool_calls。

```python
def route_model_output(state: State) -> Literal["__end__", "tools"]:
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(...)
    if not last_message.tool_calls:
        return "__end__"
    # 新增：mode=1 但仍有 tool_calls → 丢弃工具，直接结束
    decision = _infer_supervisor_decision(last_message)
    if decision.mode == 1:
        return "__end__"
    return "tools"
```

**风险**：`_infer_supervisor_decision` 基于 tool_calls 推断——有 tool_calls 就是 mode 2/3。**短路条件 `decision.mode == 1 and has tool_calls` 永远不成立**。方案 1 直接套用 `_infer_supervisor_decision` 无效。

要让方案 1 成立，必须改 `_infer_supervisor_decision` 的判别信号——从"基于 tool_calls"改成"基于 content 语义"（LLM 是否已经写出完整答案）。但这等于方案 2。

### 方案 2：`call_model` 里 content 语义判别 + strip tool_calls（推荐）

在 `call_model` 里，LLM 响应返回后，先判别 content 是否已经是完整最终答案。如果是，且 tool_calls 是冗余的（LLM "想 mode A 却调了工具"），strip 掉 `response.tool_calls` 再返回——这样 `route_model_output` 自然走 `__end__`。

```python
# call_model 内，response 返回后
if response.tool_calls and _looks_like_final_answer(response.content):
    logger.info("[MODE-DISCIPLINE] LLM 输出完整答案但仍有 tool_calls，strip 工具调用")
    response = response.model_copy(update={"tool_calls": []})

decision = _infer_supervisor_decision(response)
return {"messages": [response], "supervisor_decision": decision}
```

**`_looks_like_final_answer(content)` 的判别信号**（需设计）：
- content 非空且长度 > 阈值（如 50 字符）
- content 不含"正在调用""接下来""需要先"等过程性措辞
- content 含结论性结构（表格、列表、总结句、"根据..."等）

**风险**：
- 误判 1：LLM 在中间轮写出"我已经知道答案了，接下来调用 Executor 验证"——content 像最终答案但 LLM 确实想继续。strip 会打断合理流程。
- 误判 2：LLM 在最终轮写出完整答案但 tool_calls 是合理的下一步（如 ingest 教训）。strip 会丢失主动行为。

**缓解**：判别信号要严格——content 必须**同时**满足"长度足、无过程性措辞、有结论性结构"三个条件才 strip。宁可漏 strip（让工具执行），不要误 strip（打断合理流程）。

### 方案 3：mode 推断前置 + 硬路由（最彻底，改动最大）

把 mode 推断从 `call_model` 末尾移到 `call_model` 开头（基于上一轮 state 推断当前应该走什么 mode），然后在 `call_model` 里根据预期 mode 限制 LLM 的工具集：

- mode A → 不 bind 任何工具，LLM 只能纯文本回答
- mode B → 只 bind `call_executor`
- mode C → bind `call_planner` + `call_executor`

```python
# call_model 内，load model 时
expected_mode = _predict_mode_from_state(state)
if expected_mode == 1:
    model = load_chat_model(...).bind_tools([])  # 不 bind 任何工具
elif expected_mode == 2:
    model = load_chat_model(...).bind_tools([call_executor])
else:
    model = load_chat_model(...).bind_tools([call_planner, call_executor])
```

**优点**：从源头限制 LLM 的工具选择，不会"想 mode A 却调 Executor"。
**风险**：
- mode 预测错误会导致 LLM 无法完成合理任务（如预测 mode A 但实际需要工具）。
- 失去 ReAct 的灵活性——LLM 不能在循环中动态切换 mode。
- 改动大，需要设计 mode 预测器，且预测器本身的准确率会成为新的瓶颈。

---

## 六、推荐路径

**先方案 2（content 语义判别 + strip）**，因为：
- 改动最小（只加一个判别函数 + `call_model` 里几行）
- 保留 ReAct 灵活性（LLM 仍可在中间轮调工具）
- 只在"LLM 已经写出完整答案但冗余调工具"时干预，误判风险可控

**验证标准**：重新跑本次复现的触发句（"Executor 都有哪些内置工具？"），应走 mode A 直接回答，`tool_calls=[]`，`duration_s < 10s`。

**如果方案 2 误判率高**，再考虑方案 3（mode 前置硬路由）。

---

## 七、诊断 instrumentation

本次诊断在 4 个文件加了 `[PROBE-TIMING]` 计时日志（前缀统一，便于 grep）：

| 文件 | 位置 | 日志 |
|------|------|------|
| `src/supervisor_agent/tools.py` | `call_executor` dispatch / `_wait_for_executor_result` 入口/预检/超时/完成 | `sup_dispatch_start/done`, `sup_wait_enter/prefetch_hit/probe/done/DEADLINE` |
| `src/supervisor_agent/graph.py` | `call_model` LLM 调用前后 | `sup_llm_start/done/TIMEOUT` |
| `src/executor_agent/server.py` | `_run_executor_task` 入口/完成/异常/mailbox push | `exec_task_start/done/exc/cancelled`, `exec_mailbox_push_start/done/failed` |
| `src/executor_agent/graph.py` | `call_executor` LLM 调用前后 | `exec_llm_start/done/TIMEOUT` |

**注意**：`langgraph dev` 接管了 logger 输出，`[PROBE-TIMING]` 不会透到 stdout。需要在 dev server 启动时配置 logging 直写到文件，或改用 `print` flush=True。本次诊断主要证据来自 probe 客户端返回的 `duration_s`/`tool_calls`/`supervisor_decision`，instrumentation 日志未实际用到。

**建议**：保留 instrumentation（对未来诊断有用），但补一个 logging 配置让 `[PROBE-TIMING]` 能落到独立文件。

---

## 八、待决问题

1. **方案 2 的 `_looks_like_final_answer` 判别信号**：需要从 probe 历史 logs 里提取若干"LLM 写完整答案但冗余调工具"的样本，提炼判别规则。可从 `logs/probes/2026-06-29/session-*/turns.jsonl` 找。
2. **MAX_REPLAN 常量一致性**（报告 P2）：prompt 声称 MAX_REPLAN=2，报错显示 3。本次诊断未核对，需单独查。
3. **probe 基础设施 3 项缺陷**（报告附录）：stale state 自毁 / tool_calls 累计字段 / cron 积压防重入——均未修，建议合并到下次 probe 基础设施改进。
