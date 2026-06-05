"""Static capability descriptions shared across Agent prompts.

Keep prompt-facing capability text outside concrete agent tool modules so Planner
can understand Executor capabilities without importing Executor side-effect tools.
"""

EXECUTOR_CAPABILITIES_DOCS = "\n".join(
    [
        "- 写入文本文件并返回结构化确认信息。限制：文件大小不超过 1MB，路径必须在 Agent 工作区内。",
        "- 编辑工作区内文本文件：精确匹配并替换指定字符串。支持单次替换（默认）和全局替换（replace_all=True）。匹配不唯一时报错提示。",
        "- 在本地执行命令并返回执行结果。执行期间可被 Supervisor 软中断。限制：命令长度不超过 2000 字符，超时上限 3600 秒。",
        (
            "- run_local_command 使用提示：执行命令时必须使用安全、最小权限原则；"
            "禁止关机/重启/格式化/高风险删除命令；默认在 Agent 工作区执行，且自动使用工作区内 Python venv。"
        ),
        "- 列出工作区内目录项（只读）。",
        "- 读取工作区内文本文件（只读）。",
        "- 按 glob 模式搜索工作区内文件名（只读）。支持模式如 \"*.py\"、\"**/*.md\"。",
        "- 在工作区文件内容中搜索正则匹配（只读）。",
        "- 读取工作区目录树结构概览（只读）。",
    ]
)


def get_executor_capabilities_docs() -> str:
    """Return prompt-facing documentation of Executor capabilities."""
    return EXECUTOR_CAPABILITIES_DOCS
