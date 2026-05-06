---
title: Supervisor 图拓扑结构
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: architecture
---

Supervisor LangGraph 图拓扑为线性循环：
__start__ → kt_retrieve → call_model → route_model_output → __end__/tools → call_model。

kt_retrieve 节点：每次用户消息时自动检索知识树（高阈值 RAG），结果注入 kt_context 字段。
call_model 将 kt_context 拼接到最后一条 HumanMessage（不污染 state.messages 原始记录）。
仅在 __start__ 入口执行 kt_retrieve，工具循环中不重复注入。

route_model_output 根据 call_model 输出决定路由：
- 有 tool_calls → tools 节点 → call_model 继续 ReAct 循环
- 无 tool_calls → __end__ 结束（Supervisor 直接回复用户）

dynamic_tools_node 处理工具调用后同步更新：
- PlannerSession：plan_json、planner_reasoning、规划对话线程
- executor_task_history：执行记录、状态追踪
- Entry A：Executor 完成后自动提取知识写入知识树

State 数据结构见 src/supervisor_agent/state.py（State TypedDict）。
