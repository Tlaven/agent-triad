# Web Search 功能修复方案

## 问题诊断

### 当前状态
- ❌ `WebSearch` 工具一直返回空结果
- ❌ `mcp__web-search-prime__web_search_prime` 返回 "[]"
- ✅ `mcp__plugin_context7_context7__query-docs` 可以正常工作

### 根本原因
1. **环境配置禁用了网络流量**：
   ```json
   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
   ```

2. **使用自定义 API**：`api.z.ai` 可能不支持 web search 功能

3. **缺少 Web Search MCP 服务器**：未在 settings.json 中配置

## 解决方案

### 方案 A：修改配置（推荐）

#### 步骤 1：编辑 settings.json
```bash
# 备份配置
cp ~/.claude/settings.json ~/.claude/settings.json.backup

# 编辑配置
nano ~/.claude/settings.json
```

#### 步骤 2：修改以下设置
```json
{
  "env": {
    // ... 其他配置
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "0",  // 改为 0
    "TAVILY_API_KEY": "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"  // 已有
  },
  "enabledPlugins": {
    // ... 其他插件
    "web-search@claude-plugins-official": true  // 添加这个
  }
}
```

#### 步骤 3：重启 Claude Code
```bash
# 完全退出并重启
```

### 方案 B：使用 Tavily API（已配置）

你已经在 `.env` 中配置了 `TAVILY_API_KEY`，可以尝试：

#### 创建自定义 Web Search 工具

```python
# src/tools/web_search_tool.py
import os
import requests
from langchain_core.tools import tool

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

@tool
def web_search_tavily(query: str, max_results: int = 5) -> str:
    """使用 Tavily API 进行网络搜索"""
    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results
    }

    response = requests.post(url, json=payload, headers=headers)
    results = response.json()

    # 格式化结果
    formatted = []
    for result in results.get("results", []):
        formatted.append(f"- {result['title']}\n  {result['url']}\n  {result['content'][:200]}...")

    return "\n\n".join(formatted)
```

#### 集成到 Agent

```python
# src/supervisor_agent/tools.py
from src.tools.web_search_tool import web_search_tavily

async def get_tools(runtime_context: Context):
    return [
        _build_call_planner_tool(runtime_context),
        _build_call_executor_tool(runtime_context),
        _build_get_executor_full_output_tool(),
        web_search_tavily,  # 添加搜索工具
    ]
```

### 方案 C：使用已有工具（临时方案）

既然 `mcp__plugin_context7_context7__query-docs` 可以工作，我们可以：

1. **利用 Context7 查询文档**
2. **利用已有的代码库知识**
3. **手动查找相关资料**

### 方案 D：手动配置 MCP Web Search

创建 `~/.claude/mcp-servers.json`：

```json
{
  "mcpServers": {
    "web-search": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-web-search"
      ],
      "env": {
        "TAVILY_API_KEY": "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"
      }
    }
  }
}
```

## 推荐行动步骤

### 立即行动（5分钟）
1. ✅ **方案 C**：使用 Context7 查询 LangGraph 文档（已验证可用）
2. ✅ **手动分析**：基于已有代码知识给出方案（已完成）

### 短期方案（1小时）
1. 🔄 **方案 B**：创建 Tavily 搜索工具
2. 🔄 **方案 D**：配置 MCP Web Search 服务器

### 长期方案（需要重启）
1. 📋 **方案 A**：修改 settings.json，启用网络流量
2. 📋 重启 Claude Code

## ✅ 最终解决方案（已实施）

### 方案 B：创建自定义 Tavily 搜索工具

**实施时间**：2026-04-10 22:07

**实施步骤**：

1. ✅ 创建 `src/tools/web_search.py`
   - 实现 `web_search_tavily` 工具
   - 实现 `web_search_tavily_quick` 简化版工具
   - 使用 Tavily API（已有 API Key）

2. ✅ 集成到 Supervisor Agent
   - 在 `src/supervisor_agent/tools.py` 导入工具
   - 在 `get_tools()` 函数中添加 `web_search_tavily`

3. ✅ 测试验证
   - 成功搜索 "LangGraph concurrent execution"
   - 返回高质量结果：AI 答案 + 相关文档链接
   - 相关度评分准确（0.80-0.83）

**优势**：
- ✅ 完全自主控制，不依赖第三方插件
- ✅ 高质量搜索结果（AI 答案摘要）
- ✅ 支持域名过滤、搜索深度控制
- ✅ 轻量级实现（~150 行代码）
- ✅ 可扩展性好（易于添加新功能）

**测试结果**：
```
TAVILY_API_KEY 已配置: True
API Key 长度: 58

测试 1: 基础搜索 - LangGraph 并发执行
## AI 答案
LangGraph enables concurrent execution of multiple nodes to speed up workflows...

## 搜索结果
1. Parallel execution with LangChain and LangGraph. | Focused (相关度: 0.83)
2. Scaling LangGraph Agents: Parallelization, Subgraphs... (相关度: 0.82)
3. Parallel AI Agents with LangGraph: Running Tool Calls... (相关度: 0.80)
```

**对比 Z.ai web-search-prime**：
| 特性 | Z.ai web-search-prime | Tavily 自定义工具 |
|------|---------------------|------------------|
| 可用性 | ❌ 返回空结果 | ✅ 工作正常 |
| 结果质量 | ❌ N/A | ✅ 高（AI 答案 + 评分） |
| 控制力 | ❌ 受限于 Z.ai | ✅ 完全控制 |
| 可维护性 | ❌ 依赖第三方 | ✅ 自主维护 |

## 临时解决方案（已废弃）

由于我们已经：
- ✅ 通过 Context7 获取了 LangGraph 文档
- ✅ 分析了当前架构
- ✅ 设计了并发执行方案
- ✅ 实现了自定义搜索工具

**不再需要**：
- Context7 查询文档
- 已有的代码库知识
- 手动查找资料

现在可以使用 `web_search_tavily` 工具直接搜索！

## 验证命令

修改配置后，测试搜索是否恢复：

```python
# 测试搜索
from src.tools.web_search_tool import web_search_tavily

result = web_search_tavily.invoke({"query": "langgraph parallel execution"})
print(result)
```
