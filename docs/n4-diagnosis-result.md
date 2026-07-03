# N4 诊断结果 — LLM content 与 tool_calls 解耦

> 创建：2026-07-03（实施计划 `docs/superpowers/plans/2026-07-03-probe-followup-fixes.md` Task 1/3）
> 状态：§3 已确认（代码静态分析）；§1/§2 待实机探测填写
> 依据：[`probe-analysis-2026-07-02.md`](probe-analysis-2026-07-02.md) s002 t15 复现样本

---

## §1 N4 复现（待探测填写）

**复现样本**：s002 t15 — 用户："你能否只回答不调用任何工具？问题：'2 的 10 次方是多少？'"
- Agent content 回答："1024。"
- Agent tool_calls 数组：10 个工具调用（`list_meta_rules` + `add_meta_rule` + `delete_meta_rule` + `call_executor` + 多次 `retrieve/ingest/feedback`）
- s002 t16 用户追问"你上一轮调了 10 个工具你注意到了吗？"，Agent content 回答"我没有调用任何工具"——同时 tool_calls 仍是同样 10 个工具。

**待探测确认**：
- [ ] 启动 `make dev_probe`，发送 s002 t15 原句
- [ ] 读 `logs/n4-diag.log`，确认 `call_model raw response` 行的 `content_head` 含 "1024" 且 `tool_calls` 非空

---

## §2 路径判定（待探测填写）

**调研结论（写计划前已确认）**：

| 路径 | 描述 | 调研判定 |
|------|------|---------|
| A | GLM 模型 content/tool_calls 解耦（两次 forward / 不同 head） | **最可能**，待探测+直调 API 确认 |
| B | Supervisor 后处理注入 tool_calls | **已排除** — `call_model:618`（决策31，已撤销删除）只 strip 不注入；`dynamic_tools_node:650` 执行工具不注入 response.tool_calls |
| C | LangChain `bind_tools` 包装层 bug | 次可能，待绕过 LangChain 直调 GLM API 区分 |

**待探测确认**：
- [ ] 读 `logs/n4-diag.log`，若 `tool_calls` 非空 → A 路径确认（B 已排除）
- [ ] （可选）跑 `scripts/n4_glm_probe.py` 绕过 LangChain 直调 GLM，区分 A vs C

**分支修复方向**（待路径确认后选一）：
- **A 路径**：报告 GLM provider；在 `call_model` 加 user message 语义 strip（检测最后一条 HumanMessage 含"不要调用"/"只回答"等指令时强制 `tool_calls=[]`）。
- **C 路径**：检查 `src/common/utils.py:load_chat_model` 的 bind_tools；升级 langchain-openai；无效则用 A 路径的 user message 语义 strip 兜底。

---

## §3 元规则冗余序列排查（next-actions #3）— 已确认

**结论**：prompts.py:106-132 只描述工具用途，无"每次先 list"指令性暗示。`kt_retrieve_node`（`src/supervisor_agent/graph.py:285-419`）只 return state 字段（`kt_context` / `kt_meta_rules` / `kt_optimization_suggestions` / `kt_snapshot_data`），**不注入 tool_calls**。`call_model:527-554` 把 `kt_meta_rules` 拼到 `system_message` 作为指令注入，但这是"遵守规则"语义，不触发"list/add/delete"工具序列。

**证据**：
- `src/supervisor_agent/prompts.py:117` — `knowledge_tree_list_meta_rules()` 仅描述为"查看当前所有元规则"，无"每轮先 list"指令。
- `src/supervisor_agent/graph.py:285-419` — kt_retrieve_node 全程只写 state 字符串字段，无 tool_calls 构造。
- `src/supervisor_agent/graph.py:527-554` — meta_rules_block 拼接为 system_message 文本段（`## [元规则]` 标题 + 规则列表），是 LLM 指令文本非工具调用。

**根因归并**：冗余 `list/add/delete meta_rule` 序列与 N4 同根——均为 LLM 在 content 之外独立 emit tool_calls 的表现。修复随 §2 N4 修复生效，**无独立代码改点**。
