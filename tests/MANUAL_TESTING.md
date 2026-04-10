# AgentTriad 手动测试指南

## 🎯 测试方式概览

### 方式 1：Studio UI 可视化测试（⭐ 推荐）

**步骤**：

1. **打开 Studio UI**
   ```
   访问: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
   ```

2. **选择 Supervisor 图**
   - 在左侧选择 `supervisor` 助手
   - 查看三层架构图：Supervisor → Planner/Executor

3. **测试场景**

#### 场景 A：简单问答（Mode 1）
```
输入: 什么是 Python？用一句话回答。
预期: 直接回答，不调用工具
验证: 查看工具调用数为 0
```

#### 场景 B：简单文件操作（Mode 2）
```
输入: 在 workspace 目录创建 hello.txt，内容为 Hello World
预期:
  - 可能调用 call_planner（如需要）
  - 调用 call_executor
  - 创建文件成功
验证: 检查 workspace/hello.txt 存在
```

#### 场景 C：多步骤任务（Mode 3）
```
输入:
1. 创建文件夹 test_project
2. 在其中创建 README.md，内容为 # My Project
3. 创建 main.py，内容为 print("Hello")

预期:
  - 生成包含多个步骤的 Plan JSON
  - 逐步执行每个步骤
  - 返回完整执行报告
验证: 检查文件夹和文件创建正确
```

#### 场景 D：V3+ 异步执行（需设置 ENABLE_V3PLUS_ASYNC=true）
```
输入: 创建 5 个文件（async_1.txt 到 async_5.txt），使用异步模式

预期:
  - 使用 call_executor_async 工具
  - 返回 task_id
  - 后台执行，不阻塞 Supervisor
  - 可用 get_executor_status 查询进度

验证:
  1. 收到 task_id
  2. 任务在后台运行
  3. 文件最终被创建
```

---

### 方式 2：命令行测试

**快速测试脚本**：
```bash
# 运行完整测试套件
python -m tests.manual_test_script

# 或使用 pytest
pytest tests/e2e/test_v3plus_simple_e2e.py -v -s -m live_llm
```

**单个场景测试**：

```python
# test_quick.py
import asyncio
from langchain_core.messages import HumanMessage
from src.common.context import Context
from src.supervisor_agent.graph import graph

async def test():
    ctx = Context(max_executor_iterations=10)
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="创建文件 test.txt，内容为 Hello")]},
        context=ctx,
    )
    print(result["messages"][-1].content)

asyncio.run(test())
```

运行：
```bash
python test_quick.py
```

---

### 方式 3：cURL API 测试

**查看可用端点**：
```bash
curl http://127.0.0.1:2024/docs
```

**调用 Supervisor API**：
```bash
curl -X POST http://127.0.0.1:2024/runs \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "supervisor",
    "input": {
      "messages": [
        {
          "role": "user",
          "content": "创建文件 hello.txt，内容为 Hello World"
        }
      ]
    }
  }'
```

**查询运行状态**：
```bash
# 替换 {run_id} 为实际返回的 ID
curl http://127.0.0.1:2024/runs/{run_id}
```

---

## 📋 完整测试清单

### 基础功能测试

- [ ] **Mode 1: 简单问答**
  - 输入: "什么是 AI？"
  - 预期: 直接回答，无工具调用

- [ ] **Mode 2: 单步任务**
  - 输入: "创建 test.txt，内容为 test"
  - 预期: 调用 call_executor，创建文件

- [ ] **Mode 3: 多步任务**
  - 输入: "创建文件夹、写文件、读文件"
  - 预期: 调用 call_planner + call_executor

### 高级功能测试

- [ ] **V3+ 异步执行**
  - 前置: ENABLE_V3PLUS_ASYNC=true
  - 输入: 长时间运行任务
  - 预期: 使用 call_executor_async，返回 task_id

- [ ] **任务状态查询**
  - 输入: "查询任务 {task_id} 状态"
  - 预期: 返回当前状态、进度

- [ ] **任务取消**
  - 输入: "取消任务 {task_id}"
  - 预期: 任务被取消

### 边界情况测试

- [ ] **失败处理**
  - 输入: 访问不存在的 URL
  - 预期: 触发重规划，最终优雅终止

- [ ] **重规划上限**
  - 输入: 反复失败的任务
  - 预期: 达到 MAX_REPLAN 后停止

- [ ] **工具禁用模式**
  - 前置: ENABLE_V3PLUS_ASYNC=false
  - 输入: 任何任务
  - 预期: 不使用异步工具

---

## 🔧 测试环境准备

### 1. 启动开发服务器
```bash
make dev
# 或
uv run langgraph dev --config langgraph.json --no-browser
```

### 2. 检查环境变量
```bash
# 确保设置了 API Keys
cat .env | grep API_KEY

# 启用 V3+ 异步（可选）
echo "ENABLE_V3PLUS_ASYNC=true" >> .env
```

### 3. 准备工作目录
```bash
mkdir -p workspace/test_manual
```

---

## 📊 验证标准

### 成功标准
- ✅ 简单问答 < 10 秒响应
- ✅ 文件操作成功创建文件
- ✅ 多步骤任务生成 Plan JSON
- ✅ V3+ 异步返回 task_id
- ✅ 无异常崩溃

### 失败信号
- ❌ 超过 60 秒无响应
- ❌ 文件未创建
- ❌ Plan JSON 格式错误
- ❌ 异常堆栈输出

---

## 🐛 问题排查

### 问题 1: API Key 错误
```
解决: 检查 .env 中的 SILICONFLOW_API_KEY
```

### 问题 2: 文件未创建
```
解决: 检查 AGENT_WORKSPACE_DIR 权限
```

### 问题 3: 异步工具不可用
```
解决: 确保 ENABLE_V3PLUS_ASYNC=true
```

### 问题 4: Studio UI 连接失败
```
解决: 检查端口 2024 是否被占用
      确认 langgraph dev 正在运行
```

---

## 📝 测试报告模板

```markdown
## 测试记录

**日期**: 2026-04-10
**测试人**: [你的名字]
**环境**: Windows 11, Python 3.11.9

### 测试结果

| 场景 | 预期 | 实际 | 状态 |
|------|------|------|------|
| Mode 1 简单问答 | < 10s | 4.2s | ✅ |
| Mode 2 文件操作 | 创建文件 | 成功 | ✅ |
| Mode 3 多步任务 | 生成 Plan | 成功 | ✅ |
| V3+ 异步执行 | 返回 task_id | 成功 | ✅ |

### 发现的问题
1. [记录任何问题]

### 建议
1. [改进建议]
```

---

## 🎓 下一步

测试完成后，你可以：
- 查看 LangSmith 追踪: https://smith.langchain.com/
- 运行完整测试套件: `make test_all`
- 阅读架构文档: `docs/architecture-decisions.md`
- 查看 V3+ 使用指南: `V3PLUS_USAGE.md`

**Happy Testing! 🚀**
