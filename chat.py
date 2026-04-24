"""AgentTriad 交互式端到端测试工具.

直接与 Supervisor Agent 对话，实时查看回复、工具调用和状态变化。
支持流式输出，知识树功能可切换。

用法：
    uv run chat.py [选项]

示例：
    uv run chat.py                        # 默认配置
    uv run chat.py --kt                   # 启用知识树
    uv run chat.py --model siliconflow:Qwen/Qwen3-8B  # 指定模型
    uv run chat.py --verbose              # 显示完整工具调用细节
"""  # noqa: D415, T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── 加载 .env ───────────────────────────────────────────────
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)


from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.common.context import Context
from src.supervisor_agent.graph import graph


# ─── 颜色工具 ────────────────────────────────────────────────
class C:  # noqa: D415
    """ANSI 颜色码."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"
    BG_GRAY = "\033[48;5;236m"


def cprint(text: str, *colors: str, end: str = "\n"):
    """带颜色的 print。"""
    prefix = "".join(colors)
    print(f"{prefix}{text}{C.RESET}", end=end, flush=True)


def print_divider(char: str = "─", width: int = 60):
    """打印分割线。"""
    cprint(char * width, C.DIM)


# ─── 消息格式化 ──────────────────────────────────────────────
def format_tool_calls(msg: AIMessage, verbose: bool) -> list[str]:
    """格式化工具调用信息。"""
    lines = []
    for tc in msg.tool_calls:
        name = tc.get("name", "?")
        args = tc.get("args", {})
        args_str = json.dumps(args, ensure_ascii=False, indent=None)
        if len(args_str) > 200 and not verbose:
            args_str = args_str[:200] + "…"
        lines.append(f"  🔧 {C.CYAN}{name}{C.RESET}({args_str})")
    return lines


def format_tool_result(msg: ToolMessage, verbose: bool) -> str:
    """格式化工具返回结果。"""
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    if len(content) > 500 and not verbose:
        content = content[:500] + "…"
    return content


def print_message(msg, verbose: bool, show_thinking: bool = False):
    """打印一条消息的摘要。"""
    if isinstance(msg, AIMessage):
        # 思维链
        if show_thinking and hasattr(msg, "additional_kwargs"):
            reasoning = msg.additional_kwargs.get("reasoning_content", "")
            if reasoning:
                cprint(f"  💭 {reasoning[:300]}{'…' if len(reasoning) > 300 else ''}", C.DIM)

        # 工具调用
        if msg.tool_calls:
            for line in format_tool_calls(msg, verbose):
                print(line)

        # 文本内容
        if msg.content:
            content = msg.content
            # 跳过纯工具调用消息的空 content
            if content and content.strip():
                print(f"  {content}")

    elif isinstance(msg, ToolMessage):
        name = getattr(msg, "name", "tool")
        result = format_tool_result(msg, verbose)
        cprint(f"  📥 {name} → {result}", C.DIM)

    elif isinstance(msg, HumanMessage):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        cprint(f"  👤 {content}", C.YELLOW)


# ─── 会话状态 ────────────────────────────────────────────────
class ChatSession:
    """管理一次交互式会话。"""

    def __init__(self, ctx: Context, verbose: bool = False, show_thinking: bool = False):
        self.ctx = ctx
        self.verbose = verbose
        self.show_thinking = show_thinking
        self.messages: list = []
        self.turn_count = 0
        self.total_time = 0.0

    def print_banner(self):
        """打印启动横幅。"""
        print()
        cprint("╔══════════════════════════════════════════════╗", C.BOLD, C.CYAN)
        cprint("║     AgentTriad 交互式测试工具               ║", C.BOLD, C.CYAN)
        cprint("╚══════════════════════════════════════════════╝", C.BOLD, C.CYAN)
        print()
        cprint(f"  模型: {self.ctx.supervisor_model}", C.DIM)
        cprint(f"  知识树: {'✓ 启用' if self.ctx.enable_knowledge_tree else '✗ 关闭'}", C.DIM)
        if self.ctx.enable_knowledge_tree:
            cprint(f"  知识树根目录: {self.ctx.knowledge_tree_root}", C.DIM)
        cprint(f"  深度思考: {'✓ 可见' if self.show_thinking else '✗ 隐藏'}", C.DIM)
        cprint(f"  详细模式: {'✓' if self.verbose else '✗'}", C.DIM)
        print()
        cprint("  命令:", C.BOLD)
        cprint("    /help    — 显示帮助", C.DIM)
        cprint("    /status  — 查看会话状态", C.DIM)
        cprint("    /history — 查看完整对话历史", C.DIM)
        cprint("    /config  — 查看当前配置", C.DIM)
        cprint("    /reset   — 重置会话", C.DIM)
        cprint("    /kt on|off — 切换知识树", C.DIM)
        cprint("    /verbose on|off — 切换详细模式", C.DIM)
        cprint("    /thinking on|off — 切换思维链显示", C.DIM)
        cprint("    /quit    — 退出", C.DIM)
        print()
        print_divider()

    async def send(self, text: str) -> str | None:
        """发送消息并获取回复，流式打印中间过程。"""
        self.turn_count += 1
        start = time.perf_counter()

        user_msg = HumanMessage(content=text)
        self.messages.append(user_msg)

        cprint(f"\n[Turn {self.turn_count}] 处理中…", C.DIM)

        try:
            result = await graph.ainvoke(
                {"messages": self.messages},
                context=self.ctx,
            )
        except KeyboardInterrupt:
            cprint("\n  ⚠ 被用户中断", C.YELLOW)
            return None
        except Exception as e:
            elapsed = time.perf_counter() - start
            cprint(f"\n  ❌ 错误 ({elapsed:.1f}s): {e}", C.RED)
            if self.verbose:
                import traceback
                traceback.print_exc()
            return None

        elapsed = time.perf_counter() - start
        self.total_time += elapsed

        # 提取新的消息（去重）
        result_messages = result.get("messages", [])
        new_messages = result_messages[len(self.messages):]
        self.messages = result_messages

        # 打印过程
        print()
        cprint(f"── 回复 ({elapsed:.1f}s) ──", C.DIM)

        final_response = None
        for msg in new_messages:
            print_message(msg, self.verbose, self.show_thinking)
            # 找最后一条有文本内容的 AIMessage（最终回复）
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                final_response = msg.content

        print()
        print_divider()
        return final_response

    def print_status(self):
        """打印当前会话状态。"""
        print()
        cprint("── 会话状态 ──", C.BOLD)
        cprint(f"  轮次: {self.turn_count}", C.DIM)
        cprint(f"  总耗时: {self.total_time:.1f}s", C.DIM)
        cprint(f"  消息数: {len(self.messages)}", C.DIM)
        ai_count = sum(1 for m in self.messages if isinstance(m, AIMessage))
        tool_count = sum(1 for m in self.messages if isinstance(m, ToolMessage))
        cprint(f"  AI消息: {ai_count}  工具返回: {tool_count}", C.DIM)
        print()

    def print_history(self):
        """打印完整对话历史。"""
        print()
        cprint("── 对话历史 ──", C.BOLD)
        for i, msg in enumerate(self.messages):
            role = type(msg).__name__
            cprint(f"[{i}] {role}:", C.BOLD)
            print_message(msg, self.verbose, self.show_thinking)
        print()

    def print_config(self):
        """打印当前配置。"""
        print()
        cprint("── 当前配置 ──", C.BOLD)
        cprint(f"  supervisor_model: {self.ctx.supervisor_model}", C.DIM)
        cprint(f"  planner_model:    {self.ctx.planner_model}", C.DIM)
        cprint(f"  executor_model:   {self.ctx.executor_model}", C.DIM)
        cprint(f"  enable_knowledge_tree: {self.ctx.enable_knowledge_tree}", C.DIM)
        cprint(f"  knowledge_tree_root:   {self.ctx.knowledge_tree_root}", C.DIM)
        cprint(f"  enable_deepwiki:       {self.ctx.enable_deepwiki}", C.DIM)
        cprint(f"  enable_filesystem_mcp: {self.ctx.enable_filesystem_mcp}", C.DIM)
        cprint(f"  enable_implicit_thinking: {self.ctx.enable_implicit_thinking}", C.DIM)
        cprint(f"  supervisor_thinking_visibility: {self.ctx.supervisor_thinking_visibility}", C.DIM)
        cprint(f"  verbose: {self.verbose}", C.DIM)
        cprint(f"  show_thinking: {self.show_thinking}", C.DIM)
        print()

    def reset(self):
        """重置会话。"""
        self.messages = []
        self.turn_count = 0
        self.total_time = 0.0
        cprint("  ✓ 会话已重置", C.GREEN)


# ─── 主循环 ──────────────────────────────────────────────────
async def repl(session: ChatSession):
    """交互式主循环。"""
    session.print_banner()

    while True:
        try:
            line = input(f"{C.GREEN}你>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        # 命令处理
        if line.startswith("/"):
            cmd = line.lower().split()

            if cmd[0] in ("/quit", "/exit", "/q"):
                break
            elif cmd[0] == "/help":
                session.print_banner()
                continue
            elif cmd[0] == "/status":
                session.print_status()
                continue
            elif cmd[0] == "/history":
                session.print_history()
                continue
            elif cmd[0] == "/config":
                session.print_config()
                continue
            elif cmd[0] == "/reset":
                session.reset()
                continue
            elif cmd[0] == "/kt":
                if len(cmd) > 1 and cmd[1] in ("on", "off", "true", "false", "1", "0"):
                    val = cmd[1] in ("on", "true", "1")
                    session.ctx.enable_knowledge_tree = val
                    cprint(f"  ✓ 知识树: {'启用' if val else '关闭'}", C.GREEN)
                else:
                    cprint("  用法: /kt on|off", C.YELLOW)
                continue
            elif cmd[0] == "/verbose":
                if len(cmd) > 1 and cmd[1] in ("on", "off", "true", "false", "1", "0"):
                    session.verbose = cmd[1] in ("on", "true", "1")
                    cprint(f"  ✓ 详细模式: {'开启' if session.verbose else '关闭'}", C.GREEN)
                else:
                    cprint("  用法: /verbose on|off", C.YELLOW)
                continue
            elif cmd[0] in ("/thinking", "/think"):
                if len(cmd) > 1 and cmd[1] in ("on", "off", "true", "false", "1", "0"):
                    session.show_thinking = cmd[1] in ("on", "true", "1")
                    cprint(f"  ✓ 思维链: {'显示' if session.show_thinking else '隐藏'}", C.GREEN)
                else:
                    cprint("  用法: /thinking on|off", C.YELLOW)
                continue
            else:
                cprint(f"  未知命令: {cmd[0]}  输入 /help 查看帮助", C.YELLOW)
                continue

        # 发送给 Supervisor
        await session.send(line)

    print()
    cprint("再见！", C.CYAN)
    session.print_status()


# ─── CLI ─────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AgentTriad 交互式测试工具")
    p.add_argument("--model", default=None, help="Supervisor 模型 (格式: provider:model)")
    p.add_argument("--planner-model", default=None, help="Planner 模型")
    p.add_argument("--executor-model", default=None, help="Executor 模型")
    p.add_argument("--kt", action="store_true", help="启用知识树")
    p.add_argument("--kt-root", default=None, help="知识树根目录")
    p.add_argument("--verbose", "-v", action="store_true", help="详细模式（显示完整工具参数和返回）")
    p.add_argument("--thinking", action="store_true", help="显示 Supervisor 思维链")
    p.add_argument("--no-thinking", action="store_true", help="禁用隐式思维")
    return p.parse_args()


def main():
    args = parse_args()

    ctx_kwargs: dict = {}

    if args.model:
        ctx_kwargs["supervisor_model"] = args.model
    if args.planner_model:
        ctx_kwargs["planner_model"] = args.planner_model
    if args.executor_model:
        ctx_kwargs["executor_model"] = args.executor_model

    if args.kt:
        ctx_kwargs["enable_knowledge_tree"] = True
    if args.kt_root:
        ctx_kwargs["knowledge_tree_root"] = args.kt_root

    if args.no_thinking:
        ctx_kwargs["enable_implicit_thinking"] = False
    if args.thinking:
        ctx_kwargs["supervisor_thinking_visibility"] = "visible"

    ctx = Context(**ctx_kwargs)
    session = ChatSession(
        ctx,
        verbose=args.verbose,
        show_thinking=args.thinking,
    )

    try:
        asyncio.run(repl(session))
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
