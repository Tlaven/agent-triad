# 2026-07-02 探测后修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 `docs/next-actions-2026-07-02.md` 列出的 5 项探测后修复（N4 诊断、决策31死代码清理、prompts 暗示排查、Executor os.getcwd 修复、失败教训机制推广）。

**Architecture:** 5 个任务相互独立可分别提交。Task 1/3/4 为调查型（加诊断→复现→分支决策），Task 2/5 为确定型（TDD 删代码 / 写脚本）。调研已确认关键事实：`call_model:618` 只 strip 不注入 tool_calls → N4 的 B 路径（后处理注入）已排除；prompts.py 无"每次先 list"暗示 → #3 根因同 #1；`langgraph dev` 吞 logger 输出（p0-beta §七）→ 所有诊断日志必须写文件或 print(flush=True)。

**Tech Stack:** Python 3.12 / LangGraph / LangChain / FastAPI+uvicorn / pytest / ruff+mypy(strict) / uv。

**依据**：[`next-actions-2026-07-02.md`](../../next-actions-2026-07-02.md) · [`probe-analysis-2026-07-02.md`](../../probe-analysis-2026-07-02.md) · [`p0-beta-diagnosis-2026-06-30.md`](../../p0-beta-diagnosis-2026-06-30.md) §七。

---

## 关键调研结论（写计划前已确认）

| 事实 | 来源 | 对计划的影响 |
|------|------|-------------|
| `call_model:618-627` 只 strip 不注入 tool_calls | `src/supervisor_agent/graph.py:618-627` | N4 的 B 路径排除，tool_calls 必来自 LLM 本身（A）或 bind_tools 包装（C） |
| `dynamic_tools_node:650` 执行工具，不注入 tool_calls 到 response | `src/supervisor_agent/graph.py:650` | 同上，确认 B 路径排除 |
| prompts.py 只描述工具用途，无"每次先 list"暗示 | `src/supervisor_agent/prompts.py:106-132` | #3 根因同 #1，无独立代码改点 |
| 决策31谓词触发 0 次（07-01+07-02 两次探测） | `probe-analysis-2026-07-02.md:52` | #2 删死代码方向 A |
| `_looks_like_final_answer` 要求 content≥80+markdown → N4 场景 content="1024" 不命中 | `src/supervisor_agent/graph.py:1437-1439` | 决策31谓词对 N4 无效，N4 需独立修复 |
| `langgraph dev` 吞 logger，`[PROBE-TIMING]` 不透 stdout | `p0-beta-diagnosis-2026-06-30.md:254` | 诊断日志必须写文件，不能依赖 logger.info |
| `executor_agent/graph.py:231` 已 `asyncio.to_thread(os.getcwd)` | `src/executor_agent/graph.py:231` | os.getcwd BlockingError 嫌疑在 observation.py:59 或第三方库 |
| `__main__.py` 用同步 `uvicorn.Server().run()` | `src/executor_agent/__main__.py:52` | 同步入口本身不产生 BlockingError，需抓堆栈定位 |

---

## File Structure

| 文件 | 改动类型 | 职责 |
|------|---------|------|
| `src/supervisor_agent/graph.py` | Modify (Task 1 加诊断 / Task 2 删决策31) | 主循环 |
| `src/supervisor_agent/prompts.py` | Read-only (Task 3 确认) | 系统提示 |
| `src/executor_agent/__main__.py` | Modify (Task 4 加异常日志) | 子进程入口 |
| `src/executor_agent/server.py` | Modify (Task 4 加异常日志) | FastAPI 服务器 |
| `src/common/observation.py` | Read+Maybe Modify (Task 4) | Observation 规范化 |
| `tests/unit_tests/supervisor_agent/test_mode_discipline.py` | Delete (Task 2) | 决策31测试 |
| `scripts/ingest_known_issues.py` | Create (Task 5) | 批量摄入脚本 |
| `docs/architecture-decisions.md` | Modify (Task 2 标注撤销) | 决策记录 |
| `docs/troubleshooting.md` | Modify (Task 2 更新) | 排查指南 |
| `docs/environment-variables.md` | Modify (Task 2 删除 §10) | 环境变量参考 |
| `CLAUDE.md` | Modify (Task 2 更新硬约束段) | 助手必读 |
| `logs/n4-diag.log` | Create at runtime (Task 1) | 诊断输出 |

---

## Task 1: N4 诊断——定位 LLM content 与 tool_calls 解耦根因

**Files:**
- Modify: `src/supervisor_agent/graph.py:575-597`（LLM 调用后加诊断）
- Create at runtime: `logs/n4-diag.log`

**背景**：s002 t15 用户问"2 的 10 次方？"并要求"不调用任何工具"，Agent content 回答"1024"但 tool_calls 数组有 10 个工具调用。这是比 06-29 mode 路由脱节更深的 bug。B 路径（后处理注入）已通过读代码排除——`call_model:618` 只 strip 不注入。

- [ ] **Step 1: 加诊断日志到 call_model，写文件避开 langgraph dev 吞日志**

在 `src/supervisor_agent/graph.py` 顶部 import 区之后（约 line 30 附近，与其他模块级常量一起）加诊断 helper：

```python
import time as _time


def _n4_diag(msg: str) -> None:
    """N4 诊断日志：写文件，避开 langgraph dev 吞 logger 的问题。探测后删除。"""
    try:
        with open("logs/n4-diag.log", "a", encoding="utf-8") as _f:
            _f.write(f"{_time.time():.3f} {msg}\n")
    except OSError:
        pass
```

在 `call_model` 内 LLM 响应返回后（line 596 `)` 之后、line 615 `if _is_thinking_visible...` 之前）加诊断：

```python
        _n4_diag(
            "call_model raw response: content_len=%d content_head=%r tool_calls=%s"
            % (
                len(response.content or "") if isinstance(response.content, str) else len(str(response.content or "")),
                (response.content or "")[:60],
                [tc.get("name") for tc in (response.tool_calls or [])],
            )
        )
```

- [ ] **Step 2: 验证诊断日志写入**

启动 dev_probe 并触发一次简单问答（不要求复现 N4，只验证日志文件能写）：

Run: `make dev_probe`（另一终端）
Run: 用 probe 客户端发一句"你好"
Run: `Get-Content logs\n4-diag.log -Tail 5`
Expected: 文件存在且每行含 `call_model raw response:`，`content_len` / `tool_calls` 字段有值。

- [ ] **Step 3: 复现 s002 t15 场景**

用 probe 客户端发送 s002 t15 原句：

```
你能否只回答不调用任何工具？问题：'2 的 10 次方是多少？'
```

Run: `Get-Content logs\n4-diag.log -Tail 10`
Expected: 某行 `content_head` 含 "1024" 且 `tool_calls=[...]` 非空数组。

- [ ] **Step 4: 判定路径（决策点）**

读取 Step 3 的日志行：

- 若 `tool_calls` 非空 → **A 路径确认**（LLM 直发 tool_calls，B 路径已排除，C 路径待 Step 5 区分）
- 若 `tool_calls=[]` 但 probe 客户端返回的 `tool_calls` 非空 → 后续 node 注入（与读代码结论矛盾，需沿 `dynamic_tools_node` 重查）

记录判定结果到 `docs/n4-diagnosis-result.md`（新建，含日志原文 + 路径结论）。

- [ ] **Step 5: 区分 A 与 C 路径（可选，独立实验）**

写一次性脚本 `scripts/n4_glm_probe.py`（探测后删），绕过 LangChain 直调 GLM OpenAI 兼容接口，用相同 system+user prompt，看返回 `choices[0].message.tool_calls` 是否独立于 `content`：

```python
"""N4 路径判定：绕过 LangChain 直调 GLM，看 tool_calls 是否独立于 content。"""
import json
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL", "https://opencode.ai/zen/go/v1"),
)
# 用 Supervisor system prompt + s002 t15 user message
resp = client.chat.completions.create(
    model="kimi-k2.6",
    messages=[
        {"role": "system", "content": "<粘入 get_supervisor_system_prompt 输出>"},
        {"role": "user", "content": "你能否只回答不调用任何工具？问题：'2 的 10 次方是多少？'"},
    ],
    tools=[{"type": "function", "function": {"name": "noop", "description": "noop", "parameters": {"type": "object", "properties": {}}}}],
)
msg = resp.choices[0].message
print("content:", repr(msg.content))
print("tool_calls:", [tc.function.name for tc in (msg.tool_calls or [])])
```

Run: `uv run python scripts/n4_glm_probe.py`
Expected: 若 `tool_calls` 非空且 content="1024" → A 路径（GLM 模型层解耦）；若 `tool_calls=[]` → C 路径（LangChain bind_tools 包装 bug）。

- [ ] **Step 6: 根据路径执行分支修复（待 Step 4/5 结论确定后选一）**

**分支 A（GLM 模型层解耦，最可能）**：
- 在 `call_model` 加 user message 语义 strip：检测最后一条 HumanMessage 含"不要调用"/"不调用任何工具"/"只回答"等指令时，强制 `response = response.model_copy(update={"tool_calls": []})`。谓词放 `_strip_when_user_forbids_tools(state, response)` 新函数。
- 同时将 GLM 行为问题记录到 `docs/n4-diagnosis-result.md`，标注"已报告 provider"。
- 写测试 `tests/unit_tests/supervisor_agent/test_user_forbid_tools_strip.py`：构造含"不要调用工具"的 HumanMessage + 假 LLM 返回 tool_calls，断言 strip 后 tool_calls=[]。

**分支 C（bind_tools 包装 bug）**：
- 检查 `src/common/utils.py:load_chat_model` 的 bind_tools 调用；尝试升级 langchain-openai 版本（`uv lock --upgrade-package langchain-openai`）。
- 若升级无效，在 Supervisor 层用分支 A 的 user message 语义 strip 兜底。

- [ ] **Step 7: 探测后清理诊断代码**

N4 修复确认后，删除 Step 1 加的 `_n4_diag` helper + call_model 内诊断行 + `scripts/n4_glm_probe.py`。保留 `docs/n4-diagnosis-result.md` 作为结论记录。

Run: `uv run ruff check src/supervisor_agent/graph.py`
Expected: 无 lint 错误。

- [ ] **Step 8: 提交**

```bash
git add src/supervisor_agent/graph.py tests/unit_tests/supervisor_agent/test_user_forbid_tools_strip.py docs/n4-diagnosis-result.md
git commit -m "fix(supervisor): N4 — strip tool_calls when user explicitly forbids tools"
```

---

## Task 2: 删除决策31死代码（独立可先做）

**Files:**
- Modify: `src/supervisor_agent/graph.py:618-627`（删 strip 逻辑）
- Modify: `src/supervisor_agent/graph.py:1412-1446`（删常量 + `_looks_like_final_answer`）
- Delete: `tests/unit_tests/supervisor_agent/test_mode_discipline.py`
- Modify: `docs/architecture-decisions.md:1049-1103`（决策31段标注撤销）
- Modify: `docs/troubleshooting.md:155-157`（更新排查项）
- Modify: `docs/environment-variables.md:215-` （删 §10）
- Modify: `CLAUDE.md`（硬约束段"Mode 纪律（决策 31）"行）

**背景**：决策31谓词在 07-01+07-02 两次探测触发 0 次。根因：LLM 当前行为下，mode1 时 content 完整但 tool_calls=[]（谓词不适用），mode2 时 content 为空+tool_calls 非空（content<80 不命中）。谓词是死代码。方向 A（删除）推荐。

- [ ] **Step 1: 先删测试文件（确认被删后实现才删，避免悬空引用）**

Run: `git rm tests/unit_tests/supervisor_agent/test_mode_discipline.py`

- [ ] **Step 2: 删除 call_model 内 strip 逻辑**

删除 `src/supervisor_agent/graph.py:618-627` 整段：

```python
    if response.tool_calls and _looks_like_final_answer(
        response.content, response.tool_calls
    ):
        if os.getenv("SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS", "1") != "0":
            logger.info(
                "[MODE-DISCIPLINE] strip tool_calls=%s content_len=%d",
                [tc.get("name") for tc in response.tool_calls],
                len(response.content or ""),
            )
            response = response.model_copy(update={"tool_calls": []})
```

保留前后空行结构（line 615 的 thinking-visible 处理 + line 629 的 `_infer_supervisor_decision` 不动）。

- [ ] **Step 3: 删除常量与谓词函数**

删除 `src/supervisor_agent/graph.py:1412-1446` 整段（`_FINAL_STRUCT_RE` + `_PROCESS_MARKERS` + `_looks_like_final_answer`）。注意保留 line 1449 的 `_is_thinking_visible` 及之后函数。

- [ ] **Step 4: 检查 `import re` 是否还被使用**

Run: `grep -n "re\." src/supervisor_agent/graph.py`
Expected: 若无其他 `re.` 使用，删除顶部 `import re`；若有，保留。

- [ ] **Step 5: 检查 `os` 是否还被使用**

Run: `grep -n "os\." src/supervisor_agent/graph.py`
Expected: `os.getenv` 可能还有其他用途（如 line 621 已删）。若无其他 `os.` 使用，删除顶部 `import os`；若有，保留。

- [ ] **Step 6: 跑单元测试确认无破坏**

Run: `uv run pytest tests/unit_tests/supervisor_agent/ -q`
Expected: 全绿（test_mode_discipline.py 已删，其余测试不应引用 `_looks_like_final_answer`）。

- [ ] **Step 7: lint + typecheck**

Run: `uv run ruff check src/supervisor_agent/graph.py`
Run: `uv run mypy --strict src/supervisor_agent/graph.py`
Expected: 无错误。

- [ ] **Step 8: 更新 CLAUDE.md 硬约束段**

删除 CLAUDE.md 中"Mode 纪律（决策 31）"那一行（位于"硬约束"列表）：

原文：
```
- **Mode 纪律**（决策 31）：LLM 输出完整答案但冗余调工具时，`call_model` 内 strip 掉 `tool_calls`。判别：长度 ≥ 80 + markdown 结构 + 无过程词（`接下来`/`我将`/`[EXECUTOR_RESULT]` 等）+ 非 mode-3。env `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS=0` 即时关闭。
```

替换为：
```
- **Mode 纪律**（决策 31，已撤销 2026-07-03）：探测触发 0 次，谓词为死代码，已删除 strip 逻辑与 `_looks_like_final_answer`。mode 路由脱节问题由 N4 修复（见决策 34/Task 1）。
```

- [ ] **Step 9: 更新 architecture-decisions.md 决策31段**

在 `docs/architecture-decisions.md:1049` 标题行下方加撤销标注：

```markdown
## 决策 31：Supervisor mode 纪律——strip 冗余 tool_calls

> ⚠️ **已撤销（2026-07-03）**：07-01+07-02 两次探测中谓词触发 0 次（mode1 时 content 完整但 tool_calls=[]，谓词不适用；mode2 时 content 为空，content<80 不命中）。底层 mode 路由脱节问题已被更深的 N4（LLM content/tool_calls 解耦）取代。strip 逻辑、`_looks_like_final_answer`、`_FINAL_STRUCT_RE`、`_PROCESS_MARKERS`、env `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS`、测试 `test_mode_discipline.py` 均已删除。N4 修复见 Task 1 / `docs/n4-diagnosis-result.md`。原决策文本保留于下，仅供历史溯源。
```

保留原决策正文不动（历史溯源）。

- [ ] **Step 10: 更新 troubleshooting.md**

将 `docs/troubleshooting.md:155-157` 的决策31排查项替换为撤销说明：

```markdown
**解决**：决策 31 已于 2026-07-03 撤销（探测触发 0 次，死代码）。mode 路由脱节问题由 N4 修复（用户明示禁止工具时 strip tool_calls）。

**关联**：[`architecture-decisions.md`](architecture-decisions.md) 决策 31（已撤销）；N4 诊断 [`n4-diagnosis-result.md`](n4-diagnosis-result.md)。
```

- [ ] **Step 11: 更新 environment-variables.md**

删除 `docs/environment-variables.md` §10「Mode 纪律（决策 31）」整节（含 `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS` 条目）。在目录/索引中移除该节引用。

- [ ] **Step 12: 验证文档无悬空引用**

Run: `grep -rn "SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS\|_looks_like_final_answer\|MODE-DISCIPLINE" docs/ src/ tests/`
Expected: 仅 `architecture-decisions.md` 决策31历史段 + `n4-diagnosis-result.md`（如有）出现，无 src/ 或 tests/ 命中。

- [ ] **Step 13: 提交**

```bash
git add src/supervisor_agent/graph.py tests/unit_tests/supervisor_agent/test_mode_discipline.py CLAUDE.md docs/architecture-decisions.md docs/troubleshooting.md docs/environment-variables.md
git commit -m "refactor(supervisor): 撤销决策31死代码 — strip 谓词触发0次已删"
```

---

## Task 3: 确认 prompts.py 无"每次 list 元规则"暗示

**Files:**
- Read-only: `src/supervisor_agent/prompts.py:106-132`
- Read-only: `src/supervisor_agent/graph.py:286-420`（kt_retrieve_node）
- Create: `docs/n4-diagnosis-result.md`（追加 #3 结论，与 Task 1 Step 4 同文件）

**背景**：s002 t8-t16 稳定出现冗余 `list_meta_rules + add_meta_rule + delete_meta_rule + call_executor` 序列，跨多轮稳定不像自由决策。调研已读 prompts.py:106-132，未发现"每次先 list"文本暗示。

- [ ] **Step 1: grep 全 supervisor 包确认无 prompt 文本暗示**

Run: `grep -rn "list_meta_rules\|每次.*list\|先.*查看.*元规则\|先.*list" src/supervisor_agent/`
Expected: 仅 `tools.py:1068`（工具注册）和 `prompts.py:117`（工具描述 `knowledge_tree_list_meta_rules()` — 查看当前所有元规则）命中，无"每次先 list"指令性文本。

- [ ] **Step 2: 确认 kt_retrieve_node 不每轮注入固定工具序列**

Read: `src/supervisor_agent/graph.py:286-420`
Expected: kt_retrieve_node 只写 `kt_context` / `kt_meta_rules` / `kt_optimization_suggestions` / `kt_snapshot_data` 到 state，不注入 tool_calls。call_model:527-554 把 kt_meta_rules 拼到 system_message，也不注入 tool_calls。

- [ ] **Step 3: 记录结论**

在 `docs/n4-diagnosis-result.md`（Task 1 Step 4 创建）追加 §3：

```markdown
## §3 元规则冗余序列排查（next-actions #3）

**结论**：prompts.py:106-132 只描述工具用途，无"每次先 list"指令性暗示。kt_retrieve_node
（graph.py:286-420）只写 state 字段，不注入 tool_calls。call_model:527-554 把元规则拼到
system_message 作为指令注入，但这是"遵守规则"语义，不触发"list/add/delete"序列。

**根因归并**：冗余 list/add/delete meta_rule 序列与 N4 同根——均为 LLM 在 content 之外
独立 emit tool_calls 的表现。修复随 Task 1 N4 修复生效，无独立代码改点。
```

- [ ] **Step 4: 提交（若 Task 1 未一并提交此文件）**

```bash
git add docs/n4-diagnosis-result.md
git commit -m "docs(probe): #3 元规则冗余序列排查结论 — 根因同 N4"
```

---

## Task 4: 修复 Executor os.getcwd BlockingError

**Files:**
- Modify: `src/executor_agent/__main__.py:34-59`（加异常日志）
- Modify: `src/executor_agent/server.py:75-160`（加 traceback）
- Maybe Modify: `src/common/observation.py:59`（若堆栈指向此处）
- Create at runtime: `logs/executor-startup.log`

**背景**：07-02 探测 Executor 子进程启动即崩 `BlockingError: Blocking call to os.getcwd`。`__main__.py` 用同步 `uvicorn.Server().run()`，本身不在 asyncio 上下文。`graph.py:231` 已 `to_thread` 包装。嫌疑在 `observation.py:59`（同步 `os.getcwd()`）或第三方库。N1 缓解（`--no-reload`）让 Supervisor 不再卡死，但 Executor 仍崩，mode 2/3 全失败。

- [ ] **Step 1: 在 __main__.py 加未捕获异常日志**

将 `src/executor_agent/__main__.py:34-59` 的 `if __name__ == "__main__":` 块用 try/except 包裹，写 traceback 到文件：

```python
if __name__ == "__main__":
    import logging
    import traceback

    logging.basicConfig(
        filename="logs/executor-startup.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("executor_main")

    try:
        import uvicorn

        port = int(os.environ.get("EXECUTOR_PORT", "0"))
        port_file = _get_port_file()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if port != 0:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        actual_port = sock.getsockname()[1]

        _write_port_file(port_file, actual_port)
        log.info("Executor binding port=%d (plan_id=%s)", actual_port, os.environ.get("PLAN_ID", ""))

        sock.listen()
        uvicorn.Server(
            uvicorn.Config(
                "src.executor_agent.server:app",
                host="0.0.0.0",
                port=actual_port,
                log_level="info",
            )
        ).run(sockets=[sock])
    except Exception:
        traceback.print_exc()
        log.error("Executor 启动失败:\n%s", traceback.format_exc())
        raise
```

- [ ] **Step 2: 在 server.py lifespan 加启动日志**

`src/executor_agent/server.py:163-171` 的 `lifespan` 已有 `logger.info`，但 langgraph dev 可能吞。补一行写文件：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    logging.basicConfig(
        filename="logs/executor-startup.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Executor server started (PID=%d)", os.getpid())
    yield
    for plan_id, task in _running_tasks.items():
        task.cancel()
        logger.info("Cancelled task for plan_id=%s during shutdown", plan_id)
    _running_tasks.clear()
```

- [ ] **Step 3: 复现并抓堆栈**

Run: `make dev_probe`（终端 A）
Run: 用 probe 客户端发一个 mode2 任务（如"在 workspace 下创建一个 test.txt 文件"）
Run: `Get-Content logs\executor-startup.log -Tail 50`
Expected: 捕获 `BlockingError: Blocking call to os.getcwd` 的完整 traceback，定位具体文件:行号。

- [ ] **Step 4: 根据堆栈定位修复（决策点）**

读取 Step 3 堆栈，按调用源分支：

**分支 A：堆栈指向 `observation.py:59`（同步 `os.getcwd()`）**
- `normalize_observation` 是同步函数，需确认其调用方。grep：
  Run: `grep -rn "normalize_observation" src/`
- 若调用方在 async 上下文直接调用（未 `to_thread`），改调用方包 `await asyncio.to_thread(normalize_observation, result, context=context, cwd=cwd)`。
- 或改 `observation.py:59` 接受调用方传入的 cwd（已有 `cwd: str | None = None` 参数），让调用方在 to_thread 中先算好 cwd 传入，移除内部 `os.getcwd()` 兜底。

**分支 B：堆栈指向第三方库（uvicorn/langgraph_api 内部）**
- 若 `uvicorn` 内部 `os.getcwd`：在 `__main__.py` 启动前显式 `os.chdir(str(Path(__file__).resolve().parent))` 把 cwd 锁定到已知目录（但这有副作用，需评估）。
- 若 `langgraph_api` 检测器误报：在 `langgraph.json` 或环境变量层面配置 `allow_blocking`（仅抑制，不解决根因，作为兜底）。

**分支 C：堆栈指向 `executor_agent/graph.py:231` 之外的其他 os.getcwd**
- grep 全 Executor 调用链：
  Run: `grep -rn "os.getcwd\|getcwd\|Path.cwd" src/executor_agent/ src/common/`
- 对每个命中点，若在 async 函数内，包 `asyncio.to_thread`；若在同步函数，确保调用方在 to_thread 中。

- [ ] **Step 5: 实施修复并验证**

按 Step 4 分支实施修复后：

Run: `make dev_probe` + probe 发 mode2 任务
Expected: `logs/executor-startup.log` 无 BlockingError；probe 客户端返回 `status=success`，Executor 实际执行了任务（如 test.txt 已创建）。

Run: `uv run pytest tests/integration_tests/ -q -k executor`
Expected: Executor 集成测试通过。

- [ ] **Step 6: lint + typecheck**

Run: `uv run ruff check src/executor_agent/ src/common/observation.py`
Run: `uv run mypy --strict src/executor_agent/ src/common/observation.py`
Expected: 无错误。

- [ ] **Step 7: 保留启动日志配置（可选）或回退**

若 `logging.basicConfig(filename=...)` 对生产路径有副作用（如重复配置），评估是否保留。最小化方案：仅保留 `__main__.py` 的 try/except + traceback 写文件，回退 server.py 的 basicConfig 改动（lifespan 用原有 logger 即可）。

- [ ] **Step 8: 提交**

```bash
git add src/executor_agent/__main__.py src/executor_agent/server.py src/common/observation.py
git commit -m "fix(executor): 修复 os.getcwd BlockingError — <分支说明>"
```

---

## Task 5: 失败教训机制推广——批量摄入历史 known issues

**Files:**
- Create: `scripts/ingest_known_issues.py`
- Read: `logs/probes/state.json`（数据源，需先确认存在）
- Read: `src/common/knowledge_tree/`（ingest API）

**背景**：07-02 s001 t36 实战验证决策32失败教训 inject 机制生效（Agent 从 06-29 假阴性学会了区分"项目根目录"vs"Executor 工作区"）。06-29 的 10 个 known issues 中仅 #8 被摄入。推广：批量摄入其余 known issues 为失败教训节点。

- [ ] **Step 1: 确认数据源与 ingest API**

Run: `Test-Path logs/probes/state.json`（若不存在，先 `Get-ChildItem logs/probes/ -Recurse -Filter state.json` 定位）
Read: `logs/probes/state.json` 中 `known_issues_found` 字段结构。
Read: `src/common/knowledge_tree/` 的 `__init__.py` 找 ingest 入口（`get_or_create_kt` + ingest 方法签名）。
Read: `src/supervisor_agent/graph.py:1154-1212`（`_try_auto_ingest_executor_result` 参考摄入方式：`extract_knowledge_from_executor_result` + `extract_experience_from_executor_result`）。

记录到 `docs/ingest-known-issues-design.md`（新建）：数据源路径、字段结构、ingest API 签名、拟摄入的 known issues 列表。

- [ ] **Step 2: 写摄入脚本骨架**

创建 `scripts/ingest_known_issues.py`：

```python
"""批量摄入历史 known issues 为失败教训节点（node_type=experience, executor_status=failed）。

数据源：logs/probes/state.json:known_issues_found
摄入路径：get_or_create_kt() + ingest（带 metadata.executor_status="failed"）
检索侧：失败教训节点 inject 时自动加 [失败教训] 前缀（决策32）。

Usage: uv run python scripts/ingest_known_issues.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATE_PATH = Path("logs/probes/state.json")


def load_known_issues() -> list[dict]:
    if not STATE_PATH.exists():
        print(f"数据源不存在: {STATE_PATH}", file=sys.stderr)
        return []
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    issues = data.get("known_issues_found", [])
    return issues if isinstance(issues, list) else []


def ingest_one(kt, issue: dict, dry_run: bool) -> str:
    title = issue.get("title") or issue.get("summary") or issue.get("id", "unknown")
    content = issue.get("detail") or issue.get("description") or issue.get("summary", "")
    trigger = f"known_issue:{issue.get('id', title)}"
    if dry_run:
        print(f"[dry-run] would ingest: {title}")
        return "dry-run"
    # 调用 KT ingest API（具体方法名以 Step 1 确认为准）
    node_id = kt.ingest(
        text=f"{title}\n\n{content}",
        trigger=trigger,
        node_type="experience",
        metadata={"executor_status": "failed", "source": "known_issues_batch"},
    )
    return node_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    issues = load_known_issues()
    if not issues:
        print("无 known issues 可摄入。")
        return 0

    from src.common.knowledge_tree import get_or_create_kt
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    kt = get_or_create_kt(KnowledgeTreeConfig())
    ingested = 0
    for issue in issues:
        try:
            ingest_one(kt, issue, args.dry_run)
            ingested += 1
        except Exception as e:
            print(f"摄入失败 {issue.get('id', '?')}: {e}", file=sys.stderr)
    print(f"完成：摄入 {ingested}/{len(issues)} 条 known issues。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: dry-run 验证**

Run: `uv run python scripts/ingest_known_issues.py --dry-run`
Expected: 打印每条 known issue 的 title，无报错。若数据源字段名与脚本不符，按 Step 1 确认的真实字段调整 `issue.get(...)` 的 key。

- [ ] **Step 4: 实际摄入**

Run: `uv run python scripts/ingest_known_issues.py`
Expected: 输出 "完成：摄入 N/M 条 known issues。" 失败教训节点写入知识树。

- [ ] **Step 5: 验证 inject 生效**

Run: `make dev_probe` + probe 发一句与 known issue 相关的问题（如"Executor 启动为什么会崩？"）
Run: `Select-String -Path logs\dev-server.log -Pattern "失败教训"`
Expected: ≥1 行命中，证明失败教训节点被 inject。

- [ ] **Step 6: lint + typecheck**

Run: `uv run ruff check scripts/ingest_known_issues.py`
Run: `uv run mypy --strict scripts/ingest_known_issues.py`
Expected: 无错误（或仅 KT ingest API 类型不精确的已知警告，酌情加 `# type: ignore`）。

- [ ] **Step 7: 提交**

```bash
git add scripts/ingest_known_issues.py docs/ingest-known-issues-design.md
git commit -m "feat(kt): 批量摄入历史 known issues 为失败教训节点"
```

---

## 执行顺序建议

1. **Task 2（独立确定，先做）** — 删死代码，减少代码噪音，无依赖。
2. **Task 1 + Task 3（调查型，一起做）** — N4 诊断 + #3 归并，同根同修。
3. **Task 4（调查型，独立）** — Executor 修复，让 mode 2/3 可用，是产品里程碑。
4. **Task 5（产品优化，最后）** — 失败教训推广，主流程稳定后锦上添花。

---

## 验证探测（全部完成后）

完成 Task 1-5 后，跑一次定点探测（参照 07-01 规模 ~7-10 turns），重点验证：

- [ ] `Select-String -Path logs\dev-server.log -Pattern "MODE-DISCIPLINE"` 无命中（死代码已删）
- [ ] N4 不复现（"不调工具却调了"模式消失，probe 客户端 tool_calls 与 content 一致）
- [ ] Executor 成功执行任意 mode 2 任务（probe 返回 status=success）
- [ ] 冗余 list/add/delete meta_rule 序列消失（随 N4 修复生效）
- [ ] `Select-String -Path logs\dev-server.log -Pattern "失败教训"` ≥1 命中

---

## Self-Review

**1. Spec coverage**（对照 next-actions 5 项）：
- #1 N4 诊断 → Task 1 ✅
- #2 撤销决策31 → Task 2 ✅
- #3 prompts 暗示排查 → Task 3 ✅
- #4 Executor os.getcwd → Task 4 ✅
- #5 失败教训推广 → Task 5 ✅

**2. Placeholder scan**：无 TBD/TODO。调查型任务（Task 1 Step 6、Task 4 Step 4）的分支修复方向已列出具体代码/命令，待探测结果选分支——这是调查任务的固有不确定性，非占位符。

**3. Type consistency**：
- `_n4_diag(msg: str) -> None`（Task 1）— 仅在 graph.py 内部用，签名一致。
- `ingest_one(kt, issue: dict, dry_run: bool) -> str`（Task 5）— `kt` 参数类型依赖 Step 1 确认的 API，脚本内 `from ... import get_or_create_kt` 一致。
- Task 2 删除的函数/常量在其余 Task 无引用（Task 1 的 strip 分支用新函数 `_strip_when_user_forbids_tools`，不复用 `_looks_like_final_answer`）。
