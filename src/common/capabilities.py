"""Static capability descriptions shared across Agent prompts.

Keep prompt-facing capability text outside concrete agent tool modules so Planner
can understand Executor capabilities without importing Executor side-effect tools.
"""

EXECUTOR_CAPABILITIES_DOCS = "\n".join(
    [
        "- 写入文本文件并返回结构化确认信息。",
        "- 在本地执行命令并返回执行结果。执行期间可被 Supervisor 软中断。",
        (
            "- run_local_command 使用提示：执行命令时必须使用安全、最小权限原则；"
            "禁止关机/重启/格式化/高风险删除命令；默认在 Agent 工作区执行，且自动使用工作区内 Python venv。"
        ),
        "- 列出工作区内目录项（只读）。",
        "- 读取工作区内文本文件（只读）。",
    ]
)


def get_executor_capabilities_docs() -> str:
    """Return prompt-facing documentation of Executor capabilities."""
    return EXECUTOR_CAPABILITIES_DOCS
