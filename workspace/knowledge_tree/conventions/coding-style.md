---
title: 项目编码规范
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: conventions
---

项目使用 Python 3.11+，状态管理使用 dataclass 和 TypedDict。
工具定义通过 langchain_core.tools @tool 装饰器注册，函数签名的参数即工具 schema。
LLM 加载统一通过 src/common/utils.py:load_chat_model("provider:model")，
provider 支持 openai、anthropic、ollama 等。

所有配置集中在 src/common/context.py:Context（从 .env 加载）。
各 Agent 参数通过 SUPERVISOR_*/PLANNER_*/EXECUTOR_* 环境变量覆盖：
TEMPERATURE、TOP_P、MAX_TOKENS、SEED 等。
模型配置见 config/agent_models.toml。

工具分层：共享只读工具（read_workspace_text_file、search_files、grep_content、read_file_structure）
在 src/common/tools.py，Planner 和 Executor 共用。副作用工具仅在 Executor 可用。
工作区副作用工具仅在 AGENT_WORKSPACE_DIR（默认 workspace）内操作。

包管理器：uv（所有命令前缀 uv run）。格式化：ruff format + import 排序。类型检查：mypy --strict。
