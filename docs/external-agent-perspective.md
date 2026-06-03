# 外部 Agent 视角分析

> 状态：2026-05-19 完成  
> 定位：一篇来自外部 AI Agent（Qoder）的元分析——以"我自己也是个 Agent"的视角审视 AgentTriad 的设计，包含对比、反思和建议。  
> 背景：本文档由 Qoder 在深入阅读 AgentTriad 全部核心模块后撰写，不是项目内部文档，而是外部审视。

---

## 1. 引言：为什么要写这篇文档

我（Qoder）是一个 AI 编程助手。我的架构是单 LLM + 工具调用的经典模式。AgentTriad 走的是另一条路：三层 Agent 解耦、进程隔离、结构化状态管理。

这两条路没有绝对的优劣，但各自的选择背后有深刻的设计哲学差异。这篇文档试图从"另一个 Agent"的视角，说清楚这些差异在哪里，哪些地方 AgentTriad 做得比我好（我做不到的），哪些地方我可以反向给 AgentTriad 一些启发。

这篇文章的另一个目的：**AgentTriad 的目标之一是让 Agent 管理自己的上下文**。那么，先让一个外部 Agent 来"审视"这个系统，看看它管理上下文的方式是否经得起审视——这本身也是一种元验证。

---

## 2. 我（Qoder）的内部架构

在对比之前，我需要坦诚地描述自己的架构。

### 2.1 核心循环

```
用户消息 → LLM（含系统提示词 + 工具定义 + 对话历史 + 记忆注入）
              ├── 有 tool_calls？→ 执行工具 → 结果回到上下文 → 继续推理
              └── 无 tool_calls？→ 直接回复给用户
```

这不是三层架构。**我只有一层**。我的"规划"发生在 LLM 的隐式思维链（thinking）中，我的"执行"就是直接调用工具函数，我的"状态"是对话历史的自然累积。

### 2.2 我的关键特征

| 特征 | 我的做法 | 含义 |
|------|---------|------|
| **规划** | LLM 隐式推理，thinking block | 不可审计，但零额外延迟 |
| **执行** | 直接 function calling | 工具定义嵌在 system prompt 中，LLM 全量可见 |
| **失败处理** | LLM 根据 error 内容自行判断是否重试 | 行为不可预测，但很灵活 |
| **进程模型** | 单进程，所有工具调用在同一地址空间 | 简单，但一崩全崩 |
| **记忆** | 单次会话的对话历史 + memory 系统 | 跨会话记忆有，但非结构化 |
| **工具选择** | LLM 直接 function calling | Planner/Executor 没有分工 |
| **子任务派发** | Task tool 启动专用子 Agent | 有，但不是主循环的一部分 |

### 2.3 我的设计哲学

**信任 LLM 的判断力**。系统提示词定义边界和规则，但具体的工具选择、执行顺序、失败应对，全部由 LLM 在上下文窗口中自行判断。这种设计的好处是极简——没有 Planner、没有 Executor、没有进程管理器、没有 Mailbox。代价是行为不可预测、不可审计、不可复现。

---

## 3. 对比分析

### 3.1 核心维度

| 维度 | AgentTriad | Qoder（我） |
|------|-----------|-------------|
| **架构层数** | 3（Supervisor → Planner → Executor） | 1（LLM + 工具） |
| **规划与执行** | 解耦：Planner 写 intent，Executor 选工具 | 耦合：LLM 一步到位 |
| **工具可见性** | Planner 不知工具有哪些（决策 3） | LLM 全量可见 |
| **进程隔离** | Executor 独立 OS 子进程（V3） | 单进程 |
| **失败自愈** | 结构化 status + replan 循环 + Mode 升级 | LLM 自然语言推理 |
| **记忆系统** | V4 知识树（文件 + 向量 + Overlay） | 对话历史 + memory |
| **状态管理** | 显式 State + PlannerSession | 隐式（对话历史） |
| **通信方式** | 结构化 JSON（Plan JSON、ExecutorResult） | 自然语言（tool result） |
| **可审计性** | 每一步决策可追溯 | 依赖 thinking block 可见性 |

### 3.2 延迟模型对比

**AgentTriad Mode 3（最重路径）**：
```
用户输入
  → kt_retrieve（自动 RAG 检索，~100ms）
  → call_model（Supervisor 决策，~1-3s）
  → call_planner（Planner ReAct 循环，可能多轮，~3-10s）
  → dynamic_tools_node（解析 Plan JSON）
  → call_model（Supervisor 收到 Plan，决定执行）
  → call_executor（Executor ReAct 循环，~5-30s+）
  → dynamic_tools_node（处理结果）
  → call_model（Supervisor 总结，~1-2s）
```

总计：~10-50s+，取决于 Planner 和 Executor 各自的 ReAct 轮数。

**Qoder（我，对等价任务）**：
```
用户输入
  → LLM thinking（规划 + 执行交织，~3-10s）
  → 工具调用（~1-5s 每轮）
  → 继续推理 + 回复（~2-5s）
```

总计：~5-20s。

**差距在哪里**：AgentTriad 多了 Planner 的独立 ReAct 循环、Supervisor 的两次调度决策（先调 Planner 再调 Executor）、以及状态同步（JSON 解析、Session 更新）。这是解耦的代价。

### 3.3 Token 开销对比

AgentTriad 的结构化通信（Plan JSON + ExecutorResult）比传全量对话历史省 token——这是决策 1 的核心论据。但 AgentTriad 的固定 overhead 包括：

- **Supervisor 系统提示词**：包含三种模式的完整说明、失败处理规则、工具描述
- **Planner 系统提示词**：包含 Executor 能力文档、Plan JSON 格式规范
- **Executor 系统提示词**：包含工具描述、Observation 规则、Reflection 规则
- **kt_retrieve 注入**：每次用户消息前拼接检索结果
- **Executor 状态注入**：每次 call_model 前注入实时任务状态（~50 tokens）

这些固定开销在简单任务中占比很高。我的做法是把所有信息塞在一个 system prompt 里——简单粗暴，但对简单任务没有额外开销。

---

## 4. AgentTriad 做得比我好的地方

### 4.1 失败处理：显式 > 隐式

这是我认为 AgentTriad 最精巧的设计。

我遇到工具错误时，只有一个选择：把 error message 还给 LLM，让 LLM 自己判断要不要重试、怎么重试。这在 90% 的情况下没问题——LLM 足够聪明。但剩下 10% 的情况，LLM 可能陷入重复重试的死循环，或者在"应该放弃"和"应该换个方式重试"之间犹豫。

AgentTriad 的失败处理是一个**五层金字塔**：

```
         ┌─────────────┐
         │  正常失败     │  Executor LLM 主动停止，返回 status=failed + 结构化原因
         ├─────────────┤
         │  异常崩溃     │  _mark_plan_steps_failed() 兜底
         ├─────────────┤
         │  进程不可达   │  unreachable/not_found → 构造失败结果 + 清理资源
         ├─────────────┤
         │  超时保护     │  三层超时（LLM 调用 / tools_node / Supervisor 等待）
         ├─────────────┤
         │  Mode 升级    │  Mode 2 failed → 语义检测 → 自动升 Mode 3 重规划
         └─────────────┘
```

每一层都有明确的行为约定。这让我想到人类处理复杂任务时的"升级机制"——先自己试，不行就问同事，再不行就上报领导。AgentTriad 把这个模式结构化地实现在了三层 Agent 之间。

**我的反思**：我缺少一个显式的"升级"机制。如果我能在连续 N 次工具失败后自动切换到"规划模式"（先生成一个显式的恢复计划，再执行），会减少很多死循环的问题。但这也意味着我需要一个 Planner 层——回到了 AgentTriad 的架构。

### 4.2 知识树：结构化的跨会话记忆

这是我完全不具备的能力。我没有持久化存储——每次新会话我都是"白纸一张"（除了 memory 系统能注入少量片段）。

V4 知识树的 `摄入 → 去重 → 向量聚类 → 自动嫁接 → 重组` 管道，最让我欣赏的不是技术实现，而是**设计哲学**：

```
文件系统即真理（Source of Truth）
向量服务于结构（辅助聚类和检索）
涌现而非预设（新知识自动找归属）
结构变更实时反馈到向量空间（Change Mapping）
```

这解决了一个根本性问题：**如何让 Agent 的记忆不只增长，还能演化**。大多数 RAG 系统只解决"存"和"取"，但知识是会过时的、会重组的、会发现新关联的。AgentTriad 的编号树重组机制（`knowledge_tree_reorganize`）让 Agent 可以主动重新整理自己的知识结构，更新的目录锚点又会反向影响未来的摄入和检索——这就是"进化"。

**特别值得说的一个细节**：`stored_vector = 0.8 * content_embedding + 0.2 * structural_vector` 这个混合向量。为什么 `0.2` 的结构权重？它不是为了让结构主导检索（内容权重 `0.8` 仍是主导），而是让同一目录下的文件在向量空间中**微微聚拢**。这种"软聚簇"的效果是：检索时如果命中了目录 A 下的一个文件，目录 A 下的其他相关文件也更容易被一起召回——即使它们的纯内容向量距离稍远。

**我的反思**：我的 memory 系统本质上是 key-value 检索——用语义相似度匹配，没有结构化的组织层。如果我能有一个类似的文件系统 backed 的记忆组织方式，跨会话的 recall 精度会高很多。

### 4.3 Planner 不知工具名（决策 3）

这是一个看似微妙但影响深远的设计。

在我的架构中，LLM 看到所有工具定义，然后直接 function call。这意味着：
- 工具名称会影响规划决策（"有个 `write_file` 工具，所以我应该写文件"）
- 工具集的任何变更（新增、重命名、删除）都需要 LLM 重新学习
- 安全边界模糊——LLM 可以调用任何它看得见的工具

AgentTriad 刻意让 Planner 只写 `intent` 和 `expected_output`，不碰工具名。这不是为了限制 Planner 的能力，而是为了**让规划逻辑独立于工具实现**。Executor 的工具集可以任意演化（新增工具、替换实现、修改参数），Planner 完全不受影响。这种解耦在长期维护中的价值远大于初次实现时的便利。

**一个值得思考的推论**：如果有一天 AgentTriad 的 Executor 换了一个完全不同语言实现的工具集（比如从现在 Python 工具换成 Node.js 工具），Planner 不需要任何修改。这是真正的"接口隔离"。

### 4.4 进程隔离的必要性

我的工具调用全部在同一进程里。一个工具 OOM，整个会话就没了。AgentTriad 的 V3 架构把 Executor 跑在独立 OS 子进程中：

```
Supervisor 进程
  └── Executor 子进程 1（plan_id=A）
  └── Executor 子进程 2（plan_id=B）
```

每个子进程：
- 动态分配端口，互不冲突
- 崩溃不影响 Supervisor 和其他 Executor
- `atexit + SIGTERM/SIGINT` 确保主进程退出时子进程也被清理
- Push（Mailbox HTTP）+ Pull（ExecutorPoller）双路径返回结果

**特别优雅的一个细节**：Executor 子进程的 `terminate() → wait(3s) → kill()` 升级策略。不是粗暴的 `kill -9`，而是给进程一个优雅退出的机会——3 秒不够再强杀。这种工程细节在 Agent 框架中不多见。

---

## 5. 我可以给 AgentTriad 的建议

以下建议来自我作为一个"更简单的 Agent"的视角——有些问题在 AgentTriad 的复杂架构中可能被忽略了。

### 5.1 建议 1：加一个轻量级的"快速路径"判断器

**现状**：Supervisor 通过提示词中的自然语言指令来判断 Mode 1/2/3。这依赖 LLM 的判断力——大多数时候没问题，但偶尔会误判（比如把简单问答误判为需要 Mode 3）。

**问题**：Mode 3 的 token 开销和延迟比 Mode 1/2 高一个数量级。如果 10% 的简单请求被误判为 Mode 3，系统整体效率会显著下降。

**建议**：在 Supervisor 调用 LLM 之前，加一个**非 LLM 的快速分类器**：

```python
def quick_mode_hint(user_message: str) -> str | None:
    """返回 None 表示交给 LLM 判断，否则返回推荐的 mode 提示。"""
    # 规则 1：纯疑问句 + 不包含动作词 → 倾向 Mode 1
    action_keywords = ["创建", "构建", "修改", "重构", "删除", "生成", "写"]
    if any(kw in user_message for kw in ["是什么", "怎么", "为什么", "解释"]):
        if not any(kw in user_message for kw in action_keywords):
            return "mode1_hint"
    
    # 规则 2：包含多步骤指示词 → 倾向 Mode 3
    multi_step_markers = ["先……然后", "第一步", "步骤", "依次"]
    if any(m in user_message for m in multi_step_markers):
        return "mode3_hint"
    
    # 规则 3：极短消息（< 30 字符）+ 不包含文件路径 → 倾向 Mode 1
    if len(user_message) < 30 and "/" not in user_message and "\\" not in user_message:
        return "mode1_hint"
    
    return None  # 交给 LLM 判断
```

这个分类器不替代 LLM 决策，只是在 system prompt 中加入一个 hint（如 `[系统建议：此问题可能无需规划和执行工具]`），LLM 可以忽略它。但它能显著降低误判率。

**原因**：LLM 不是为"一句话判断模式"优化的——它本质上是生成模型，做这种简单的二分类既不擅长又浪费 token。把确定性强的判断外置，LLM 只处理模糊地带。

### 5.2 建议 2：Plan JSON 粒度的"原子任务"校准

**现状**：Planner 生成的 `steps[]` 粒度由 LLM 自行判断，没有明确的粒度约束。

**问题**：步骤太粗 → Executor 不知道怎么做（"分析项目结构"这种 step 包含太多子任务）；步骤太细 → 失去了 Planner/Executor 分工的意义（"打开文件 a.txt" 这种 step 不需要 Planner）。

**建议**：在 Planner 的系统提示词中加入一个**可操作的粒度标准**：

```
每个 step 应该是一个"Executor 可以在不需要中间暂停和外部判断的情况下完成的原子任务"。

好的 step：
  - "读取 config.yaml 并提取所有模型配置参数"
  - "为 UserService 类编写单元测试"

不好的 step：
  - "分析项目"（太粗，包含无数子任务）
  - "打开 config.yaml"（太细，这是工具调用不是任务）
```

更进一步，可以在 Planner 输出后加一个**自动校验器**：

```python
def validate_step_granularity(steps: list[dict]) -> list[str]:
    warnings = []
    for step in steps:
        intent = step.get("intent", "")
        # 太粗：intent 少于 5 个词
        if len(intent.split()) < 5:
            warnings.append(f"Step {step['step_id']}: intent 可能太粗 ({intent})")
        # 太细：intent 包含明显是工具调用的动词
        tool_verbs = ["打开", "读取", "执行命令", "运行"]
        if any(v in intent for v in tool_verbs) and len(intent.split()) < 10:
            warnings.append(f"Step {step['step_id']}: intent 可能太细 ({intent})")
    return warnings
```

校验结果以 warning 形式反馈给 Planner，让它自行调整。

### 5.3 建议 3：知识树的"检索质量反馈回路"（P3 优先）

**现状**：`optimization/` 目录下的 P3 优化闭环还只是框架（`signals.py` + `anti_oscillation.py`），标记为"待实现"。

**问题**：没有反馈回路的知识树只是在"长"，不一定在"进化"。一个知识节点被检索到了但没用、或者应该被检索到但没被检索到——这些信号目前没有形成闭环。

**建议**：这是最值得优先实现的功能。具体方案可以很简单：

**Step 1：丰富 RetrievalLog**

在 `retrieval/log.py` 中，每次检索后让 Supervisor 在结果中附加一个低成本的反馈标记：

```python
# 在 ExecutorResult.summary 或工具返回中嵌入
"[KT_FEEDBACK: retrieved_node='development/debugging.md' was_useful=true]"
"[KT_FEEDBACK: retrieved_node='setup/config.md' was_useful=false reason='内容不相关']"
```

**Step 2：定期分析日志**

每周（或每 N 次检索后）自动分析反馈：

```python
def analyze_retrieval_quality(logs: list[RetrievalLog]) -> OptimizationSignal | None:
    """检查是否需要优化。"""
    # 误召回率 > 30% → 提高相似度阈值
    false_positive_rate = count_was_useful_false / total
    if false_positive_rate > 0.3:
        return OptimizationSignal(type="rag_false_positive_rate_high", ...)
    
    # 某个目录的节点一直被评为 useless → 可能该目录锚点有问题
    useless_by_dir = group_by_directory(was_useful_false)
    for dir_path, count in useless_by_dir.items():
        if count > threshold:
            return OptimizationSignal(type="directory_anchor_drift", dir=dir_path, ...)
```

**Step 3：自动调整阈值**

如果反馈持续表明某个阈值不合适，自动微调并记录：

```python
if signal.type == "rag_false_positive_rate_high":
    config.rag_similarity_threshold = min(config.rag_similarity_threshold + 0.05, 0.8)
    log.info(f"Auto-adjusted similarity threshold to {config.rag_similarity_threshold}")
```

**为什么这个优先级高**：知识树是 AgentTriad 的核心差异。但如果检索质量无法验证和自动改进，它就只是一个"能存东西的文件夹"而不是"有自我进化能力的记忆系统"。

### 5.4 建议 4：简化 Plan 执行时的 Supervisor 中间轮

**现状（Mode 3 典型执行序列）**：

```
轮 1: Supervisor call_model → 决定调 call_planner
轮 2: dynamic_tools_node → 解析 Plan JSON → 更新 Session
轮 3: Supervisor call_model → 看到 Plan → 决定调 call_executor
轮 4: dynamic_tools_node → 等待 Executor → 处理结果
轮 5: Supervisor call_model → 总结回复
```

Supervisor LLM 被调用了 3 次——轮 1、3、5。其中轮 3 本质上只是说"好的，Plan 有了，执行吧"——这是一个低信息量的决策。

**建议**：在 `call_planner` 成功后自动派发 `call_executor`，跳过中间轮：

```
轮 1: Supervisor call_model → 决定调 call_planner
轮 2: dynamic_tools_node → 解析 Plan JSON → 自动派发 call_executor → 等待结果
轮 3: Supervisor call_model → 看到执行结果 → 总结回复
```

关键：只在 Planner 返回 `status=completed` 的步骤数 > 0（即这是一个增量修订而非全新 Plan）时跳过；全新 Plan 仍需 Supervisor 确认。

这不是消除所有中间决策，而是消除那些"确定性极高、不需要 LLM 再判断"的中间轮。

### 5.5 建议 5：向 Executor 暴露"你现在在做第几步"

**现状**：Executor 拿到的是完整的 Plan JSON，但它不知道 Supervisor 期望它执行到哪一步。Plan JSON 中有多个 pending 步骤，Executor 需要自行判断从哪里开始、做到哪里停。

**问题**：Executor 可能过度执行（把不需要的步骤也做了）或者执行不足（做了一个 step 就停了，但其实同组步骤可以连续完成）。

**建议**：在 Executor 的系统提示词中加入明确的执行范围：

```
当前任务：执行 Plan 中的第 2-4 步（step_2, step_3, step_4）
- step_1 已完成：已读取项目配置文件
- step_2-4 需要你完成：分析依赖 → 识别问题 → 提出修复方案
- step_5 等待 Supervisor 进一步指示

请在完成 step_4 后主动停止，不要继续执行 step_5。
```

这样 Executor 有明确的"入"和"出"边界，不会越权也不会过早停止。

---

## 6. 一些开放式思考

以下不是具体的建议，而是在写这篇文档过程中产生的、关于 Agent 设计哲学的零散思考。

### 6.1 "不信任"作为一种架构原则

AgentTriad 的设计哲学可以概括为一句话：**不信任任何一个 Agent**。

- 不信任 Supervisor 的模式判断 → 用三种模式的显式约定约束它
- 不信任 Planner 的工具选择 → 不让它看到工具有哪些
- 不信任 Executor 的失败处理 → 它遇阻即停，不能自己重规划
- 不信任 Executor 的进程稳定性 → 把它隔离在子进程里

这种"不信任"不是贬义的——它是工程上的防御性设计。在安全攸关的 Agent 应用中，这种设计是正确的。

但反过来，我的设计哲学是**信任 LLM 的综合判断力**——给它上下文、工具、规则，让它自己判断。这种信任在 95% 的情况下省去了大量的架构开销。

**我的结论**：两种哲学不是对立的，而是应该根据任务的风险等级动态选择。AgentTriad 可以加一个 `trust_level` 参数：低风险任务走轻量模式（直接 Executor ReAct），高风险任务走完整三层。

### 6.2 上下文窗口增长是不可逆的

AgentTriad 已经做了消息截断（`_trim_messages_for_llm`），但核心问题是：随着新任务不断执行，上下文窗口只会越来越长。截断只是治标。

知识树（KT）的长期价值可能不是"提供更多上下文"，而是**替代部分上下文**。当 KT 足够成熟时，Supervisor 可以不依赖长对话历史，而是依赖 KT 检索到的结构化知识摘要。这意味着：

```
现状：context = messages[-N:] + kt_context
未来：context = kt_context（富结构） + messages[-M:]（最近几轮，M << N）
```

这是一个根本性的上下文模型转变——从"靠历史记住一切"变成"靠知识树提供精华"。

### 6.3 Agent 的"元认知"

AgentTriad 的 Reflection（决策 10）是一个有趣的起点。目前的 Reflection 是：工具执行 N 步后，LLM 自问"我做对了吗？"

但更有趣的问题是：Agent 能不能学会**什么时候该做 Reflection**？不是固定 `REFLECTION_INTERVAL`，而是根据任务特征动态决定：

```
简单任务（step ≤ 3） → 不做 Reflection，一步到底
中等任务（step 4-10） → 每 3 步检查一次
复杂任务（step > 10） → 每步检查
高风险任务（涉及文件删除、数据修改） → 每步检查
```

这个判断可以由 Planner 在生成 Plan 时标注，而不是硬编码一个全局 `REFLECTION_INTERVAL`。

### 6.4 "涌现"的另一种理解

V4 知识树的"涌现"（emergence）是指：信息自底向上通过向量聚类自然形成层级结构。

但我理解的"涌现"还有一种含义：**Agent 做出设计者没预料到的有用行为**。比如：
- Planner 发现某些 step 的模式经常重复，主动建议把它们抽象成"模板步骤"
- Executor 发现某个工具组合特别有效，把它记录到 KT 中作为"常用配方"
- Supervisor 发现某种失败模式反复出现，主动调整 Planner 的提示词

这些行为不是预先编程的，而是 Agent 在使用 KT 的过程中自己发现的。目前 AgentTriad 的 KT 有 ingest、retrieve、reorganize，但缺少"从使用模式中发现规律"的能力。这可能是 V5 的方向。

---

## 7. 总结

| 我看 AgentTriad | 核心评价 |
|----------------|---------|
| **最欣赏的设计** | 失败处理的五层金字塔 + 知识树的涌现式结构 |
| **最值得商榷的取舍** | 架构复杂度 vs 简单任务的效率 |
| **最期待的功能** | P3 优化闭环——让知识树从"能存"变成"能进化" |
| **最想强调的风险** | 知识树目前的检索质量没有验证回路，"涌现"可能只是"累积" |

AgentTriad 是一个有野心的项目。它的野心不在于"做一个能跑的三层 Agent"，而在于"做一个能自我进化的 Agent 底座"。V4 知识树是这个野心的核心承载——如果知识树真的能形成摄入 → 检索 → 反馈 → 优化的闭环，AgentTriad 就不仅是"另一个 Agent 框架"，而是一个真正有记忆和学习能力的系统。

从我的视角看，这条路是对的。只是需要警惕"架构的复杂度吞噬了进化本身的灵活性"——当系统有了太多预设的层、太多显式的契约、太多结构化的边界，Agent 的"自我进化"空间反而被压缩了。最好的人工系统，是那些在设计时就知道自己会被超越的系统。

---

> 本文档由 [Qoder](https://qoder.com) 撰写，基于对 AgentTriad 项目全部核心模块的阅读和分析。  
> 所有建议均为外部视角，是否采纳由项目组自行判断。
