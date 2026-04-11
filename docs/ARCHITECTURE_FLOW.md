# AgentTriad 完整流程图

## 系统架构总览

```mermaid
graph TB
    User[👤 用户] -->|输入任务| Supervisor[🎛️ Supervisor Agent<br/>主循环 + ReAct]

    Supervisor -->|模式 A<br/>直接回复| Direct[💬 直接回答]
    Supervisor -->|模式 B<br/>简单任务| Executor[🔧 Executor Agent<br/>工具执行]
    Supervisor -->|模式 C<br/>复杂任务| Planner[📋 Planner Agent<br/>生成计划]

    Planner -->|Plan JSON| Supervisor
    Supervisor -->|执行计划| Executor

    Executor -->|工具调用| Tools[🔨 工具层]
    Tools -->|写入文件<br/>运行命令<br/>等| Workspace[💾 工作区]

    Supervisor -->|最终答案| User

    style Supervisor fill:#ff9999
    style Planner fill:#99ccff
    style Executor fill:#99ff99
    style User fill:#ffcc99
```

---

## 详细执行流程

```mermaid
flowchart TD
    Start([用户输入任务]) --> Supervisor[Supervisor 接收输入]

    Supervisor --> Analyze[意图分析<br/>思考任务需求]

    Analyze --> Decision{任务类型判断}

    Decision -->|简单问答| ModeA[模式 A: 直接回复]
    Decision -->|1-2个工具| ModeB[模式 B: Tool-use ReAct]
    Decision -->|多步骤/复杂| ModeC[模式 C: 计划执行]

    ModeA --> Answer1[组织语言回答]
    Answer1 --> End1([返回用户])

    ModeB --> CallExec1[调用 call_executor]
    CallExec1 --> ExecSync[Executor 同步执行]
    ExecSync --> Result1{执行结果}
    Result1 -->|成功| Answer2[汇总答案]
    Result1 -->|失败<br/>可修复| Replan[重规划循环]
    Result1 -->|失败<br/>不可修复| Error1[失败分析]
    Answer2 --> End2([返回用户])
    Replan --> ModeC
    Error1 --> End3([终止])

    ModeC --> CallPlanner[调用 call_planner]
    CallPlanner --> PlanGen[Planner 生成 Plan JSON]
    PlanGen --> StorePlan[存储到 planner_session]

    StorePlan --> CallExec2{执行模式选择}

    CallExec2 -->|同步| CallExecSync[调用 call_executor]
    CallExec2 -->|异步| CallExecAsync[调用 call_executor_async]

    CallExecSync --> ExecSync2[Executor 同步执行]
    ExecSync2 --> Result2{执行结果}

    CallExecAsync --> AsyncStart[后台异步启动]
    AsyncStart --> TaskID[返回 task_id]
    TaskID --> NonBlock[非阻塞，可继续]

    NonBlock --> CanQuery{用户操作?}
    CanQuery -->|查询状态| GetStatus[调用 get_executor_status]
    CanQuery -->|取消任务| CancelTask[调用 cancel_executor]
    CanQuery -->|新问题| Continue[继续处理其他请求]

    GetStatus --> Status[返回状态/进度]
    CancelTask --> Cancelled[任务已取消]

    Result2 -->|成功| Answer3[汇总答案]
    Result2 -->|失败| Replan2[触发重规划]

    Replan2 --> MaxReplan{达到重规划上限?}
    MaxReplan -->|否| CallPlanner
    MaxReplan -->|是| Error2[终止并说明原因]

    Answer3 --> End4([返回用户])

    style Supervisor fill:#ffcccc
    style Planner fill:#ccccff
    style Executor fill:#ccffcc
    style CallExecAsync fill:#ffcc99
```

---

## V3+ 异步并发模式流程

```mermaid
sequenceDiagram
    participant User as 👤 用户
    participant Supervisor as 🎛️ Supervisor
    participant Planner as 📋 Planner
    participant ExecutorAsync as ⚡ Executor Async
    participant ExecutorManager as 📦 ExecutorManager
    participant Workspace as 💾 工作区

    Note over User,Workspace: V3+ 异步并发模式

    User->>Supervisor: 复杂任务（如：处理100个文件）
    Supervisor->>Supervisor: 分析：长时间任务 → 适合异步

    alt 需要计划
        Supervisor->>Planner: call_planner(任务描述)
        Planner-->>Supervisor: Plan JSON
    end

    Supervisor->>ExecutorAsync: call_executor_async(Plan)
    ExecutorAsync->>ExecutorManager: start_executor(Plan)
    ExecutorManager-->>ExecutorAsync: task_id
    ExecutorAsync-->>Supervisor: task_id (立即返回)

    Supervisor-->>User: ✅ 任务已启动<br/task_id: task_xxx<br/>后台执行中...

    Note over User,Workspace: 非阻塞 - 用户可以继续提问

    User->>Supervisor: 其他问题
    Supervisor-->>User: 立即响应

    User->>Supervisor: 任务进度如何？
    Supervisor->>ExecutorAsync: get_executor_status(task_id)
    ExecutorAsync->>ExecutorManager: get_task_status(task_id)
    ExecutorManager-->>ExecutorAsync: 状态/进度
    ExecutorAsync-->>Supervisor: Running: 45/100

    alt 用户想取消
        User->>Supervisor: 取消任务
        Supervisor->>ExecutorAsync: cancel_executor(task_id)
        ExecutorAsync->>ExecutorManager: cancel_task(task_id)
        ExecutorManager-->>Supervisor: 已取消
        Supervisor-->>User: ✅ 任务已取消
    else 任务完成
        ExecutorManager->>Workspace: 执行任务
        ExecutorManager-->>Supervisor: 完成
        Supervisor->>ExecutorAsync: get_executor_full_output
        ExecutorAsync-->>Supervisor: 完整结果
        Supervisor-->>User: 📊 执行结果
    end
```

---

## 三种模式决策树

```mermaid
graph TD
    Start([Supervisor 接收任务]) --> Analyze[分析任务]

    Analyze --> Q1{需要外部信息<br/>或执行操作?}
    Q1 -->|否| Direct[模式 A: 直接回复]
    Q1 -->|是| Q2{目标明确<br/>1次Executor?}

    Q2 -->|是| Simple[简单任务]
    Q2 -->|否| Complex[复杂任务]

    Simple --> Q3{预计时间}
    Q3 -->|< 10秒| Sync[call_executor 同步]
    Q3 -->|> 30秒| Async[call_executor_async 异步]
    Q3 -->|10-30秒| User{用户需要<br/>立即结果?}
    User -->|是| Sync
    User -->|否| Async

    Complex --> Plan[模式 C: 计划执行]
    Plan --> PlanExec[call_planner → Plan JSON]
    PlanExec --> Async

    Direct --> End([返回答案])
    Sync --> Execute[Executor 执行]
    Async --> StartAsync[启动后台任务]
    StartAsync --> End

    style Direct fill:#90EE90
    style Sync fill:#87CEEB
    style Async fill:#FFD700
    style Plan fill:#DDA0DD
```

---

## 重规划机制流程

```mermaid
flowchart TD
    Exec([Executor 执行]) --> Check{执行结果}
    Check -->|completed| Success([成功])
    Check -->|paused| Paused([检查点处理])
    Check -->|failed| Failed([失败])

    Failed --> Reason{失败原因}
    Reason -->|可修复| Fixable[可修复失败]
    Reason -->|不可修复| Unfixable[不可修复]

    Fixable --> PlanCheck{当前模式}
    PlanCheck -->|Mode B| Upgrade[升级到 Mode C<br/>重新规划]
    PlanCheck -->|Mode C| Replan[重规划当前计划]

    Upgrade --> Count{重规划次数}
    Replan --> Count

    Count -->|< MAX_REPLAN| CallPlanner[调用 call_planner<br/>重新规划]
    Count -->|≥ MAX_REPLAN| Terminate([终止<br/>说明失败原因])

    CallPlanner --> Retry([重新执行])
    Terminate --> End([结束])

    Unfixable --> End

    style Success fill:#90EE90
    style Failed fill:#FFB6C1
    style Terminate fill:#FF6B6B
```

---

## 工具调用完整示例

```mermaid
sequenceDiagram
    participant User as 👤 用户
    participant Supervisor as 🎛️ Supervisor
    participant Planner as 📋 Planner
    participant Executor as 🔧 Executor
    participant Tools as 🔨 工具

    Note over User,Tools: 场景：批量处理 50 个文件

    User->>Supervisor: 处理 workspace/ 下所有文件

    Supervisor->>Supervisor: 分析：<br/>- 多步骤任务<br/>- 可能耗时较长

    Supervisor->>Planner: call_planner("处理所有文件")
    Planner-->>Supervisor: Plan JSON:<br/>{<br/>  "goal": "处理所有文件",<br/>  "steps": [<br/>    {"intent": "列举文件"},<br/>    {"intent": "逐个处理"},<br/>    {"intent": "生成报告"}<br/>  ]<br/>}

    Supervisor->>Supervisor: 判断：<br/>- 可能超过 30 秒<br/>- 适合异步模式

    Supervisor->>Executor: call_executor_async(Plan)
    Executor-->>Supervisor: task_id: task_20260411_102045

    Supervisor-->>User: ✅ 任务已启动（后台）<br/>ID: task_xxx<br/>我会继续处理其他问题

    User->>Supervisor: 这些文件里有什么关键词？

    Supervisor->>Supervisor: Mode A - 直接回答<br/>无需工具
    Supervisor-->>User: 💬 根据我的知识，通常有...

    User->>Supervisor: 任务完成了吗？
    Supervisor->>Executor: get_executor_status(task_xxx)
    Executor-->>Supervisor: Running: 25/50 completed

    Note over Executor: 后台继续处理...

    User->>Supervisor: 现在呢？
    Supervisor->>Executor: get_executor_status(task_xxx)
    Executor-->>Supervisor: ✅ completed!

    Supervisor->>Executor: get_executor_full_output
    Executor-->>Supervisor: 完整结果：<br/>{<br/>  "files_processed": 50,<br/>  "keywords_found": [...]<br/>}

    Supervisor-->>User: 📊 处理完成！<br/>共处理 50 个文件<br/>找到关键词：...
```

---

## 状态流转图

```mermaid
stateDiagram-v2
    [*] --> Idle: 用户输入

    Idle --> Analyzing: Supervisor 分析
    Analyzing --> Planning: 模式 C（复杂任务）
    Analyzing --> Executing: 模式 B（简单任务）
    Analyzing --> Responding: 模式 A（问答）

    Planning --> Planned: Plan 已生成
    Planned --> Executing: 执行计划

    Executing --> SyncExec: 同步执行
    Executing --> AsyncExec: 异步执行

    SyncExec --> Completed: 完成
    SyncExec --> Failed: 失败

    AsyncExec --> Background: 后台运行
    Background --> Completed: 完成
    Background --> Cancelled: 已取消

    Failed --> Replanning: 重规划
    Replanning --> Planning: 重新规划
    Replanning --> Terminate: 达到上限

    Completed --> Responding: 汇总答案
    Cancelled --> Responding: 说明取消
    Terminate --> [*]: 结束
    Responding --> [*]
```

---

## 核心数据结构

```mermaid
classDiagram
    class State {
        +messages List
        +planner_session PlannerSession
        +supervisor_decision SupervisorDecision
        +replan_count int
        +executors List
        +is_last_step bool
    }

    class PlannerSession {
        +session_id str
        +plan_json str
        +last_executor_status str
        +last_executor_error str
        +last_executor_summary str
        +last_executor_full_output str
        +planner_history_by_plan_id dict
        +planner_last_version_by_plan_id dict
        +planner_last_output_by_plan_id dict
        +plan_archive_by_plan_id dict
    }

    class PlanJSON {
        +plan_id str
        +version int
        +goal str
        +steps List
    }

    class Step {
        +step_id str
        +intent str
        +expected_output str
        +status str
        +result_summary str
        +failure_reason str
    }

    State --> PlannerSession
    PlannerSession --> PlanJSON
    PlanJSON --> Step
```

---

## 使用流程图

```mermaid
graph LR
    A[📝 编写任务描述] --> B[🎯 选择执行模式]
    B --> C[⚙️ 配置参数]

    C --> D[🚀 提交 Supervisor]

    D --> E{任务类型?}

    E -->|简单问答| F[💬 直接回答]
    E -->|1-2个工具| G[🔧 同步执行]
    E -->|多步骤| H[📋 计划 → 执行]

    H --> I[生成计划]
    I --> J{任务时长?}

    J -->|< 10秒| K[🔄 同步执行]
    J -->|> 30秒| L[⚡ 异步执行]
    J -->|10-30秒| M{用户需要<br/>立即结果?}

    M -->|是| K
    M -->|否| L

    K --> N[✅ 获得结果]
    L --> O[📝 获得 task_id]

    O --> P{用户操作}
    P -->|查询| Q[📊 查看状态]
    P -->|取消| R[⏹️ 取消任务]
    P -->|等待| S[⏳ 等待完成]

    Q --> T[📈 继续运行]
    R --> U[🛑 任务终止]
    S --> N

    style F fill:#87CEEB
    style K fill:#87CEEB
    style L fill:#FFD700
    style O fill:#FFD700
```

---

## 完整工作流总结

### 阶段 1：接收与分析
1. **用户** → **Supervisor**：输入任务
2. **Supervisor** 分析任务意图和复杂度

### 阶段 2：模式选择
| 模式 | 条件 | 行为 |
|------|------|------|
| A | 无需外部信息 | 直接回答 |
| B | 1-2个工具，< 10秒 | 同步调用 Executor |
| C | 多步骤，> 30秒 | 先计划 → 后台异步执行 |

### 阶段 3：执行
**同步执行**：
```
Supervisor → Executor → 工具 → 立即返回结果
```

**异步执行**：
```
Supervisor → call_executor_async → 后台启动
         → 返回 task_id（非阻塞）
用户 → 继续提问 / 查询状态 / 取消任务
```

### 阶段 4：结果返回
- 收集执行结果
- 汇总成最终答案
- 返回给用户

---

**所有流程图均基于实际代码实现！** 📊
