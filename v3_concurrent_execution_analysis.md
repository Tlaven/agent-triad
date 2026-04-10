# V3+ 并发执行与 LangSmith 实时可视化分析

## 用户需求

1. **并发可见性**：在 LangSmith 上同时看到 Supervisor 和 Executor 运行
2. **非阻塞交互**：一边与 Supervisor 聊天，一边 Executor 在后台执行
3. **实时监督**：Supervisor 可以监控 Executor 进度并干预
4. **动态调整**：可以随时调整计划或下达新计划

## 当前架构限制

### 问题 1：阻塞式调用
```python
# src/supervisor_agent/tools.py (line 331)
executor_result = await run_executor(
    plan_json,
    context=runtime_context,
)
```
- Supervisor **等待** Executor 完成才继续
- LangSmith 只能看到串行的执行轨迹
- 用户无法在 Executor 执行期间与 Supervisor 交互

### 问题 2：单线程执行模型
- 当前 V3 "并行" 只是批次的并发（`asyncio.gather`）
- Supervisor 和 Executor 仍然是串行的
- 没有实现真正的"后台执行"

## LangGraph 的并发能力

### 1. Send API - 并行节点执行
```python
from langgraph.types import Send

def fan_out_executors(state: OverallState) -> list[Send]:
    return [Send("executor_node", {"plan": p}) for p in state["plans"]]

builder.add_conditional_edges("supervisor", fan_out_executors, ["executor_node"])
```
**优势**：
- ✅ 真正的并行执行
- ✅ LangSmith 可以看到多个并发流
- **限制**：所有节点必须是同一图的节点

### 2. stream() - 实时输出
```python
for event in graph.stream(inputs, config):
    print(f"Node: {event}")
```
**优势**：
- ✅ 实时查看每个节点输出
- ✅ LangSmith 自动记录每个事件
- **限制**：需要在主循环中调用 stream()

### 3. thread_id - 状态隔离
```python
config = {"configurable": {"thread_id": str(uuid.uuid4())}}
```
**优势**：
- ✅ 支持多个并发会话
- ✅ 每个会话状态独立
- **用途**：可以为 Executor 创建独立的 thread

## 实现方案

### 方案 A：使用 Send API（推荐用于真正的并发）

#### 架构
```
Supervisor Node
    ↓
[条件路由]
    ↓
    ├─→ Executor Node 1 (并行)
    ├─→ Executor Node 2 (并行)
    └─→ Executor Node 3 (并行)
         ↓
    [聚合节点]
         ↓
    Supervisor Node (继续)
```

#### 代码结构
```python
# src/supervisor_agent/graph.py
from langgraph.types import Send

class State(TypedDict):
    messages: list
    executor_tasks: list[dict]  # 待执行的 Executor 任务
    executor_results: dict      # 执行结果

def route_to_executors(state: State) -> list[Send]:
    """将任务分发给多个 Executor 实例"""
    return [
        Send("run_executor", {"task": task})
        for task in state["executor_tasks"]
    ]

async def run_executor_node(state: dict) -> dict:
    """Executor 节点（非阻塞）"""
    task = state["task"]
    result = await run_executor(task["plan_json"], context=task["context"])
    return {"executor_results": {task["task_id"]: result}}

def aggregate_results(state: State) -> dict:
    """聚合 Executor 结果"""
    all_results = state["executor_results"]
    # 合并结果逻辑
    return {"messages": [汇总结果]}

# 图构建
builder = StateGraph(State)
builder.add_node("supervisor", call_model)
builder.add_node("run_executor", run_executor_node)
builder.add_node("aggregate", aggregate_results)

builder.add_conditional_edges("supervisor", route_to_executors, ["run_executor"])
builder.add_edge("run_executor", "aggregate")
builder.add_edge("aggregate", "supervisor")
```

#### LangSmith 可视化效果
```
Run 12345
├─ supervisor (0:00 - 0:01)
│   ├─ call_model
│   └─ route_to_executors
├─ run_executor_1 (0:01 - 0:05) ⏸️ 并发
├─ run_executor_2 (0:01 - 0:03) ⏸️ 并发
├─ run_executor_3 (0:01 - 0:04) ⏸️ 并发
└─ aggregate (0:05 - 0:06)
```

### 方案 B：使用 asyncio.create_task（后台任务）

#### 架构
```
Supervisor Node
    ├─→ 启动 Executor (create_task，立即返回)
    └─→ 继续处理用户输入
         ↓
    定期查询 Executor 状态
```

#### 代码结构
```python
# src/supervisor_agent/tools.py

# 全局任务管理
executor_tasks: dict[str, asyncio.Task] = {}

@tool
async def call_executor_async(
    state: Annotated[State, InjectedState],
    plan_id: str,
) -> str:
    """非阻塞启动 Executor"""
    task_id = f"exec_{uuid.uuid4().hex[:8]}"

    # 创建后台任务（不等待）
    task = asyncio.create_task(
        run_executor_background(plan_id, task_id)
    )
    executor_tasks[task_id] = task

    return f"Executor 已启动（task_id={task_id}），正在后台执行..."

async def run_executor_background(plan_id: str, task_id: str):
    """后台执行 Executor"""
    # 执行逻辑
    result = await run_executor(plan_json, context)
    # 保存结果到共享状态
    executor_results[task_id] = result

@tool
def check_executor_status(
    task_id: str,
) -> str:
    """查询 Executor 状态"""
    if task_id not in executor_tasks:
        return "任务不存在"
    task = executor_tasks[task_id]
    if task.done():
        return "已完成: " + str(executor_results[task_id])
    return "正在执行中..."
```

#### LangSmith 可视化效果
```
Run 12346
├─ supervisor (0:00 - 0:01)
│   ├─ call_executor_async ⚡ 启动后立即返回
│   └─ call_model (继续与用户聊天)
├─ supervisor (0:05 - 0:06)
│   └─ check_executor_status (查询进度)
└─ supervisor (0:10 - 0:11)
    └─ check_executor_status (获取结果)
```

### 方案 C：混合方案（推荐）

结合 Send API 和状态查询：

```python
class State(TypedDict):
    messages: list
    active_executors: dict[str, dict]  # 正在执行的 Executor
    executor_history: list[dict]       # 历史记录

def supervisor_should_continue(state: State) -> bool:
    """判断是否应该继续接收用户输入"""
    # 如果有 Executor 在运行，继续监听
    # 如果用户主动干预，可以中断 Executor
    return True

async def call_model(state: State, runtime: Runtime) -> dict:
    """Supervisor 主逻辑"""
    # 1. 检查 Executor 状态
    active = state["active_executors"]
    if active:
        status_report = f"当前有 {len(active)} 个任务正在执行"
        # 将状态注入到提示词
        system_prompt += f"\n\n{status_report}"

    # 2. 正常的模型调用
    response = await model.invoke(...)
    return {"messages": [response]}
```

## 与 LangSmith 的集成

### 1. 配置 LangSmith 追踪
```python
# langgraph.json
{
  "langsmith_tracing": true,
  "langchain_api_key": "lsv2_xxx"
}
```

### 2. 使用 stream() 实时推送
```python
async def main():
    config = {"configurable": {"thread_id": "user_123"}}

    async for event in graph.stream(
        {"messages": [user_message]},
        config,
        stream_mode="updates"  # 只显示更新
    ):
        # 实时推送到 LangSmith
        print(f"Event: {event}")
```

### 3. 在 LangSmith 中查看
访问 https://smith.langchain.com/ 可以看到：
- **并发轨迹**：多个 Executor 同时运行的节点
- **时间线**：每个节点的开始和结束时间
- **状态更新**：实时看到状态变化

## 对比当前 V3 实现

| 特性 | 当前 V3 | 方案 A (Send API) | 方案 B (create_task) | 方案 C (混合) |
|------|---------|-------------------|----------------------|---------------|
| 并发执行 | ❌ 批次并发 | ✅ 节点并发 | ✅ 后台任务 | ✅ 节点并发 |
| LangSmith 可见 | ⚠️ 部分可见 | ✅ 完全可见 | ⚠️ 需手动记录 | ✅ 完全可见 |
| 用户交互 | ❌ 阻塞 | ⚠️ 受限 | ✅ 完全非阻塞 | ✅ 完全非阻塞 |
| 实时监督 | ❌ | ✅ | ✅ | ✅ |
| 实现复杂度 | 中 | 高 | 低 | 高 |

## 推荐实施路径

### 阶段 1：方案 B（后台任务）- 1-2 天
**目标**：快速验证非阻塞执行
- [ ] 实现 `call_executor_async` 工具
- [ ] 实现 `check_executor_status` 工具
- [ ] 添加全局任务管理
- [ ] 测试基础场景

**优势**：
- 实现简单，改动最小
- 立即可用的非阻塞交互
- 验证用户需求

### 阶段 2：方案 A（Send API）- 3-5 天
**目标**：完整的 LangSmith 并发可视化
- [ ] 重构图结构，将 Executor 作为独立节点
- [ ] 实现 `route_to_executors` 路由
- [ ] 实现 `aggregate_results` 聚合
- [ ] 配置 LangSmith 追踪
- [ ] 测试并发场景

**优势**：
- 原生的 LangGraph 并发
- LangSmith 完美可视化
- 符合框架最佳实践

### 阶段 3：方案 C（混合优化）- 2-3 天
**目标**：最佳用户体验
- [ ] 结合方案 A 和 B 的优势
- [ ] 实现智能调度（根据任务类型选择执行方式）
- [ ] 添加 Executor 中断/恢复机制
- [ ] 优化状态管理

## 关键代码示例

### Executor 状态管理
```python
# src/common/executor_manager.py

class ExecutorManager:
    def __init__(self):
        self.active_tasks: dict[str, asyncio.Task] = {}
        self.results: dict[str, ExecutorResult] = {}

    async def start_executor(
        self,
        plan_json: str,
        context: Context,
    ) -> str:
        """启动 Executor（非阻塞）"""
        task_id = f"exec_{uuid.uuid4().hex[:8]}"
        task = asyncio.create_task(
            self._run_executor_wrapper(task_id, plan_json, context)
        )
        self.active_tasks[task_id] = task
        return task_id

    async def _run_executor_wrapper(
        self,
        task_id: str,
        plan_json: str,
        context: Context,
    ):
        """包装 Executor 执行"""
        try:
            result = await run_executor(plan_json, context)
            self.results[task_id] = result
        except Exception as e:
            self.results[task_id] = ExecutorResult(
                status="failed",
                summary=str(e),
                updated_plan_json=None,
            )

    def get_status(self, task_id: str) -> dict:
        """获取 Executor 状态"""
        if task_id not in self.active_tasks:
            return {"status": "not_found"}

        task = self.active_tasks[task_id]
        if task.done():
            result = self.results.get(task_id)
            return {"status": "completed", "result": result}
        return {"status": "running"}

    def cancel(self, task_id: str) -> bool:
        """取消 Executor"""
        if task_id in self.active_tasks:
            self.active_tasks[task_id].cancel()
            return True
        return False

# 全局单例
executor_manager = ExecutorManager()
```

## 测试场景

### 场景 1：后台执行 + 实时查询
```python
# 1. 用户：创建一个复杂的 todo app
# Supervisor: 启动 Executor（后台），立即返回
"Executor 已启动（task_id=exec_123），正在后台执行..."

# 2. 用户：进度如何？
# Supervisor: 查询状态
"已完成 3/5 步骤：正在执行 step_4（创建测试文件）"

# 3. 用户：把 step_5 改成用 JavaScript
# Supervisor: 更新计划，重启 Executor
"已更新计划，重新启动 Executor..."
```

### 场景 2：并发执行多个任务
```python
# 1. 用户：同时创建 3 个不同的 API 端点
# Supervisor: 使用 Send API 并发启动 3 个 Executor
# LangSmith: 显示 3 条并发执行流

# 2. 用户：查看进度
# Supervisor: 聚合所有 Executor 的状态
"Executor 1: ✅ 完成\nExecutor 2: ⏳ 执行中\nExecutor 3: ⏳ 执行中"
```

## 总结

**可行性**：✅ 完全可行
- LangGraph 原生支持并发执行（Send API）
- LangSmith 可以实时可视化并发流
- 可以实现非阻塞的用户交互

**推荐方案**：
1. 先实现 **方案 B**（后台任务），快速验证
2. 再升级到 **方案 A**（Send API），获得完美的 LangSmith 可视化
3. 最后优化为 **方案 C**（混合），获得最佳用户体验

**预计时间**：
- 方案 B：1-2 天
- 方案 A：3-5 天
- 方案 C：2-3 天
- 总计：6-10 天完成完整的并发执行系统
