---
title: Executor 子进程协议与 V3 生命周期
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: architecture
---

V3 架构中每个 call_executor spawn 独立子进程（python -m src.executor_agent），
子进程启动 FastAPI + uvicorn，动态分配端口。端口号写入 logs/executor_{plan_id}.port 文件。

通信双路径：
- Push：Executor 完成后 POST /inbox 推送到 Mailbox（主要路径）
- Pull：ExecutorPoller 后台 asyncio Task 定时 GET /result 兜底（保险路径）
Supervisor 的 _wait_for_executor_result 阻塞读取 Mailbox。

ExecutorResult 格式：[EXECUTOR_RESULT] meta JSON，含 status（completed/failed/paused）、
summary、updated_plan_json、snapshot_json（paused 时结构化快照）。

子进程生命周期由 V3LifecycleManager（懒加载单例）管控：
- ProcessManager：spawn/stop per-task 子进程，健康检查
- Mailbox：线程安全 per-plan 结果缓存
- atexit + SIGTERM/SIGINT 信号处理确保子进程随主进程退出
- sync_terminate 使用 terminate → kill 升级策略

超时保护：executor_call_model_timeout（180s）→ 终止进程；executor_tool_timeout（300s）→ 返回部分结果。
Supervisor 侧 executor_wait_timeout（300s）→ 终止 executor 进程并标记失败。
