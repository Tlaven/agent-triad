# 故障排查（Troubleshooting）

> 定位：AgentTriad 常见错误的排查参考。每条记录**症状 + 原因 + 解决方案**，仅收录已观察到的真实问题。
> 配置变量详见 [`environment-variables.md`](environment-variables.md)。

---

## 1. BlockingError：LangGraph dev server 中 KT 操作阻塞

**症状**：`make dev` 启动的 LangGraph dev server 中，Supervisor 调用知识树相关操作时报：

```
RuntimeError: asyncio.run() cannot be called from a running event loop
# 或
BlockingError: Async operations can only be ...
```

**原因**：LangGraph dev server 在已运行的 asyncio 事件循环中执行图节点。KT 的同步操作（`retrieve`/`ingest`/`get_meta_rules`）会阻塞事件循环，dev server 检测到后抛 `BlockingError`。

**解决**：auto-ingest 链路已用 `asyncio.to_thread` 包裹（`src/supervisor_agent/graph.py` `_try_auto_ingest_executor_result` 调用处）。若自定义代码触发此错误，将同步 KT 调用改为：

```python
await asyncio.to_thread(kt.retrieve, query)
# 而非
kt.retrieve(query)
```

**关联**：commit `ae87b80`。

---

## 2. Executor 子进程端口冲突

**症状**：`call_executor` 报 `TimeoutError: Executor failed to start within {timeout}s`，`logs/executor_{plan_id}.port` 文件不存在或内容为空。

**原因**：
- 上一次 Executor 进程未正常退出，端口仍被占用
- 动态端口分配（`EXECUTOR_PORT=0`）失败
- 防火墙/安全软件拦截子进程网络

**解决**：
1. 检查残留进程：`tasklist | findstr python`（Windows）/ `ps aux | grep python`（Linux）
2. 手动终止残留：`taskkill /PID <pid> /F`
3. 确认 `logs/` 目录可写
4. 调高 `EXECUTOR_STARTUP_TIMEOUT`（默认 30s）如果机器启动慢
5. 重启 LangGraph dev server

**关联**：[`v3-lifecycle-reference.md`](v3-lifecycle-reference.md) §3 ExecutorProcessManager。

---

## 3. Mailbox 驱逐导致结果丢失

**症状**：Executor 已完成但 Supervisor 的 `_wait_for_executor_result` 超时，日志显示 "no completion in mailbox"。

**原因**：高并发或长时间运行后，Mailbox box 数量超过 `_MAX_BOXES=80`，触发驱逐。虽然驱逐策略优先移除已完成 box，但极端情况下未完成 box 也可能被移除（`_RETAIN_BOXES=50` 之后仍超限时）。

**解决**：
- 正常场景不会触发——单用户对话通常 < 10 个 active box
- 压测或批量任务时，调高 `src/common/mailbox.py` 的 `_MAX_BOXES` 和 `_RETAIN_BOXES`
- 检查是否有泄漏的 plan_id 未清理（`active_executor_tasks` 应在任务终态后移除）

**关联**：[`v3-lifecycle-reference.md`](v3-lifecycle-reference.md) §2 Mailbox 驱逐策略。

---

## 4. Executor 子进程残留（僵尸进程）

**症状**：主进程退出后，`tasklist`/`ps` 仍能看到 `python -m src.executor_agent` 进程。

**原因**：信号处理未正确注册，或主进程被 `kill -9` 强杀（绕过了 atexit）。

**解决**：
- 正常退出（SIGTERM/SIGINT/atexit）：`V3LifecycleManager._sync_cleanup` 会 `sync_terminate` 所有子进程（terminate → wait 3s → kill 升级）
- 强杀后残留：手动 `taskkill /PID <pid> /F` 或 `kill -9 <pid>`
- 批量清理：`taskkill /IM python.exe /F`（Windows，慎用——会杀所有 python）

**关联**：[`v3-lifecycle-reference.md`](v3-lifecycle-reference.md) §5 信号处理与 atexit。

---

## 5. 向量索引重建导致启动变慢

**症状**：启用 KT 后，首次启动或修改 `.md` 文件后启动，KT 初始化明显变慢（数十秒到数分钟）。

**原因**：向量索引持久化机制（`.vector_index.json` + manifest 新鲜度检测）检测到 `.md` 文件变更，回退到全量重建。重建需要重新计算所有节点的 embedding。

**解决**：
- 这是正常行为——重建确保向量与文件内容一致
- 启用 embedding 缓存加速：`.embedding_cache_{model}.json` 会缓存已计算过的内容哈希
- 使用 API embedder（`KT_EMBEDDER_TYPE=api`）通常比本地模型快
- 如确认无变更却仍重建，检查 manifest 文件（`.vector_index.json` 同目录）的时间戳

**关联**：commit `7cdd03e`（embedding cache）、[`kt-subsystems.md`](kt-subsystems.md) §2 embedding/。

---

## 6. `.env` 配置不生效

**症状**：修改 `.env` 后重启，行为未变化；或环境变量值与预期不符。

**原因**：`load_dotenv(override=True)` 设计为 `.env` **覆盖**系统环境变量，但若系统环境变量优先级更高（某些 shell 配置），`.env` 可能不生效。

**解决**：
1. 确认 `.env` 在项目根目录（与 `chat.py` 同级）
2. 检查系统环境变量是否已设置同名变量：`echo $VAR_NAME`（Linux）/ `echo %VAR_NAME%`（Windows）
3. 临时清除系统变量后重启
4. 在代码中验证：`from dotenv import load_dotenv; load_dotenv(override=True)` 必须在导入 Context 之前

**关联**：[`CLAUDE.md`](../CLAUDE.md) §运行与环境。

---

## 7. AIMessage 内容类型错误（`.strip()` on list）

**症状**：Executor 或 Supervisor 报 `AttributeError: 'list' object has no attribute 'strip'`。

**原因**：部分模型（如 Anthropic 兼容接口）返回的 `AIMessage.content` 是结构化列表（`[{type: "text", text: "..."}, {type: "thinking", ...}]`），而非纯字符串。下游代码调用 `.strip()` 时失败。

**解决**：已在 `src/common/utils.py` `invoke_chat_model` 的 `_normalize_content()` 中统一处理——将列表 content 拼接为字符串。若自定义代码触发，使用相同的归一化逻辑：

```python
def _normalize(msg):
    if isinstance(msg.content, list):
        parts = [b["text"] if isinstance(b, dict) and "text" in b else str(b) for b in msg.content]
        return msg.model_copy(update={"content": "".join(parts)})
    return msg
```

**关联**：commit `262623`（类型检查修复）、压力测试 L3 验证。

---

## 8. 元规则数量超限无法添加

**症状**：调用 `knowledge_tree_add_meta_rule` 报错 "meta-rules count exceeds MAX_META_RULES (15)"。

**原因**：决策 28 的存储层硬限制，`MAX_META_RULES = 15`。

**解决**：
- 使用自救工具 `knowledge_tree_delete_meta_rule(title)` 删除旧规则释放空间
- 优先删除优先级低或已被消解（同优先级矛盾）的规则
- 不要随意调高 `MAX_META_RULES`——压力测试表明 20+ 条矛盾规则会导致 Supervisor 推理崩溃

**关联**：[`meta-cognition-design.md`](meta-cognition-design.md) §4 元规则治理、[`architecture-decisions.md`](architecture-decisions.md) 决策 28。

---

## 9. Supervisor duration 异常长 + mode=1 但执行了 Executor

**症状**：用户问"Executor 都有哪些内置工具？"或"查找 X 配置"等本应 mode 1（纯问答）的问题，dev server 返回 `supervisor_decision.mode=1` 但 `duration_s > 60s`，且 `tool_calls` 列表含 `call_executor`/`call_planner`。

**原因**：LLM 在中间轮"既写出完整答案又冗余调工具"——`route_model_output` 只看 tool_calls 是否为空，无条件路由到 tools 节点执行 Executor 链路（200s+ 阻塞）。这是 LLM 行为纪律问题，prompt 治不了。

**解决**：决策 31 在 `call_model` 内加 `_looks_like_final_answer()` content 语义判别 + strip 冗余 tool_calls。默认开启（`SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS=1`）。若新场景出现误判（合理工具调用被误 strip），设 `=0` 即时关闭，无需重启。

**关联**：[`architecture-decisions.md`](architecture-decisions.md) 决策 31；诊断报告 [`p0-beta-diagnosis-2026-06-30.md`](p0-beta-diagnosis-2026-06-30.md)；探测分析 [`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md)。

---

## 诊断工具

| 需求 | 方法 |
|------|------|
| 查看 KT 节点数 | `knowledge_tree_get_status` 工具，或读 `workspace/knowledge_tree/` 目录 |
| 查看 Executor 子进程 | `ls logs/executor_*.port`，每个文件对应一个活跃子进程 |
| 查看 Mailbox 状态 | `manage_executor(action="list_tasks")` |
| 查看 Supervisor 决策 | 启用 `LANGCHAIN_TRACING_V2=true` + LangSmith |
| 查看 LLM 调用耗时 | 日志中 `LLM ainvoke completed in X.Xs`（`invoke_chat_model` 自动记录） |
| 查看 KT 快照 | 启用 `KT_SNAPSHOT_ENABLED=true`，读 `logs/kt_snapshot_*.json` |

---

## 10. KT 检索结果含 `[失败教训]` 前缀

**症状**：Supervisor 检索 KT 时，`[相关知识]` 注入的某些节点 tag 形如 `[失败教训][高可信] xxx`。

**原因**：决策 32 给 Entry A 摄入的节点加 `executor_status` metadata。检索 inject 时，`failed` 状态的节点会被加 `[失败教训]` 前缀，提示 Supervisor 该节点源自失败执行（可能是假阴性），不应作为事实引用。

**解决**：这是预期行为，不是错误。如果 tag 出现频率过高（说明失败结果被大量摄入），可：
- 检查 Entry A 摄入是否过滤失效（`src/common/knowledge_tree/ingestion/filter.py` 的 `_NEGATIVE_FACT_PATTERNS`）
- 调整 `extract_failure_reason` 的提取逻辑
- 后续可考虑加方式 B（检索层按 source 调相似度阈值）

**关联**：决策 32；探测分析 [`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md) §九。

---

## 11. Thread bricked：连续多轮返回相同 stale 响应

**症状**：某轮触发 MAX_REPLAN 后，后续任何请求（"你好"/"?"）都秒回上一轮的失败消息（byte-identical）。`messages_count` 仅 +1（只有 user，无新 AI 响应）。Thread 不可恢复，只能 switch session。

**原因**：MAX_REPLAN 早返回分支（决策 33 之前）的 guard 条件（`last_executor_status=="failed"` + `replan_count >= max_replan`）永不重置。早返回 return dict 只写 `messages` + `supervisor_decision`，不写 `replan_count` 或 `planner_session`。下一轮 call_model 进入时 guard 仍命中 → deterministic 早返回（不到 LLM 调用）→ "2.3s 秒回 byte-identical" 的根因。

**解决**：决策 33 在 MAX_REPLAN 早返回 return dict 加 `replan_count=0` + `planner_session`（用 `dataclasses.replace` 清 `last_executor_status`）。下一轮 user message 进入时 guard 不再命中，正常走 LLM 分支。

**关联**：决策 33；探测分析 [`probe-analysis-2026-06-29.md`](probe-analysis-2026-06-29.md) §七。

---

## 关联文档

- [`environment-variables.md`](environment-variables.md) — 配置变量完整参考
- [`v3-lifecycle-reference.md`](v3-lifecycle-reference.md) — V3 基础设施内部细节
- [`meta-cognition-design.md`](meta-cognition-design.md) — 元认知与元规则
- [`architecture-decisions.md`](architecture-decisions.md) — 各决策的背景与原因
