# V3 执行流程全景分析

> 本文档完整梳理 V3 进程分离架构下所有执行路径、分支条件、超时处理与异常恢复机制。
> 目的：帮助开发者在调试或扩展时快速定位"代码运行到哪一步、为什么走了这条路"。

---

## 目录

1. [架构总览](#1-架构总览)
2. [Supervisor 主循环](#2-supervisor-主循环)
3. [dynamic_tools_node 分支处理](#3-dynamic_tools_node-分支处理)
4. [call_executor 完整派发流程](#4-call_executor-完整派发流程)
5. [Executor 子进程启动](#5-executor-子进程启动)
6. [Executor 内部 ReAct 循环](#6-executor-内部-react-循环)
7. [结果回传双路径](#7-结果回传双路径)
8. [_wait_for_executor_result 守候逻辑](#8-_wait_for_executor_result-守候逻辑)
9. [超时全景图](#9-超时全景图)
10. [异常场景矩阵](#10-异常场景矩阵)
11. [已覆盖 vs 潜在遗漏](#11-已覆盖-vs-潜在遗漏)

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                     Supervisor 进程                              │
│                                                                  │
│  ┌──────────┐     ┌──────────────────┐     ┌──────────────────┐ │
│  │call_model│────▶│dynamic_tools_node│────▶│   call_model     │ │
│  │ (LLM决策) │◀────│  (工具执行+状态更新)│◀────│  (下一轮循环)     │ │
│  └──────────┘     └──────────────────┘     └──────────────────┘ │
│       │                    │                                     │
│       │  early returns:    │  调用:                               │
│       │  - replan 耗尽     │  - call_planner                     │
│       │  - Mode2→3 升级    │  - call_executor ──────────┐        │
│       │  - max step        │  - get_executor_result      │        │
│       │                    │  - check_executor_progress  │        │
│       │                    │  - list_executor_tasks      │        │
│       │                    │  - stop_executor            │        │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  V3LifecycleManager（懒加载单例）                              ││
│  │  ├── ExecutorProcessManager — 每任务子进程管理                  ││
│  │  ├── Mailbox                 — 线程安全结果缓存                 ││
│  │  ├── MailboxHTTPServer       — 后台线程，接收 Executor 推送     ││
│  │  └── ExecutorPoller          — 后台定时轮询 /result 兜底       ││
│  └─────────────────────────────────────────────────────────────┘│
└──────────────────────────────────┬──────────────────────────────┘
                                   │ HTTP（POST /execute）
                                   ▼
                    ┌──────────────────────────────┐
                    │   Executor 子进程（FastAPI）    │
                    │   ├── POST /execute  接收任务   │
                    │   ├── GET  /result   返回结果   │
                    │   ├── GET  /status   进度查询   │
                    │   ├── POST /stop     软中断     │
                    │   └── POST /shutdown 优雅关闭   │
                    │                                │
                    │   内部 ReAct 循环:               │
                    │   call_executor → tools_node    │
                    │        ↑              │         │
                    │        └──────────────┘         │
                    └──────────────┬─────────────────┘
                                   │ Push: POST {mailbox_url}/inbox
                                   │ Pull: GET /result/{plan_id}
                                   ▼
                         Supervisor 的 Mailbox
```

---

## 2. Supervisor 主循环

Supervisor 图结构是经典 ReAct 循环：`call_model → route → tools → call_model → ... → __end__`。

### 2.1 call_model 三条早返回路径

```
call_model 入口
  │
  ├─ [1] ensure_started(v3_manager)
  │     ├─ CancelledError → 重新抛出
  │     └─ 其他异常 → 日志记录，继续执行（不阻断 Supervisor）
  │
  ├─ [2] force_poll_active_tasks → 刷新 Mailbox 状态
  │
  ├─★ 早返回 A：重规划耗尽
  │   条件：planner_session 存在
  │         AND last_executor_status == "failed"
  │         AND replan_count >= max_replan（默认 3）
  │   动作：返回合成 AIMessage，告知 LLM 报告失败
  │   结果：无 tool_calls → __end__
  │
  ├─★ 早返回 B：Mode 2 → Mode 3 升级
  │   条件：planner_session 存在
  │         AND last_executor_status == "failed"
  │         AND replan_count < max_replan
  │         AND plan_json 为空
  │         AND _needs_mode3_upgrade(summary, error) == True
  │              （10 个语义信号：需要计划/重规划/无法继续/.../replan/...）
  │   动作：返回合成 AIMessage，内含强制 call_planner tool_call
  │   结果：有 tool_calls → tools 节点 → call_planner 执行
  │
  ├─ 调用 LLM（正常路径）
  │
  ├─ [3] thinking_visible 检查（仅 mode=1 直接回答时）
  │
  ├─ 推断 SupervisorDecision（mode 1/2/3）
  │
  ├─★ 早返回 C：Max Step 强制终止
  │   条件：is_last_step == True AND response 有 tool_calls
  │   动作：返回合成 AIMessage，告知已达最大步数
  │   结果：无 tool_calls → __end__
  │
  └─ 正常返回：{messages: [response], supervisor_decision: decision}
      │
      ▼
  route_model_output
      ├─ 无 tool_calls → "__end__"（终止）
      └─ 有 tool_calls → "tools"（执行工具后循环回来）
```

### 2.2 重规划计数器状态机

```
replan_count 变化规则（在 _build_executor_updates 中）：

  executor_status == "failed"    →  replan_count += 1
  executor_status == "completed" →  replan_count = 0（重置）
  executor_status == "paused"    →  replan_count 不变
  其他状态                        →  replan_count 不变
```

**关键路径**：当 `replan_count >= max_replan` 时，`call_model` 早返回 A 触发，Supervisor 直接向用户报告失败并终止，不再尝试重规划。

---

## 3. dynamic_tools_node 分支处理

`dynamic_tools_node` 是 Supervisor 的"工具执行 + 状态同步"节点。它对所有 ToolMessage 按 tool_name 分派：

```
dynamic_tools_node
  │
  ├─ 执行 ToolNode（实际工具调用）
  │   └─ CancelledError → 日志 + 重抛出
  │
  ├─ force_poll_active_tasks（刷新 Mailbox）
  │
  └─ 遍历每个 ToolMessage，按 tool_name 分支：

      ├─ call_planner
      │   ├─ 有内容：拆分 [PLANNER_REASONING] + plan_json
      │   │         更新 PlannerSession（history、version、archive）
      │   └─ 空/无内容：透传原始 ToolMessage
      │
      ├─ call_executor
      │   ├─ 含 [EXECUTOR_RESULT]：→ _process_executor_completion
      │   │   ├─ 提取 updated_plan、status、error、summary、snapshot
      │   │   ├─ 构建反馈给 LLM 的公开文本（隐藏原始标记）
      │   │   ├─ 更新 replan_count、planner_session
      │   │   └─ 记录 executor_task_history
      │   │
      │   ├─ 含 [EXECUTOR_DISPATCH]：异步派发成功
      │   │   ├─ 添加 ActiveExecutorTask(status="dispatched")
      │   │   └─ 记录 executor_task_history
      │   │
      │   └─ 两者都不含：透传原始 ToolMessage
      │
      ├─ get_executor_result
      │   ├─ 含 [EXECUTOR_RESULT]：
      │   │   ├─ 同上 _process_executor_completion
      │   │   ├─ 如果 detail=="full" → 追加完整输出到 ToolMessage
      │   │   ├─ 终态（completed/failed/stopped）→ 从 active_executor_tasks 移除
      │   │   └─ 非终态 → 更新 active_executor_tasks 的 status
      │   └─ 不含标记：透传
      │
      ├─ check_executor_progress
      │   ├─ 透传 ToolMessage
      │   └─ 如果内容含"任务运行中" → 提升 dispatched→running
      │
      ├─ list_executor_tasks
      │   ├─ 透传 ToolMessage
      │   └─ 解析 [EXECUTOR_REGISTRY_UPDATE] → 合并到 executor_task_history
      │
      └─ 其他工具：透传原始 ToolMessage
```

---

## 4. call_executor 完整派发流程

`call_executor` 是 Supervisor 最复杂的工具，包含 7 个阶段：

```
call_executor(state, task_description, plan_id, wait_for_result)
  │
  ├─ Phase 1：参数验证 & 计划解析
  │   ├─ 两者都传 → 错误："不能同时传 task_description 和 plan_id"
  │   │
  │   ├─ plan_id 非空 → Mode 3 路径
  │   │   ├─ 无 planner_session → 错误
  │   │   ├─ plan_json 解析失败 → 错误
  │   │   ├─ plan_id 不匹配 → 错误
  │   │   └─ 通过：使用现有 plan_json
  │   │
  │   └─ task_description 非空 → Mode 2 路径
  │       ├─ 空描述 → 错误
  │       └─ 通过：生成单步计划 plan_{uuid}
  │
  ├─ Phase 2：V3 基础设施启动
  │   └─ 失败 → 标记步骤失败 + 返回 [EXECUTOR_RESULT]{status:"failed"}
  │
  ├─ Phase 3：启动 Executor 子进程
  │   ├─ pm.start_for_task(plan_id, ctx, mailbox_url)
  │   └─ 失败 → 标记步骤失败 + 返回 [EXECUTOR_RESULT]{status:"failed"}
  │
  ├─ Phase 4：LangSmith 分布式追踪头注入（可选）
  │
  ├─ Phase 5：POST /execute 派发任务
  │   ├─ HTTP 409 → "Executor 已在执行"
  │   ├─ HTTP 非 200 → 错误
  │   ├─ ConnectError → "无法连接到 Executor 服务"
  │   ├─ 其他异常 → "Executor 派发异常"
  │   └─ 200 OK → 继续
  │
  ├─ Phase 6：注册 Poller
  │   └─ poller.register(plan_id, plan_json, executor_base_url)
  │
  └─ Phase 7：同步 vs 异步返回
      ├─ wait_for_result=True（默认）
      │   └─ await _wait_for_executor_result(...) → 阻塞等待
      │       返回 [EXECUTOR_RESULT]{...}
      │
      └─ wait_for_result=False（异步）
          └─ 立即返回 [EXECUTOR_DISPATCH]{plan_id, status:"accepted"}
```

### 4.1 Mode 2 vs Mode 3 的 plan_id 处理差异

| | Mode 2（task_description） | Mode 3（plan_id） |
|---|---|---|
| plan_id 来源 | 自动生成 `plan_{uuid}` | 使用传入的 plan_id |
| 计划内容 | 自动构建单步计划 | 使用 PlannerSession 中的计划 |
| 子进程复用 | 每次新起 | 同 id 复用 |
| 每次调用 | 独立进程 | 复用已有进程（如果存活） |

---

## 5. Executor 子进程启动

`ExecutorProcessManager.start_for_task` 的完整流程：

```
start_for_task(plan_id, ctx, mailbox_url)
  │
  ├─ [1] 驱逐已死亡的旧 handle（returncode != None 的条目）
  │
  ├─ [2] 复用检查：如果同 plan_id 的进程仍存活 → 直接返回 handle
  │
  ├─ [3] 构建环境变量
  │      EXECUTOR_PORT=0, PLAN_ID=plan_id, MAILBOX_URL=...
  │
  ├─ [4] 清除旧端口文件
  │
  ├─ [5] spawn 子进程
  │      python -m src.executor_agent
  │      ├─ 优先 asyncio.create_subprocess_exec
  │      └─ Windows 回退 subprocess.Popen + _PopenProcessAdapter
  │
  ├─ [6] 端口发现循环（每 0.3s 轮询端口文件）
  │      截止时间 = now + executor_startup_timeout（默认 30s）
  │      ├─ 成功读到端口 → 继续
  │      └─ 超时 → terminate() + 收集 stdout + 抛出 TimeoutError
  │
  ├─ [7] 健康检查循环（每 0.3s GET /health）
  │      独立截止时间 = now + max(remaining, _HEALTH_CHECK_BUDGET=10s)
  │      ├─ 200 OK → 创建 ProcessHandle，存入 dict，返回
  │      ├─ ConnectError/TimeoutException → 静默重试
  │      └─ 超时 → terminate() + 关闭 httpx + 抛出 TimeoutError
  │
  └─ 返回 ProcessHandle
```

**已修复**：健康检查使用独立 deadline，保证至少 10s（`_HEALTH_CHECK_BUDGET`），不再与端口发现共享截止时间。

### 5.1 子进程内部启动

```
__main__.py（子进程入口）
  │
  ├─ 读取 EXECUTOR_PORT 环境变量（默认 0 = 动态分配）
  ├─ 创建 TCP socket，bind 到 0.0.0.0:{port}
  ├─ 获取实际端口 → 写入 logs/executor_{plan_id}.port
  ├─ sock.listen()
  └─ uvicorn.run(server, sock=sock)
      └─ FastAPI 应用启动，注册路由
```

---

## 6. Executor 内部 ReAct 循环

```
Executor graph 拓扑:

  START → call_executor → route_executor_output
                              ├─ 无 tool_calls → "__end__" → END
                              └─ 有 tool_calls → "tools"
                                                    │
                                               tools_node
                                                    │
                                              route_after_tools
                                              ├─ 反射条件满足 → reflection_node → END
                                              └─ 否则 → call_executor（循环）
```

### 6.1 call_executor 节点

```
call_executor(state, config)
  │
  ├─ 检查 stop event（软中断）
  │   └─ 已设置 → 返回 {status:"failed", summary:"Executor stopped"} 无 tool_calls → END
  │
  ├─ 加载工具（本地 + 可选 MCP）
  │
  └─ LLM 调用（带超时保护）
      ├─ executor_call_model_timeout > 0（默认 180s）
      │   └─ asyncio.wait_for(llm.invoke, timeout)
      │       ├─ 超时 → RuntimeError（传播到 _run_executor_task → 失败结果）
      │       └─ 成功 → 继续
      │
      ├─ is_last_step == True 且有 tool_calls
      │   └─ 剥离 tool_calls → 强制终止
      │
      └─ 正常返回 AIMessage
```

### 6.2 tools_node 节点

```
tools_node(state, config)
  │
  ├─ 设置 plan_id 上下文（线程局部变量）
  │
  ├─ 工具执行（带超时保护）
  │   ├─ executor_tool_timeout > 0（默认 300s）
  │   │   └─ asyncio.wait_for(tool_node.ainvoke, timeout)
  │   │       ├─ 超时 → 合成超时 ToolMessage（不崩溃！LLM 可继续）
  │   │       │   "工具执行超时，已等待 {timeout} 秒..."
  │   │       └─ 成功 → 继续
  │   │
  │   └─ 其他异常 → 传播（由 ToolNode 转为 ToolMessage）
  │
  ├─ finally: 清除 plan_id 上下文
  │
  ├─ Observation 规范化（截断/外置）
  │
  └─ 软中断检查
      └─ 检测到 INTERRUPT_PROMPT → 注入停止提示 → LLM 产出终态摘要
```

### 6.3 _run_executor_task（server.py 中的后台任务包装器）

```
_run_executor_task(plan_id, plan_json, ...)
  │
  ├─ 设置 status = "running"
  │
  ├─ Mock 模式检查（EXECUTOR_MOCK_MODE 环境变量）
  │   ├─ "failed" → raise RuntimeError
  │   └─ 其他值 → 返回 mock completed 结果
  │
  ├─ 正常执行：await run_executor(plan_json, context)
  │
  ├─ 执行完成后检查 stop event
  │   └─ 如果已设置 → 覆盖 status 为 "stopped"
  │
  ├─ 存储 ExecutorResult 到 _results
  │
  ├─ 异常处理：
  │   ├─ Exception → ExecutorResult(status="failed", summary="Executor crashed: {e}")
  │   └─ CancelledError → ExecutorResult(status="stopped")
  │
  ├─ finally:
  │   ├─ 清理 _running_tasks, _stop_events, _statuses
  │   ├─ _cleanup_old_results（上限 200 条，优先淘汰 accepted 占位）
  │   ├─ _push_result_to_mailbox（asyncio.shield 保护 + 同步兜底）
  │   └─ per-task 模式：_schedule_self_shutdown（2s 后自毁）
  │
  └─ 结束
```

---

## 7. 结果回传双路径

Executor 完成后通过两种路径将结果送达 Supervisor：

```
                     Executor 完成
                         │
          ┌──────────────┴──────────────┐
          ▼                              ▼
    Push 路径（优先）              Pull 路径（兜底）
          │                              │
  _push_result_to_mailbox          ExecutorPoller._poll_one
          │                              │
  POST {mailbox_url}/inbox         GET {base_url}/result/{plan_id}
  最多重试 3 次，每次超时 5s         每 1.5s 轮询一次
          │                              │
  ┌───────┴────────┐              ┌──────┴──────┐
  │ 成功 → Mailbox │              │ 终态 → Mailbox│
  │ 失败 → 依赖 Pull│              │ 非终态 → 等待 │
  └────────────────┘              └─────────────┘
```

### 7.1 Push 路径细节

```
_push_result_to_mailbox(result, plan_id)
  │
  ├─ 检查 MAILBOX_URL 环境变量（per-task 模式才有）
  │   └─ 未设置 → 直接返回（不走 Push）
  │
  ├─ 构建 payload: {item_type:"completion", plan_id, status, summary, ...}
  │
  └─ 重试循环（最多 3 次，间隔 1s）
      ├─ POST {MAILBOX_URL}/inbox
      │   ├─ 200 → 成功，退出
      │   ├─ 非 200 → 日志警告，重试
      │   └─ ConnectError/TimeoutException → 重试
      │
      └─ 3 次全失败 → 日志错误（结果仍在 _results 中，Pull 可兜底）
```

**推送取消保护**：`_run_executor_task` 的 finally 块中，`_push_result_to_mailbox` 使用 `asyncio.shield()` 保护。若 shield 也被取消（Supervisor 退出），则调用 `_sync_push_result_to_mailbox`（urllib 同步兜底，3s 超时）确保结果至少有一次推送机会。

### 7.2 Pull 路径细节

```
ExecutorPoller._poll_loop（后台 asyncio Task）
  │
  ├─ 创建共享 httpx.AsyncClient(timeout=3.0)
  │
  └─ 循环（间隔 1.5s，可被 force_event 提前唤醒）:
      │
      ├─ 遍历所有活跃 plan_id
      │   └─ _poll_one(client, plan_id)
      │       ├─ Mailbox 已有 completion → 注销，跳过
      │       ├─ 过期检测（registered_at > max_staleness，默认 5min）
      │       │   └─ 过期 → 合成失败写入 Mailbox + 注销
      │       ├─ 连续失败检测（consecutive_failures >= 10）
      │       │   └─ 超限 → 合成失败写入 Mailbox + 注销
      │       ├─ 解析 base_url（_Registration.base_url 或 fallback）
      │       ├─ 获取信号量（最多 5 并发）
      │       ├─ GET {base}/result/{plan_id}
      │       │   ├─ 200 + 终态 → post Mailbox + 注销
      │       │   ├─ 200 + 非终态 → 重置 consecutive_failures
      │       │   ├─ 404 → 重置 consecutive_failures（任务可能仍在启动）
      │       │   ├─ 非 200 → consecutive_failures += 1
      │       │   └─ 异常 → consecutive_failures += 1
      │       └─ 无 base_url → 跳过
      │
      └─ CancelledError → 退出循环
```

**过期与失败保护**：Poller 现在有双层保护——`max_staleness`（默认 300s）防止注册永久残留，`max_consecutive_failures`（默认 10）防止持续连接失败。超限后自动注销并写入合成失败到 Mailbox，确保异步模式下的 Executor 崩溃/OOM 能被检测和清理。

---

## 8. _wait_for_executor_result 守候逻辑

这是 `call_executor(wait_for_result=True)` 和 `get_executor_result` 的核心等待函数：

```
_wait_for_executor_result(plan_id, plan_json, ctx, timeout=120.0)
  │
  ├─ [Step 1] 获取 Mailbox 实例
  │   ├─ 成功 → 继续
  │   └─ RuntimeError（未初始化）→ 尝试 ensure_started 再获取
  │       ├─ 成功 → 继续
  │       └─ 失败 → 返回 [EXECUTOR_RESULT]{status:"failed", "回调邮箱未初始化"}
  │
  ├─ [Step 2] 非阻塞预检：Mailbox 已有结果？
  │   └─ 有 → 直接返回 _format_completion_result
  │
  ├─ [Step 3] 探测 Executor 存活
  │   └─ _probe_executor_task(plan_id, ctx)
  │       ├─ "not_found" → 标记步骤失败 + 清理进程 + 返回失败结果
  │       ├─ "unreachable" → 标记步骤失败 + 清理进程 + 返回失败结果
  │       ├─ "completed"/"failed"/"stopped" → 尝试直接获取结果（Step 4）
  │       └─ "running" → 进入轮询（Step 5）
  │
  ├─ [Step 4] 终态但回调丢失
  │   └─ _fetch_executor_result_directly
  │       ├─ 找到 → 返回结果
  │       └─ 未找到 → 进入轮询（Step 5）
  │
  ├─ [Step 5] 注册 Poller + 轮询循环
  │   ├─ poller.register(plan_id, plan_json, base_url)
  │   └─ while time < deadline（每 1s 检查一次 Mailbox）
  │       ├─ completion 不为空 → 退出循环
  │       └─ await asyncio.sleep(1.0)
  │       │
  │       └─ CancelledError → 返回 [EXECUTOR_RESULT]{status:"failed"}（标记步骤失败）
  │           "Executor 执行被中断：等待 Executor 结果时被中断..."
  │
  ├─ [Step 6] 超时处理
  │   └─ result_data is None（超时退出循环）
  │       ├─ error_detail = "等待 Executor 完成超时（{timeout}秒）"
  │       ├─ 标记所有步骤为 failed
  │       ├─ 构造 [EXECUTOR_RESULT]{status:"failed"}
  │       ├─ _cleanup_dead_executor → 终止卡住的进程
  │       └─ 返回超时错误
  │
  └─ [Step 7] 正常完成
      ├─ 注销 Poller（best-effort）
      └─ 返回 _format_completion_result
```

### 8.1 _probe_executor_task 探测逻辑

```
_probe_executor_task(plan_id, ctx)
  │
  ├─ 获取 base_urls（优先 task-specific，然后所有活跃 URL）
  │
  └─ 遍历每个 base_url:
      ├─ GET {base}/status/{plan_id}（timeout 3s）
      │   ├─ 200 → 返回 resp.status
      │   ├─ 404 → 尝试 GET {base}/result/{plan_id}
      │   │   ├─ 200 → "completed"
      │   │   └─ 其他 → 继续下一个 base_url
      │   └─ ConnectError/Timeout → 继续下一个 base_url
      │
      └─ 所有 URL 尝试完毕:
          ├─ 没有任何连接成功 → "unreachable"
          └─ 连接成功但未找到 → "not_found"
```

---

## 9. 超时全景图

所有超时参数及其默认值、触发位置、超时行为一览：

### 9.1 Supervisor 侧

| 超时参数 | 默认值 | 位置 | 超时行为 |
|---|---|---|---|
| `executor_startup_timeout` | 30s | `ProcessManager.start_for_task` | 端口发现 deadline + 健康检查独立 deadline（至少 10s）；超时 → terminate + TimeoutError |
| `_wait_for_executor_result.timeout` | 120s | `call_executor` 同步模式 | 轮询 Mailbox 超时 → 标记 failed + 杀进程 |
| `_stop_handle` 等待 | 10s + 5s | 进程停止 | HTTP /shutdown → wait(10s) → terminate → wait(5s) → kill |
| `sync_terminate` 等待 | 3s | atexit/信号处理 | terminate → wait(3s) → kill |
| `MailboxHTTPServer.stop` join | 5s | 服务器关闭 | 等线程退出 5s，超时仅日志警告 |
| Mailbox 上限 | 80 个 | Mailbox._maybe_evict | 超过 80 个已完成的 box 时清理到 50 个 |

### 9.2 Executor 侧

| 超时参数 | 默认值 | 位置 | 超时行为 |
|---|---|---|---|
| `executor_call_model_timeout` | 180s | `call_executor` + `reflection_node` | RuntimeError → 传播 → 失败结果 |
| `executor_tool_timeout` | 300s | `tools_node` | **优雅降级**：合成超时 ToolMessage，LLM 可继续 |
| `max_executor_iterations` | 20 | `run_executor` recursion_limit | GraphRecursionError → 失败结果 |
| `run_with_interrupt_check.timeout` | 120s | interrupt.py | terminate → wait(5s) → kill → TimeoutExpired |
| `_push_result_to_mailbox` 单次 | 5s | server.py | 重试（最多 3 次） |
| `_push_result_to_mailbox` 总计 | ~15s | server.py | 3 次全失败 → 依赖 Poller 兜底；asyncio.shield 保护 + 同步 urllib 兜底 |
| `_results` 上限 | 200 条 | server.py | 优先淘汰 accepted 占位，保护终态条目 |

### 9.3 基础设施层

| 超时参数 | 默认值 | 位置 | 超时行为 |
|---|---|---|---|
| Poller 轮询间隔 | 1.5s | ExecutorPoller | 每 1.5s 扫描一次活跃任务 |
| Poller HTTP 超时 | 3s | _poll_one | consecutive_failures +1；达上限（10）→ 合成失败 + 注销 |
| Poller 过期阈值 | 300s | _Registration.max_staleness | 注册超过 5min → 合成失败 + 注销 |
| Poller 连续失败上限 | 10 | _Registration.max_consecutive_failures | 连续 10 次 HTTP 失败 → 合成失败 + 注销 |
| Poller 并发上限 | 5 | Semaphore | 最多 5 个并发 HTTP 请求 |
| Mailbox push 重试 | 3 次 × 5s | server.py | 失败后依赖 Pull 兜底 |
| Port 发现轮询间隔 | 0.3s | start_for_task | 每 0.3s 读端口文件 |
| 健康检查轮询间隔 | 0.3s | start_for_task | 每 0.3s GET /health |
| 健康检查最小保证 | 10s | `_HEALTH_CHECK_BUDGET` | 独立 deadline，至少保证 10s |
| 健康检查 HTTP 超时 | 5s | start_for_task | ConnectError/Timeout 静默重试 |
| `_collect_stdout` | 2s | start_for_task | 超时或异常返回空字符串 |

---

## 10. 异常场景矩阵

### 10.1 完整异常 → 结果映射

| # | 触发场景 | 在哪里被捕获 | 最终结果 |
|---|---|---|---|
| 1 | LLM API 超时（Executor） | `call_executor` → RuntimeError → `_run_executor_task` Exception | `ExecutorResult(status="failed")` |
| 2 | 工具执行超时（Executor） | `tools_node` → asyncio.TimeoutError | 合成超时 ToolMessage → LLM 继续处理 |
| 3 | Supervisor 发送 /stop | `call_executor` 节点检查 stop event | `ExecutorResult(status="stopped")` |
| 4 | Executor 进程 OOM/segfault | 进程直接死亡 | Supervisor 检测超时 → 标记 failed + 杀进程 |
| 5 | LLM API 网络错误 | `run_executor` → `_run_executor_task` Exception | `ExecutorResult(status="failed")` |
| 6 | 超过 max_executor_iterations | LangGraph GraphRecursionError → `_run_executor_task` | `ExecutorResult(status="failed")` |
| 7 | plan_json 为空 | `run_executor` ValueError → `_run_executor_task` | `ExecutorResult(status="failed")` |
| 8 | Executor 无输出 | `run_executor` RuntimeError → `_run_executor_task` | `ExecutorResult(status="failed")` |
| 9 | 重复 /execute 同 plan_id | server.py 409 | 返回 "Executor 已在执行" |
| 10 | /result 在完成前查询 | 返回预填 "accepted" 占位 | `ExecutorResult(status="accepted")` |
| 11 | Mailbox push 全部失败 | 3 次重试后放弃 | 依赖 Poller Pull 兜底 |
| 12 | 进程启动超时 | `start_for_task` TimeoutError | call_executor 返回 failed |
| 13 | 端口文件写入失败 | `__main__.py` 异常传播 → 进程退出 | 同 #12（启动超时检测） |
| 14 | `_wait_for_executor_result` 超时 | 轮询循环超时 | 标记 failed + 杀进程 |
| 15 | `CancelledError` 在等待期间 | `_wait_for_executor_result` 捕获 | 返回 `[EXECUTOR_RESULT]{status:"failed"}`（**已修复**：标记步骤失败，dynamic_tools_node 正常更新状态） |
| 16 | 重规划耗尽（≥max_replan） | `call_model` 早返回 A | 合成失败消息 → __end__ |
| 17 | Mode2→Mode3 升级触发 | `call_model` 早返回 B | 强制调用 call_planner |
| 18 | Max step 强制终止 | `call_model` 早返回 C | 合成终止消息 → __end__ |
| 19 | V3 基础设施启动失败 | `call_executor` Phase 2 | 标记步骤 failed + 返回失败结果 |
| 20 | Reflection LLM 调用超时 | `reflection_node` → asyncio.TimeoutError → RuntimeError | `ExecutorResult(status="failed")` |
| 21 | Poller 过期/连续失败 | `_poll_one` 检测 | 合成失败写入 Mailbox + 自动注销 |
| 22 | Push 取消 + shield 取消 | `_run_executor_task` finally | urllib 同步兜底 `_sync_push_result_to_mailbox` |

### 10.2 失败后的处理决策树

```
ExecutorResult 返回 Supervisor
  │
  ├─ status == "completed"
  │   └─ replan_count = 0
  │      使用 summary 收束，结束
  │
  ├─ status == "paused"
  │   └─ replan_count 不变
  │      读 snapshot_json → Supervisor 决定续跑或重规划
  │
  ├─ status == "failed"
  │   │
  │   ├─ replan_count >= max_replan
  │   │   └─ 向用户报告失败，终止
  │   │
  │   ├─ updated_plan_json 非空
  │   │   └─ replan_count += 1
  │   │      call_planner(plan_id) → call_executor（重规划循环）
  │   │
  │   ├─ updated_plan_json 为空 AND _needs_mode3_upgrade == True
  │   │   └─ 强制升级到 Mode 3 → call_planner
  │   │
  │   └─ updated_plan_json 为空 AND 不需要升级
  │       └─ Supervisor 自行判断下一步（LLM 决策）
  │
  └─ status == "stopped"
      └─ 同 failed 处理逻辑
```

---

## 11. 已覆盖 vs 潜在遗漏

### 11.1 已完整覆盖的场景

- 正常 Mode 1/2/3 执行流程
- Executor 进程启动失败（端口/健康检查超时）
- LLM 调用超时（Executor 侧 180s，含 reflection_node）
- 工具执行超时（Executor 侧 300s，优雅降级）
- Supervisor 等待超时（120s）
- Mailbox push 失败 → Poller pull 兜底
- 重规划耗尽（max_replan）
- Mode2 → Mode3 升级
- Max step 强制终止
- 软中断（stop event）
- 子进程 OOM → 超时检测
- CancelledError 传播（返回 `[EXECUTOR_RESULT]{status:"failed"}`，状态正确更新）
- 进程生命周期清理（atexit + 信号处理）
- Poller 过期检测 + 连续失败自动注销（异步模式 Executor 崩溃可检测）
- 健康检查独立 deadline（至少保证 10s）
- Mailbox push 取消保护（asyncio.shield + urllib 同步兜底）
- 结果 LRU 淘汰保护（上限 200，优先淘汰 accepted 占位）

### 11.2 已修复的边界情况

#### (A) ~~CancelledError 返回路径不一致~~ — **已修复**

`_wait_for_executor_result` 捕获 `CancelledError` 时现在返回带 `[EXECUTOR_RESULT]{status:"failed"}` 标记的格式化字符串，包含 `updated_plan_json`（步骤标记失败）。`dynamic_tools_node` 走正常的 `_process_executor_completion` 路径，正确更新 `planner_session`、`replan_count` 和 `executor_task_history`。

**修复位置**：`src/supervisor_agent/tools.py` `_wait_for_executor_result` CancelledError 分支。

#### (B) ~~Poller 无重试上限~~ — **已修复**

`ExecutorPoller._poll_one` 现在有双层保护：
1. **过期检测**：每个注册记录 `registered_at`，超过 `max_staleness`（默认 300s）→ 自动注销 + 合成失败写入 Mailbox
2. **连续失败计数**：每次 HTTP 异常 `consecutive_failures += 1`，成功（200/404）重置；达到 `max_consecutive_failures`（默认 10）→ 自动注销 + 合成失败写入 Mailbox

异步模式下 Executor 崩溃/OOM 后，Poller 最多在 `10 × 1.5s = 15s` 内检测到连续失败并自动清理。

**修复位置**：`src/common/polling.py` `_Registration` dataclass + `_poll_one` + `_post_synthetic_failure`。

#### (C) ~~start_for_task 端口/健康检查共享 deadline~~ — **已修复**

健康检查现在使用独立 deadline：端口发现成功后，重新计算 `now + max(remaining, _HEALTH_CHECK_BUDGET=10s)`。保证健康检查至少有 10s 时间，不再被端口发现饿死。

**修复位置**：`src/common/process_manager.py` `start_for_task` 健康检查块。

#### (D) ~~MailboxHTTPServer 的类级别共享~~ — **已修复**

`_InboxHandler` 不再使用类属性 `mailbox`。改为 `_MailboxHTTPServer(HTTPServer)` 子类携带实例属性，`_InboxHandler` 通过 `self.server.mailbox` 访问。

**修复位置**：`src/common/mailbox_server.py` `_MailboxHTTPServer` + `_InboxHandler._get_mailbox()`。

#### (E) ~~异步模式下推送取消竞态~~ — **已修复**

`_run_executor_task` 的 finally 块中，`_push_result_to_mailbox` 使用 `asyncio.shield()` 保护。若 shield 也被取消，则调用 `_sync_push_result_to_mailbox`（urllib 同步兜底，3s 超时）。

**修复位置**：`src/executor_agent/server.py` `_run_executor_task` finally 块 + `_sync_push_result_to_mailbox`。

#### (H) ~~reflection_node 无超时保护~~ — **已修复**

`reflection_node` 现在用 `asyncio.wait_for(..., timeout=executor_call_model_timeout)` 包装 LLM 调用，超时抛 `RuntimeError`。与 `call_executor` 节点的超时模式一致。

**修复位置**：`src/executor_agent/graph.py` `reflection_node`。

#### ~~结果 LRU 淘汰可能丢失~~ — **已修复**

`_MAX_STORED_RESULTS` 从 50 提升到 200；`_cleanup_old_results` 改为优先淘汰 `status=accepted`（预占位）条目，保护终态条目不被过早淘汰。

**修复位置**：`src/executor_agent/server.py` `_MAX_STORED_RESULTS` + `_cleanup_old_results`。

### 11.3 仍存在的边界情况

#### (F) get_executor_result(detail="full") 的缓存窗口

`detail="full"` 读取 `state.planner_session.last_executor_full_output`。这是上一次 executor 完成时缓存的内容。如果此后又发起了新的 executor 调用，缓存会被覆盖。

**影响**：用户无法查看更早的 executor 完整输出。只有最近一次的结果保存在 session 中。

#### (G) executor_task_history 无上限增长控制

`_trim_task_history` 限制在 50 条，但它只修剪 `executor_task_history`。`active_executor_tasks` 字典没有大小限制——如果有大量异步任务未被消费，可能持续增长。

**缓解**：正常使用中 LLM 会主动查询和处理任务；异常情况下会话结束会清理所有状态。Poller 的过期检测现在也会清理长期无响应的注册。

#### (I) Plan JSON 步骤失败标记后的 json.dumps 格式

`_mark_plan_steps_failed` 对非 dict 类型的 step 跳过（`continue`），也处理 list 格式（不含顶层 `steps` 键的纯步骤列表）。但如果 `plan_json` 是完全无效的 JSON，它返回原始字符串而不做任何修改——此时 `updated_plan_json` 字段包含原始无效 JSON，下游 Planner 可能无法解析。

---

## 附录：状态流转图

```
State 关键字段变化时序：

call_planner 返回后:
  planner_session ← PlannerSession(session_id, plan_json, planner_reasoning, ...)
  planner_session.planner_history_by_plan_id[plan_id] ← 对话记录

call_executor 返回后（EXECUTOR_RESULT）:
  planner_session ← 更新 last_executor_*, plan_json（如 updated_plan_json 非空）
  replan_count ← 按规则增减
  executor_task_history[plan_id] ← ExecutorTaskRecord

call_executor 返回后（EXECUTOR_DISPATCH）:
  active_executor_tasks[plan_id] ← ActiveExecutorTask(status="dispatched")
  executor_task_history[plan_id] ← ExecutorTaskRecord(status="dispatched")

get_executor_result 终态后:
  active_executor_tasks[plan_id] ← 删除
  planner_session ← 更新 last_executor_*
  replan_count ← 按规则增减

check_executor_progress:
  active_executor_tasks[plan_id].status ← "dispatched"→"running"（条件满足时）

list_executor_tasks:
  executor_task_history ← 合并 [EXECUTOR_REGISTRY_UPDATE] 数据
```
