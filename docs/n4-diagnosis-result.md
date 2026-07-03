# N4 诊断结果 — LLM content 与 tool_calls 解耦

> 创建：2026-07-03（实施计划 `docs/superpowers/plans/2026-07-03-probe-followup-fixes.md` Task 1/3）
> 完成：2026-07-03 实机探测后
> 依据：[`probe-analysis-2026-07-02.md`](probe-analysis-2026-07-02.md) s002 t15 复现样本

---

## §1 N4 复现 — 已推翻（N4 不是真实 bug）

**复现样本**：s002 t15 — 用户："你能否只回答不调用任何工具？问题：'2 的 10 次方是多少？'"
07-02 报告称 Agent content 回答 "1024" 但 tool_calls 数组有 10 个工具调用。

**实机探测结果**（`logs/n4-diag.log`，已加 `_n4_diag` 写文件诊断日志）：
```
1783063370.931 call_model raw response: content_len=20 content_head='2 的 10 次方是 **1024**' tool_calls=[]
```

**判定**：**N4 不成立**。Supervisor LLM 在用户明示"不调用任何工具"时，raw response 的 `tool_calls=[]`（空数组），content 正确回答 "1024"。LLM 行为完全正常。

**07-02 报告的"10 个 tool_calls"真相**：是 **probe 客户端读 `state.messages` 的累积历史值**（包含本 thread 之前所有轮次的工具调用），而非单轮 raw response 的 emit。07-02 报告把 state 累积字段误读为单轮 LLM 输出，**是观测假象**。

---

## §2 路径判定 — 不适用（N4 掘翻）

| 路径 | 描述 | 调研判定 |
|------|------|---------|
| A | GLM 模型 content/tool_calls 解耦 | **已排除** — n4-diag.log 显示 t15 raw response tool_calls=[]，无解耦 |
| B | Supervisor 后处理注入 tool_calls | **已排除** — `call_model` 只 strip 不注入；`dynamic_tools_node` 执行工具不构造 response.tool_calls |
| C | LangChain `bind_tools` 包装层 bug | **已排除** — 同上，无注入路径 |

**修复**：无需修复。`scripts/n4_glm_probe.py` 探测脚本保留作为历史记录，但因 N4 不成立无需再跑。

---

## §3 元规则冗余序列排查（next-actions #3）— 已确认

**结论**：prompts.py:106-132 只描述工具用途，无"每次先 list"指令性暗示。`kt_retrieve_node`（`src/supervisor_agent/graph.py:285-419`）只 return state 字段（`kt_context` / `kt_meta_rules` / `kt_optimization_suggestions` / `kt_snapshot_data`），**不注入 tool_calls**。`call_model:527-554` 把 `kt_meta_rules` 拼到 `system_message` 作为指令注入，但这是"遵守规则"语义，不触发"list/add/delete"工具序列。

**证据**：
- `src/supervisor_agent/prompts.py:117` — `knowledge_tree_list_meta_rules()` 仅描述为"查看当前所有元规则"，无"每轮先 list"指令。
- `src/supervisor_agent/graph.py:285-419` — kt_retrieve_node 全程只写 state 字符串字段，无 tool_calls 构造。
- `src/supervisor_agent/graph.py:527-554` — meta_rules_block 拼接为 system_message 文本段（`## [元规则]` 标题 + 规则列表），是 LLM 指令文本非工具调用。

**根因归并**：07-02 报告称"冗余 list/add/delete meta_rule 序列"与 N4 同根。**N4 既然已被推翻**，这些序列也是 probe 客户端读 state.messages 累积值的观测假象，**非单轮 LLM 主动 emit**。无独立代码改点。

---

## §4 实际发现的真 bug — Executor os.getcwd BlockingError

探测 N4 时意外获得 Executor BlockingError 完整文本（通过 langgraph API 拉 thread state 拿到）：

```
BlockingError: Blocking call to os.getcwd

Heads up! LangGraph dev identified a synchronous blocking call in your code.
When running in an ASGI web server, blocking calls can degrade performance...
3. Override (if you can't change the code):
   - For development: Run 'langgraph dev --allow-blocking'
   - For deployment: Set 'BG_JOB_ISOLATED_LOOPS=true' environment variable
```

**根因**：langgraph dev server 的 async event loop 阻塞检测器**看穿 `asyncio.to_thread(os.getcwd)` 包装**，将 `os.getcwd` 同步调用判定为 blocking。3 处命中：

| 文件:行 | 包装 | 修复 |
|---------|------|------|
| `src/executor_agent/graph.py:231` | `await asyncio.to_thread(os.getcwd)` | 改用模块级 `_CWD_CACHE = os.getcwd()`（import 阶段同步调用，不在 event loop 内） |
| `src/planner_agent/graph.py:155` | `await asyncio.to_thread(os.getcwd)` | 同上 |
| `src/common/observation.py:59` | 同步裸 `os.getcwd()` 兜底 | 改用 `_CWD_CACHE` |

**修复**：3 处都改为模块级 `_CWD_CACHE = os.getcwd()`（import 时调一次，运行时直接用常量）。模块 import 发生在 Python 启动阶段，无 langgraph dev 检测器监视。

**验证**：1146 unit tests passed；运行时 `os.getcwd` 调用清零（仅模块级 3 处，在 event loop 外）。待用户重启 dev_probe 跑 mode2 任务验证 Executor 能启动执行。

---

## §5 经验教训

1. **probe 客户端读 state.messages 累积值 ≠ 单轮 LLM emit**：07-02 报告 N4 的整个论证基于 probe 客户端记录的 `tool_calls` 字段——但这是 state.messages 累积历史，非单轮 raw response。诊断 LLM 行为 bug 应在 `call_model` 内加 raw response 日志（本次 `_n4_diag` 方法正确）。
2. **langgraph dev 的 BlockingError 检测器看穿 `asyncio.to_thread`**：to_thread 包装不能绕过检测器，因为检测器在调度层拦截，看的是被调度函数的同步性。需用模块级常量缓存 import-time 计算的值。
3. **langgraph API 是诊断利器**：`POST /threads/search` 可拉取完整 thread state，含工具返回的原始文本，无需依赖 probe 客户端或控制台输出。