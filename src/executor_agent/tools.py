# executor_agent/tools.py
import os
import re
import subprocess
import sys
from pathlib import PurePath
from typing import TypedDict

from langchain_core.tools import tool

from src.common.tools import list_workspace_entries, read_workspace_text_file
from src.executor_agent.interrupt import (
    ToolInterrupted,
    check_interrupt,
    run_with_interrupt_check,
    INTERRUPT_PROMPT,
)


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
DEFAULT_AGENT_WORKSPACE_DIR = "workspace"
DEFAULT_AGENT_VENV_DIRNAME = ".venv"
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
    "禁止关机/重启/格式化/高风险删除命令；默认在 Agent 工作区执行，且自动使用工作区内 Python venv。"
)


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _agent_workspace_root() -> str:
    configured = os.environ.get("AGENT_WORKSPACE_DIR", DEFAULT_AGENT_WORKSPACE_DIR).strip()
    if not configured:
        configured = DEFAULT_AGENT_WORKSPACE_DIR
    root = os.path.join(_project_root(), configured)
    os.makedirs(root, exist_ok=True)
    return os.path.abspath(root)


def _agent_venv_dir() -> str:
    dirname = os.environ.get("AGENT_VENV_DIRNAME", DEFAULT_AGENT_VENV_DIRNAME).strip()
    if not dirname:
        dirname = DEFAULT_AGENT_VENV_DIRNAME
    return os.path.join(_agent_workspace_root(), dirname)


def _ensure_agent_venv() -> str:
    venv_dir = _agent_venv_dir()
    if os.path.isdir(venv_dir):
        return venv_dir
    completed = subprocess.run(
        [sys.executable, "-m", "venv", venv_dir],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise OSError(
            f"创建 venv 失败（exit={completed.returncode}）：{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return venv_dir


def _resolve_workspace_path(relative_path: str) -> str:
    workspace_root = _agent_workspace_root()
    # 兼容两种输入：
    # - 传统相对路径（拼到 workspace_root 下）
    # - 已在 workspace_root 内的绝对路径（直接使用）
    if os.path.isabs(relative_path):
        abs_path = os.path.abspath(relative_path)
    else:
        abs_path = os.path.abspath(os.path.join(workspace_root, relative_path))
    if os.path.commonpath([workspace_root, abs_path]) != workspace_root:
        raise ValueError("path 超出 Agent 工作区范围")
    return abs_path


def _venv_bin_dir(venv_dir: str) -> str:
    return os.path.join(venv_dir, "Scripts" if os.name == "nt" else "bin")


def _build_subprocess_env_with_venv(venv_dir: str) -> dict[str, str]:
    env = os.environ.copy()
    bin_dir = _venv_bin_dir(venv_dir)
    env["VIRTUAL_ENV"] = venv_dir
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


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
    abs_path = ""
    if normalized_path:
        try:
            abs_path = _resolve_workspace_path(normalized_path)
        except ValueError as e:
            validation_error = str(e)
        except OSError as e:
            validation_error = str(e)

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
        try:
            exec_cwd = _resolve_workspace_path(cwd)
        except ValueError as e:
            return str(e)
        if not os.path.isdir(exec_cwd):
            return "cwd 不存在或不是目录"

    return None


@tool
def run_local_command(command: str, cwd: str | None = None, timeout: int = 120) -> LocalCommandResult:
    """在本地执行命令并返回执行结果。执行期间可被 Supervisor 软中断。"""
    normalized_command = command.strip()
    workspace_root = _agent_workspace_root()

    validation_error = _validate_run_local_command_input(
        command=normalized_command,
        timeout=timeout,
        cwd=cwd,
    )
    if validation_error:
        return {
            "ok": False,
            "command": normalized_command,
            "cwd": workspace_root,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "error": validation_error,
        }

    exec_cwd = _resolve_workspace_path(cwd) if cwd else workspace_root

    try:
        venv_dir = _ensure_agent_venv()
        run_env = _build_subprocess_env_with_venv(venv_dir)

        if os.name == "nt":
            cmd_args = ["powershell", "-NoProfile", "-Command", normalized_command]
            shell = False
        else:
            cmd_args = normalized_command
            shell = True

        completed = run_with_interrupt_check(
            cmd_args,
            shell=shell,
            cwd=exec_cwd,
            env=run_env,
            timeout=timeout,
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
    except ToolInterrupted:
        return {
            "ok": False,
            "command": normalized_command,
            "cwd": exec_cwd,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "error": INTERRUPT_PROMPT,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "command": normalized_command,
            "cwd": exec_cwd,
            "returncode": None,
            "timed_out": True,
            "stdout": e.stdout if isinstance(e.stdout, str) else (e.output or ""),
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
    return [write_file, run_local_command, list_workspace_entries, read_workspace_text_file]


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


