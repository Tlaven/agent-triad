# V3+ 并发执行最终方案

## 研究总结

### LangGraph 并行执行机制

通过 Tavily 搜索和 Context7 文档查询，发现 LangGraph 支持以下并行机制：

1. **Send API**：动态并行执行
   ```python
   from langgraph.types import Send

   def route_to_workers(state):
       return [Send("worker", {"task": t}) for t in state["tasks"]]

   builder.add_conditional_edges("supervisor", route_to_workers, ["worker"])
   ```

2. **静态并行节点**：多边并行
   ```python
   builder.add_edge(START, "agent1")
   builder.add_edge(START, "agent2")
   builder.add_edge(START, "agent3")
   ```

3. **Streaming**：实时输出
   ```python
   for chunk in graph.stream(input, config, stream_mode="tasks"):
       # 实时接收节点开始/结束事件
   ```

4. **Human-in-the-Loop**：中断和恢复
   ```python
   # 支持在节点执行中暂停并获取用户输入
   ```

### 关键发现

**LangGraph 的限制**：
- 图执行期间是**同步阻塞**的（即使在图内部并行执行）
- 无法在 `graph.stream()` 循环中接收新的用户输入
- Send API 只能在**同一个图内部**实现并行

**我们的需求挑战**：
- ❌ LangGraph **不支持**：在图执行期间接收新消息
- ❌ 无法实现：一边 Executor 在图内执行，一边 Supervisor 接收新用户输入

## 最终方案：双图异步架构

### 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    Main Application                      │
│                                                           │
│  ┌──────────────────────┐         ┌────────────────────┐│
│  │ Supervisor Graph     │         │ Executor Graph     ││
│  │  (ReAct Loop)        │         │  (Task Execution)  ││
│  │                      │         │                    ││
│  │  call_model          │         │  execute_steps     ││
│  │    ↓                 │         │    ↓               ││
│  │  call_executor ───────┐  ─────→│  run_tools         ││
│  │    ↓                 │  │     │    ↓               ││
│  │  return_answer       │  └────→│  return_result     ││
│  └──────────────────────┘         └────────────────────┘│
│          ↓                                 ↑            │
│    用户交互                          后台异步执行        │
└─────────────────────────────────────────────────────────┘
                      ↑         ↓
                 共享状态存储（Checkpoint）
```

### 核心机制

#### 1. 异步任务管理器

```python
# src/common/executor_manager.py

import asyncio
import uuid
from dataclasses import dataclass
from typing import Dict
from langgraph.checkpoint.memory import MemorySaver

@dataclass
class ExecutorTask:
    task_id: str
    plan_id: str
    status: "pending|running|completed|failed"
    result: dict | None = None
    error: str | None = None

class ExecutorManager:
    """管理 Executor 后台任务"""

    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_data: Dict[str, ExecutorTask] = {}
        self.checkpointer = MemorySaver()

    async def start_executor(
        self,
        plan_json: str,
        context: "Context",
    ) -> str:
        """启动 Executor（非阻塞）"""
        task_id = f"exec_{uuid.uuid4().hex[:8]}"
        plan_id = json.loads(plan_json).get("plan_id", "unknown")

        # 创建任务数据
        task_data = ExecutorTask(
            task_id=task_id,
            plan_id=plan_id,
            status="pending",
        )
        self.task_data[task_id] = task_data

        # 创建后台任务（不等待）
        task = asyncio.create_task(
            self._run_executor_wrapper(task_id, plan_json, context)
        )
        self.active_tasks[task_id] = task

        return task_id

    async def _run_executor_wrapper(
        self,
        task_id: str,
        plan_json: str,
        context: "Context",
    ):
        """包装 Executor 执行"""
        task_data = self.task_data[task_id]
        task_data.status = "running"

        try:
            # 调用 Executor 图（使用独立的 thread_id）
            config = {
                "configurable": {
                    "thread_id": f"executor_{task_id}"
                }
            }

            from src.executor_agent.graph import executor_graph

            result = await executor_graph.ainvoke(
                {"plan_json": plan_json},
                config,
            )

            # 保存结果
            task_data.status = "completed"
            task_data.result = result

        except Exception as e:
            task_data.status = "failed"
            task_data.error = str(e)

    def get_task_status(self, task_id: str) -> dict:
        """查询任务状态"""
        if task_id not in self.task_data:
            return {"error": "Task not found"}

        task_data = self.task_data[task_id]
        active_task = self.active_tasks.get(task_id)

        return {
            "task_id": task_data.task_id,
            "plan_id": task_data.plan_id,
            "status": task_data.status,
            "done": active_task.done() if active_task else True,
            "result": task_data.result if task_data.status == "completed" else None,
            "error": task_data.error,
        }

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id in self.active_tasks:
            self.active_tasks[task_id].cancel()
            del self.active_tasks[task_id]
            self.task_data[task_id].status = "cancelled"
            return True
        return False

# 全局单例
executor_manager = ExecutorManager()
```

#### 2. Supervisor 工具改造

```python
# src/supervisor_agent/tools.py

@tool
async def call_executor_async(
    state: Annotated[State, InjectedState],
    runtime_context: Annotated[Context, InjectedContext],
) -> str:
    """非阻塞启动 Executor 执行

    使用场景：Mode 3 - 需要长时间执行的任务

    Returns:
        task_id: 后台任务 ID，可用于查询进度
    """

    # 获取 plan_json
    plan_json = state.get("plan_json")
    if not plan_json:
        return "错误：当前没有 plan_json，请先调用 call_planner"

    # 启动后台任务
    from src.common.executor_manager import executor_manager

    task_id = await executor_manager.start_executor(
        plan_json,
        runtime_context,
    )

    return f"""Executor 已启动（后台执行模式）

任务 ID: {task_id}
计划 ID: {json.loads(plan_json).get('plan_id')}

使用 get_executor_status 工具查询执行进度。
在执行期间，你可以继续接收用户输入或下达其他任务。
"""

@tool
def get_executor_status(
    task_id: str,
) -> str:
    """查询 Executor 后台任务的状态和进度

    Args:
        task_id: call_executor_async 返回的任务 ID

    Returns:
        当前状态、进度、结果（如果已完成）
    """

    from src.common.executor_manager import executor_manager

    status_info = executor_manager.get_task_status(task_id)

    if "error" in status_info:
        return f"错误：{status_info['error']}"

    # 格式化输出
    output = f"""任务状态报告

任务 ID: {status_info['task_id']}
计划 ID: {status_info['plan_id']}
状态: {status_info['status']}
是否完成: {status_info['done']}
"""

    if status_info['status'] == 'completed' and status_info['result']:
        output += f"\n执行结果：\n{status_info['result']}"

    if status_info['error']:
        output += f"\n错误信息：\n{status_info['error']}"

    return output

@tool
def cancel_executor(
    task_id: str,
) -> str:
    """取消正在运行的 Executor 任务

    Args:
        task_id: 要取消的任务 ID

    Returns:
        操作结果
    """

    from src.common.executor_manager import executor_manager

    success = executor_manager.cancel_task(task_id)
    if success:
        return f"任务 {task_id} 已取消"
    else:
        return f"无法取消任务 {task_id}（可能已完成或不存在）"
```

#### 3. Supervisor 工具注册

```python
# src/supervisor_agent/tools.py

async def get_tools(runtime_context: Context | None = None) -> List[Callable[..., Any]]:
    """主 ReAct 循环返回的工具集。"""
    if runtime_context is None:
        runtime_context = Context()

    return [
        _build_call_planner_tool(runtime_context),
        # 保留原有的同步 call_executor（Mode 2 短任务）
        _build_call_executor_tool(runtime_context),
        # 新增异步工具（Mode 3 长任务）
        call_executor_async,
        get_executor_status,
        cancel_executor,
        _build_get_executor_full_output_tool(),
        web_search_tavily,
    ]
```

### 使用场景示例

#### 场景 1：后台执行 + 实时查询

```python
# 用户：创建一个复杂的 todo app
# Supervisor: 使用 call_executor_async
"Executor 已启动（后台执行模式）
任务 ID: exec_abc123
计划 ID: plan_v1"

# 用户：进度如何？
# Supervisor: 使用 get_executor_status
"任务状态：running
已完成 3/5 步骤"

# 用户：把 step_5 改成用 JavaScript
# Supervisor: 可以立即响应，因为 Executor 在后台运行
"好的，我可以帮你修改计划。
但当前 exec_abc123 还在运行，是否先取消它？"
```

#### 场景 2：并发执行多个任务

```python
# 用户：同时处理 3 个不同的 API 端点
# Supervisor: 连续调用 3 次 call_executor_async
task1 = await call_executor_async(plan_api_1)  # exec_001
task2 = await call_executor_async(plan_api_2)  # exec_002
task3 = await call_executor_async(plan_api_3)  # exec_003

# 3 个 Executor 在后台并发运行
# Supervisor 继续响应其他请求

# 用户：查看所有任务
# Supervisor: 调用 get_executor_status
status1 = get_executor_status("exec_001")  # completed
status2 = get_executor_status("exec_002")  # running
status3 = get_executor_status("exec_003")  # running
```

### LangSmith 可视化

#### Supervisor 图轨迹
```
Run 12345 (Supervisor Thread)
├─ call_model (理解用户意图)
├─ call_planner (生成计划)
├─ call_executor_async (启动 Executor) ⚡ 立即返回
├─ call_model (继续与用户交互)
├─ get_executor_status (查询进度)
└─ call_model (基于进度给出反馈)
```

#### Executor 图轨迹（独立线程）
```
Run 12346 (Executor Thread: exec_001)
├─ execute_step_1 (0:00 - 0:02)
├─ execute_step_2 (0:02 - 0:05)
├─ execute_step_3 (0:05 - 0:08)
└─ return_result (0:08)
```

**关键**：两个图的轨迹是**独立记录**的，但可以通过 `task_id` 关联。

### 实施步骤

#### Phase 1：基础架构（1-2 天）
- [ ] 创建 `src/common/executor_manager.py`
- [ ] 实现 `ExecutorManager` 类
- [ ] 添加 `call_executor_async` 工具
- [ ] 添加 `get_executor_status` 工具
- [ ] 添加 `cancel_executor` 工具
- [ ] 单元测试

#### Phase 2：集成测试（1 天）
- [ ] 端到端测试：启动 Executor → 查询状态 → 获取结果
- [ ] 并发测试：同时运行多个 Executor
- [ ] 取消测试：启动后取消任务
- [ ] 异常处理测试：Executor 失败场景

#### Phase 3：优化和文档（1 天）
- [ ] 添加重试机制
- [ ] 添加超时控制
- [ ] 添加执行日志
- [ ] 更新 CLAUDE.md 文档
- [ ] 使用示例

### 优势对比

| 特性 | 当前 V3 | V3+ 双图架构 |
|------|---------|-------------|
| 并发执行 | ❌ 批次串行 | ✅ 真正后台并发 |
| 用户交互 | ❌ 阻塞式 | ✅ 完全非阻塞 |
| LangSmith 可见 | ⚠️ 单线程轨迹 | ✅ 双线程独立轨迹 |
| 实时监督 | ❌ 无法查询 | ✅ 随时查询状态 |
| 任务管理 | ❌ 无法取消 | ✅ 可取消/重启 |
| 实现复杂度 | 中 | 中 |
| 与现有代码兼容 | - | ✅ 保留同步接口 |

### 关键设计决策

1. **保留同步接口**：
   - `call_executor`（同步）用于 Mode 2 短任务
   - `call_executor_async`（异步）用于 Mode 3 长任务
   - Supervisor 根据任务类型选择

2. **独立线程隔离**：
   - 每个 Executor 使用独立的 `thread_id`
   - 避免状态冲突
   - 支持 checkpoint 恢复

3. **全局任务管理**：
   - `ExecutorManager` 作为全局单例
   - 跨请求保持任务状态
   - 支持任务查询和取消

4. **LangSmith 集成**：
   - Supervisor 和 Executor 分别记录轨迹
   - 通过 `task_id` 关联
   - 可以看到完整的执行时间线

## 总结

**可行性**：✅ 完全可行
- 不依赖 LangGraph 的 Send API（避免图内限制）
- 使用 Python 原生 asyncio 实现真正的后台执行
- 完全兼容现有架构
- LangSmith 可以看到双线程并发轨迹

**预计时间**：3-4 天完成基础实现和测试

**下一步**：开始实施 Phase 1
