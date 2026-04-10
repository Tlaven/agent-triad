# MEMORY - 项目记忆与进度跟踪

> 本文件用于记录项目的历史、重要决策和当前进度，是项目的持久化记忆。

---

## 项目概述

**AgentTriad** 是一个三层 Multi-Agent 自主任务系统框架：
- **Supervisor**：主循环，理解意图、调度 Planner/Executor、管理重规划
- **Planner**：将任务转化为意图层 Plan JSON
- **Executor**：按 Plan 自主选工具执行

**架构**：自然语言 → 任务计划 → 工具执行 → 结果融合

---

## 版本历史

### V1.0 - 单线程闭环 MVP ✅ (2025-04-02)

**核心成果**：
- ✅ Supervisor ReAct 主循环（call_model + dynamic_tools_node + 路由判断）
- ✅ Supervisor 三种回复模式（Direct Response / Tool-use ReAct / Plan → Execute）
- ✅ call_planner / call_executor 工具
- ✅ Planner Agent（单次 LLM 调用，输出意图层 Plan JSON）
- ✅ Executor Agent（ReAct 循环，自主选工具）
- ✅ ExecutorResult 结构化返回
- ✅ 失败处理双重保障
- ✅ 重规划闭环（最多 MAX_REPLAN 次）
- ✅ dynamic_tools_node 双向同步
- ✅ 基础 Executor 工具（write_file + run_local_command）
- ✅ 基础单元测试

**验收**：给定多步骤任务，系统能完成"计划生成 → 工具执行 → 失败时重规划 → 最终答案"完整流程

### V2.0 - 运行时边界 + Planner 扩展 ✅ (2026-04-09)

**V2-a - 上下文与工具输出治理**：
- ✅ 统一工具结果策略（硬上限截断 + 明确提示）
- ✅ Observation 进入消息历史前规范化
- ✅ 配置项：单条 observation 最大字符数、是否启用落盘引用模式

**V2-b - Planner 辅助工具按需接入**：
- ✅ 将规划辅助工具接入 Planner graph
- ✅ MCP 只读能力复用（Planner 与 Executor 共用）
- ✅ 工具权限分层（Planner 仅可用只读工具）
- ✅ 避免重复定义，通过同一 MCP 接口保证行为一致

**V2-c - Executor Reflection + Snapshot（精简版）**：
- ✅ 步骤计数器 + 可配置 REFLECTION_INTERVAL
- ✅ Reflection 节点：LLM 自评路径是否偏离、置信度
- ✅ Snapshot 最小结构（当前进度摘要 + Reflection 结论 + 建议）
- ✅ 上报语义：Executor 停止当前轮次，将 Snapshot 交给 Supervisor
- ✅ 默认配置：REFLECTION_INTERVAL=0（关闭），按需配置为正整数启用

**测试覆盖**：
- 从基线 224 项测试增至 331 项（+107 项）
- V2-a/V2-b/V2-c 全部通过 unit + integration 测试

---

## 当前状态

### V3.0 - 多 Executor 并行与规模化 🚧 (进行中)

**目标**：落实决策 11（fan-out / 多实例），并在并行下补齐决策 9 的 Planner 会话顺序化

**核心特性**：
- [ ] **Supervisor fan-out**：将 Plan 拆为多个子 Plan，并行分发给多个 Executor 实例
- [ ] **并行 Executor 管理**：asyncio.gather、LangGraph Parallel 等，各 Executor 独立 State
- [ ] **Completion 融合**：Supervisor 收集各 Executor 结果，ReAct 融合与冲突处理
- [ ] **fan-in 策略**：多路输出冲突时的合并优先级（可配置或提示词约束）
- [ ] **Planner 会话与并行**：plan_id 索引的会话在并发写入下的顺序化或锁
- [ ] **上下文治理延伸**：并行时多路 observation 叠加，必须复用并加固 V2-a 的预算策略

**验收标准**：
给定可拆为 N 条独立路径的任务，能并行执行并融合为单一用户可见答案；并发下无 Planner 会话错乱；在故意制造大工具输出时仍符合 V2-a 的边界行为

### V4.0 - 记忆模块 📋 (规划中)

**目标**：增加记忆模块，全部使用 Markdown 文件来构建持久记忆、版本规划和任务跟踪

---

## 重要架构决策

### 决策 3：意图层 Plan
- Planner 不知道工具名，只描述 intent + expected_output

### 决策 4：Executor 遇阻即停
- Executor 不内部重规划，重规划权只在 Supervisor

### 决策 8：Supervisor 三种模式
- 模式1：Direct Response（简单事实、无需工具）
- 模式2：Tool-use ReAct（少量工具、短流程）
- 模式3：Plan → Execute（多步骤、有依赖）

### 决策 9：Planner 会话复用
- 同一 plan_id 复用同一 Planner 对话线程

### 决策 10：Reflection
- REFLECTION_INTERVAL=0 默认关闭；配置为正整数启用

### 决策 11：单线程 → 多线程（V3）
- V1-V2：Supervisor 每次只调用一个 Executor
- V3：支持 fan-out 多个 Executor 实例并行执行

### 决策 12：Planner 只读
- Planner 仅可用只读工具/MCP，不可调用副作用工具

---

## 技术栈

- **语言**：Python 3.12+
- **框架**：LangGraph (Multi-Agent Orchestration)
- **LLM**：OpenAI-compatible APIs (SiliconFlow)
- **测试**：pytest + pytest-anyio
- **包管理**：uv

---

## 开发规范

### 测试驱动开发（TDD）
1. 先写测试，描述预期行为
2. 实现代码让测试通过
3. 重构优化

### Git 提交规范
- 每个子任务完成后 commit
- 格式：`feat: iteration $(date +%H:%M) - 子任务描述`
- 只有测试通过时才 commit

### 代码质量
- 每次变更后运行 lint、安全扫描
- 确保所有测试通过

---

## 当前工作日志

### 2026-04-10 - 开始 V3 开发
- 创建 MEMORY.md 记录项目历史
- 准备开始 V3.0 的第一个子任务

---

## 参考资料

- [`CLAUDE.md`](CLAUDE.md) - AI 助手必读的硬规则
- [`ROADMAP.md`](ROADMAP.md) - 版本规划与验收标准
- [`tests/TESTING.md`](tests/TESTING.md) - 测试注意事项
- [`docs/architecture-decisions.md`](docs/architecture-decisions.md) - 架构决策详细说明
