# V3 Architecture — Mermaid Flowcharts

> 用 Mermaid 可视化 V3 进程分离并行架构。工具名以实际代码为准。

---

## 1. 系统架构总览

```mermaid
graph TB
    subgraph ProcessA["Process A — Supervisor+Planner (port 8101)"]
        SG[Supervisor ReAct Graph]
        PG[Planner Graph]
        MB[Mailbox<br/>in-memory]
        CB[Callback Server<br/>FastAPI :8101]
        PM[ProcessManager<br/>spawn/monitor]

        SG -->|call_planner| PG
        PG -->|plan JSON| SG
        CB -->|write| MB
        SG -->|read| MB
    end

    subgraph ProcessB["Process B — Executor (port 8100)"]
        ES[Executor Server<br/>FastAPI :8100]
        EG[Executor Graph<br/>ReAct Loop]
        SF[Stop Events<br/>asyncio.Event dict]
        SS[Snapshot Buffer]

        ES -->|ainvoke| EG
        ES -->|set flag| SF
        EG -->|check| SF
        EG -->|emit| SS
    end

    PM -->|subprocess.Popen| ProcessB

    SG -->|"POST /execute"| ES
    SG -->|"POST /stop/{plan_id}"| ES
    SG -->|"GET /status/{plan_id}"| ES

    EG -->|"POST /callback/snapshot"| CB
    EG -->|"POST /callback/completed"| CB

    style ProcessA fill:#e8f4e8,stroke:#2d7d2d
    style ProcessB fill:#e8e8f4,stroke:#2d2d7d
    style MB fill:#fff3cd,stroke:#856404
    style SF fill:#f8d7da,stroke:#842029
```

---

## 2. 完整执行流程（Mode 3 — V3 并行模式）

```mermaid
sequenceDiagram
    participant User as 用户
    participant SM as Supervisor call_model
    participant DT as dynamic_tools_node
    participant PM as ProcessManager
    participant ES as Executor Server
    participant EG as Executor Graph
    participant MB as Mailbox
    participant PL as Planner

    User->>SM: 用户任务
    SM->>SM: LLM 决定 Mode 3
    SM->>DT: tool_call: call_planner(task_core)
    DT->>PL: run_planner()
    PL-->>DT: plan JSON
    DT-->>SM: plan ready

    SM->>SM: LLM 决定调用 call_executor
    SM->>DT: tool_call: call_executor(plan_id)
    DT->>ES: POST /execute {plan_json, plan_id, callback_url}

    Note over ES: 创建 asyncio.Task
    ES-->>DT: {plan_id, status: "accepted"}
    DT-->>SM: Executor 已派发

    Note over SM: Supervisor 不阻塞，<br/>可以继续其他工作

    loop 每 N 次工具调用
        EG->>EG: tools_node 执行工具
        EG->>MB: POST /callback/snapshot<br/>{tool_rounds, step_progress}
        Note over MB: 快照进入邮箱<br/>Supervisor 按需查看
    end

    SM->>SM: LLM 决定 get_executor_result
    SM->>DT: tool_call: get_executor_result(plan_id)

    loop 轮询邮箱
        DT->>MB: 检查 completion
        MB-->>DT: 尚未完成
    end

    EG->>EG: 执行完成
    EG->>MB: POST /callback/completed<br/>{status, summary, updated_plan_json}

    DT->>MB: 检查 completion
    MB-->>DT: has_completion = true
    DT-->>SM: [EXECUTOR_RESULT] {status, summary, plan}

    SM->>SM: LLM 合成最终答案
    SM-->>User: 任务完成
```

---

## 3. 快照上报流程（轻量级，不阻塞 ReAct）

```mermaid
flowchart TD
    A[tools_node 执行完毕] --> B{snapshot_interval > 0?}
    B -->|No| C[正常返回<br/>继续 ReAct]
    B -->|Yes| D{tool_rounds % interval == 0?}
    D -->|No| C
    D -->|Yes| E[_extract_lightweight_snapshot]
    E --> F[从 plan JSON 提取:<br/>已完成步骤数<br/>当前步骤<br/>tool_rounds]
    F --> G["asyncio.create_task(<br/>callback(snapshot_payload))<br/>fire-and-forget"]
    G --> C

    G -.->|HTTP POST| H[Supervisor Callback Server]
    H --> I[写入 Mailbox]
    I --> J["邮箱累积<br/>(Supervisor 稍后按需查看)"]

    style G fill:#fff3cd,stroke:#856404
    style I fill:#e8f4e8,stroke:#2d7d2d
```

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

    SM->>DT: tool_call: stop_executor(plan_id, reason)
    DT->>ES: POST /stop/{plan_id} {reason}
    ES->>SF: stop_events[plan_id].set()
    ES-->>DT: {acknowledged: true}
    DT-->>SM: 停止信号已发送

    Note over EG: Executor 仍在运行,<br/>下一次 call_executor 时检查

    EG->>SF: stop_events[plan_id].is_set()?
    SF-->>EG: True
    EG->>EG: 生成部分完成的结果:<br/>status=failed<br/>summary="Stopped by Supervisor"
    Note over EG: AIMessage 无 tool_calls<br/>→ route → __end__
    EG->>ES: run_executor 返回 ExecutorResult
    ES->>SM: POST /callback/completed {status: failed, summary, ...}

    Note over SM: Supervisor 在 get_executor_result 中收到结果
```

---

## 5. 邮箱模式（Mailbox Pattern）

```mermaid
flowchart LR
    subgraph Executor Process
        EG[Executor Graph]
    end

    subgraph Mailbox["Mailbox (in-memory, per plan_id)"]
        S1[snapshot #1<br/>tool_rounds=3]
        S2[snapshot #2<br/>tool_rounds=6]
        S3[snapshot #3<br/>tool_rounds=9]
        C["completion<br/>status=completed<br/>summary=...<br/>updated_plan_json=..."]
    end

    subgraph Supervisor Process
        WFE[get_executor_result<br/>polls has_completion]
        GES["GET /status/{plan_id}<br/>quick overview"]
        GMS["GET /mailbox/{plan_id}<br/>full snapshots"]
    end

    EG -->|"POST /callback/snapshot"| S1
    EG -->|"POST /callback/snapshot"| S2
    EG -->|"POST /callback/snapshot"| S3
    EG -->|"POST /callback/completed<br/>(must-read)"| C

    WFE -->|polls| C
    GES -->|read latest| S3
    GMS -->|read all| S1
    GMS -->|read all| S2
    GMS -->|read all| S3

    style C fill:#f8d7da,stroke:#842029
    style S1 fill:#e8e8f4,stroke:#2d2d7d
    style S2 fill:#e8e8f4,stroke:#2d2d7d
    style S3 fill:#e8e8f4,stroke:#2d2d7d
```

**关键区分**：
- **蓝色** (snapshot) = 邮箱信息，Supervisor 按需查看
- **红色** (completion) = 必读，`get_executor_result` 阻塞直到收到

---

## 6. 进程生命周期管理

```mermaid
stateDiagram-v2
    [*] --> Spawning: call_model 首次调用<br/>且 enable_v3_parallel=true<br/>(lazy singleton)
    Spawning --> Starting: subprocess.Popen<br/>python -m src.executor_agent
    Starting --> Ready: GET /health → 200<br/>(轮询直到成功或超时)
    Starting --> Failed: 超时<br/>executor_startup_timeout
    Failed --> Spawning: 重试（可选）

    Ready --> Executing: POST /execute
    Executing --> Executing: POST /execute (多个任务)
    Executing --> Ready: 任务完成

    Ready --> ShuttingDown: Supervisor 关闭<br/>POST /shutdown
    Executing --> ShuttingDown: Supervisor 关闭

    ShuttingDown --> Stopped: 进程退出
    ShuttingDown --> Terminated: 10s 超时 → process.terminate()
    Terminated --> Killed: 5s 超时 → process.kill()
    Killed --> [*]
    Stopped --> [*]
```

---

## 7. V2 vs V3 对比

```mermaid
flowchart LR
    subgraph V2["V2 — 单进程同步"]
        V2_SM[Supervisor] -->|await run_executor| V2_EG[Executor]
        V2_EG -->|阻塞返回| V2_SM
        V2_SM -->|await run_planner| V2_PG[Planner]
        V2_PG -->|阻塞返回| V2_SM
    end

    subgraph V3["V3 — 双进程异步"]
        V3_SM[Supervisor] -->|await run_planner| V3_PG[Planner]
        V3_PG -->|阻塞返回| V3_SM

        V3_SM -->|"POST /execute"| V3_ES[Executor Server]
        V3_ES -->|asyncio.Task| V3_EG[Executor Graph]
        V3_EG -->|"callback/snapshot"| V3_MB[Mailbox]
        V3_EG -->|"callback/completed"| V3_MB
        V3_SM -->|"get_executor_result<br/>poll mailbox"| V3_MB
    end

    style V2 fill:#f0f0f0,stroke:#666
    style V3 fill:#e8f4e8,stroke:#2d7d2d
```

**核心区别**：
- V2: `call_executor` **阻塞**等 Executor 完成
- V3: `call_executor` **立即返回**，`get_executor_result` **按需**等待
- V3 可用 `check_executor_progress` 查看实时快照进度
- V3 快照在 ReAct 循环中 **异步上报**，不中断执行
- V3 软中断通过 **asyncio.Event** 实现，Executor **优雅退出**

---

## 8. Supervisor LLM 工具决策树（V3 模式）

```mermaid
flowchart TD
    A[Supervisor call_model] --> B{LLM 分析任务}
    B -->|Mode 1| C[Direct Response]
    B -->|Mode 2| D[call_executor<br/>task_description]
    B -->|Mode 3| E[call_planner → call_executor<br/>plan_id]

    D --> F[get_executor_result<br/>poll until done]
    E --> F

    F --> G{结果?}
    G -->|completed| H[合成最终答案]
    G -->|failed + 可重规划| I[call_planner → call_executor]
    G -->|failed + 超过 MAX_REPLAN| J[返回失败分析]

    subgraph Optional["可选：并行监控"]
        K[check_executor_progress<br/>查看实时快照]
        L[stop_executor<br/>软中断]
    end

    F -.->|等待期间| K
    F -.->|需要中断时| L

    style Optional fill:#fff3cd,stroke:#856404,stroke-dasharray: 5 5
```

---

## 9. 数据流：从 Executor 到 Supervisor

```mermaid
flowchart TD
    subgraph Executor Process
        A[call_executor node] -->|LLM response| B{has tool_calls?}
        B -->|Yes| C[tools_node]
        C --> D{snapshot interval?}
        D -->|Yes, hit| E["snapshot payload<br/>{plan_id, tool_rounds, steps}"]
        D -->|No| F[route_after_tools → call_executor]
        B -->|No| G["final result<br/>{status, summary, plan}"]
        E --> F
    end

    subgraph HTTP
        E -.->|"fire-and-forget<br/>POST /callback/snapshot"| H[Callback Server]
        G -.->|"POST /callback/completed<br/>(must-read)"| H
    end

    subgraph Supervisor Process
        H --> I[Mailbox]
        I --> J{item type}
        J -->|snapshot| K["邮箱 (read=false)<br/>Supervisor 按需查看"]
        J -->|completion| L["必读 (flag)<br/>get_executor_result 阻塞等待"]
        K --> M["GET /mailbox/{plan_id}<br/>快速查看最新快照"]
        L --> N["[EXECUTOR_RESULT]<br/>dynamic_tools_node 解析"]
        N --> O[更新 PlannerSession<br/>replan_count 等状态]
    end

    style E fill:#e8e8f4,stroke:#2d2d7d
    style G fill:#f8d7da,stroke:#842029
    style L fill:#f8d7da,stroke:#842029
```
