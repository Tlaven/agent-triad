# V3+ 异步并发模式使用指南

## 概述

V3+ 异步并发模式允许 Executor 在后台执行，支持实时进度查询和任务管理，而不阻塞 Supervisor 的响应。

## 配置

### 启用/禁用

在 `.env` 文件中设置：

```bash
# 禁用（默认）- 使用同步执行模式
ENABLE_V3PLUS_ASYNC=false

# 启用 - 使用异步并发模式
ENABLE_V3PLUS_ASYNC=true
```

### 验证配置

```python
from src.common.context import Context

ctx = Context()
print(f"V3+ async enabled: {ctx.enable_v3plus_async}")
```

## 使用方式

### 同步模式（禁用时）

```python
# 仅可使用 call_executor（阻塞等待）
result = await call_executor({"plan_id": "plan_123"})
# Executor 执行期间 Supervisor 被阻塞
print(result)  # 收到最终结果
```

### 异步模式（启用时）

#### 1. 启动后台任务

```python
# 使用 call_executor_async（非阻塞）
task_id = await call_executor_async({"plan_id": "plan_123"})
# 立即返回，可以继续处理其他请求
print(f"任务已启动: {task_id}")
```

**返回示例**：
```
✅ Executor 已启动（后台异步执行模式）

**任务 ID**: exec_abc12345
**计划 ID**: plan_v1

**后续操作**：
- 使用 `get_executor_status` 工具查询执行进度
- Executor 完成前，你可以继续下达其他指令
- 完成后可使用 `get_executor_full_output` 查看详细结果
```

#### 2. 查询任务状态

```python
# 随时查询进度
status = get_executor_status({"task_id": "exec_abc12345"})
print(status)
```

**状态示例**：
```
📊 **任务状态报告**

▶️ **任务 ID**: exec_abc12345
📋 **计划 ID**: plan_v1
🔄 **状态**: running
✓ **完成**: 否
🕐 **创建时间**: 2026-04-10T22:30:00
🕒 **更新时间**: 2026-04-10T22:30:15
```

#### 3. 取消任务

```python
# 取消正在执行的任务
result = cancel_executor({"task_id": "exec_abc12345"})
print(result)  # "✅ 任务 exec_abc12345 已取消"
```

## 典型场景

### 场景 1：长时间运行任务 + 用户交互

```python
# 用户：创建一个复杂的应用（需要 5 分钟）
# Supervisor: 启动后台任务
task_id = await call_executor_async({"plan_id": "plan_build_app"})
# 立即返回

# 用户：进度如何？
# Supervisor: 查询状态
status = get_executor_status({"task_id": task_id})
# "已完成 3/5 步骤，正在执行 step_4（编写测试）"

# 用户：把 step_5 改成用 TypeScript
# Supervisor: 可以立即响应，因为 Executor 在后台
# "当前任务还在运行，是否先取消它？"
```

### 场景 2：并发执行多个独立任务

```python
# 用户：同时处理 3 个 API 端点
task1 = await call_executor_async({"plan_id": "plan_api_users"})
task2 = await call_executor_async({"plan_id": "plan_api_posts"})
task3 = await call_executor_async({"plan_id": "plan_api_comments"})

# 3 个 Executor 在后台并发运行
# Supervisor 可以继续处理其他请求

# 用户：查看所有任务状态
for tid in [task1, task2, task3]:
    print(get_executor_status({"task_id": tid}))
```

### 场景 3：同步模式（向后兼容）

```python
# ENABLE_V3PLUS_ASYNC=false 时

# 用户：创建简单文件
result = await call_executor({"task_description": "创建 hello.txt"})
# Supervisor 阻塞等待结果
# "✅ 已创建 hello.txt"
```

## 状态说明

| 状态 | 说明 | 可执行操作 |
|------|------|----------|
| `pending` | 任务已创建，等待执行 | 等待、取消 |
| `running` | 正在执行中 | 查询进度、取消 |
| `completed` | 执行完成 | 查看结果 |
| `failed` | 执行失败 | 查看错误 |
| `cancelled` | 已取消 | 无 |

## 工具对比

| 工具 | 模式 | 阻塞 | 可用条件 |
|------|------|------|---------|
| `call_executor` | 同步 | ✅ 阻塞 | 始终可用 |
| `call_executor_async` | 异步 | ❌ 非阻塞 | 仅启用时 |
| `get_executor_status` | 查询 | ❌ 非阻塞 | 仅启用时 |
| `cancel_executor` | 控制 | ❌ 非阻塞 | 仅启用时 |

## LangSmith 可视化

启用后，LangSmith 会记录两个独立的执行线程：

1. **Supervisor Thread**：Supervisor 主循环，包含用户交互
2. **Executor Thread**：Executor 执行线程，每个任务独立记录

可以通过 `task_id` 关联查看完整的执行轨迹。

## 故障排查

### Q: 异步工具不可用？

**检查配置**：
```python
from src.common.context import Context
ctx = Context()
print(ctx.enable_v3plus_async)  # 应该是 True
```

**检查环境变量**：
```bash
echo $ENABLE_V3PLUS_ASYNC  # 应该是 "true"
```

### Q: 任务卡在 pending 状态？

可能原因：
1. Executor 图未正确初始化
2. 计划 JSON 格式错误
3. Context 配置问题

解决方法：
```python
status = get_executor_status({"task_id": task_id})
print(status)  # 查看详细错误信息
```

### Q: 如何切换回同步模式？

```bash
# .env 文件
ENABLE_V3PLUS_ASYNC=false

# 重启应用
```

## 性能考虑

- **内存占用**：每个后台任务约占用 10-50MB
- **并发限制**：建议不超过 `MAX_PARALLEL_EXECUTORS`（默认 4）
- **适用场景**：
  - ✅ 长时间任务（> 30 秒）
  - ✅ 需要并发多任务
  - ✅ 需要实时进度反馈
  - ❌ 简单快速任务（< 5 秒）

## 最佳实践

1. **简单任务用同步**：短任务直接用 `call_executor`
2. **复杂任务用异步**：长时间任务用 `call_executor_async`
3. **定期查询进度**：避免轮询过于频繁
4. **及时清理**：完成的任务可以调用 `cleanup` 释放内存

## 相关文档

- [V3+ 最终方案](v3plus_final_proposal.md)
- [V3 并发执行分析](v3_concurrent_execution_analysis.md)
- [CLAUDE.md](CLAUDE.md) - 项目架构文档
