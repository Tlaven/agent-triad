# V3 生命周期基础设施参考

> 定位：V3 进程分离架构的基础设施层参考文档。本文是 [`v3-execution-flow.md`](v3-execution-flow.md) 的配套，聚焦"单例如何 wire 各组件"，不重复执行流分析。
> 硬规则见 [`CLAUDE.md`](../CLAUDE.md) §V3 进程分离架构 与 §硬约束。

---

## 1. V3LifecycleManager 单例 wiring

**源码**：`src/supervisor_agent/v3_lifecycle.py`

**模块级单例**：`v3_manager = V3LifecycleManager()`（L175）。整个 Supervisor 进程共享一个实例。

**懒加载**：`ensure_started(ctx)`（L45）是唯一入口，由 `call_model()` 每次调用时触发：

```
ensure_started(ctx)
  ├─ 检查 _shutting_down → 直接返回（关闭中不重启）
  ├─ 获取 asyncio.Lock（防并发启动）
  ├─ started == True → 返回缓存的 _infra
  └─ _start(ctx) 首次启动
```

**V3Infrastructure dataclass**（L25-33）持有 4 个组件：

| 字段 | 类型 | 职责 |
|------|------|------|
| `process_manager` | `ExecutorProcessManager` | spawn/stop per-task 子进程 |
| `mailbox` | `Mailbox` | 线程安全 per-plan 结果缓存 |
| `mailbox_server` | `MailboxHTTPServer` | 后台线程接收 Executor 推送 |
| `poller` | `ExecutorPoller` | 后台 asyncio Task 轮询 Executor |

**启动顺序**（`_start` L61-99）：

```
Mailbox() → set_mailbox(_mailbox)           # 全局 mailbox 单例
  → MailboxHTTPServer(mailbox, port).start() # HTTP 后台线程
  → ExecutorProcessManager(ctx)
  → ExecutorPoller(mailbox).start()          # asyncio Task
  → atexit.register(_sync_cleanup)           # 仅注册一次
```

---

## 2. Mailbox 内部结构与驱逐

**源码**：`src/common/mailbox.py`

**数据结构**：

```python
@dataclass
class MailboxItem:
    item_type: Literal["completion", "status"]
    payload: dict
    read: bool = False
```

每个 `plan_id` 对应一个 box（`dict[str, list[MailboxItem]]`），box 内可有多条 status + 至多一条 completion。

**驱逐策略**：

| 常量 | 值 | 含义 |
|------|-----|------|
| `_MAX_BOXES` | 80 | 触发驱逐的阈值 |
| `_RETAIN_BOXES` | 50 | 驱逐后保留的数量 |

**驱逐流程**（`_maybe_evict` L124-149）：
1. `len(_boxes) > _MAX_BOXES` 时触发
2. **优先驱逐** `has_completion=True` 的 box（已完成的结果最可能不再需要）
3. 若仍超限，驱逐未完成 box（防止无限堆积）
4. 按插入顺序移除最旧的，直到 `len == _RETAIN_BOXES`

**双 API**：同步方法（`_post_sync`/`_get_completion_sync` 等，供 HTTP 线程调用）+ 异步方法（`post`/`get_completion` 等，供 Supervisor asyncio 调用，内部委托同步实现）。

**模块单例**：`_mailbox`（L30）+ `set_mailbox()` / `get_mailbox()`，供跨模块访问。

---

## 3. ExecutorProcessManager

**源码**：`src/common/process_manager.py`

**Spawn 流程**（`start_for_task` L205）：

```
start_for_task(plan_id, plan_json)
  ├─ 清理已死亡的 handle
  ├─ 设置子进程环境：EXECUTOR_PORT=0, PLAN_ID, MAILBOX_URL
  ├─ _spawn_executor_process  # asyncio subprocess 或 Popen fallback
  ├─ 端口发现：轮询 logs/executor_{plan_id}.port（每 0.3s）
  │     直到 executor_startup_timeout（默认 30s）
  └─ 健康检查：GET /health（每 0.3s，10s 预算）
```

**端口发现**：子进程动态分配端口并写入 `logs/executor_{plan_id}.port`，父进程轮询读取。失败时捕获子进程 stdout 用于诊断，然后 `terminate()` + 抛 `TimeoutError`。

**停止流程**（`_stop_handle` L317）——升级策略：

```
POST /shutdown        # 优雅关闭
  → wait(timeout=10)  # 等待退出
  → terminate()       # SIGTERM
  → wait(timeout=5)
  → kill()            # SIGKILL 兜底
  → 清理 port 文件 + 关闭 httpx client
```

**`sync_terminate`**（L368）：atexit 路径专用，更短超时（terminate→wait(3)→kill），用于主进程退出时快速清理所有子进程。

---

## 4. ExecutorPoller

**源码**：`src/common/polling.py`

**注册上限**：

| 常量 | 值 | 含义 |
|------|-----|------|
| `_MAX_ACTIVE_TASKS` | 100 | 注册任务上限 |
| `_TERMINAL_STATUSES` | `{completed, failed, stopped}` | 终态集合 |

**默认参数**（`__init__`）：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `interval` | 1.5s | 轮询间隔 |
| `max_concurrent` | 5 | 并发轮询上限（Semaphore） |
| `max_staleness` | 300.0s | 最大陈旧度，超时判定失败 |
| `max_consecutive_failures` | 10 | 连续失败上限 |
| HTTP timeout | 3.0s | 单次 GET /result 超时 |

**注册驱逐**（`_maybe_evict_active` L140-155）：`len > _MAX_ACTIVE_TASKS` 时按 `registered_at` 排序，弹出最旧的条目，防止长时间运行后内存泄漏。

**`force_poll_once`**（L174）：`call_model()` 在 LLM 调用前强制刷新一次 Mailbox，确保 Supervisor 看到最新状态。无 active 任务时早返回。

**单次轮询逻辑**（`_poll_one` L226）：
1. Mailbox 已有 completion → 跳过
2. 陈旧度检查（> `max_staleness` → 合成失败）
3. 连续失败检查（> `max_consecutive_failures` → 合成失败）
4. 终态 → `mailbox.post` completion
5. 合成失败时调用 `_mark_plan_steps_failed`

---

## 5. 信号处理与 atexit

**atexit**：`_start()` 中通过 `_atexit_registered` flag 保证仅注册一次（L94-97），回调 `_sync_cleanup`（L138）。

**信号处理**（`_register_signal_handlers` L152）：

| 信号 | 行为 |
|------|------|
| SIGTERM | `_signal_handler` → `_sync_cleanup` → 恢复 SIG_DFL → `os.kill` 重抛 |
| SIGINT | 同上 |

**`_sync_cleanup`** 流程：停止 poller → 停止所有子进程（`sync_terminate`）→ 停止 mailbox server。

**设计意图**：确保 executor 子进程**随主进程退出**，不残留僵尸进程。

---

## 6. Push / Pull 双路径时序

```
Supervisor: call_executor(plan_id)
  → pm.start_for_task(plan_id) → POST /execute
                                          ↓
                              Executor 子进程（FastAPI）
                                ├─ POST /execute   接收任务
                                ├─ GET  /result    返回 ExecutorResult
                                ├─ POST /stop      软中断
                                └─ _push_result_to_mailbox
                                       ↓
                              POST {mailbox_url}/inbox  ← Push 路径（主）
                                       ↓
                              MailboxHTTPServer → Mailbox.store
                                       ↑
                              ExecutorPoller._poll_one ← Pull 路径（兜底）
                                GET /result → 终态 → mailbox.post
                                       ↑
Supervisor: _wait_for_executor_result
  → mailbox.get_completion(plan_id)  阻塞读取
```

**Push**：Executor 完成后**主动** POST `/inbox` 推结果到 Mailbox。主要路径，实时性好。

**Pull**：`ExecutorPoller` 后台 asyncio Task 定时 GET `/result` 兜底。Push 失败时的保险机制。

**结果消费**：Supervisor 的 `_wait_for_executor_result` 阻塞读取 Mailbox，拿到 `ExecutorResult` 后继续。

---

## 关联文档

- [`v3-execution-flow.md`](v3-execution-flow.md) — 完整执行流分析（派发、双路径返回、超时全景、异常矩阵）
- [`v3-architecture-diagrams.md`](v3-architecture-diagrams.md) — Mermaid 总览与组件对比
- [`architecture-decisions.md`](architecture-decisions.md) 决策 13（Executor 子进程安全与超时）、决策 27（等待超时配置化）
- [`CLAUDE.md`](../CLAUDE.md) §V3 进程分离架构
