#!/usr/bin/env python
"""Probe Supervisor — 无状态、纯执行、单行紧凑 JSON 输出.

子命令:
  health        检查 dev server 在线
  new-session   新建 LangGraph thread
  send          发送消息并等待响应

设计原则:
  - 所有 stdout 单行紧凑 JSON（无缩进、无 ANSI color）
  - 所有错误捕获为 {status, error_type, error_message}，不抛 stderr
  - 不做决策（决策在 slash command 里 Claude 做）
  - 路径基于 __file__，不依赖 cwd
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

SERVER_URL = os.environ.get("PROBE_SERVER_URL", "http://localhost:2024")
ASSISTANT_ID = "supervisor"

KT_ROOT = PROJECT_ROOT / "workspace" / "kt_probe"
KT_ROOT.mkdir(parents=True, exist_ok=True)


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _classify_exception(e: Exception) -> tuple[str, str]:
    err_msg = str(e).lower()
    if any(k in err_msg for k in ("401", "403", "unauthorized", "forbidden", "auth")):
        return "auth_error", "auth"
    if "429" in err_msg or "rate limit" in err_msg:
        return "rate_limit", "rate"
    if any(k in err_msg for k in ("connection", "refused", "unreachable", "timeout")):
        return "server_unreachable", "net"
    return "error", "other"


async def cmd_health(_args) -> int:
    from langgraph_sdk import get_client

    start = time.perf_counter()
    try:
        client = get_client(url=SERVER_URL)
        assistants = await asyncio.wait_for(client.assistants.search(), timeout=5.0)
        elapsed = time.perf_counter() - start
        emit({
            "status": "ok",
            "server_url": SERVER_URL,
            "assistants_count": len(assistants),
            "elapsed_s": round(elapsed, 3),
        })
        return 0
    except asyncio.TimeoutError:
        emit({
            "status": "server_unreachable",
            "error_type": "TimeoutError",
            "error_message": "assistants.search() timed out after 5s",
            "server_url": SERVER_URL,
        })
        return 2
    except Exception as e:
        status, _ = _classify_exception(e)
        emit({
            "status": status,
            "error_type": type(e).__name__,
            "error_message": str(e),
            "server_url": SERVER_URL,
        })
        return 2 if status == "server_unreachable" else (4 if status == "auth_error" else 1)


async def cmd_new_session(_args) -> int:
    from langgraph_sdk import get_client

    try:
        client = get_client(url=SERVER_URL)
        thread = await asyncio.wait_for(client.threads.create(), timeout=10.0)
        emit({
            "status": "ok",
            "thread_id": thread["thread_id"],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        })
        return 0
    except Exception as e:
        status, _ = _classify_exception(e)
        emit({
            "status": status,
            "error_type": type(e).__name__,
            "error_message": str(e),
        })
        return 2 if status == "server_unreachable" else (4 if status == "auth_error" else 1)


async def cmd_send(args) -> int:
    from langgraph_sdk import get_client

    timeout_s = args.timeout
    message = args.message
    thread_id = args.thread

    context: dict = {"enable_knowledge_tree": not args.no_kt}
    if not args.no_kt:
        context["knowledge_tree_root"] = str(KT_ROOT.resolve())

    try:
        client = get_client(url=SERVER_URL)
    except Exception as e:
        emit({
            "status": "error",
            "error_type": type(e).__name__,
            "error_message": f"get_client failed: {e}",
            "thread_id": thread_id,
        })
        return 1

    start = time.perf_counter()

    try:
        run = await asyncio.wait_for(
            client.runs.create(
                thread_id=thread_id,
                assistant_id=ASSISTANT_ID,
                input={"messages": [{"role": "user", "content": message}]},
                context=context,
            ),
            timeout=15.0,
        )
        run_id = run["run_id"]
    except Exception as e:
        status, _ = _classify_exception(e)
        emit({
            "status": status,
            "error_type": type(e).__name__,
            "error_message": f"runs.create failed: {e}",
            "thread_id": thread_id,
            "duration_s": round(time.perf_counter() - start, 2),
        })
        return 2 if status == "server_unreachable" else (4 if status == "auth_error" else 1)

    status = "unknown"
    poll_deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < poll_deadline:
        try:
            rs = await client.runs.get(thread_id, run_id)
            status = rs.get("status", "unknown")
            if status in ("success", "error", "cancelled"):
                break
        except Exception:
            pass
        await asyncio.sleep(1)
    else:
        try:
            await client.runs.cancel(thread_id, run_id)
        except Exception:
            pass
        status = "timeout"

    duration_s = time.perf_counter() - start

    try:
        state = await asyncio.wait_for(client.threads.get_state(thread_id), timeout=10.0)
        messages = state.get("values", {}).get("messages", [])
        values = state.get("values", {})
    except Exception as e:
        emit({
            "status": status,
            "thread_id": thread_id,
            "run_id": run_id,
            "duration_s": round(duration_s, 2),
            "error_message": f"get_state failed: {e}",
            "ai_message": "",
            "ai_message_truncated": False,
            "tool_calls": [],
            "had_tool_calls_only": False,
            "empty_response": True,
            "supervisor_decision": None,
            "messages_count_in_state": 0,
        })
        return 3 if status == "timeout" else 1

    ai_text = ""
    for msg in reversed(messages):
        if msg.get("type") == "ai" and not msg.get("tool_calls"):
            ai_text = msg.get("content", "") or ""
            break

    tool_calls: list[str] = []
    for msg in messages:
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc.get("name", "")
                if name:
                    tool_calls.append(name)

    truncated = False
    if len(ai_text) > 2000:
        ai_text = ai_text[:2000]
        truncated = True

    had_tool_calls_only = bool(tool_calls) and not ai_text
    empty_response = not ai_text and not tool_calls

    supervisor_decision = None
    sd = values.get("supervisor_decision")
    if sd is not None:
        try:
            if isinstance(sd, dict):
                sd_dict = sd
            elif hasattr(sd, "model_dump"):
                sd_dict = sd.model_dump()
            elif hasattr(sd, "__dict__"):
                sd_dict = sd.__dict__
            else:
                sd_dict = {}
            supervisor_decision = {
                "mode": sd_dict.get("mode"),
                "reason": sd_dict.get("reason"),
                "tools_enabled": sd_dict.get("tools_enabled"),
            }
        except Exception:
            supervisor_decision = None

    emit({
        "status": status,
        "thread_id": thread_id,
        "run_id": run_id,
        "duration_s": round(duration_s, 2),
        "ai_message": ai_text,
        "ai_message_truncated": truncated,
        "tool_calls": tool_calls,
        "had_tool_calls_only": had_tool_calls_only,
        "empty_response": empty_response,
        "supervisor_decision": supervisor_decision,
        "messages_count_in_state": len(messages),
        "error_message": None,
    })

    if status == "timeout":
        return 3
    if status == "error":
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="probe_supervisor.py",
        description="Probe Supervisor (无状态、单行紧凑 JSON 输出)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_health = sub.add_parser("health", help="检查 dev server 在线")
    p_health.set_defaults(func=cmd_health)

    p_new = sub.add_parser("new-session", help="新建 LangGraph thread")
    p_new.set_defaults(func=cmd_new_session)

    p_send = sub.add_parser("send", help="发送消息并等待")
    p_send.add_argument("--thread", required=True, help="thread_id")
    p_send.add_argument("--message", required=True, help="消息内容")
    p_send.add_argument("--timeout", type=int, default=240, help="超时秒数 (默认 240, > executor_wait_timeout 200 + 余量)")
    p_send.add_argument("--no-kt", action="store_true", help="禁用 KT")
    p_send.set_defaults(func=cmd_send)

    args = parser.parse_args()

    try:
        rc = asyncio.run(args.func(args))
    except KeyboardInterrupt:
        emit({"status": "error", "error_type": "KeyboardInterrupt", "error_message": "interrupted"})
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
