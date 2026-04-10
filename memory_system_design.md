# AgentTriad 记忆系统设计方案

## 目录结构

```
memory/
├── conversations/          # 对话历史（episodic memory）
│   ├── 2026-04-10_task_1.md
│   └── 2026-04-10_task_2.md
├── executions/             # 执行记录（procedural memory）
│   ├── exec_20260410_001.md
│   └── exec_20260410_002.md
├── learnings/              # 提炼的知识（semantic memory）
│   ├── user_preferences.md
│   ├── common_patterns.md
│   └── error_solutions.md
├── entities/               # 实体记忆
│   └── user_profile.md
├── snapshots/              # 执行快照（可选）
│   └── plan_20260410_001.json
└── MEMORY_INDEX.md         # 总索引
```

## 记忆类型

| 类型 | 文件夹 | 用途 | 生命周期 |
|------|--------|------|----------|
| **Episodic** | `conversations/` | 具体对话和任务 | 长期 |
| **Procedural** | `executions/` | 执行步骤和结果 | 长期 |
| **Semantic** | `learnings/` | 提炼的知识和模式 | 永久 |
| **Entity** | `entities/` | 用户、项目等实体信息 | 永久更新 |

## 文件格式（使用 YAML Frontmatter）

```markdown
---
type: episodic
date: 2026-04-10
tags: [file_operation, todo_app]
importance: 0.8
related: [exec_20260410_001]
---

# 任务：创建 Todo App

**用户请求**：创建一个简单的todo应用

**执行过程**：
1. Planner 生成计划
2. Executor 创建 `workspace/todo_app.py`
3. 运行测试

**结果**：成功创建

**经验教训**：用户偏好命令行界面
```

## 核心机制

### 1. 记忆写入（Memory Write）

**触发时机**：
- 每次任务完成后
- 检测到新的用户偏好时
- 解决了一个新的错误模式时

**写入内容**：
```python
def write_memory(
    memory_type: Literal["episodic", "procedural", "semantic", "entity"],
    content: str,
    metadata: dict,
    importance: float = 0.5,
):
    """写入记忆到文件"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{memory_type}_{timestamp}.md"
    filepath = f"memory/{memory_type}s/{filename}"

    # 添加 frontmatter
    frontmatter = {
        "type": memory_type,
        "date": datetime.now().isoformat(),
        "importance": importance,
        **metadata
    }

    write_file_with_frontmatter(filepath, content, frontmatter)
    update_index(filepath, frontmatter)
```

### 2. 记忆读取（Memory Read）

**查询方式**：
```python
def read_memory(
    query: str,
    memory_type: str | None = None,
    tags: list[str] | None = None,
    limit: int = 5,
) -> list[dict]:
    """从记忆文件中检索相关信息"""
    # 1. 扫描 MEMORY_INDEX.md
    # 2. 过滤匹配的文件
    # 3. 按重要性和时间排序
    # 4. 返回前 N 条
```

### 3. 记忆整理（Memory Consolidation）

**定期任务**：
- 将 episodic memory 中的模式提炼到 semantic memory
- 归档旧的执行记录
- 更新实体信息
- 清理低价值记忆

**示例**：
```
conversations/2026-04-10_task_1.md
  → 提炼 → learnings/common_patterns.md （"用户喜欢Python"）
  → 归档 → executions/archived/
```

### 4. 记忆索引（MEMORY_INDEX.md）

```markdown
# AgentTriad 记忆索引

## 按类型
- [Episodic](conversations/) - 12 条
- [Procedural](executions/) - 8 条
- [Semantic](learnings/) - 5 条
- [Entity](entities/) - 1 条

## 按标签
- `#file_operation` - 3 条
- `#todo_app` - 2 条
- `#error_solution` - 4 条

## 最近更新
- [2026-04-10] 创建 Todo App (conversations/2026-04-10_task_1.md)
- [2026-04-10] 解决编码问题 (learnings/error_solutions.md)

## 高重要性记忆
- 用户偏好 Python (importance: 0.9)
- 工作区边界规则 (importance: 0.95)
```

## 集成到 AgentTriad

### Supervisor Agent

```python
# 在 supervisor_agent/tools.py 添加工具

@tool
def save_to_memory(
    content: str,
    memory_type: str,
    importance: float = 0.5,
) -> str:
    """将重要信息保存到记忆系统"""
    # 实现记忆写入
    pass

@tool
def recall_memory(
    query: str,
    memory_type: str | None = None,
) -> str:
    """从记忆中检索相关信息"""
    # 实现记忆读取
    pass
```

### Planner Agent

```python
# 在规划时可以访问过去的执行模式
@tool
def get_execution_patterns(
    task_type: str,
) -> str:
    """获取类似任务的历史执行模式"""
    # 从 learnings/common_patterns.md 读取
    pass
```

### Executor Agent

```python
# 在执行时可以记录错误和解决方案
@tool
def log_error_solution(
    error: str,
    solution: str,
) -> str:
    """记录错误和解决方案到记忆"""
    # 写入 learnings/error_solutions.md
    pass
```

## 实施步骤

### Phase 1: 基础结构（1-2天）
- [ ] 创建 memory/ 目录结构
- [ ] 实现基础的 read/write 函数
- [ ] 创建 MEMORY_INDEX.md
- [ ] 添加单元测试

### Phase 2: 集成到 Agent（2-3天）
- [ ] Supervisor 添加 save_to_memory 和 recall_memory 工具
- [ ] Planner 添加 get_execution_patterns 工具
- [ ] Executor 添加 log_error_solution 工具
- [ ] 在关键节点触发记忆写入

### Phase 3: 智能整理（3-4天）
- [ ] 实现记忆重要性评分
- [ ] 实现模式提炼（episodic → semantic）
- [ ] 实现定期归档
- [ ] 添加记忆去重

### Phase 4: 高级功能（可选）
- [ ] 语义搜索（如果需要）
- [ ] 记忆可视化
- [ ] 记忆导出/导入
- [ ] 记忆统计分析

## 优势

1. **简单可靠**：纯文本文件，易于调试和备份
2. **透明可读**：人类可以直接查看和编辑
3. **版本控制友好**：可以纳入 Git
4. **无需额外依赖**：不需要数据库或向量存储
5. **渐进式**：可以从简单开始，逐步增强

## 与 RAG 的区别

| 特性 | 文件记忆 | RAG |
|------|----------|-----|
| 存储方式 | 文本文件 | 向量数据库 |
| 检索方式 | 关键词/标签/时间 | 语义相似度 |
| 复杂度 | 低 | 高 |
| 准确性 | 高（精确匹配） | 中（模糊匹配） |
| 适用场景 | 结构化记忆 | 非结构化知识 |

## 参考资源

- Claude Code 记忆系统（我自己的记忆）
- Notion 类型的块级引用
- Zettelkasten 卡片盒笔记法
- Obsidian 的双向链接
