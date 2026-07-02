# 夜间探测分析报告 — 2026-07-01

> 探测窗口：11:02 → 11:38（约 36 分钟，定点复现规模）
> 数据来源：`logs/probes/2026-07-01/dev-server.log`（dev server stdout/stderr 全量重定向）+ `scripts/probe_supervisor.py` send JSON 行
> 基线对比：[`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md)（69 turns / 7 sessions / 10 known issues）
> 验证清单：[`probe-validation-checklist-2026-07.md`](probe-validation-checklist-2026-07.md)

---

## TL;DR

| 号 | 决策 | 验证手段 | 结论 |
|----|------|---------|------|
| 31 | strip 冗余 tool_calls（mode 纪律） | 触发 4 个 mode-2 候选场景 + 1 个典定 strip 候选 | ❌ **未生效**：`[MODE-DISCIPLINE]` 标记 0 次；典定 strip 场景仍未触发 |
| 32 | Entry A provenance tagging | 检视 `workspace/kt_probe/**/*.md` 元数据 + grep inject 日志 | ⚠️ **部分生效**：摄入层 `executor_status=failed` ✅；inject 层 `[失败教训]` 前缀未观测（无检索命中失败节点） |
| 33 | Thread bricked 自愈 | 诱导 MAX_REPLAN 后续跑 | ❌ **不可达**：genuine-mode3 run 卡死在 `_wait_for_executor_result`，永不抵达 MAX_REPLAN |

**一句话结论**：三个决策的代码路径已部署，但 **decision 31 strip 谓词在实战中匹配不上 LLM 的实际输出**（模型要么完整作答 mode-1，要么 mode-2 空 content 委派 Executor，从不"完整答案+冗余工具"并存），导致 #4/#8/#10 触发场景的 Executor 阻塞回归**未被修复**；decision 33 的恢复分支根本无法触发——任何进 Executor 的真 mode-3 都会撞上 06-29 已识别但未修复的 Executor 阻塞（`commit 47121a2` 仍**未真正修复**）。

---

## 一、启动前检查

- [x] `state.json` 归档：06-29 状态为 `stopped`，已归档为 `logs/probes/state.json.archived-2026-06-29-stopped` 并删除原文件，避免 stale state 自毁。
- [x] dev server 端口 2024：原 PID 44280 未重定向 → 已 `Stop-Process` 解放端口，重启 `uv run langgraph dev --config langgraph.json --port 2024 --no-browser` 并把 stdout/stderr 重定向到 `logs/probes/2026-07-01/dev-server.log`（新 PID 39392 cmd wrapper；后续被 watchfiles 热重载替换为 PID 55852）。
- [x] API key 有效：`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 均已设；`OPENAI_BASE_URL=https://opencode.ai/zen/go/v1`、`ANTHROPIC_BASE_URL=https://opencode.ai/zen/go`。`scripts/probe_supervisor.py health` → `{"status":"ok", elapsed_s:0.533}`。
- [x] `.env` 未把 `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS` 设为 `0`（环境变量缺失 → 默认 `1` 开启）。

---

## 二、数据对比表（vs 06-29 基线）

| 指标 | 06-29 基线 | 07-01 实测 | 改善目标 | 是否达成 |
|------|-----------|-----------|---------|---------|
| 总轮数 | 69 | 7（定点复现规模） | — | n/a |
| 会话数 | 7 | 3 | — | n/a |
| bad turns | 13 (19%) | 4 (57%) | 显著下降 | ❌ 未达成（小样本 + Executor 阻塞主导） |
| Executor 阻塞超时（≥180s） | 4 次 | 3 次（S001-t2、S002-t2、S003-t2） | 接近 0（决策 31） | ❌ 未达成 |
| MAX_REPLAN bricked session | 3 (s001/s004/s006) | 0 | = 0（决策 33） | ⚠️ 0 但因 run 永不抵达 MAX_REPLAN（不可达 ≠ 修复） |
| Entry A 假阴性高可信摄入 | 2 (s002-t5/s004-t4) | 0（无检索命中失败节点） | 全部带 `[失败教训]` tag | ⚠️ 摄入层 tagging ✅ / inject 层未观测 |
| Thread bricked 浪费 turns | 12 (17%) | 0 | = 0 | ✅（但源于 run 被客户端 cancel，非自愈） |
| Trigger 词持续扩散 | 是 | 收缩（仅复核 #2/#3/#4/#8/#10） | 稳定或收缩 | n/a |
| `[MODE-DISCIPLINE]` 标记频次 | — | **0** | ≥ 1-2 次 | ❌ 未达成 |
| `[BRICKED-RECOVERY]` 标记频次 | — | **0** | ≥ 1 次 | ❌ 未触发 |

---

## 三、3 个决策的观测证据

### 决策 31 — mode 纪律（strip 冗余 tool_calls）

**实现复核**（`src/supervisor_agent/graph.py:618-627`、`_looks_like_final_answer` at `graph.py:1433-1446`）：
strip 谓词要求 **全部满足**：
1. `response.tool_calls` 非空
2. `content` 是 `str` 且 `len >= 80`
3. `_FINAL_STRUCT_RE.search(content)` 命中（markdown 结构）
4. content 不含任一 `_PROCESS_MARKERS`（`接下来`/`我将`/`[EXECUTOR_RESULT]` 等）
5. `tool_calls` 中不含 `call_planner`
6. env `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS` ≠ `"0"`

满足后清空 tool_calls 并记 `[MODE-DISCIPLINE] strip tool_calls=[...] content_len=N`。

**实机观测**：`grep 'MODE-DISCIPLINE' dev-server.log` → **0 次**。

**为何不触发**（核心发现）——LLM 的实际输出模式从未命中 strip 谓词：

| turn | 模型实际输出 | 命中谓词？ | 结果 |
|------|------------|----------|------|
| S001-t1 暖身 | mode1 + `knowledge_tree_status` 工具，无完整文本 | ✗ 无 tool_calls 冗余 | good（20.5s） |
| S002-t1 #2 "ReAct 区别" | mode1 + 2×`knowledge_tree_retrieve`，工具后才有答 | ✗ 是合理 mode1，不应 strip | good（43.77s，比 06-29 的 240s 显著改善） |
| S002-t2 #4 "Executor 内置工具" | mode2 + `[KT×2, call_executor]`，**content 为空**（委派模式） | ✗ `len(content)<80` → `_looks_like_final_answer=False` | timeout 91s（**回归未修复**） |
| S003-t1 "timeout 默认值" | mode1 + `tool_calls=[]`，直接作答 | ✗ 无 tool_calls | good（12.81s） |
| **S003-t2 #8 "查找 timeout 配置，直接给结论"** | mode2 + `[call_executor]`，**content 为空**，但上一轮已留完整答案 | ✗ `len(content)<80` → strip 不触发 | timeout 90.99s（**回归未修复**） |

> 虽然 S003-t2 的 AI state 里残留有 t1 完整答案（"180 秒…"），但 **本响应的 `response.content` 为空**——LLM 选择 mode2 时只会 emit tool_calls 而不重述答案。strip 谓词锁的是本响应 content，看不到历史上轮的答案。
>
> checklist "s002-t5 类场景应触发 strip" 的判定**未达成**：因为模型在该场景下既不重写完整答案，也不附带冗余 `call_executor`——它要么纯作答(mode1)，要么纯委派(mode2)。**06-29 看到的"完整答案 + 冗余 Executor 调用"的并存模式在本轮实测中没复现**。

**结论**：decision 31 strip 代码正确，但**实证触发 0 次**；mode-2 触发词误判（#4 "Executor 内置工具"、#8/#10 "查找 timeout 配置"）的 Executor 阻塞回归**未被修复**。

### 决策 32 — Entry A provenance tagging

**实机证据 1（摄入层 ✅）**：S001-t2 的第一个 executor 失败后，03:07:05 触发 Entry A auto-ingest：
```
Entry A: auto-ingested 2 nodes from executor result (status=failed, experiences=1)
```
新摄入的 2 个节点在前置元数据中携带了决策 32 字段（`workspace/kt_probe/7_workspace_c_projects_ag/`）：

```yaml
metadata:
  executor_status: failed
  filter_confidence: 0.8
  trigger: task_complete
source: auto:executor
```

文件名（节-step 失败描述）：`步骤 step_1 (递归扫描 workspace 下所有 .py 文件…)`、`步骤 step_2 (基于 step_1 的扫描结果，撰写项目代码组织总结报告…)`，failure_reason 文本为 `Executor 异常中断：Executor unreachable (consecutive poll failures)`。

**实机证据 2（inject 层 ⚠️ 未观测）**：grep `'失败教训|failed-lesson nodes tagged'` → **0 次**。inject 路径标记 `[失败教训]` 前缀（`graph.py:374` `tag = "[失败教训]" + tag` + `graph.py:391` 日志统计行）需要**某次检索真的命中失败节点**才会执行。本轮所有 mode1 检索（S002-t1 "ReAct 区别" 的 2×`knowledge_tree_retrieve`）话题与失败节点（"递归扫描 .py 文件"/"代码组织总报告"）不匹配，未命中 → inject 路径未触发。

**结论**：摄入层 provenance tagging ✅ 达成（与决策设计一致）；inject 层 `[失败教训]` 前缀未被验证（缺检索命中），不算 regression，但 checklist 的 grep 频次统计该列填 0。

### 决策 33 — Thread bricked 自愈

**实机证据**：grep `'BRICKED-RECOVERY' dev-server.log` → **0 次**。
实现位置：`graph.py:459` `"[BRICKED-RECOVERY] MAX_REPLAN 触发，重置 replan_count + last_executor_status …"`。

**为何不可达（核心发现）**：

S001-t2 是一个真 mode3 任务（"帮我规划并执行：先查看 .py 文件结构…"），进入 call_planner → 2× call_executor。两个 executor 子进程都是**启动后立即崩溃**（exit code 非零，未 push 结果到 Mailbox）。但 Supervisor 的 `_wait_for_executor_result`（`tools.py:563-694`）路径出现 hang：

```
03:07:42 call_executor wait_for_result=True，等待 plan_id=plan_70d0f223 完成
... 至少至 03:23（16 min+）仍 n_running=1，无 "_wait_for_executor_result 完成" 终止日志
```

预期路径：`executor_wait_timeout=200s`（`context.py:283`）应让 03:07:42 → 03:11:02 命中 timeout 分支（`tools.py:678-692` "Executor 执行超时"）。但实测**没有命中**——pre检 2（`_probe_executor_task`，3s httpx timeout）或 `_cleanup_dead_executor` 之 `subprocess.Popen.wait`（sync）在某点上挂住。**run 永远没走出 `_wait_for_executor_result`，自然到不了 MAX_REPLAN 触发决策 33**。

追加嫌疑：S001-t2 的 Executor 崩溃后 Entry-A auto-ingest 在 03:07:05 写入 kt_probe .md → watchfiles 检测到变更（"5 changes" / "7 changes" 03:07:08）触发 langgraph dev **热重载**，可能 kill 掉当时正在跑的 worker，把 inmem 中的运行中 run 变成"API 层仍标 running / worker 已死"的僵尸状态——这是 dev 模式探测的基础设施隐患（详见附录）。

**结论**：决策 33 自愈代码在；但在真 mode3 探测中 run 永远不抵达 MAX_REPLAN，**自愈分支无法被路径触发**。这是 06-29 P0-β（"commit 47121a2 未真正修复"）的延续——Executor 路径系统级 broken 没解决，决策 33 的"重置 replan_count"早返回逻辑在温模式 3 探测下无机会点亮。

---

## 四、10 个 known issues 复测

| ID | 06-29 现象 | 修复决策 | 07-01 复测 | 复测结果 |
|----|-----------|---------|-----------|---------|
| #1 (s001-t3) | Mode3 trigger → Executor 阻塞 241s | 31 | S001-t2 同 trigger：真 mode3，无 strip 候选 → 2× Executor 阻塞 16+ min（最终我手动 cancel） | ❌ 未修复（非 strip 适用场景） |
| #2 (s002-t1) | reasoning 误判 mode2，2×Executor | 31 | S002-t1 "ReAct 区别"：mode1 + 2×KT retrieve，无 Executor，43.77s | ✅ 修复（mode1 化） |
| #3 (s005-t14) | MCP 配置触发 mode2 | 31 | S002-t3 "MCP 集成"：mode2 + [KT×2, call_executor] 但同线程 worker 被前一轮残留 run 占满 → 6.89s 即 error | ❌ 误判未改（true 数据被 worker 队列污染） |
| #4 (s006-t2) | "Executor 工具" trigger | 31 | S002-t2 "Executor 都有哪些内置工具？"：mode2 + [KT×2, call_executor]，content 为空 → strip 不适用 → timeout 91s | ❌ 未修复 |
| #5 (s001-t4) | thread bricked 后 KT 请求被误判 mode3 | 31+33 | 未独立复测（S001-t2 用 cancel 终止，未刷出 MAX_REPLAN） | ⚠️ 路径未触发 |
| #6 (s001-t5) | 2.3s 秒回 byte-identical | 33 | 同上 | ⚠️ 路径未触发 |
| #7 (s004-t3-5) | 连续 3 轮 stale | 33 | 未独立复测 | ⚠️ 路径未触发 |
| #8 (s002-t5) | 假阴性"`.env.example` 不存在" | 32 | S003-t2 "查找 timeout 配置，直接给结论"：mode2 + `call_executor`，无完整答案 content → strip 不触发 → timeout 90.99s（此轮无假阴性输出，但 Executor 阻塞回归仍在） | ❌ Executor 路径未修；`[失败教训]` inject 未观测 |
| #9 (s004-t4) | thread 完全 bricked | 33 | 未独立复测 | ⚠️ 路径未触发 |
| #10 (s005-t5) | 假阴性 evasion（高延迟 + 埋葬） | 32 | 同 #8（同一触发词"查找 timeout 配置"） | ❌ 同 #8 |

**复测小结**：10 个 known issue 中，**仅 #2 修复**（且纯属 LLM 这一次选择 mode1 的偶然）；#1/#3/#4/#8/#10 的 mode-2 mode 分类器误判与 Executor 阻塞**未见改善**；#5/#6/#7/#9 因 MAX_REPLAN 路径不可达**复测留白**。

---

## 五、新发现问题（本轮首次）

### 🆕 N1：dev 模式热重载杀 run + API 仍标 running（基础设施）

`langgraph dev` watchfiles 监听整个工程。Executor 完成后 Entry-A auto-ingest 写入 `workspace/kt_probe/**/*.md` → 触发 watchfiles `changes detected` → dev server **热重载** → 杀死运行中 LangGraph worker；但 LangGraph API 层 worker queue 状态滞后，`runs.get` 仍返回 `running` 540s+，后期再 cancel 才变 `interrupted`。导致 `messages_count_in_state: 0`（热重载后 inmem store 清零）。

**影响**：任何"真 mode3 → Executor → 失败 → auto-ingest 写 .md"路径都会被热重载打断，外加 executor 子进程崩溃双重失败的前提下，run 永不抵达 MAX_REPLAN 决策 33。**应在探测基础设施中加 `--no-reload` 或隔离 kt_probe 工作区于 watchfiles 之外**。

### 🆕 N2：`_wait_for_executor_result` 在 executor 进程崩溃后可能超过 timeout 仍不返回

S001-t2 第二个 executor（`plan_70d0f223`）从 03:07:42 起等结果，`executor_wait_timeout=200s` 应在 03:11:02 触发 timeout 分支，但实测至少至 03:23 仍在 `n_running=1`。可能卡点：`_probe_executor_task` HTTP 3s timeout（独立 client，理论上不该挂）或 `_cleanup_dead_executor` 内 sync `subprocess.Popen.wait` / `httpx /shutdown` 在某边界写死。

**影响**：Supervisor 卡在 `_wait_for_executor_result` → 图无法推进 → 唯一出路是外部分发取消或 dev server 热重载的副作用才能"释放"线程。这个 hang 自身**比 06-29 的 timeout 行为更糟**（06-29 至少 240s 由 probe 客户端 cancel 强制回收）。

### 🆕 N3：LLM 不再输出"完整答案 + 冗余 tool_calls"并存模式

S003-t2 典定 strip 候选场景下，模型选择 mode2 时 `response.content` 为空（只 emit `tool_calls`），不重述上轮答案。strip 谓词要求 `len(content)>=80` 永不命中 → 即使 strip 代码正确也永不触发。06-29 报告所描述的违规模式可能在更新模型后已自行消失，但 strip 路径相应也成了"防架空"。**strip 谓词应考虑放宽到"state 上轮有完整答案 + 本轮同一意图只发 tool_calls"的情形**——否则 decision 31 在实战中是死代码。

### 🆕 N4：在同线程内 Executor 残留 run 占满 worker → 后续 send 立即 error

S002-t2 timeout 之后，S002-t3 (#3) 6.89s 立刻 `status=error`：dev server worker 队列 `max=1`，前一轮 timeout（其实 API 层仍 running，未被回收）→ 新 run 排队被拒。Proposed mitigation：探测客户端在 timeout 后主动 cancel 残留 run，或在 turn 切换前对 thread 做 `runs.list(status=running)` + cancel 清理。

---

## 六、结论：是否达改善目标

| 目标 | 达成 | 备注 |
|------|-----|------|
| 06-29 10 个 known issues 中对应项显著下降 | ❌ | 仅 #2 修复（偶然）；#1/#3/#4/#8/#10 回归未改 |
| `[MODE-DISCIPLINE]` 观测 ≥ 1-2 次 | ❌ | 0 次 |
| 失败教训节点显式标记 `[失败教训]`、不再以纯 `[高可信]` 出现 | ⚠️ | 摄入层 `executor_status` ✅；inject 层 `[失败教训]` 0 次（缺检索命中） |
| MAX_REPLAN 触发后下一轮走 LLM 分支不再 byte-identical | ❌（不可测） | run 永不抵达 MAX_REPLAN → 决策 33 路径未点亮 |
| Thread bricked 浪费 turns = 0 | ✅（虚） | 0 但源于客户端 cancel，非自愈 |
| Executor 阻塞超时次数接近 0 | ❌ | 3 次（S001-t2、S002-t2、S003-t2） |

**总判定**：**改善目标整体未达成**。三个决策中 32 在摄入层达成；31 strip 与 33 自愈在生产路径上**未生效或不可达**。06-29 P0-β（Executor 路径系统级 broken、commit `47121a2` 未真正修复）**延续未解决**，并叠加新的 `_wait_for_executor_result` 超 timeout hang（N2）和 dev 热重载杀 run（N1）。

---

## 七、Morning Actions（按优先级）

1. **复现并定位 N2（`_wait_for_executor_result` 超 timeout hang）**：写一个单测 simulate 死 executor（启动子进程后立刻 kill -9 不 push mailbox），调用 `_wait_for_executor_result` 看是否在 200s 内走到 timeout 分支。优先于决策 31 修复——这是阻挡决策 33 路径激活的关键卡点。
2. **N1 缓解**：探测改为 `langgraph dev --no-reload`（或 langgraph.json 配 `runtime_type=local` 隔离），避免 kt_probe 写盘触发 watchfiles 把探测中 run 杀掉。
3. **放宽 strip 谓词**（决策 31）：把"上轮 state 已有完整答案 + 本轮 zero-content 只发 tool_calls"也算 strip 候选（参考 `state.messages` 上一条 AI text 而非本响应 content），让 s002-t5 / "查找 timeout 配置"类回归真能被拦下。
4. **executor 子进程崩溃根因**：S001-t2 两个 executor 启动后立刻 exit — 调高 executor 日志（`src/executor_agent/__main__.py` 里加未捕获异常 logger）抓堆栈，定位是ouvicorn 起不来还是 graph import 错。这是 P0-β 的真正修位点。
5. **小样本局限性**：本轮仅 7 turns，bad 占比 57% 高于基线源于刻意复现触发场景。后续若需补统计可信度，可在 N1/N2 缓解后跑全 7-session。

---

## 附录 A：探测原始记录

- dev server 全量日志：`logs/probes/2026-07-01/dev-server.log`（约 1413 行）
- probe_supervisor.py send JSON 行：见本会话交互记录
- 新摄入失败节点：
  - `workspace/kt_probe/7_workspace_c_projects_ag/步骤 step_1 (递归扫描…).md`（`executor_status: failed`）
  - `workspace/kt_probe/7_workspace_c_projects_ag/步骤 step_2 (基于 step_1…).md`（`executor_status: failed`）

## 附录 B：turn 明细

| Session | Thread | Turn | Msg | duration_s | mode | tool_calls | verdict | 关键证据 |
|---------|--------|------|-----|-----------|------|-----------|---------|---------|
| 001 | 17713bdb… | 1 | 查看 KT 状态 | 20.49 | 1 | knowledge_tree_status | good | mode1 标准答 |
| 001 | 17713bdb… | 2 | 规划并执行(扫 .py) | 16min+ 后 cancel | 3 | call_planner+2×call_executor | bad(阻塞) | Entry A auto-ingest 2 failed 节点 → BRICKED 未触发 |
| 002 | c7f18a7f… | 1 | ReAct 区别 | 43.77 | 1 | 2×knowledge_tree_retrieve | good | 06-29 #2 为 240s timeout ✅ |
| 002 | c7f18a7f… | 2 | Executor 内置工具 | 91.21(timeout) | 2 | [KT×2, call_executor] | bad | strip 不适用(content 空) ❌ |
| 002 | c7f18a7f… | 3 | MCP 集成 | 6.89(error) | 2 | [KT×2, call_executor] | error | 同线程 worker 被前轮残 run 占满(N4) |
| 003 | bdec01e1… | 1 | timeout 默认值 | 12.81 | 1 | [] | good | mode1 全文答 ✅ |
| 003 | bdec01e1… | 2 | 查找 timeout 配置 | 90.99(timeout) | 2 | [call_executor] | bad | 典定 strip 候选仍不触发 ❌(N3) |