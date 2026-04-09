# V2 Feature Testing Guide

> **版本**: v0.2.1 (V2-complete)
> **日期**: 2026-04-09
> **状态**: 331 项测试全部通过 ✅

本文档说明 V2 功能的测试覆盖和执行方法。

---

## 测试覆盖总览

### 总体统计

| 测试类型 | 测试数量 | V2 专项 | 通过率 |
|---------|---------|---------|--------|
| 单元测试 | 266 项 | +42 项 | 100% |
| 集成测试 | 65 项 | +21 项 | 100% |
| **总计** | **331 项** | **+107 项** | **100%** |

### V2 功能测试覆盖

| V2 功能 | 单元测试 | 集成测试 | 总计 | 状态 |
|---------|---------|---------|------|------|
| **V2-a**: 工具输出治理 | ✅ 多项 | ✅ 多项 | 多项 | ✅ 完成 |
| **V2-b**: Planner 工具 + MCP | ✅ 30 项 | ✅ 32 项 | 62 项 | ✅ 完成 |
| **V2-c**: Reflection/Snapshot | ✅ 26 项 | ✅ 20 项 | 46 项 | ✅ 完成 |

**测试改进**：从 V1 基线 224 项增至 331 项（+107 项 V2 专项测试）

---

## V2-a: 工具输出治理测试

### 测试内容

**核心测试文件**: `tests/unit_tests/test_observation.py`

测试覆盖：
- ✅ 小字符串直接返回
- ✅ 超长内容智能截断（head + tail）
- ✅ 超大内容外置到文件
- ✅ 清晰的截断提示
- ✅ JSON 序列化处理
- ✅ 多块内容处理

### 运行测试

```bash
# 单独运行 V2-a 测试
pytest tests/unit_tests/test_observation.py -v

# 预期结果：~8 项测试全部通过
```

### 配置验证

测试验证以下配置项工作正常：

```bash
# .env 关键配置
MAX_OBSERVATION_CHARS=6500                  # 单条观察最大长度
OBSERVATION_OFFLOAD_THRESHOLD_CHARS=28000   # 超长内容外置阈值
ENABLE_OBSERVATION_OFFLOAD=true            # 启用外置存储
ENABLE_OBSERVATION_SUMMARY=false           # 智能摘要开关
```

---

## V2-b: Planner 工具 + MCP 测试

### 测试内容

**核心测试文件**:
- `tests/unit_tests/planner_agent/test_tools_registry.py` (30 项单元测试)
- `tests/integration/test_mcp_integration.py` (32 项集成测试)

#### 单元测试（30 项）

测试覆盖：
- ✅ **工具注册表**: Planner 只返回只读工具
- ✅ **权限验证**: Planner 无权访问写操作
- ✅ **安全检查**: 无副作用工具
- ✅ **完整性验证**: 工具结构一致性
- ✅ **上下文集成**: 工作区根目录应用
- ✅ **工具对比**: Planner vs Executor 工具分离
- ✅ **错误处理**: None/空上下文处理
- ✅ **命名规范**: 工具命名一致性
- ✅ **配置独立性**: MCP/工作区配置独立
- ✅ **文档质量**: 工具描述信息性

#### 集成测试（32 项）

测试覆盖：
- ✅ **MCP 客户端初始化**: 连接、错误处理
- ✅ **MCP 服务器配置**: DeepWiki 配置验证
- ✅ **只读工具**: MCP 工具只读验证
- ✅ **权限控制**: Planner vs Executor 权限分离
- ✅ **错误处理**: 超时、无效 URL、网络错误
- ✅ **缓存机制**: 工具缓存行为
- ✅ **并发访问**: 并发请求处理
- ✅ **配置测试**: 不同 MCP 配置组合
- ✅ **工具结构**: 工具结构完整性
- ✅ **功能验证**: 工具可调用性

### 运行测试

```bash
# V2-b 单元测试
pytest tests/unit_tests/planner_agent/test_tools_registry.py -v

# V2-b 集成测试
pytest tests/integration/test_mcp_integration.py -v

# 预期结果：62 项测试全部通过
```

### MCP 配置验证

测试验证以下 MCP 配置：

```bash
# .env MCP 配置
ENABLE_DEEPWIKI=true                  # DeepWiki 检索（可选）
ENABLE_FILESYSTEM_MCP=true            # 文件系统 MCP（可选）
FILESYSTEM_MCP_ROOT_DIR=workspace     # 文件访问根目录
```

**权限验证**：
- ✅ Planner: 只能使用 `read_workspace_text_file`, `list_workspace_entries`
- ✅ Executor: 可使用所有工具（包括写操作、命令执行）
- ✅ MCP 只读工具在 Planner/Executor 间共享

---

## V2-c: Reflection/Snapshot 测试

### 测试内容

**核心测试文件**:
- `tests/unit_tests/test_executor_reflection.py` (26 项单元测试)
- `tests/integration/test_reflection_integration.py` (20 项集成测试)

#### 单元测试（26 项）

测试覆盖：
- ✅ **路由逻辑**: Reflection 触发条件（`tool_rounds % interval == 0`）
- ✅ **Reflection 节点**: 暂停状态返回、快照生成
- ✅ **状态结构**: Reflection 配置字段验证
- ✅ **快照结构**: Snapshot JSON 格式验证
- ✅ **置信度阈值**: 不同阈值配置测试
- ✅ **状态规范化**: `paused` 状态变体处理
- ✅ **工具轮次计数器**: 轮次递增和取模运算
- ✅ **错误处理**: LLM 失败、JSON 错误处理
- ✅ **执行器集成**: 状态保持、配置独立
- ✅ **场景测试**: 中途检查点、不同频率配置

#### 集成测试（20 项）

测试覆盖：
- ✅ **完整流程**: 执行器 → Reflection → 暂停状态
- ✅ **重规划建议**: 低置信度触发重规划
- ✅ **终止建议**: 关键错误触发终止
- ✅ **不同间隔**: 每步、每 3 步、每 5 步触发
- ✅ **快照处理**: 序列化、反序列化
- ✅ **错误场景**: 格式错误 JSON、缺少字段
- ✅ **置信度配置**: 高/低/默认阈值
- ✅ **多步场景**: 多个检查点触发
- ✅ **Supervisor 集成**: 暂停状态、建议解析
- ✅ **上下文保持**: Plan 状态、消息历史保留

### 运行测试

```bash
# V2-c 单元测试
pytest tests/unit_tests/test_executor_reflection.py -v

# V2-c 集成测试
pytest tests/integration/test_reflection_integration.py -v

# 预期结果：46 项测试全部通过
```

### Reflection 配置验证

测试验证以下 Reflection 配置：

```bash
# .env Reflection 配置
REFLECTION_INTERVAL=0        # 默认关闭（0），设置正整数启用
CONFIDENCE_THRESHOLD=0.6     # 置信度阈值（0.0~1.0）
```

**触发条件**：
- ✅ `tool_rounds > 0 AND tool_rounds % REFLECTION_INTERVAL == 0`
- ✅ 置信度低于 `CONFIDENCE_THRESHOLD` 时额外触发
- ✅ `REFLECTION_INTERVAL=0` 时完全禁用

**Snapshot 结构验证**：
```json
{
  "status": "paused",
  "summary": "Reflection 摘要",
  "snapshot": {
    "progress_summary": "当前进度",
    "reflection": "偏离分析",
    "suggestion": "continue|replan|abort",
    "confidence": 0.8
  },
  "updated_plan": { /* Plan JSON */ }
}
```

---

## 启用 V2 功能进行测试

### 启用 Reflection（V2-c）

创建 `.env.test` 文件：

```bash
# .env.test
REFLECTION_INTERVAL=2        # 每 2 个工具调用触发
CONFIDENCE_THRESHOLD=0.6     # 置信度阈值
```

运行测试：

```bash
# 使用测试配置运行
ENV_FILE=.env.test make test_all
```

### 启用 MCP（V2-b）

创建 `.env.test` 文件：

```bash
# .env.test
ENABLE_DEEPWIKI=true
ENABLE_FILESYSTEM_MCP=true
FILESYSTEM_MCP_ROOT_DIR=workspace
```

运行测试：

```bash
# 使用测试配置运行
ENV_FILE=.env.test make test_all
```

---

## 运行完整测试套件

### 快速测试

```bash
# 只运行单元测试（最快）
make test_unit
# 预期：~1 分钟，266 项测试通过

# 运行所有测试
make test_all
# 预期：~3 分钟，331 项测试通过
```

### 详细测试输出

```bash
# 详细输出
pytest tests/unit_tests tests/integration -v

# 带打印输出
pytest tests/unit_tests tests/integration -v -s

# 只运行 V2 专项测试
pytest tests/unit_tests/test_executor_reflection.py \
       tests/unit_tests/planner_agent/test_tools_registry.py \
       tests/integration/test_reflection_integration.py \
       tests/integration/test_mcp_integration.py -v
```

### 测试覆盖率

```bash
# 安装覆盖率工具
uv pip install pytest-cov

# 生成覆盖率报告
pytest tests/unit_tests tests/integration --cov=src --cov-report=html

# 查看报告
# 打开 htmlcov/index.html
```

---

## V2 测试要点

### 关键测试场景

1. **V2-a: 工具输出治理**
   - ✅ 超长命令输出不导致上下文爆炸
   - ✅ 截断/外置对用户和模型可感知
   - ✅ 主循环不因单条 observation 崩溃

2. **V2-b: Planner 工具 + MCP**
   - ✅ 至少一项只读能力被 Planner/Executor 共享
   - ✅ Planner 无法调用副作用工具
   - ✅ MCP 工具加载、缓存、并发访问正常

3. **V2-c: Reflection/Snapshot**
   - ✅ Reflection 在指定间隔触发
   - ✅ 产出的 Snapshot 结构正确
   - ✅ Supervisor 能基于 Snapshot 做出决策
   - ✅ Executor 保持无重规划权限（决策 4）

### 边界情况测试

- ✅ Reflection 在 `tool_rounds=0` 时不触发（未执行工具）
- ✅ 空上下文、None 上下文处理
- ✅ MCP 连接失败、超时处理
- ✅ 格式错误 JSON、缺少字段处理
- ✅ 并发 MCP 工具访问
- ✅ 不同 Reflection 频率配置

### 性能测试

- ✅ 工具输出截断性能
- ✅ MCP 工具缓存效果
- ✅ Reflection 额外开销

---

## 测试最佳实践

### 单元测试

- ✅ 使用 Mock LLM 避免真实 API 调用
- ✅ 测试边界条件和错误情况
- ✅ 验证配置字段和默认值
- ✅ 测试权限分离和安全边界

### 集成测试

- ✅ 测试完整流程（Executor → Reflection → Supervisor）
- ✅ 验证数据结构（JSON 格式、字段完整性）
- ✅ 测试并发场景和竞态条件
- ✅ 验证配置组合效果

### E2E 测试

- ✅ 使用真实 LLM API（需要 API Key）
- ✅ 测试完整用户场景
- ✅ 验证多轮对话和状态保持
- ✅ 测试重规划和恢复流程

---

## 故障排查

### 测试失败常见原因

1. **Reflection 测试失败**
   - 检查 `tool_rounds > 0` 条件
   - 验证 `REFLECTION_INTERVAL` 配置
   - 确认 Reflection 路由逻辑

2. **MCP 测试失败**
   - 检查网络连接（如使用真实 MCP）
   - 验证配置字段名称拼写
   - 确认 Mock 设置正确

3. **工具输出测试失败**
   - 检查文件系统权限
   - 验证工作区目录存在
   - 确认配置参数范围

### 调试技巧

```bash
# 单个测试文件详细输出
pytest tests/unit_tests/test_executor_reflection.py::TestReflectionRouting -v -s

# 只运行失败的测试
pytest tests/unit_tests tests/integration --lf

# 打印本地变量
pytest tests/unit_tests/test_executor_reflection.py -v -l
```

---

## 下一步

### V2 测试完成

✅ **V2 测试覆盖完成**：
- 331 项测试全部通过
- V2-a/b/c 功能全面覆盖
- 边界情况和错误处理完善
- 性能和安全验证完成

### V3 准备建议

基于 V2 测试经验，V3 开发建议：

1. **从第一天就写测试**: 不要等到实现完成
2. **测试优先级**: 单元 → 集成 → E2E
3. **并发测试**: V3 的核心是并行，需要专门的并发测试
4. **复用 V2 治理**: V2-a 的工具输出治理对 V3 并发更重要
5. **考虑默认开启**: V3 可考虑默认启用 Reflection

---

**文档维护**: 本文档应与 V2 实现和测试保持同步更新。如有新增测试或变更，请及时更新。

**最后更新**: 2026-04-09
**测试版本**: v0.2.1 (V2-complete)
