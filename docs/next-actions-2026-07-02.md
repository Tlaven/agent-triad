# 下一步行动计划 — 2026-07-02 探测后

> 基于 [`probe-analysis-2026-07-02.md`](probe-analysis-2026-07-02.md) 的 4 项 Morning Action 细化
> 探测数据：74 turns / 3 sessions / 81% good / 6 known issues
> 创建：2026-07-02 探测收尾后

---

## 优先级总览

| # | 优先级 | 任务 | 类型 | 预估 |
|---|-------|------|------|------|
| 1 | 🔴 P0 | 定位 LLM 输出与 tool_calls 解耦根因（N4） | bug 调查 | 1-2h |
| 2 | 🟠 P1 | 撤销或放宽决策 31 strip 谓词（死代码） | 代码清理 | 30min |
| 3 | 🟠 P1 | 查 supervisor prompts.py 是否暗示"每次 list 元规则" | bug 调查 | 30-60min |
| 4 | 🟡 P2 | 修 P0-β Executor `os.getcwd` BlockingError | bug 修复 | 1-2h |
| 5 | 🟢 P3 | 决策 32 失败教训机制推广 | 产品优化 | 半天 |

---

## 🔴 #1: 定位 LLM 输出与 tool_calls 解耦（N4）

### 问题
**s002 turn 15-16 提供了最干净的复现样本**：
- 用户："你能否只回答不调用任何工具？问题：'2 的 10 次方是多少？'"
- Agent content 回答："1024。"
- Agent tool_calls 数组：**10 个工具调用**（`list_meta_rules` + `add_meta_rule` + `delete_meta_rule` + `call_executor` + 多次 `retrieve/ingest/feedback`）

s002 turn 16 用户追问"你上一轮调了 10 个工具你注意到了吗？"，Agent content 回答"**我没有调用任何工具**"——**同时 tool_calls 数组里仍是同样的 10 个工具**。

### 这是比 06-29 P0 mode 路由脱节更深的 bug
- 06-29 P0：mode 标记错误（标 mode1 实际 mode2），但工具调用是 LLM 主动 emit
- 今日 N4：**LLM content 输出和 tool_calls 数组完全来自两条独立路径**，Agent 自我不感知

### 怀疑路径（按可能性排序）

#### A. GLM 模型的 tool_calls 生成与 content 解耦（最可能）
GLM-5.2 在 function calling 训练中，content 和 tool_calls 可能是两次独立 forward 或不同 head 生成。LLM 自己"看到"的只是 content，但服务端把 tool_calls 也包装进 response。

**验证方法**：直接调 GLM API（不走 LangChain），用相同 prompt 看返回的 `tool_calls` 字段是否独立于 `content`。

#### B. Supervisor 后处理注入（次可能）
`src/supervisor_agent/graph.py:call_model` 或 `dynamic_tools_node` 在 LLM 输出后又注入了工具调用。

**验证方法**：在 `call_model` 加日志，记录 `response.tool_calls` 的来源：
```python
# graph.py call_model 内
logger.info(
    "call_model raw response: content_len=%d, tool_calls=%s",
    len(response.content or ""),
    [tc.get("name") for tc in (response.tool_calls or [])],
)
# 如果日志显示 tool_calls 非空且 content 是"1024"，
# 则是 LLM 直接 emit；如果日志显示 tool_calls=[]，
# 则是后续 node 注入
```

#### C. LangChain `bind_tools` 包装层 bug（可能）
`load_chat_model("provider:model").bind_tools(...)` 在 GLM provider 上可能有问题。

### 修复方向（待定位后确定）
- 如果是 A：报告给 GLM provider；或在 Supervisor 层加"用户明说不要工具时强制 strip"
- 如果是 B：找到注入点，加条件判断
- 如果是 C：升级 LangChain 或换 provider 适配

### 关键文件
- `src/supervisor_agent/graph.py:call_model`（日志加在这里）
- `src/supervisor_agent/graph.py:dynamic_tools_node`（检查是否有注入）
- `src/common/utils.py:load_chat_model`（检查 bind_tools 行为）

### 验证方式
1. 加日志后跑 `make dev_probe`，触发 s002 t15 复现场景（"不调用任何工具" + 简单题）
2. 看 `call_model` 日志的 `tool_calls` 是否非空
3. 若非空 → A 路径；若为空 → 沿 `dynamic_tools_node` 继续追

---

## 🟠 #2: 撤销或放宽决策 31 strip 谓词

### 问题
决策 31 的 strip 谓词（`src/supervisor_agent/graph.py:618-627` + `_looks_like_final_answer` at `1433-1446`）在 07-01 + 07-02 两次探测中**触发 0 次**——谓词是死代码。

### 根因
谓词要求"content 是 str ≥ 80 字符 + markdown 结构 + 不含过程词"——但当前 LLM 行为已变化：
- 选 mode 1 时：`content` 完整 + `tool_calls=[]`（谓词不适用）
- 选 mode 2 时：`content` 为空 + `tool_calls` 非空（content 长度<80，谓词不命中）

LLM **不再输出**"完整答案 + 冗余工具并存"的模式——06-29 看到的违规模式可能已自行消失。

### 修复方向（二选一）

#### 方向 A：撤销谓词 + 删除死代码（推荐）
直接删除 `_looks_like_final_answer` + strip 逻辑 + `[MODE-DISCIPLINE]` 日志 + env `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS`。

**理由**：底层 mode 路由脱节问题已被更深 bug N4 取代，strip 谓词无法解决 N4。

#### 方向 B：放宽到跨响应判断（更激进）
判断"上轮 state 有完整答案 + 本轮 zero-content 只发 tool_calls" → 视为冗余 strip 掉。

但这需要读 `state.messages` 上一条 AI text，且 N4 的 tool_calls 是脱离 LLM 控制的，strip 也治标不治本。

### 关键文件
- `src/supervisor_agent/graph.py:618-627`（strip 逻辑）
- `src/supervisor_agent/graph.py:1433-1446`（`_looks_like_final_answer`）
- `src/supervisor_agent/graph.py:_FINAL_STRUCT_RE / _PROCESS_MARKERS`（常量）
- `tests/unit_tests/supervisor_agent/test_mode_discipline.py`（测试）
- `CLAUDE.md`（决策 31 描述段）
- `docs/architecture-decisions.md`（决策 31 完整记录）

### 验证方式
- 方向 A：删除后跑 `make test_unit`，更新 `test_mode_discipline.py`；探测 grep `[MODE-DISCIPLINE]` 不再出现
- 方向 B：保留测试但放宽断言

---

## 🟠 #3: 查 supervisor prompts.py 是否暗示"每次 list 元规则"

### 问题
s002 turn 8-16 + t13-16 稳定出现冗余 `list_meta_rules + add_meta_rule + delete_meta_rule + call_executor` 序列，即使：
- 用户没要求（t8 列文件、t9 检索 MAX_REPLAN、t15 算 2^10）
- Agent 自己否认调用过（t13/t16）

这种**跨多轮稳定的固定 pattern** 不像 LLM 自由决策，更像是某处 prompt/template 暗示触发。

### 调查步骤

1. **grep supervisor prompts**：
   ```
   grep -r "list_meta_rules\|add_meta_rule\|delete_meta_rule" src/supervisor_agent/
   ```
   找是否有 prompt 文本明确建议"每次先 list 一下"。

2. **检查 system prompt**：
   `src/supervisor_agent/prompts.py` 看 system message 是否提到元规则检查。

3. **检查 dynamic_tools_node**：
   是否有逻辑在每轮自动 prepend 工具调用。

4. **检查 KT auto-inject**：
   `src/supervisor_agent/graph.py:kt_retrieve_node` 是否在每轮都触发某个固定工具序列。

### 关键文件
- `src/supervisor_agent/prompts.py`
- `src/supervisor_agent/graph.py:kt_retrieve_node / dynamic_tools_node`
- `src/common/knowledge_tree/`（KT 工具定义）

### 验证方式
找到触发点后，删除/修改暗示；下次探测 grep tool_calls 列表，确认冗余序列消失。

### 与 #1 的关系
如果 #1 定位为 B 路径（Supervisor 后处理注入），则 #3 的根因可能就在那个注入点。**建议先做 #1，可能 #3 一起解决**。

---

## 🟡 #4: 修 P0-β Executor `os.getcwd` BlockingError

### 问题
07-02 探测中 Executor 子进程启动即崩，错误：
```
BlockingError: Blocking call to os.getcwd
```

这是 langgraph_api 在 asyncio 环境中检测到同步阻塞调用的报错。06-30 [`p0-beta-diagnosis-2026-06-30.md`](p0-beta-diagnosis-2026-06-30.md) 已诊断但未修复。

### 今日状态
- ✅ N1 缓解（`--no-reload`）让决策 33 路径可达——Supervisor 不再因 Executor 崩溃而卡死
- ❌ Executor 本身仍崩——所有 mode 2/3 任务实际无法执行
- ❌ s001 t3-t56 所有 Executor 调用失败

### 调查方向

#### 1. 加异常日志抓堆栈
`src/executor_agent/__main__.py` 或 `server.py` 加未捕获异常 logger：
```python
import traceback
import logging
logger = logging.getLogger(__name__)

try:
    # 启动逻辑
except Exception:
    logger.error("Executor 启动失败:\n%s", traceback.format_exc())
    raise
```

#### 2. 定位 `os.getcwd` 调用源
grep：
```
grep -rn "os.getcwd\|getcwd" src/executor_agent/ src/common/
```
常见嫌疑：
- `subprocess.Popen` 默认 cwd=None 时会调 getcwd
- `pathlib.Path.cwd()`
- 第三方库（uvicorn、langgraph_api）内部

#### 3. 候选修复
- 如果是 Popen：显式传 `cwd=str(Path(__file__).parent)`
- 如果是库内部：可能需要 `asyncio.to_thread` 或 `loop.run_in_executor` 包装
- 或者加 `langgraph.json` 的 `allow_blocking` 配置（但只是抑制错误不解决根因）

### 关键文件
- `src/executor_agent/__main__.py`（子进程入口）
- `src/executor_agent/server.py`（FastAPI 服务器）
- `src/common/process_manager.py:stop_task / _stop_handle`（已知 sync wait 嫌疑点）
- `docs/p0-beta-diagnosis-2026-06-30.md`（06-30 诊断记录）

### 验证方式
修复后跑 s001 t3 的复现场景（任意 mode 2/3 任务），看 Executor 是否能正常启动 + 执行 + 返回结果。

---

## 🟢 #5: 决策 32 失败教训机制推广（产品优化）

### 背景
今日 s001 t36 实战验证：决策 32 失败教训 inject 机制**真实生效**——Agent 从 06-29 假阴性"`.env.example` 不存在"的失败教训中学会了区分"项目根目录"vs"Executor 工作区"。

这表明 V4 知识树的"失败教训 → 跨 session 继承"机制是有效的产品设计。

### 推广方向

#### 1. 主动摄入历史 known issues
当前 06-29 报告的 10 个 known issues 中只有 #8 被摄入失败教训节点（通过 Entry A auto-ingest 偶然形成）。

**建议**：写脚本批量摄入其他 known issues 为失败教训节点：
- #1（Executor 阻塞）→ 已通过 N1 缓解，但仍可摄入
- #2（reasoning 误判 mode2）→ 已修复但教训仍有价值
- #5/#6/#7（thread bricked 系列）→ 决策33 修复，但失败模式仍值得记忆
- #9（thread 完全 bricked）→ 同上

#### 2. 设计"失败教训 review"流程
定期（每周？）review known_issues_found，挑选跨场景通用的摄入为失败教训节点。

#### 3. 监控 inject 频次
在 dev-server.log grep `[失败教训]`，统计跨 session 的 inject 频次，确认机制持续生效。

### 关键文件
- `scripts/`（新写摄入脚本）
- `src/common/knowledge_tree/`（KT ingest 工具）
- `logs/probes/state.json:known_issues_found`（数据源）

### 验证方式
下次探测时 grep `[失败教训]`，应有 ≥ 1 次注入（vs 07-01 的 0 次）。

---

## 执行顺序建议

1. **先做 #1（P0）** —— 这是今日最重要的发现，影响所有后续 Supervisor 改进的方向判断
2. **#1 完成后看是否 #3 一起解决** —— 如果 #1 定位为 B 路径，#3 根因可能相同
3. **#2（死代码清理）** —— 独立任务，做完减少代码噪音
4. **#4（Executor P0-β）** —— 让 mode 2/3 真正可用，是产品里程碑
5. **#5（失败教训推广）** —— 锦上添花，可在主流程稳定后做

## 验证探测建议

完成 #1-#4 后，跑一次定点探测（参照 07-01 规模 ~7-10 turns），重点验证：
- `[MODE-DISCIPLINE]` 是否还在 grep 结果（应该消失——死代码已删）
- N4 是否复现（应该不再有"不调工具却调了"的模式）
- Executor 是否成功执行任意 mode 2 任务（应该成功）
- 冗余 list/add/delete meta_rule 序列是否消失（应该消失）

---

## 附录：本计划依据

- [`probe-analysis-2026-07-02.md`](probe-analysis-2026-07-02.md) — 今日 8h 探测分析（74 turns）
- [`probe-analysis-2026-07-01.md`](probe-analysis-2026-07-01.md) — 07-01 定点复现基线
- [`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md) — 06-29 69 turns 基线
- [`p0-beta-diagnosis-2026-06-30.md`](p0-beta-diagnosis-2026-06-30.md) — Executor P0-β 历史诊断
- [`architecture-decisions.md`](architecture-decisions.md) — 决策 31/32/33 完整记录
