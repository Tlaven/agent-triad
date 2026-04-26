# Architecture — Mermaid Flowcharts

> 用 Mermaid 可视化 进程分离 Push 架构。工具名以实际代码为准。
>
> **核心变化**：
> - **Push 模式**：Executor 主动 POST 结果到 Supervisor 内的 MailboxHTTPServer 线程，不再依赖 Supervisor 主动拉取
> - **统一后台轮询**：`ExecutorPoller`（单一 `asyncio.Task`）替代分散的 `_poll_executor_results`，每 1.5s 用 `asyncio.gather` + `Semaphore(5)` 并发扫描所有活跃任务，共享一个 `httpx.AsyncClient` 连接池
> - **Per-task 进程**：每次 `call_executor` 创建独立 Executor 子进程，完成后自动退出
> - **软中断装饰器**：工具执行期间检查 `stop_event`，可中断长时间运行的命令
> - **Mailbox 内存管理**：写入时自动驱逐已完成的旧 box（上限 80，保留 50）
>
> **设计原则**：
> - Executor 完成后通过 `POST /inbox` 主动推送结果到 MailboxHTTPServer（Push 优先）
> - `ExecutorPoller` 作为兜底：每 1.5s 对 `/result/{pid}` 发起一次拉取，填补 Push 失败的场景
> - `manage_executor(action="get_result")` 工具不再自行轮询 HTTP，只等待 Mailbox（poller 负责写入）；可选 `detail=full` 拉取步骤级详情（见下文第 2 节序列图与第 7 节决策树）
> - `ActiveExecutorTask` 不存 `plan_json`（移至 poller 缓存），Graph State 保持轻量
> - `executor_task_history` 上限 50 条，防止长期运行内存膨胀

---

## 1. 系统架构总览

```mermaid
graph TB
    subgraph ProcessA["Process A — Supervisor+Planner (LangGraph ASGI 内)"]
        SG[Supervisor ReAct Graph]
        PG[Planner Graph]
        MB["Mailbox<br/>in-memory<br/>(上限80条，自动驱逐)"]
        MBS["MailboxHTTPServer<br/>独立线程 :动态端口"]
        EP["ExecutorPoller<br/>asyncio.Task<br/>1.5s 间隔 + Semaphore(5)"]
        PM[ProcessManager<br/>asyncio.create_subprocess_exec]
        PP["logs/executor_{plan_id}.port<br/>端口文件持久化"]

        SG -->|call_planner| PG
        PG -->|plan JSON| SG
        MBS -->|"_post_sync<br/>(Push写入)"| MB
        EP -->|"终态写入<br/>(Pull兜底)"| MB
        SG -->|"force_poll_once()<br/>刷新后注入"| EP
        SG -->|读取注入| MB
        PM -->|端口写入| PP
        PP -->|热重载恢复| PM
        EP -->|"GET /result/{pid}"| PM
    end

    subgraph ProcessB["Process B — Executor (动态端口, per-task)"]
        ES[Executor Server<br/>FastAPI :动态端口]
        EG[Executor Graph<br/>ReAct Loop]
        RS["_results dict<br/>(最多50条, FIFO淘汰)"]
        SF[Stop Events<br/>asyncio.Event dict]

        ES -->|ainvoke| EG
        ES -->|set flag| SF
        EG -->|check| SF
        EG -->|完成写入| RS
        ES -->|派发时预填| RS
    end

    PM -->|"asyncio.create_subprocess_exec<br/>MAILBOX_URL 注入环境变量"| ProcessB

    SG -->|"POST /execute"| ES
    SG -->|"POST /stop/{plan_id}"| ES
    SG -->|"GET /status/{plan_id}"| ES
    SG -->|"GET /tasks"| ES

    ES -->|"POST /inbox<br/>(Push 结果)"| MBS
    EP -->|"GET /result/{pid}<br/>(Pull 兜底)"| ES

    style ProcessA fill:#e8f4e8,stroke:#2d7d2d
    style ProcessB fill:#e8e8f4,stroke:#2d2d7d
    style MB fill:#fff3cd,stroke:#856404
    style MBS fill:#ffeeba,stroke:#856404
    style EP fill:#d4edda,stroke:#155724
    style RS fill:#fff3cd,stroke:#856404
    style PP fill:#d1ecf1,stroke:#0c5460
```

**与旧架构的关键区别**：
- ❌ 无 Callback Server（不再嵌套 ASGI）
- ❌ 无固定端口（动态分配，避免冲突）
- ❌ 无 `subprocess.Popen`（换 `asyncio.create_subprocess_exec`）
- ❌ 无分散的 `_poll_executor_results`（统一为 `ExecutorPoller` 后台任务）
- ✅ Executor Push 结果到 MailboxHTTPServer，Push 失败时 Poller 兜底拉取
- ✅ `ActiveExecutorTask` 不存 `plan_json`，Graph State 轻量
- ✅ Mailbox 自动驱逐已完成条目，防内存泄漏
- ✅ LangSmith `parent_run_id` 跨进程传递，trace 链路可见

---

## 2. 完整执行流程（并行模式）

```mermaid
sequenceDiagram
    participant User as 用户
    participant SM as Supervisor call_model
    participant DT as dynamic_tools_node
    participant EP as ExecutorPoller<br/>(后台 asyncio.Task)
    participant PM as ProcessManager
    participant ES as Executor Server
    participant EG as Executor Graph
    participant MBS as MailboxHTTPServer<br/>(独立线程)
    participant MB as Mailbox<br/>(in-memory)
    participant PL as Planner

    User->>SM: 用户任务
    SM->>SM: LLM 决定 复杂规划模式
    SM->>DT: tool_call: call_planner(task_core)
    DT->>PL: run_planner()
    PL-->>DT: plan JSON
    DT-->>SM: plan ready

    SM->>SM: LLM 决定调用 call_executor
    SM->>DT: tool_call: call_executor(plan_id)
    DT->>PM: start_for_task(plan_id)
    PM-->>DT: Executor 进程就绪 base_url
    DT->>ES: POST /execute {plan_json, plan_id, config:{parent_run_id}}
    Note over ES: 创建 asyncio.Task
    ES-->>DT: {plan_id, status: "accepted"}
    DT->>EP: poller.register(plan_id, plan_json)
    DT->>EP: poller.set_base_url(base_url)
    DT-->>SM: Executor 已派发 [EXECUTOR_DISPATCH]

    Note over SM: Supervisor 不阻塞，继续其他工作
    Note over EP: 后台每 1.5s 轮询活跃任务

    SM->>EP: force_poll_once() (call_model 前刷新)
    EP->>ES: GET /result/{plan_id}
    ES-->>EP: status: running

    SM->>SM: LLM 决定 manage_executor(action="get_result")
    SM->>DT: tool_call: manage_executor(action="get_result", plan_id[, detail])
    Note over DT: 查 Mailbox → 无结果<br/>probe → 任务运行中<br/>等待 Mailbox (poller 写入)<br/>detail=full 时终态后再拼 last_executor_full_output

    Note over EG: 执行完成
    EG->>ES: 结果写入 _results dict
    ES->>MBS: POST /inbox {plan_id, payload}
    MBS->>MB: _post_sync() 写入 completion

    Note over EP: 后台轮询也同步写入<br/>(Push 已完成则跳过)
    EP->>MB: has_completion? → 已存在，unregister

    DT->>MB: get_completion(plan_id) → 命中
    DT->>EP: poller.unregister(plan_id)
    DT-->>SM: [EXECUTOR_RESULT] {status, summary, plan}

    SM->>SM: LLM 合成最终答案
    SM-->>User: 任务完成
```

---

## 3. 结果写入 Mailbox 的两条路径

```mermaid
flowchart TD
    subgraph Push["路径 1：Push（Executor 主动推送）"]
        P1[Executor 执行完成] --> P2["_push_result_to_mailbox()"]
        P2 --> P3["POST /inbox<br/>MAILBOX_URL 环境变量"]
        P3 --> P4["MailboxHTTPServer 线程<br/>_InboxHandler.do_POST()"]
        P4 --> P5["Mailbox._post_sync()<br/>+ _maybe_evict()"]
    end

    subgraph Pull["路径 2：Pull（ExecutorPoller 后台兜底）"]
        Q1["ExecutorPoller._poll_loop()<br/>每 1.5s 醒来"] --> Q2["asyncio.gather<br/>Semaphore(5) 控并发"]
        Q2 --> Q3["GET /result/{pid}<br/>单个 httpx.AsyncClient 复用"]
        Q3 --> Q4{终态?}
        Q4 -->|Yes| Q5["Mailbox.post() + unregister()"]
        Q4 -->|No| Q6["等下次循环"]
    end

    subgraph Consume["消费点"]
        R1["manage_executor(get_result)<br/>等待 Mailbox（无 HTTP）<br/>可选 detail=full"]
        R2["_build_executor_status_brief<br/>注入 system prompt（含 summary 前100字）"]
        R3["dynamic_tools_node<br/>解析 [EXECUTOR_RESULT]"]
    end

    P5 --> R1
    P5 --> R2
    Q5 --> R1
    Q5 --> R2
    R1 --> R3

    subgraph ForceFlush["强制刷新（同步点）"]
        F1["call_model 开始前<br/>force_poll_once()"]
        F2["dynamic_tools_node 完成后<br/>force_poll_once()"]
    end

    F1 --> Q2
    F2 --> Q2

    style P5 fill:#fff3cd,stroke:#856404
    style Q5 fill:#fff3cd,stroke:#856404
    style R2 fill:#e8f4e8,stroke:#2d7d2d
```

**关键原则**：
- Push 是主路径，延迟最低（Executor 完成即通知）
- Pull 是兜底，确保 Push 丢失时（网络异常等）结果也能到达
- `manage_executor(action="get_result")` 纯等待 Mailbox，不自行发 HTTP 请求（`detail=full` 不改变收束路径，仅在终态后附加步骤级正文，或任务已结束时读会话缓存）
- `force_poll_once()` 在 LLM 决策前强制刷新一次，消除信息滞后

---

## 4. 软中断流程

```mermaid
sequenceDiagram
    participant SM as Supervisor LLM
    participant DT as dynamic_tools_node
    participant ES as Executor Server
    participant SF as Stop Events<br/>(asyncio.Event)
    participant EG as Executor Graph<br/>call_executor node

    Note over SM: Supervisor 发现需要停止 Executor

    SM->>DT: tool_call: manage_executor(action="stop", plan_id, reason)
    DT->>ES: POST /stop/{plan_id} {reason}
    ES->>SF: stop_events[plan_id].set()
    ES-->>DT: {acknowledged: true}
    DT-->>SM: 停止信号已发送

    Note over EG: Executor 仍在运行,<br/>下一次 call_executor 时检查

    EG->>SF: stop_events[plan_id].is_set()?
    SF-->>EG: True
    EG->>EG: 生成部分完成的结果:<br/>status=stopped<br/>summary="Stopped by Supervisor"
    Note over EG: AIMessage 无 tool_calls<br/>→ route → __end__
    EG->>ES: 结果写入 _results dict
    ES->>ES: _push_result_to_mailbox()

    Note over SM: Push 到 MailboxHTTPServer<br/>或 Poller 下次轮询时写入
```

---

## 5. 邮箱模式（Mailbox Pattern）

```mermaid
flowchart LR
    subgraph ExecProc["Executor Process"]
        EG[Executor Graph] -->|完成写入| RS["_results dict<br/>plan_id → ExecutorResult<br/>(最多50条)"]
        EG -->|"POST /inbox"| MBS_ext["MailboxHTTPServer<br/>(Supervisor 进程内线程)"]
    end

    subgraph MailboxStore["Mailbox<br/>(in-memory, per plan_id, 上限80)"]
        C["completion<br/>status / summary / updated_plan_json<br/>has_completion=True"]
    end

    subgraph SupervisorProc["Supervisor Process"]
        EP_box["ExecutorPoller<br/>(后台 asyncio.Task)<br/>GET /result/{pid} 兜底"]
        GER["manage_executor(get_result)<br/>等待 Mailbox (无 HTTP)<br/>detail=full 可选"]
        BES["_build_executor_status_brief<br/>注入 system prompt<br/>+ summary 前100字预览"]
        MBS_box["MailboxHTTPServer<br/>独立线程 :port"]
    end

    MBS_ext --> MBS_box
    MBS_box -->|"_post_sync() Push写入"| C
    RS -->|"GET /result/{pid}<br/>Pull兜底"| EP_box
    EP_box -->|终态写入| C
    GER -->|等待 Mailbox| C
    BES -->|读 Mailbox| C

    style C fill:#f8d7da,stroke:#842029
    style RS fill:#e8e8f4,stroke:#2d2d7d
    style EP_box fill:#d4edda,stroke:#155724
    style MBS_box fill:#ffeeba,stroke:#856404
```

**关键区分**：
- Executor 完成后通过 `POST /inbox` Push 结果到 MailboxHTTPServer（主路径）
- `ExecutorPoller` 后台拉取 `/result/{pid}` 作为 Pull 兜底
- `manage_executor(action="get_result")` 纯 Mailbox 等待，不主动发 HTTP（120s 超时）；`detail` 见决策 1 与上文「消费点」
- `_build_executor_status_brief` 将 Mailbox 内容（含 summary 摘要）注入 system prompt

---

## 6. 进程生命周期管理

```mermaid
stateDiagram-v2
    [*] --> Recovering: call_model 首次调用<br/>ensure_started()

    state Recovering {
        [*] --> StartMailboxThread: 启动 MailboxHTTPServer 线程
        StartMailboxThread --> StartPoller: 启动 ExecutorPoller asyncio.Task
        StartPoller --> Ready_infra: V4 基础设施就绪
    }

    Recovering --> Ready: 基础设施启动完成

    state PerTask {
        [*] --> CheckExisting: call_executor 调用
        CheckExisting --> ReuseHandle: 进程已在运行
        CheckExisting --> SpawnNew: 需要新进程
        SpawnNew --> Writing: asyncio.create_subprocess_exec<br/>MAILBOX_URL 注入环境
        Writing --> WaitPort: 写 logs/executor_{plan_id}.port
        WaitPort --> PollHealth: GET /health 轮询
        PollHealth --> HandleReady: 200 OK
        PollHealth --> TimedOut: 超时 executor_startup_timeout
    }

    Ready --> PerTask: 每次 call_executor
    PerTask --> Executing: POST /execute

    Executing --> Completing: 任务完成
    Completing --> PushResult: POST /inbox → MailboxHTTPServer
    PushResult --> SelfShutdown: _schedule_self_shutdown()
    SelfShutdown --> Stopped: 子进程自动退出

    Ready --> ShuttingDown: Supervisor 关闭 / stop()
    ShuttingDown --> Cleanup: 停 Poller → 停各子进程 → 停 Mailbox 线程
    Cleanup --> [*]
```

**与旧版的改进**：
- 启动用 `asyncio.create_subprocess_exec`（非阻塞），不再用 `subprocess.Popen`
- 端口动态分配（port=0），每个任务独立端口，不再固定 8100
- Per-task 进程（`logs/executor_{plan_id}.port`），任务完成后进程自动退出
- V4 基础设施启动同时包含 MailboxHTTPServer + ExecutorPoller

---

## 7. Supervisor LLM 工具决策树

```mermaid
flowchart TD
    A[Supervisor call_model] --> A0["force_poll_once()<br/>LLM 决策前刷新 Mailbox"]
    A0 --> B{LLM 分析任务}
    B -->|Mode 1| C[Direct Response]
    B -->|Mode 2| D["call_executor(task_description)<br/>poller.register(plan_id)"]
    B -->|Mode 3| E["call_planner → call_executor(plan_id)<br/>poller.register(plan_id)"]

    D --> F["manage_executor(action=get_result, plan_id[, detail])<br/>等待 Mailbox（poller 负责写入）<br/>120s 超时；detail=full 步骤级"]
    E --> F

    F --> G{结果?}
    G -->|completed| H[合成最终答案]
    G -->|"failed + 可重规划<br/>(replan_count < MAX_REPLAN)"| I[call_planner → call_executor]
    G -->|"failed + 超过 MAX_REPLAN"| J[返回失败分析]

    subgraph Optional["可选：并行监控"]
        K["manage_executor(check_progress)<br/>GET /status"]
        L["manage_executor(stop)<br/>软中断"]
        M["manage_executor(list_tasks)<br/>GET /tasks + 探测"]
    end

    F -.->|等待期间| K
    F -.->|需要中断时| L
    F -.->|查看所有任务| M

    subgraph Background["后台常驻（无需 LLM 触发）"]
        N["ExecutorPoller<br/>每 1.5s 扫描 active 任务<br/>Push 失败兜底"]
    end

    D -.->|register| N
    E -.->|register| N
    F -.->|unregister| N

    style Optional fill:#fff3cd,stroke:#856404,stroke-dasharray: 5 5
    style Background fill:#d4edda,stroke:#155724,stroke-dasharray: 5 5
```

---

## 8. 数据流：从 Executor 到 Supervisor

```mermaid
flowchart TD
    subgraph ExecGraph["Executor Process"]
        A["call_executor node"] -->|LLM response| B{has tool_calls?}
        B -->|Yes| C[tools_node]
        C --> F["route_after_tools → call_executor"]
        B -->|No| G["final result<br/>{status, summary, plan}"]
    end

    subgraph Storage["Executor 结果存储"]
        G --> H["_results dict<br/>plan_id → ExecutorResult<br/>(终态常驻，最多50条)"]
        I["派发时预填<br/>status=accepted"] --> H
        G --> PUSH["_push_result_to_mailbox()<br/>POST /inbox → MailboxHTTPServer"]
    end

    subgraph SupervisorInfra["Supervisor 进程 — 基础设施"]
        MBS["MailboxHTTPServer<br/>(独立线程)"]
        EP["ExecutorPoller<br/>(asyncio.Task, 1.5s 间隔)"]
        PUSH --> MBS
        MBS -->|Push 写入| MB["Mailbox<br/>(上限80, 自动驱逐)"]
        EP -->|"GET /result/{pid}<br/>Pull 兜底"| H
        EP -->|Pull 写入| MB
    end

    subgraph SupervisorGraph["Supervisor Graph — 同步点"]
        FC1["call_model 前<br/>force_poll_once()"] --> EP
        FC2["dynamic_tools_node 后<br/>force_poll_once()"] --> EP
    end

    subgraph Consumption["邮箱消费"]
        MB --> O["_build_executor_status_brief<br/>注入 system prompt<br/>（含 summary 前100字）"]
        MB --> GER["manage_executor(get_result)<br/>纯 Mailbox 等待<br/>可选 detail=full"]
        GER --> P["dynamic_tools_node<br/>解析 [EXECUTOR_RESULT]"]
        P --> Q["更新 PlannerSession<br/>executor_task_history (上限50)"]
    end

    style H fill:#fff3cd,stroke:#856404
    style MB fill:#f8d7da,stroke:#842029
    style MBS fill:#ffeeba,stroke:#856404
    style EP fill:#d4edda,stroke:#155724
    style O fill:#e8f4e8,stroke:#2d7d2d
```

---

## 9. 阻塞风险分析

| 操作 | 阻塞？ | 说明 |
|------|--------|------|
| `asyncio.create_subprocess_exec` | ✅ 不阻塞 | asyncio 原生异步子进程 |
| `process.stdout.readline()` | ✅ 不阻塞 | await，异步读取端口 |
| `httpx.AsyncClient.get/post` | ✅ 不阻塞 | 所有 Executor 通信都是 async HTTP |
| `ExecutorPoller._poll_loop` | ✅ 不阻塞 | 独立 asyncio.Task，Semaphore(5) 限并发 |
| `force_poll_once()` | ✅ 不阻塞 | await gather，短暂等待一轮结果 |
| `manage_executor(action="get_result")` 等待 | ✅ 不阻塞（协程内） | asyncio.sleep(1) 循环，等 Mailbox；`detail=full` 时同循环，终态后附加步骤级文本或读缓存 |
| Mailbox dict 读写 | ✅ 不阻塞 | threading.Lock 内存操作，微秒级 |
| MailboxHTTPServer 写入 | ✅ 不阻塞 | 独立线程，Lock 隔离 asyncio 事件循环 |
| 端口文件读写 | ⚠️ <1ms | 已用 `asyncio.to_thread` 包裹 |
