# V3+ 异步模式手动测试快速指南

## ✅ 当前状态

**异步模式已启用！**
- ✅ .env 配置: `ENABLE_V3PLUS_ASYNC=true`
- ✅ LangGraph Dev 服务器运行中: http://127.0.0.1:2024
- ✅ Studio UI 可用

---

## 🚀 立即开始测试

### 方式 1：Studio UI 测试（⭐ 推荐）

**第 1 步：打开 Studio UI**
```
访问: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
```

**第 2 步：选择 Supervisor**
- 在左侧面板选择 `supervisor` 助手
- 查看三层架构图

**第 3 步：测试异步功能**

#### 测试案例 1：简单异步任务
```
输入:
创建 5 个文件（async_1.txt 到 async_5.txt），使用异步执行模式

预期结果:
- 调用 call_executor_async 工具
- 收到 task_id（格式: task_20260410_xxxxxx）
- 后台执行，不阻塞
- 提示可以用 get_executor_status 查询进度
```

#### 测试案例 2：查询异步任务状态
```
输入:
查询任务 {task_id} 的状态

预期结果:
- 显示当前状态（pending/running/completed/failed）
- 显示进度信息
- 如果完成，显示执行结果
```

#### 测试案例 3：取消异步任务
```
输入:
取消任务 {task_id}

预期结果:
- 显示任务已取消
- 或提示任务已完成/不存在
```

#### 测试案例 4：并发多个异步任务
```
输入:
启动 3 个异步任务，分别创建 file_a.txt, file_b.txt, file_c.txt

预期结果:
- 每个任务返回独立的 task_id
- 所有任务并发执行
- 不互相阻塞
```

---

### 方式 2：命令行快速测试

创建测试文件 `test_async.py`:

```python
import asyncio
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from src.common.context import Context
from src.supervisor_agent.graph import graph

load_dotenv()

async def test_async():
    print("\n[TEST] V3+ Async Mode Test")
    print("="*60)

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=10,
        max_replan=1,
    )

    task = "创建 3 个文件（async_1.txt 到 async_3.txt），使用异步执行"

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    messages = result["messages"]
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            print(f"\n{msg.content[:300]}")

    print("\n[OK] Test completed")

asyncio.run(test_async())
```

运行测试：
```bash
python test_async.py
```

---

### 方式 3：API 调用测试

```bash
# 调用 Supervisor API
curl -X POST http://127.0.0.1:2024/runs \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "supervisor",
    "input": {
      "messages": [
        {
          "role": "user",
          "content": "创建 3 个文件，使用异步执行模式"
        }
      ]
    }
  }'
```

---

## 📊 验证清单

测试时检查以下内容：

### ✅ 异步工具可用性
- [ ] call_executor_async 工具出现在工具列表
- [ ] get_executor_status 工具可用
- [ ] cancel_executor 工具可用

### ✅ 异步执行行为
- [ ] call_executor_async 返回 task_id
- [ ] Supervisor 不被阻塞，可以继续接收消息
- [ ] 任务在后台执行
- [ ] 可以查询任务状态

### ✅ 状态查询功能
- [ ] get_executor_status 返回正确的状态
- [ ] 显示任务进度信息
- [ ] 完成的任务显示结果

### ✅ 任务取消功能
- [ ] cancel_executor 成功取消运行中的任务
- [ ] 返回取消确认消息

---

## 🎯 典型测试流程

```
1. Studio UI → 选择 supervisor

2. 输入: "创建 5 个文件，使用异步模式"
   → 观察：调用 call_executor_async
   → 获得：task_id

3. 输入: "查询任务 {task_id} 状态"
   → 观察：调用 get_executor_status
   → 获得：当前状态（如：running）

4. 等待几秒

5. 再次输入: "查询任务 {task_id} 状态"
   → 观察：状态变为 completed
   → 获得：执行结果

6. （可选）输入: "创建新任务"
   → 观察：Supervisor 可以继续工作
```

---

## 🔍 观察要点

在 Studio UI 中注意观察：

1. **工具调用**
   - 查看是否使用了 `call_executor_async`
   - 而不是普通的 `call_executor`

2. **执行时间**
   - 异步调用应该立即返回（< 5秒）
   - 不会等待整个任务完成

3. **返回消息**
   - 包含 task_id
   - 提示可以查询状态
   - 说明后台执行模式

4. **Trace 视图**
   - 查看 LLM 的推理过程
   - 观察工具调用序列
   - 检查是否有错误

---

## 🐛 常见问题

### Q: 异步工具没有出现？
**A**: 检查 .env 中的 `ENABLE_V3PLUS_ASYNC=true`，然后重启服务器

### Q: 收不到 task_id？
**A**: 确保任务需要先调用 Planner 生成计划，然后再异步执行

### Q: 如何确认任务真的在后台运行？
**A**:
1. 收到 task_id 后立即发送新消息
2. 如果 Supervisor 能响应新消息，说明是非阻塞的
3. 用 get_executor_status 查询，看到状态为 running

### Q: 任务一直显示 pending？
**A**: 可能是 ExecutorManager 的后台 worker 还没启动任务，等待几秒后再查询

---

## 📝 测试记录模板

```markdown
### 异步功能测试记录

**日期**: 2026-04-10
**环境**: ENABLE_V3PLUS_ASYNC=true

**测试案例**:
1. 创建 5 个文件（异步）
   - [ ] 收到 task_id
   - [ ] 工具调用为 call_executor_async
   - [ ] 文件创建成功

2. 查询任务状态
   - [ ] 显示正确状态
   - [ ] 进度信息准确

3. 任务取消
   - [ ] 成功取消运行中的任务
   - [ ] 返回确认消息

**发现的问题**:
-

**建议**:
-
```

---

## 🎓 下一步

测试完成后：
- 查看 LangSmith 运行追踪
- 尝试更复杂的异步任务
- 测试并发场景
- 阅读完整文档: `V3PLUS_USAGE.md`

**Happy Testing! 🚀**
