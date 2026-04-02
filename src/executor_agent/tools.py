# executor_agent/tools.py
import os
import re
import subprocess
from pathlib import PurePath
from typing import TypedDict



from langchain_core.tools import tool


class WriteFileResult(TypedDict):
    ok: bool
    path: str
    overwritten: bool
    bytes: int
    error: str | None


class LocalCommandResult(TypedDict):
    ok: bool
    command: str
    cwd: str
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    error: str | None


MAX_WRITE_FILE_BYTES = 1_000_000
MAX_LOCAL_COMMAND_LENGTH = 2_000
MAX_LOCAL_COMMAND_TIMEOUT = 3_600
_BLOCKED_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-rf\s+/", "禁止执行高风险删除命令"),
    (r"\bdel\b.*\/(?:s|f).*\b[a-z]:", "禁止执行高风险删除命令"),
    (r"\bremove-item\b.*-recurse\b.*-force", "禁止执行高风险删除命令"),
    (r"\bformat\s+[a-z]:", "禁止执行磁盘格式化命令"),
    (r"\bdiskpart\b", "禁止执行磁盘分区命令"),
    (r"\bmkfs\b", "禁止执行文件系统格式化命令"),
    (r"\bdd\s+if=", "禁止执行原始磁盘写入命令"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b", "禁止执行关机/重启命令"),
)
RUN_LOCAL_COMMAND_LLM_HINT = (
    "- run_local_command 使用提示：执行命令时必须使用安全、最小权限原则；"
    "禁止关机/重启/格式化/高风险删除命令；优先只读命令。"
)



def _validate_write_file_input(path: str, content: str) -> str | None:

    """校验 write_file 入参，避免越界写入和超大内容。"""
    normalized_path = path.strip()
    if not normalized_path:
        return "path 不能为空"
    if os.path.isabs(normalized_path):
        return "path 必须是相对路径"

    path_parts = PurePath(normalized_path).parts
    if any(part == ".." for part in path_parts):
        return "path 不允许包含父目录跳转（..）"

    content_bytes = len(content.encode("utf-8"))
    if content_bytes > MAX_WRITE_FILE_BYTES:
        return f"content 过大，当前限制 {MAX_WRITE_FILE_BYTES} bytes"

    return None


@tool
def write_file(path: str, content: str, overwrite: bool = True) -> WriteFileResult:
    """写入文本文件并返回结构化确认信息。"""
    normalized_path = path.strip()
    validation_error = _validate_write_file_input(normalized_path, content)
    abs_path = os.path.abspath(normalized_path) if normalized_path else ""

    if validation_error:
        return {
            "ok": False,
            "path": abs_path,
            "overwritten": False,
            "bytes": 0,
            "error": validation_error,
        }

    parent_dir = os.path.dirname(abs_path)

    try:

        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        if not overwrite and os.path.exists(abs_path):
            return {
                "ok": False,
                "path": abs_path,
                "overwritten": False,
                "bytes": 0,
                "error": "目标文件已存在且 overwrite=False",
            }

        existed_before = os.path.exists(abs_path)
        encoded = content.encode("utf-8")
        with open(abs_path, "w", encoding="utf-8") as f:
            _ = f.write(content)

        return {
            "ok": True,
            "path": abs_path,
            "overwritten": existed_before,
            "bytes": len(encoded),
            "error": None,
        }
    except OSError as e:
        return {
            "ok": False,
            "path": abs_path,
            "overwritten": False,
            "bytes": 0,
            "error": str(e),
        }


def _validate_run_local_command_input(command: str, timeout: int, cwd: str | None) -> str | None:
    """校验 run_local_command 入参，拒绝高风险命令。"""
    normalized_command = command.strip()
    if not normalized_command:
        return "command 不能为空"
    if len(normalized_command) > MAX_LOCAL_COMMAND_LENGTH:
        return f"command 过长，当前限制 {MAX_LOCAL_COMMAND_LENGTH} 字符"

    if timeout <= 0:
        return "timeout 必须为正整数秒"
    if timeout > MAX_LOCAL_COMMAND_TIMEOUT:
        return f"timeout 过大，当前限制 {MAX_LOCAL_COMMAND_TIMEOUT} 秒"

    lowered_command = normalized_command.lower()
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, lowered_command):
            return reason

    if cwd:
        exec_cwd = os.path.abspath(cwd)
        if not os.path.isdir(exec_cwd):
            return "cwd 不存在或不是目录"

    return None


@tool
def run_local_command(command: str, cwd: str | None = None, timeout: int = 600) -> LocalCommandResult:
    """在本地执行命令并返回执行结果。"""
    normalized_command = command.strip()
    exec_cwd = os.path.abspath(cwd) if cwd else os.getcwd()

    validation_error = _validate_run_local_command_input(
        command=normalized_command,
        timeout=timeout,
        cwd=cwd,
    )
    if validation_error:
        return {
            "ok": False,
            "command": normalized_command,
            "cwd": exec_cwd,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "error": validation_error,
        }

    try:
        completed = subprocess.run(
            normalized_command,
            cwd=exec_cwd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "command": normalized_command,
            "cwd": exec_cwd,
            "returncode": completed.returncode,
            "timed_out": False,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": None,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "command": normalized_command,
            "cwd": exec_cwd,
            "returncode": None,
            "timed_out": True,
            "stdout": e.stdout if isinstance(e.stdout, str) else "",
            "stderr": e.stderr if isinstance(e.stderr, str) else "",
            "error": f"命令执行超时（>{timeout}s）",
        }
    except OSError as e:
        return {
            "ok": False,
            "command": normalized_command,
            "cwd": exec_cwd,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "error": str(e),
        }



def get_executor_tools() -> list[object]:
    """返回 Executor 可用的工具列表。"""
    return [write_file, run_local_command]


def get_executor_capabilities_docs() -> str:
    """返回供 Planner/Executor 共享的能力描述文案。"""
    capabilities: list[str] = []
    for idx, tool_obj in enumerate(get_executor_tools(), start=1):
        description = str(getattr(tool_obj, "description", "") or "").strip()
        if description:
            first_line = description.splitlines()[0].strip()
            capabilities.append(f"- {first_line}")
        else:
            capabilities.append(f"- 工具 {idx}")

        tool_name = str(getattr(tool_obj, "name", "") or "")
        if tool_name == "run_local_command":
            capabilities.append(RUN_LOCAL_COMMAND_LLM_HINT)

    return "\n".join(capabilities) if capabilities else "- （当前无可用工具）"


