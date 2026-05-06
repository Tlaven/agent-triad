---
title: V3 子进程管理与 Mailbox 协议
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: patterns
---

V3 子进程管理确保 Executor 与 Supervisor 进程隔离。

ProcessManager（src/common/process_manager.py）：
- 每个 call_executor 按 plan_id spawn 独立子进程
- 子进程运行 FastAPI（POST /execute、GET /result、POST /stop）
- 动态端口分配，端口号写入 logs/executor_{plan_id}.port
- 健康检查：启动后轮询 /health 直到 ready

Mailbox（src/common/mailbox.py）：
- 线程安全 per-plan 结果缓存（plan_id → MailboxItem）
- MailboxHTTPServer 后台线程接收 Executor POST /inbox 推送
- Supervisor 的 _wait_for_executor_result 阻塞读取

ExecutorPoller（src/common/polling.py）：
- 后台 asyncio Task 定时轮询 Executor /result 作为兜底
- force_poll_once 用于即时检查

进程清理：
- sync_terminate 使用 terminate → kill 升级策略
- atexit + SIGTERM/SIGINT 信号处理确保子进程随主进程退出
- V3LifecycleManager 是懒加载单例，统一管理所有组件

plan_id 与子进程关系：Mode 3 同 id 复用子进程，Mode 2 每次新 id 新进程。
