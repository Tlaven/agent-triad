---
title: 工具参考手册
source: project_seed
created_at: '2026-05-07T00:00:00+08:00'
metadata:
  category: conventions
---

AgentTriad 工具分三类：Supervisor 工具、Executor 工具、共享只读工具。

**Supervisor 工具（7 个，含 4 个 KT）**：
- `call_planner(task_core, plan_id)` — 生成/修订 Plan JSON
- `call_executor(task_description, plan_id, wait_for_result)` — 派发执行（默认阻塞等待）
- `manage_executor(action, plan_id)` — 管理 Executor（stop/get_result/check_progress/list_tasks）
- `knowledge_tree_retrieve(query)` — 主动搜索记忆
- `knowledge_tree_ingest(text, trigger)` — 写入记忆
- `knowledge_tree_status()` — 记忆库概览
- `knowledge_tree_list(directory)` — 浏览记忆内容

**Executor 工具（4 个）**：
- `write_file(path, content)` — 写入文件（限制：1MB，必须在 Agent 工作区内）
- `run_local_command(command, timeout)` — 执行命令（限制：2000 字符，3600s 超时，安全沙箱）
- `list_workspace_entries(relative_path)` — 列出目录
- `read_workspace_text_file(relative_path)` — 读取文本文件

**Planner 工具（5 个，全部只读）**：
- `read_workspace_text_file`、`list_workspace_entries`、`search_files`（glob）、`grep_content`（正则）、`read_file_structure`（目录树）

工作区限制：所有文件操作限制在 `workspace/agent/` 目录内。Planner 只能用只读工具。
