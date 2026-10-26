# DeepResearchAgent 面经 · 自学版（弄懂）

> **用途**：弄懂每道题**背后到底发生了什么**——原理、代码行号、实测数据、数字出处、被追问时的降级话术。
> **配套**：[DeepResearchAgent_面经-面试官版.md](DeepResearchAgent_面经-面试官版.md)（每题 30 秒口播答案）。
> **用法**：先读这份 → 理解了 → 再用面试官版练口条。**顺序不能反**，反了就是背话术。
> **所有行号可点击**，直接跳到代码。

---

## 第 0 章｜怎么用这份文件

**三层准备，缺一层都会被追穿：**

| 层 | 内容 | 在哪 |
|---|---|---|
| **事实层** | 数字是多少、代码在哪一行 | 第 7 章数字总账 |
| **原理层** | 为什么这么设计、代价是什么 | 第 2–6 章（按模块） |
| **诚实层** | 哪些不能报、被问到怎么降级 | 第 8、9 章 |

**这份文件的核心价值不是让你多知道几个数字，是让你知道边界在哪。** 这个项目的真实状态是：**机制实现完整、评测产物真实、但模块收益无法证明**。三句话都在简历上，三句话你都得能展开。

> **记住一个心法**：面试官不会因为你有盲区淘汰你，**会因为你说不清自己的盲区淘汰你**。

---

## 第 1 章｜系统全貌（先建立地图）

### 1.1 五大模块与代码位置

| 模块 | 简历叫法 | 代码 | 行数 |
|---|---|---|---|
| M1 | 编排引擎 | `src/orchestrator/` + `src/planner/` | — |
| M3 | 上下文治理（压缩） | `src/compressor/` | **986 行 / 4 文件** |
| M4 | 共享记忆 | `src/memory/` | — |
| M5 | 对抗降噪 | `src/adversarial/` | — |
| 评测 | 评测体系 | `evaluation/` + `scripts/` | — |

**全仓规模（实测）**：63 个文件 / **14,146 行** / 410 个函数（async 62 个 = 15.1%）/ docstring 覆盖 79.5% / 参数注解 96.9% / 返回值注解 94.4%。
出处：[outputs/extra_metrics.json](outputs/extra_metrics.json)。

### 1.2 一次 run 的完整生命周期

```
query
  │
  ▼
[IDLE] ──► [PLANNING]      Planner 输出 JSON → 建 DAG → Kahn 校验
  │                              │ 解析失败 → PlanParseError → 重试/降级
  ▼
[DISPATCHING]              Kahn 分层 → 层内 Semaphore(5) 并发派发
  │                              │ 子任务 asyncio.wait_for(30s)
  ▼
[COLLECTING]               只收 SUCCESS 结果（TIMEOUT/FAILED 被标记后丢弃）
  │
  ├─ 失败率 > 0.5 ──► [REPLANNING] ──► 回 [DISPATCHING]（上限 3 轮）
  │
  ▼
[SYNTHESIZING]             合成初稿（M3 压缩在此介入上下文）
  │
  ▼
[ADVERSARIAL]              Red 五维攻击 → Blue 三动作修复 → 循环
  │                              │ 收敛/震荡 → 退出
  ▼
[DONE]                     输出 ResearchReport

任意状态下：全局超时 600s ──► 强制 [SYNTHESIZING]
任意状态下：重规划超限 ──► [FAILED]
```

**关键心法**：`DONE` 和 `FAILED` 是循环的两个出口，**每次迭代开头都检查全局超时**。这保证了不会死循环。

---

## 第 2 章｜编排引擎 M1（Round 2 的底层）

### 2.1 九状态状态机 —「9」这个数是怎么来的

**代码**：[src/orchestrator/schemas.py:30-46](src/orchestrator/schemas.py#L30-L46)

```python
class OrchestratorState(Enum):
    IDLE = "idle"              # 正常流 ①
    PLANNING = "planning"      # ②
    DISPATCHING = "dispatching" # ③
    COLLECTING = "collecting"  # ④
    SYNTHESIZING = "synthesizing" # ⑤
    ADVERSARIAL = "adversarial" # ⑥
    REPLANNING = "replanning"  # 异常流 ⑦
    DONE = "done"              # 终态 ⑧
    FAILED = "failed"          # 终态 ⑨
```

**为什么是"字典映射"而不是"状态类"**：[orchestrator.py:83-93](src/orchestrator/orchestrator.py#L83-L93)

```python
self._state_handlers: dict[OrchestratorState, Callable[...]] = {
    OrchestratorState.IDLE: self._on_idle,
    ...
}
```

每个 handler **返回下一个状态**，而不是自己调用下一个。**好处**：状态转移全在一处可见，加状态只加一个字典项；**坏处**：没有编译期检查，写错状态名只会在运行时报 `KeyError`。

> ⚠️ **易错点**：`AgentStatus(Enum)`（[schemas.py:55](src/orchestrator/schemas.py#L55)）只有 **3** 个成员（SUCCESS/FAILED/TIMEOUT）——那是**单个子任务的执行结果**，和 9 状态的 `OrchestratorState` **不是一回事**。被问"9 个状态"时如果报了 SUCCESS/FAILED/TIMEOUT，就答错了。

### 2.2 Kahn 分层 — 和普通 Kahn 的三个区别

**代码**：[src/planner/dag.py](src/planner/dag.py)

| 步骤 | 行号 | 说明 |
|---|---|---|
| `add_edge` 自环立即拒绝 | [:49](src/planner/dag.py#L49) | `raise DAGCycleError(f"自环不允许: {from_node}")` |
| 入度表维护 | [:33](src/planner/dag.py#L33), [:53-55](src/planner/dag.py#L53-L55) | `_in_degree` 用 defaultdict |
| `topological_sort` | [:86-111](src/planner/dag.py#L86-L111) | 标准 Kahn；出队数 < 节点数 → 有环 |
| **环上节点定位** | [:108-111](src/planner/dag.py#L108-L111) | 注释：`# 找出环上的节点，便于调试` |
| `parallel_layers` | [:124-148](src/planner/dag.py#L124-L148) | 按层分组 |
| **层内排序确定性** | [:143](src/planner/dag.py#L143) | `next_layer.sort()` |

**三个区别**：

1. **自环在加边时拒绝**，不留到排序阶段——早失败，错误信息更准。
2. **环检测用"剩余节点"**：Kahn 跑完 `len(order) < len(nodes)` 说明有环，此时 `in_deg > 0` 的节点**就是环上的节点**——直接打进报错信息。这是**最经典的 Kahn 环检测法**，比 DFS 三色标记简单，且顺带给出环的位置。
3. **按层分组 + 层内排序**：`next_layer.sort()` 让同一层的任务顺序确定，**避免同 seed 下结果不可复现**。

> **面试追问预备**："为什么不用 DFS 三色标记？"→ **Kahn 天然给出"同层可并发"的分组，DFS 只给全序**。我这里要的就是分层，不是排序。

### 2.3 并发控制 — Semaphore 的语义边界

**代码**：[src/orchestrator/orchestrator.py:238](src/orchestrator/orchestrator.py#L238)
配置：[configs/default.yaml:112](configs/default.yaml#L112)

```python
asyncio.Semaphore(self._config.max_concurrent)   # = 5
```

**⚠️ 必须能说清的边界**：`Semaphore` 限制的是**在途并发数**，**不是速率**。100 个任务、并发 5，会以最大速度持续打后端，如果后端 QPS 限制是 10/s，**依然会 429**。真正限速要**令牌桶**。

**并发度 5 是怎么定的**：**经验值，没有标定。** 当时的约束是三条——后端 QPS、上下文预算（并发越高同时驻留上下文越多，压缩越频繁）、单任务 30s 超时（并发太高 P99 会突破它）。

> **诚实答法**：不要编标定过程。正确答法是"经验值 + 我知道该怎么做敏感性实验（1/3/5/10/20 五档 × 3 题）"。

### 2.4 三级降级 — 三个数字 + 一个真实缺口

**代码与配置**：

| 级别 | 触发条件 | 代码 | 配置 |
|---|---|---|---|
| 一 · 单任务超时 | 子任务 > 30s | [orchestrator.py:262-271](src/orchestrator/orchestrator.py#L262-L271) | `timeout_seconds: 30` |
| 二 · 增量重规划 | **层内失败率 > 0.5** | [orchestrator.py:533](src/orchestrator/orchestrator.py#L533) `if failure_rate > 0.5:` | — |
| 二 · 重规划上限 | — | [orchestrator.py:333](src/orchestrator/orchestrator.py#L333) | `max_replan_rounds: 3` ([yaml:116](configs/default.yaml#L116)) |
| 三 · 全局超时 | > 600s | [orchestrator.py:543-546](src/orchestrator/orchestrator.py#L543-L546) `_is_global_timeout` | `global_timeout_seconds: 600` ([yaml:114](configs/default.yaml#L114)) |

**"保留高置信结果"的定义（高危点 #1 的答案）**：[orchestrator.py `_do_replanning`](src/orchestrator/orchestrator.py)

```python
async def _do_replanning(self) -> OrchestratorState:
    """触发增量重规划。
    保留 confidence≥0.6 的成功结果，修改失败子问题。
    """
```

**所以简历里"保留高置信结果"是有精确定义的：`confidence ≥ 0.6`。** 这题答得出来就是加分项（高危点 #1 说的是"未定义"，其实定义了）。

⚠️ **但要注意一个实现细节**：`_do_replanning` 里最后 `self._results = []` ——**清空了上一轮所有结果**，注释说"保留在 memory 中，新任务可通过 `context_keys` 引用"。**所以"保留"是通过记忆层间接保留的，不是直接复用结果对象。** 被追问"保留的结果怎么复用"要答到这一层。

**🔴 真实缺口（必须主动说）**：**重规划次数在 5 次真实运行里全是 0。**

出处：5 份报告 metadata（`重规划次数` 字段）。

**这意味着**：第二级降级**从未在实战中触发过**。你只能讲设计和代码路径，**讲不了实战效果**。主动说出这一点，比被追问出来强十倍。

### 2.5 超时之后合成什么

**代码**：[orchestrator.py:185](src/orchestrator/orchestrator.py#L185)

```python
content="Research failed due to persistent errors or global timeout.",
```

**缺什么**：没跑完的子任务对应的章节会缺失或只有浅层结论。**用户能感知到吗**：不能——目前只在内部日志打 `[Replan]`，**没有把降级原因写进报告元数据**。这是明确的改进点。

### 2.6 规划器与 JSON 兜底

**核心难点不是拆解，是稳定解析。** LLM 输出的 JSON 有三种高频格式漂移：```json 围栏包裹、尾逗号、单引号。

**多级兜底顺序**：直接 `json.loads` → 剥围栏 → 正则提取第一个平衡花括号 → 降级。

**解析失败的处理分两种情况**：
- **规划阶段失败** → `PlanParseError` → 重试或降级（[orchestrator.py 的 REPLANNING 分支](src/orchestrator/orchestrator.py#L333)）
- **对抗评分阶段失败** → **返回保守分 5.0，不让流程崩**（见 5.4）

> **设计原则**：**解析兜底的严格程度和"失败代价"成反比**。规划失败会污染整条链路，所以严格；评分失败只影响一个维度，所以宽松。

---

## 第 3 章｜上下文压缩 M3（Round 4 的底层）

### 3.1 三个阈值到底是什么 — 本讲第一个纠正

**代码**：[src/compressor/compressor.py:31-33](src/compressor/compressor.py#L31-L33)

```python
_L1_THRESHOLD = 0.60
_L2_THRESHOLD = 0.80
_L3_THRESHOLD = 0.95
```

**这是一个高频误解点**：这三个数是**预算占用率**（token 占可用预算的比例），**不是语义相似度**。

**预算怎么算**（[:36-37](src/compressor/compressor.py#L36-L37), [:68](src/compressor/compressor.py#L68)）：

```
max_context_length    = 16,000 token
output_reserve        =  2,048 token
─────────────────────────────────────
available_budget      = 13,952 token
按 3.5 字符/token 换算 = 48,832 字符
```

**三级触发的字符位置**：

| 级别 | 占用率 | 字符阈值 |
|---|---|---|
| L1 向量粗筛 | 60% | **29,299** |
| L2 TextRank 抽取 | 80% | **39,066** |
| L3 LLM 摘要 | 95% | **46,390** |

出处：[outputs/compressor_metrics.json](outputs/compressor_metrics.json)（12 个 section）。

### 3.2 🔴 最强发现：闸门和分级差 4.9 倍，三级压缩从未触发

**闸门位置**：[orchestrator.py `_build_memory_context()`](src/orchestrator/orchestrator.py#L546-L592)

```python
if total_chars > 6000:
    # 调压缩
```

**算一下**：6000 字符 ≈ 1,714 token ÷ 13,952 = **12.3% 占用率**。
**而 L1 的阈值是 60%。**

```
闸门        12.3%  ├───────────────────────────────────────┤
L1 阈值     60%    │                                       ├──────┤
L2 阈值     80%    │                                              ├──┤
L3 阈值     95%    │                                                 ├┤
                 差距 4.9 倍
```

**结论**：压缩函数**会被调用**（因为 6000 字符的闸门容易过），但**每次都在 L0 直接原样返回**。

**实测证据**（[outputs/compressor_metrics.json](outputs/compressor_metrics.json)）：按调用点形状构造 30 × 200 字符 = 6000 字符输入：

```
输出 == 输入（逐字节相同）
level_distribution = {0: 1, 1: 0, 2: 0, 3: 0}
日志打印：compressed: 6000 → 6000 chars
```

**日志在撒谎**：它打印了 `compressed`，但压缩比是 1.0。

**根因**：**两处用了不同的度量单位**——闸门用**字符数**（固定值 6000），分级用**占用率**（比例）。修法是把闸门改成 `usage_ratio > 0.6`，让两者同量纲。

**另一个证据**：要让 `_memory_store` 真正涨到 L1 阈值，需要约 **140 条**记忆条目（按 ~210 字符/条）；到 L2 需要 187 条，L3 需要 221 条。

### 3.3 L1 向量粗筛 — 一个方向反了的循环

**代码**：[:170-218](src/compressor/compressor.py#L170-L218)

逻辑：encode query 和 `text[:1000]` → 归一化点积 → 按阈值过滤，**自适应阈值循环** `for threshold in [0.25, 0.20, 0.15]`，退出条件是 `if self.calculate_tokens(filtered) <= budget * 0.8: break`。

**🔴 发现的第二个 bug：这个循环方向是反的。**

**为什么**：如果第一轮阈值 0.25 把**所有**文档都过滤掉了（`filtered` 为空），那么 `calculate_tokens([]) = 0`，**`0 <= budget * 0.8` 成立，立即 break**。于是 `0.20` 和 `0.15` **永远不会被尝试**——阈值本该是"放宽"的，结果最严格的那一档一旦清空就锁死了。

**实测**：
- 所有相似度 = 0.10（都很不相关）→ 保留 **1/30**（走 fallback 保 top-1），阈值 0.20/0.15 从未被尝试
- 所有相似度 = 0.90（都相关）→ 保留 **30/30**

**兜底**：[:209-213](src/compressor/compressor.py#L209-L213) 保留 top-1 文档（`max(scored, key=lambda x: x[1])`）。所以**最坏情况不会输出空**，但会把 N 篇文档塌缩成 1 篇。

### 3.4 L2 TextRank 关键句抽取 — 技术含量最高的一级

**代码**：[src/compressor/extractive.py](src/compressor/extractive.py)

| 组件 | 行号 | 说明 |
|---|---|---|
| 分句正则 | [:26](src/compressor/extractive.py#L26) | — |
| 高价值模式（6 类） | [:28-33](src/compressor/extractive.py#L28-L33) | — |
| `_tokenize_sentences` | [:36-40](src/compressor/extractive.py#L36-L40) | 过滤 `len > 8` |
| 相似度矩阵 | [:43-57](src/compressor/extractive.py#L43-L57) | 句间余弦 |
| `_textrank_scores` | [:60-91](src/compressor/extractive.py#L60-L91) | 阻尼 0.85 / 迭代上限 30 / 容差 1e-4 / 边阈值 0.1 |
| `compress` | [:108-133](src/compressor/extractive.py#L108-L133) | 早退：`len(sentences) <= 3` |
| `textrank_sentences` | [:135-190](src/compressor/extractive.py#L135-L190) | 打分 + 选句 |

**打分公式**（[:135-190](src/compressor/extractive.py#L135-L190)）：

```python
combined = textrank * query_sims * value_bonus
k = max(1, int(n * top_ratio))
selected = set(np.argsort(combined)[::-1][:k])
# 然后按原序重排 —— 这一步不能省
```

**三个必须能讲的点**：

1. **阻尼 0.85 / 迭代 30 次 / 容差 1e-4** —— 标准 PageRank 参数。
2. **`combined` 是三因子乘积**：图重要性 × query 相关性 × 高价值加成。**乘不是加**——任何一项为 0 整句就被淘汰，这是有意的（与 query 无关的句子再重要也不该保留）。
3. **选出 top-k 后必须按原序重排**。因为 `np.argsort` 返回的是按分数排序的下标，直接用会让句子乱序、不可读。

**⚠️ 我实测纠正过的一个错误假设**：我原先以为 200 字符的短片段会 ≤ 3 句、命中 [`compress` 的早退](src/compressor/extractive.py#L128-L130)。**实测**：假文档 → 8 句；真实报告片段 → **4 句**（`4 <= 3` 为 False，**不早退**），于是走正常路径被 `k = max(1, int(4 * 0.22)) = 1` 压成 1 句。**完整 25,185 字符报告 → 570 句**，ratio 0.15 时 k=85。

### 3.5 L3 LLM 摘要 — 唯一会引入幻觉的一级

**代码**：[:249-283](src/compressor/compressor.py#L249-L283)
**Prompt**：[summarizer.py:19-29](src/compressor/summarizer.py#L19-L29)（文档级）、[:32-46](src/compressor/summarizer.py#L32-L46)（聚合级）

**两阶段**：先对每份文档各做一次摘要（`per_doc_max = max(200, budget // len(texts))`），再对所有摘要做一次聚合（`aggregate_max = max(400, budget // 2)`）。

**聚合 prompt 里有一条特别设计**（[:32-46](src/compressor/summarizer.py#L32-L46)）：
> 若文档间存在矛盾，请分别列出不同观点并标注来源

**这是全文最有设计感的一处**——它承认"不同来源会打架"，并要求保留分歧而不是强行统一。这和 M4 的矛盾消解是**同一条原则的两种实现**。

**⚠️ 结构性缺陷**：`_l3_summarize` **返回 `[aggregate]`——一个单元素列表**。这意味着**文档边界在 L3 之后彻底消失**，之后追问"这个结论来自哪份文档"就答不了。正确做法是保留 provenance（每段标注来源 doc id）。

### 3.6 L2 的注释算错了 — 文档与实现不符

**代码**：[:220-247](src/compressor/compressor.py#L220-L247)

```python
target_ratio = max(0.15, min(0.40, 0.50 - (current_tokens / max(budget, 1)) * 0.35))
```

**注释声称**（[:234](src/compressor/compressor.py#L234)）："预算用满 80% 时保留 30%，用满 100% 时保留 15%"

**实测**：

| 占用率 | 实际 target_ratio | 注释声称 |
|---|---|---|
| 60% | 0.290 | — |
| 70% | 0.255 | — |
| **80%** | **0.220** | **0.30** ❌ |
| 90% | 0.185 | — |
| 100% | 0.150 | 0.15 ✅ |

**注释在 80% 那一点错了**（0.220 vs 声称的 0.30）。而且 `target_ratio = 0.30` 实际发生在约 **57.1%** 占用率，不是 80%。

**这题的答法**："注释和实现对不上，实现是对的，注释算错了。**我倾向相信实现，因为实现是一条连续函数，注释是手算的近似。**"

### 3.7 滑动窗口兜底 — 孤儿 tool 消息（可复现的 400 报错）

**代码**：[src/compressor/sliding_window.py:65-153](src/compressor/sliding_window.py#L65-L153)

| 行为 | 行号 |
|---|---|
| FIFO `pop(0)` 循环 | [:105-120](src/compressor/sliding_window.py#L105-L120) |
| 极端内容截断 | [:122-145](src/compressor/sliding_window.py#L122-L145) |
| 返回 `system_msgs + kept` | [:120](src/compressor/sliding_window.py#L120), [:145](src/compressor/sliding_window.py#L145), [:153](src/compressor/sliding_window.py#L153) |

**三个缺陷**：

1. **按消息条数截断，不理解因果链**——`assistant(tool_call)` 和其后的 `tool(result)` 是一条链，但窗口把它们当两条独立消息。**结果：切出孤儿 tool 消息。**

   **实测**：`max_tokens=400` → 消息序列 `['system','assistant','tool','user']`，**orphan = True**；`max_tokens=200` → `['system','tool','user']`，**orphan = True**。

   **为什么严重**：OpenAI 兼容后端遇到"tool 消息前面没有对应的 tool_call"会**直接返回 400 错误**。所以这个兜底会**把系统从"上下文超限"变成"API 报错"**。

   **修法**：按**消息组**做窗口，`assistant(tool_call) + tool(result)` 作为原子单位一起保留或丢弃。

2. **原地修改调用方的 dict**：[:122-145](src/compressor/sliding_window.py#L122-L145) 里 `last_msg["content"] = ...` **直接改了传入的字典**，会污染调用方的数据。

3. **重排消息顺序**：返回 `system_msgs + kept` 把 system 消息**全部提到最前面**，改变了原始消息顺序。

**补偿手段**：system message 不参与淘汰（指令和约束在里面）。

### 3.8 `get_stats()` — 零调用者

**代码**：[:342-377](src/compressor/compressor.py#L342-L377)

**实测**：在 `src/` + `evaluation/` 范围内，`get_stats` 的调用点是 **0**。（另有 5 个同名方法分布在其他类里，合起来总数仍是 0。）

**含义**：压缩的统计信息**从来没有被用过**。这解释了为什么"压缩比 1.0 / level_distribution 全 0"这个事实**项目自己都不知道**——没人读这个统计。

### 3.9 死配置 — 4 个 YAML 键，零读取点

| 键 | 声明位置 | `src/` 读取点 |
|---|---|---|
| `l1_threshold` | `configs/default.yaml` | **0** |
| `l2_threshold` | 同上 | **0** |
| `l3_threshold` | 同上 | **0** |
| `embedding_model` | 同上 | **0** |

**活的**：`max_context_length`、`output_reserve_tokens`（[runner.py:196-197](src/core/runner.py#L196-L197)）。

**含义**：**你想调压缩阈值，改 YAML 是没用的**——代码里用的是硬编码的 `_L1_THRESHOLD = 0.60`。**这是"配置看起来可调、实际不可调"的典型陷阱。**

### 3.10 压缩的两个端到端实测（记住"成对报"原则）

**在真实报告上**（30 × 200 字符输入）：

| 级别 | 输出 | 压缩比 | 保留率 |
|---|---|---|---|
| L1 | 1 篇 / 200 字符 | 0.033 | **0.097** |
| L2 | 1 篇 / 68 字符 | 0.011 | **0.000** |
| L3 | 1 篇 / 68 字符 | 0.011 | **0.000** |

**⚠️ 关键**：**压缩比必须和保留率成对报。** 0.011 的压缩比听起来很棒，但保留率 0.000 意味着**信息全丢了**。只报压缩比是误导。

**保留率怎么算**（[:315-340](src/compressor/compressor.py#L315-L340)）：原文里的**数字实体 + 英文专有名词**，在压缩后文本中出现的比例。无实体时返回 1.0。

**对照组**（budget=3000 强制触发 L3）：occupancy 1.80 → 输出 1 篇 / 54 字符 → `{0:0,1:0,2:0,3:1}`。**说明三级代码是活的，只是真实工况下到不了。**

### 3.11 Embedder 兜底 — 语义信号有多弱

**背景**：本机没装 `sentence-transformers`，所以走随机向量兜底。

**实测**（用可控 2-D 向量精确构造）：相关句 cosine **+0.0779**，不相关句 **+0.0203**，差距 **+0.0577** —— **低于 L1 的最低阈值 0.15**，所以这条路径下**没有可用的语义信号**。

**含义**：如果你展示 L1 的实测结果，**必须说明用的是随机向量兜底**，否则数字无意义。

---

## 第 4 章｜共享记忆 M4（Round 4 后半）

### 4.1 架构 — 两层（这句简历上是对的）

**代码**：[src/memory/memory_store.py:5](src/memory/memory_store.py#L5) 的 docstring：

> 1. 持久层用 LongTermMemory（SQLite），内存层维护 numpy 向量索引，读写平衡

**逐字对应简历**："以 SQLite + NumPy 向量检索构建共享记忆"。**这句是真的，而且有 docstring 直接对应。**

| 层 | 实现 | 行号 |
|---|---|---|
| 持久层 | `LongTermMemory(db_path)` → `sqlite3` | [memory_store.py:123](src/memory/memory_store.py#L123)、[long_term.py:15](src/memory/long_term.py#L15) |
| 内存层 | `self._embeddings: np.ndarray` | [memory_store.py:130](src/memory/memory_store.py#L130) |
| 索引重建 | `_rebuild_index` / `_add_to_index` / `_remove_from_index` | [:140](src/memory/memory_store.py#L140)、[:157](src/memory/memory_store.py#L157)、[:171](src/memory/memory_store.py#L171) |
| 余弦相似度 | `_cosine_similarity` | [:53-59](src/memory/memory_store.py#L53-L59) |

**检索是暴力 O(N·d)**：没有 ANN 索引，直接 `np.linalg.norm` + 点积。**单次 run 的条目量很小**（每次 run 开始 `clear()`），所以够用；**没有做过规模压测**。

### 4.2 去重与矛盾检测 — 三个阈值

**代码**：[:30-36](src/memory/memory_store.py#L30-L36)

```python
_DEDUP_THRESHOLD = 0.92   # > 0.92 → 重复
_CONFLICT_LOW    = 0.65   # 0.65 ~ 0.92 且语义对立 → 矛盾
_CONFLICT_HIGH   = 0.92
_EVIDENCE_WEIGHTS = {"primary": 1.0, "secondary": 0.8, "inference": 0.6}
```

**三段语义**：

```
similarity:  0 ──────── 0.65 ──────────── 0.92 ────────── 1.0
             │  无关    │   可能矛盾        │    重复      │
             │          │  (需语义对立判)   │  (自动合并)  │
```

**去重逻辑**（[:263-283](src/memory/memory_store.py#L263-L283)）：`_find_duplicate` 找 cosine > 0.92 的已有条目 → **合并，保留 confidence 更高的**。

**矛盾检测**（[:285-313](src/memory/memory_store.py#L285-L313)）：在 (0.65, 0.92) 区间**且** `_is_semantically_opposite` 为真 → 记 `ConflictRecord`。

**"语义对立"怎么判**（[:62-88](src/memory/memory_store.py#L62-L88)）：否定词表 + 反义词表，分词求差集，**否定形式词占比 > 0.5 判对立**（[:86](src/memory/memory_store.py#L86)）。

**垃圾过滤**（[:195-202](src/memory/memory_store.py#L195-L202)）：`confidence < 0.3` 直接不入库。

**淘汰评分**（[:8](src/memory/memory_store.py#L8)）：`confidence × evidence_weight × recency × conflict_bonus`

**⚠️ 诚实边界**：反义词表**必然漏判**——能盖"增长/下滑"，盖不住"布局领先/份额落后"这类。**代价不对称**：漏检（真矛盾没发现）比误检（把正常结论判成矛盾）安全，所以启发式设计成偏保守。

### 4.3 冲突消解 — 三种策略，成本递增

**代码**：[:397-446](src/memory/memory_store.py#L397-L446)

| 顺序 | 策略 | 行号 | 成本 | 依据 |
|---|---|---|---|---|
| ① | `majority_vote` | [:428](src/memory/memory_store.py#L428)、[:446](src/memory/memory_store.py#L446) | 0 | 同 topic 下多数 agent 支持的 claim 胜出 |
| ② | `source_weight` | — | 0 | `evidence_type`：primary 1.0 / secondary 0.8 / inference 0.6 |
| ③ | `llm_judge` | — | 1 次调用 | 前两级分不出来才用 |

**为什么这个顺序**：**成本严格递增，可靠性严格递减**。能用确定性方法解的，绝不调用模型——**和压缩三级"能抽取就不生成"是同一条原则**。

**鲁棒性**：LLM 裁定失败会**自动回退到来源权重**，所以第三级是加成、不是必需。

### 4.4 生命周期 — 单次 run 内共享，不跨 run

**证据**：[orchestrator.py `run()`](src/orchestrator/orchestrator.py#L122) 里有 `self._memory_store.clear()`。

**含义**：
- ✅ **不会串味**——每次 run 开始清空
- ✅ 解决的是**"同一次研究里多个子 agent 对同一事实给出不同结论"**（这才是简历上"跨 Agent 结论打架"的意思）
- ❌ **不跨 run 累积**——没有实现"跨 run 召回"

**为什么刻意不做**：跨 run 就必须先解决**遗忘和污染**——旧结论会污染新任务，而且旧结论可能是错的。**这是有意的取舍，不是遗漏。** 被问到一定要说成取舍。

---

## 第 5 章｜对抗降噪 M5（Round 3 的底层）

### 5.1 Red Agent 五维 — 每个维度一次独立调用

**代码**：[src/adversarial/red_agent.py](src/adversarial/red_agent.py)

| 维度 | Prompt 行号 |
|---|---|
| ① 事实核查 `PROMPT_FACTUAL` | [:44-45](src/adversarial/red_agent.py#L44-L45) |
| ② 幻觉检测 `PROMPT_HALLUCINATION` | [:83-84](src/adversarial/red_agent.py#L83-L84) |
| ③ 逻辑一致性 `PROMPT_LOGICAL` | [:122-123](src/adversarial/red_agent.py#L122-L123) |
| ④ 来源可信度 `PROMPT_SOURCE_CREDIBILITY` | [:159-160](src/adversarial/red_agent.py#L159-L160) |
| ⑤ 覆盖完整度 `PROMPT_COVERAGE` | [:199-200](src/adversarial/red_agent.py#L199-L200) |
| 维度→Prompt 映射 | [:237](src/adversarial/red_agent.py#L237) |

**为什么独立调用**（[:8](src/adversarial/red_agent.py#L8) 的 docstring 明说）：
> 每个维度独立调用 LLM，避免单条 Prompt 过长导致模型注意力分散。

**两条设计原则**（代码注释）：
- [:8](src/adversarial/red_agent.py#L8)：独立调用避免注意力分散
- [:10](src/adversarial/red_agent.py#L10)：**对解析失败的维度返回保守分数（5.0）并记录原始输出，避免对抗循环崩溃**

### 5.2 加权公式与权重表

**出处**：[outputs/m5_verdict_metrics.json](outputs/m5_verdict_metrics.json) §3

```python
overall = Σ(维度分 × 权重)      # 0～10 分制，不是 1～5
```

| 维度 | 权重 |
|---|---|
| fact_check | **0.30** |
| hallucination | **0.25** |
| logical | **0.20** |
| source_credibility | **0.15** |
| coverage | **0.10** |
| **和** | **1.00** |

**两个鲁棒性处理**（都实测过）：
1. **缺失维度时重新归一化**——不是当 0 分。实测：只给三维都 8.0 → overall = **8.0**（不是 4.8）。**这一条很重要**：如果当 0 分，一次解析失败就会把总分打到地板上，误导 Blue 去"修"一个不存在的问题。
2. **越界 clamp**——输入 99 → **10.0**。空字典 → **0.0**。

**⚠️ 与提问者的假设不符**：很多面试官会问"怎么量化成 **1-5 分**"。**实际是 0–10 分。** 纠正这一点不是抬杠，是展示你真的看过代码。

### 5.3 Blue Agent 三动作

**代码**：[src/adversarial/blue_agent.py](src/adversarial/blue_agent.py)

| 动作 | 行号 |
|---|---|
| 就地修正 `in_place_fix` | [:282](src/adversarial/blue_agent.py#L282)、失败 [:288](src/adversarial/blue_agent.py#L288) |
| 补充检索 `supplementary_search` | [:337](src/adversarial/blue_agent.py#L337)、失败 [:343](src/adversarial/blue_agent.py#L343) |
| 整段移除 `removal` | [:367](src/adversarial/blue_agent.py#L367)、失败 [:373](src/adversarial/blue_agent.py#L373) |
| 自检发现新问题 | [:226](src/adversarial/blue_agent.py#L226) |
| 未知修复类型 | [:252](src/adversarial/blue_agent.py#L252) |
| 修复汇总日志 | [:390](src/adversarial/blue_agent.py#L390) |

**⚠️ 关键**：**动作不是 Blue 自己选的**——Red 在报 issue 时就带了 `fix_type`，Blue 按类型执行。所以"三动作怎么选"这个问题应该答**优先级公式**。

### 5.4 优先级公式 — 和一个方向反了的乘数

**出处**：[outputs/m5_verdict_metrics.json](outputs/m5_verdict_metrics.json) §5

```python
priority = severity_weight × dimension_weight × fix_difficulty   # 连乘，非相加
```

| 因子 | 取值 |
|---|---|
| `SEVERITY_WEIGHTS` | CRITICAL **10.0** / MAJOR **5.0** / MINOR **1.0** |
| `FIX_DIFFICULTY` | IN_PLACE **1.0** / REMOVAL **0.8** / SUPPLEMENTARY **0.6** |

**实测跨度**：最低 `MINOR/COVERAGE/SUPPLEMENTARY` = **0.06**，最高 `CRITICAL/FACTUAL/IN_PLACE` = **3.0**，**跨度 50 倍**。

**🔴 发现：`FIX_DIFFICULTY` 的方向是反的。**

`SUPPLEMENTARY`（补充检索）明明是**最难**的修复方式（要重新检索、要等），但乘数最小（**0.6**）→ **越难修的越往后排**。

**这不符合直觉**：难修的问题往往更严重。实测标记 `难度名与乘数方向相反 = True`。

**答法**："这是我自己审计出来的设计矛盾。**如果按'难度'理解，乘数应该是正相关**；但代码里是负相关，**效果上等于'先做省事的'**。这个可能是为了控制单轮成本，但**没有注释说明意图，所以我判断它是 bug 而不是设计**。"

### 5.5 循环刹车 — 收敛三条件 + 震荡检测

**代码**：[src/adversarial/loop.py](src/adversarial/loop.py)

```python
# loop.py:9 的 docstring —— ⚠️ 这是代码默认值，不是生效值
# 2. 收敛条件三选一：round >= 3 / overall >= 8.0 / Δscore < 0.3。
```

| 机制 | 行号 | 说明 |
|---|---|---|
| 收敛判定 | [:135](src/adversarial/loop.py#L135) → [:161-164](src/adversarial/loop.py#L161-L164) | `_check_convergence` |
| 震荡检测 | [:102-113](src/adversarial/loop.py#L102-L113) | 已修复 issue 重现即判震荡 |
| **函数默认值** | [:53-55](src/adversarial/loop.py#L53-L55) | `max_rounds=3` / `score_threshold=8.0` / `delta_threshold=0.3` |
| **配置注入** | [runner.py:233-235](src/core/runner.py#L233-L235) | `adversarial_cfg.get(..., 默认值)` |
| `resolved_issues` 集合 | [:8](src/adversarial/loop.py#L8) | 震荡检测的基础 |

**🔴 关键：docstring / 代码默认值 / 实际配置是三个不同的值。**

| 参数 | docstring（[:9](src/adversarial/loop.py#L9)） | 函数默认（[:53-55](src/adversarial/loop.py#L53-L55)） | **实际生效（`configs/default.yaml` 的 `adversarial` 段）** |
|---|---|---|---|
| 硬上限 | `round >= 3` | `max_rounds=3` | **`max_rounds: 10`** |
| 质量门槛 | `overall >= 8.0` | `score_threshold=8.0` | **`score_threshold: 9.5`** |
| 边际收益 | `Δscore < 0.3` | `delta_threshold=0.3` | **`delta_threshold: 0.2`** |

**生效值由 [runner.py:233-235](src/core/runner.py#L233-L235) 从配置读入并覆盖默认值。** 配置段的注释也说明了语义：`score_threshold` 是"达到此分即可提前终止对抗"，`delta_threshold` 是"相邻两轮评分差异低于此值视为收敛"。

**这个不一致本身就是一个好素材**：
> "**docstring 落后于配置。** `loop.py:9` 写的是 8.0/0.3/3，**但跑起来是 9.5/0.2/10**。**生效值要看配置。** 而且 `9.5` 这个门槛几乎不可能达到——**等于条件 ② 基本不生效**，这解释了为什么 5 次运行里有两次直接撞到 10 轮上限。"

**收敛三条件（生效值，任一满足即停）**：
1. `round >= 10` —— 硬上限
2. `overall >= 9.5` —— 质量够了（**门槛极高，实际难触发**）
3. `Δscore < 0.2` —— 边际收益归零（**量纲问题，实际无效**，见 5.6）

**震荡检测**：维护 `resolved_issues`。**已修复的 issue 在新轮次重新出现 → 判震荡 → 立即终止**，`stop_reason = f"oscillation_at_round_{round_idx}"`（[:111](src/adversarial/loop.py#L111)）。

### 5.6 🔴 最强发现之二：Δscore 阈值有形同虚设的量纲问题

**出处**：[outputs/m5_verdict_metrics.json](outputs/m5_verdict_metrics.json) §4

**Δscore 是五维欧氏距离，理论最大值 = √5 × 10 = 22.3607。**

| 量 | 值 |
|---|---|
| 理论最大 Δscore | **22.3607** |
| 实际配置阈值 | **0.2** |
| 阈值占理论最大 | **0.89%** |
| 阈值等价于每维变化 | **0.0894** |
| 函数默认阈值 | 0.3（等价每维 0.1342） |

**实测验证**：
- 每维 +0.1（共 5 维）→ Δ = 0.2236，**是否触发 0.2 阈值 = False**
- 每维 +0.2（共 5 维）→ Δ = 0.4472，**是否触发 0.2 阈值 = False**

**结论**：**每一维都涨 0.2 分，都触发不了收敛条件 ③。** 所以真正起作用的是硬上限 `round >= 10`——**条件 ③ 形同虚设，条件 ② 因为门槛设到 9.5 也几乎不生效**。

**这个发现的现实印证**：5 份真实报告的**对抗轮数是 2 / 2 / 10 / 10 / 5** —— **两次直接撞到 10 轮上限**。如果 Δscore 刹车有效，不该出现这种情况。

**答法**（这是全场最亮的素材）：
> "阈值用了绝对量纲，但 Δscore 是欧氏距离，量纲是 √Σ(Δd²)。**0.2 在 22.36 的量纲上等于 0.89%，根本没有区分度。** 正确做法是用**相对变化率**（Δ/理论上限）或者**归一化后**再设阈值。"

### 5.7 保守分 5.0 与它的代价

**代码**：[red_agent.py:10](src/adversarial/red_agent.py#L10) 注释 + [:83-101 / :145-154](src/adversarial/red_agent.py) 的 fallback 分支

**保守分 = 5.0**（0–10 制中位数，**既不拉低也不虚高**）。

**多级 JSON 兜底**：直接 parse → 剥 ```json 围栏 → 正则找第一个平衡花括号 → 失败则**该维度返回 5.0 并记录原始输出**。

**设计原则**：**对抗循环的可用性 > 单次评分的精度**。一轮里某维度解析失败，绝不能导致整个研究流程崩。

**⚠️ 代价（必须主动说）**：**分数会系统性向 5.0 回归。** 如果解析失败率高，五维分会被拉平到中位，**让对抗看起来"没找到问题"**——这是假阴性。**我没有统计过解析失败率**，这是设计的盲区。

### 5.8 运行时真相 — 5 份报告的 metadata

**出处**：5 份报告文件尾部的 metadata 块。

| 报告 | 置信度 | 搜索轮数 | 重规划次数 | 对抗轮数 |
|---|---|---|---|---|
| reports1 | 0.46 | 18 | **0** | 2 |
| reports2 | 0.47 | 35 | **0** | 2 |
| reports3 | 0.47 | 34 | **0** | **10** |
| reports4 | 0.46 | 32 | **0** | **10** |
| reports5 | 0.47 | 19 | **0** | 5 |

**三条可以直接用的结论**：

1. **对抗在每一次运行里都跑了**（2/2/10/10/5），**两次撞到 10 轮上限**。→ 它是活的。
2. **重规划次数全是 0** → **第二级降级从未在实战触发**。→ 主动坦白。
3. **置信度全是 0.46–0.47** → 有一次重要的翻案，见下。

### 5.9 ⚠️ 一条被运行时数据推翻的判断（要记牢）

**曾经写过的**：`orchestrator` 里有 `confidence >= 0.8` 就跳过对抗的门（[orchestrator.py:446-448](src/orchestrator/orchestrator.py#L446-L448)），所以"如果真跑消融，得先把 0.8 这个门处理掉，否则对照组的差异会被稀释"。

```python
# orchestrator.py:445-448
# 置信度足够高时跳过对抗
if report.confidence >= 0.8:
    print("[Adversarial] ✓ 报告置信度已达标 (≥0.8)，跳过对抗优化")
    return OrchestratorState.DONE
```

**这道门的日志是可查的**——第 447 行会打印 `报告置信度已达标 (≥0.8)，跳过对抗优化`。**如果它从未打印，就说明门从未触发。**

**实测推翻了它**：**5 次运行的置信度都是 0.46–0.47，离 0.8 差 0.33 → 这道门从未触发，对抗每次都跑了。**

**所以**：不需要"处理那个门"。**那个前置条件不成立。**

**这条的元教训（面试可以讲）**：
> "**静态审计会产生"理论风险"，运行时数据才是裁判。** 我之前推断那道门会稀释消融差异，后来回看 5 份报告的元数据才发现**它从未触发过**。**这件事让我在讲任何结论前都先去翻一遍实际产物。**"

---

## 第 6 章｜评测体系（Round 5 的底层）

### 6.1 ResearchBench — 35 题 / 11 领域，但分布不均

**出处**：[outputs/eval_layer_metrics.json](outputs/eval_layer_metrics.json) §7、§4

**题目数 = 35**（docstring 里写的是 20，**文档落后于代码**：[research_bench.py:8](evaluation/benchmarks/research_bench.py#L8)）。

**领域分布**：

| 领域 | 题数 | 领域 | 题数 |
|---|---|---|---|
| 科技 | **8** | 汽车 | 2 |
| 医疗 | 6 | 游戏 | 2 |
| 金融 | 6 | 交叉 | 2 |
| 教育 | 2 | 传媒 | **1** |
| 法律 | 2 | | |
| 能源 | 2 | **合计** | **35** |
| 消费 | 2 | **域数** | **11** |

**每题结构**（§7）：`{domain, expected_topics, ground_truth, id, query}`

**🔴 复数发现（§4）**：`--domain` 参数的 `choices` 是**英文** `["tech","med","fin"]`（[run_baseline.py:207-212](evaluation/run_baseline.py#L207-L212)），但数据里的 `domain` 是**中文**（科技/医疗/金融）→ **CLI choices 命中题数：tech=0, med=0, fin=0** → **传任何合法值都选 0 道题，下游全部 0.0。**

**答法**："能做 smoke test，做不了统计推断。要检出 d≈0.5、power 0.8，单组需要几百题量级。"

### 6.2 规则指标 — 实现与两个真实缺陷

**代码**：[evaluation/metrics/rule_based.py](evaluation/metrics/rule_based.py)

| 指标 | 行号 | 实现 |
|---|---|---|
| `fact_accuracy` | [:26](evaluation/metrics/rule_based.py#L26) | 逐 key fact 子串匹配 |
| `semantic_fact_accuracy` | [:48](evaluation/metrics/rule_based.py#L48) | 语义版 |
| `hallucination_rate` | [:111](evaluation/metrics/rule_based.py#L111) | 无引用 + 可疑特征 |
| `citation_coverage` | [:152](evaluation/metrics/rule_based.py#L152) | `citation_patterns` [:168](evaluation/metrics/rule_based.py#L168) |
| `logical_consistency` | [:190](evaluation/metrics/rule_based.py#L190) | ⚠️ 见下 |
| `comprehensiveness` | [:235](evaluation/metrics/rule_based.py#L235) | expected_topics 覆盖 |
| `composite_score` | [:254](evaluation/metrics/rule_based.py#L254) | 权重表 [:269-271](evaluation/metrics/rule_based.py#L269-L271) |
| `efficiency_score` | [:291](evaluation/metrics/rule_based.py#L291) | — |

**composite 权重**：factual_accuracy 0.25 / logical_consistency 0.20 / citation_coverage 0.20 / bias 0.20 / comprehensiveness 0.15

**🔴 缺陷 ① — 幻觉率的豁免正则漏了全角 `】`**（[eval_layer_metrics.json](outputs/eval_layer_metrics.json) §2b）

```
豁免正则       = [\[【（(].*?[\]）)]
开括号类       = [\[【（(]      ← 含全角【
闭括号类       = [\]）)]        ← 缺全角】  ❌
```

**后果**：规范引用格式 `【来源: ...】` **无法构成豁免匹配** → **越规范的引用越被判为幻觉**。

**实测探针**：裸数字无括号 `"2025 年市场规模达到 100 亿美元。"` → hallucination_rate = **1.0**。

**🔴 缺陷 ② — `logical_consistency` 在两个尺度上都失效**

`contradiction_pairs` 含 `("是","不是")`、`("可以","不可以")`、`("会","不会")`，判定是 `if a in sentence and b in sentence` —— **子串包含**。

**后果**：**含否定形式的句子必然同时命中 a 和 b**（因为"不是"包含"是"）。所以：

| 尺度 | 结果 |
|---|---|
| 单句 | **必然误报**——`"这不是一个好方案。"` → logical_consistency = **0.0** |
| 整篇 | 因为平均，**全是满分**（实测 reports1 = 1.0） |

**结论**：**这个指标目前不具备鉴别力。** 单句必错、整篇无区分度。

### 6.3 幻觉率的两个口径（简历数字的出处）

**出处**：[outputs/report_metrics.json](outputs/report_metrics.json) → `rows[].hallucination_rate`；[eval_layer_metrics.json](outputs/eval_layer_metrics.json) §2b

| 口径 | 算法 | 值 | 用途 |
|---|---|---|---|
| **逐份比率的算术平均** | `mean(1.45%, 2.58%, 11.71%, 0%, 0.97%)` | **3.34%** | ← **简历上就是这个** |
| **池化** | 67 可疑句 ÷ 1,581 句 | **4.24%** | 备答 |

**分母是句子数**：5 份报告共 **1,581 句**，可疑 **67 句**。

**构成**（§2b）：可疑句里 **95.52%** 来自"带单位数字"这一条正则（64/67），绝对化表述 3，模糊引用开头 0。

**所以它测的是「无引用可疑表述密度」，不是事实错误率。** 口播必须带这句。

**⚠️ 重要澄清（别自曝无法解释的数字）**：真实 5 份报告主要用 `（Result N）` 格式（[report_metrics.json](outputs/report_metrics.json) 的 `citation_markers`：`（Result N）` = **15/15/0/0/0**），所以**这 5 份的 3.34% 没被豁免正则缺陷污染**。**这一点要主动强调**，否则会被认为在自曝无法解释的数字。

### 6.4 Judge — 与生成模型分离（这题是真加分）

**代码**：[src/core/judge.py:24-27](src/core/judge.py#L24-L27)

```python
class LLMJudge:
    def __init__(self, backend: str = "mimo") -> None:
```

**配置分发**：[configs/default.yaml:99-107](configs/default.yaml#L99-L107)

```yaml
backend_mapping:
  solver:     "deepseek"   # 主研究 Agent
  planner:    "deepseek"
  summarizer: "deepseek"
  judge:      "mimo"       # ← 评分器
  red_agent:  "mimo"       # ← 攻击
  blue_agent: "mimo"       # ← 修复
  compressor: "mimo"
```

**精确结论（分两层，这是这题的标准答案）**：

| 关系 | 是否分离 | 说明 |
|---|---|---|
| **Judge vs 生成（solver）** | ✅ **分离** | MiMo vs DeepSeek，切掉最严重的 self-preference |
| **Red vs 被攻击的 Solver** | ✅ **分离** | MiMo vs DeepSeek |
| **Red vs Blue** | ❌ **同模型** | 都是 MiMo |
| **Judge vs Red/Blue** | ❌ **同模型** | 都是 MiMo |

**实现细节**：`_get_policy()` 懒加载（[:35-39](src/core/judge.py#L35-L39)）；有 `score_single`（[:45](src/core/judge.py#L45)）和 `compare_two`（[:130](src/core/judge.py#L130)，4 维 Pairwise）。⚠️ 报错信息硬编码了 MiMo（[:125](src/core/judge.py#L125) `"无法解析 MiMo Judge 输出"`）——**换后端时错误信息会误导**。

**没做的**：与人工评分的对齐度（无 Spearman、无标注样本）、Pairwise 位置偏差控制。

### 6.5 统计检验 — 工具就绪，但无数据

**代码**：[evaluation/metrics/stats.py](evaluation/metrics/stats.py)

| 函数 | 行号 |
|---|---|
| `bootstrap_ci_paired` | [:20](evaluation/metrics/stats.py#L20) |
| `bootstrap_ci_two_sample` | [:65](evaluation/metrics/stats.py#L65) |
| `n_bootstrap` 默认 | **10000**（[:22](evaluation/metrics/stats.py#L22), [:68](evaluation/metrics/stats.py#L68)） |

**⚠️ 必须说清**：**工具实现了，但没有任何跑出来的 CI 数值**——因为消融对照实验没跑成。**这是"工具就绪 ≠ 有证据"的典型。**

**方法准备（加分）**：35 题上做 5 个指标同时检验，**必须做多重比较校正**（Holm / BH），否则至少一个假阳性的概率 = 1 − 0.95⁵ ≈ **23%**。**没实现，但知道必需。**

### 6.6 消融接线审计 —「4 个臂只有 1 个生效」

**出处**：[outputs/eval_layer_metrics.json](outputs/eval_layer_metrics.json) §5

定义：[src/core/ablation.py:34-40](src/core/ablation.py#L34-L40) `DEFAULT_MODULE_ABLATIONS`，5 个 arm：`full` / `no_adversarial` / `no_compressor` / `no_memory` / `no_evolution`。

| 臂 | override 键 | 读点 | 消费点 | 状态 |
|---|---|---|---|---|
| `no_adversarial` | `adversarial.enabled` | [runner.py:312](src/core/runner.py#L312) | [orchestrator.py:429](src/orchestrator/orchestrator.py#L429) | ✅ **effective** |
| `no_compressor` | `compressor.enable_multilevel` | **0** | **0** | ❌ **unwired** |
| `no_memory` | `memory.enabled` | **0** | **0** | ❌ **unwired** |
| `no_evolution` | `evolution.enabled` | [runner.py:264](src/core/runner.py#L264), [:313](src/core/runner.py#L313) | **0** | ❌ **stub（只有 logger.info）** |

**结论**：**4 个模块消融臂：1 个真正生效，1 个空壳，2 个未接线。** 空壳臂与未接线臂的产出**与 `full` 相同**（同 seed 下逐字节），跑出来的"差异"只会是 LLM 采样噪声。

**这就是"消融为什么没跑"的真正原因**——**不是没时间，是跑了也没意义。**

### 6.7 `average_composite_score` 字段名在撒谎

**代码**：[ablation.py:114](src/core/ablation.py#L114) `scores.append(1.0)  # 占位`、[:125](src/core/ablation.py#L125) `scores.append(0.0)`、[:131](src/core/ablation.py#L131)

**含义**：它统计的是**成功率**（成功 append 1.0、失败 append 0.0），**不是质量分**。字段名叫 `average_composite_score` 但**它不是 composite score**。

**答法**："字段名和语义不符。**它算的是任务成功率，不是评分。** 用这个名字会让人以为消融跑出了质量差异，实际只能看出'跑成没跑成'。"

### 6.8 `composite_score` 三个口径三个天花板

**出处**：[outputs/eval_layer_metrics.json](outputs/eval_layer_metrics.json) §1

| 口径 | 调用方 | 用键数 | 全满分时 composite |
|---|---|---|---|
| A | `run_ablation.py` | 5 | **1.00** |
| B | `research_bench.py` | 6（含 `factual_accuracy_sem`/`_str`，但缺 `factual_accuracy`） | **0.75** |
| C | `rule_based` 原生产出 | 4（产不出 `bias`） | **0.80** |

**含义**：同一个 `composite_score`，**三个调用方三个天花板**——因为有三处权重键**静默清零**：
- 口径 B：`factual_accuracy` 权重 **0.25 静默清零**
- 口径 C：`bias` 权重 **0.20 静默清零**

**答法**："**在这个口径上跑消融，比出来的差异可能是口径差异而不是模型差异。** 这也是我判断'消融结果不可解释'的第三条理由。"

### 6.9 题库脏数据

**出处**：§3（[eval_layer_metrics.json](outputs/eval_layer_metrics.json)）

`fin_005` 的 `ground_truth` 键值带**前导空格**（如 `" Lemonade"`、`" telematics"`），**共 2 条脏数据**。

**后果**：`fact_accuracy` 用 `key_fact.lower() in report_lower` **逐键子串匹配**，键名带前导空格 → **永远匹配不到 → 该题 factual_accuracy 恒为 0**。

**答法**：这解释了为什么有些题的 fact_accuracy 是 0——**不一定是报告错，可能是题库脏**。**主动说这条会让你显得真的看过数据。**

### 6.10 HotpotQA — 是"适配器"，不是"变体"

**代码**：[evaluation/benchmarks/hotpotqa.py](evaluation/benchmarks/hotpotqa.py)

| 功能 | 行号 |
|---|---|
| 内置兜底数据（**5 题**） | [:24-60](evaluation/benchmarks/hotpotqa.py#L24-L60) |
| `to_research_format` | [:108](evaluation/benchmarks/hotpotqa.py#L108) |
| `exact_match` | [:170](evaluation/benchmarks/hotpotqa.py#L170) |
| `f1_score` | [:175](evaluation/benchmarks/hotpotqa.py#L175) |
| `pass_at_k` | [:196](evaluation/benchmarks/hotpotqa.py#L196) |
| `gold_entity_coverage` | [:217](evaluation/benchmarks/hotpotqa.py#L217) |
| `semantic_gold_coverage`（阈值 0.60） | [:239](evaluation/benchmarks/hotpotqa.py#L239) |
| 默认指标 `["em","f1","pass@1"]` | [:304](evaluation/benchmarks/hotpotqa.py#L304), [:310](evaluation/benchmarks/hotpotqa.py#L310) |

**⚠️ 两个薄弱点**：
1. **兜底数据只有 5 题，且是单跳事实题**（"曹雪芹哪个朝代"、"H100 几纳米"），**不是 multi-hop**。
2. **真集需要 `datasets` 包 + 联网**（`HotpotQA_真实加载需联网 = True`），本机都没有 → **实际只能跑兜底 5 题**。

**答法**：说"**评测适配器**"（把 multi-hop QA 转成 DeepResearch 输入格式并算 EM/F1/pass@k）比说"变体"稳。被问"跑了多少分"要能答"**只跑通格式，没跑真集**"。

### 6.11 实现清点 — 两个"仅自身入口引用"的类

**出处**：§6

| 类 | 定义处 | 外部引用 | 判定 |
|---|---|---|---|
| `BaselineEvaluator` | [evaluation/run_baseline.py](evaluation/run_baseline.py) | **0** | 仅自身入口引用 |
| `ResearchMetrics` | [evaluation/metrics.py](evaluation/metrics.py) | **0** | 仅自身入口引用 |
| `HotpotQABenchmark` | [hotpotqa.py](evaluation/benchmarks/hotpotqa.py) | 1 (`scripts/run_eval.py`) | 接在链路上 |
| `Judge` | [judge.py](src/evolution/judge.py) | **13** | 接在链路上 |
| `RuleBasedMetrics` | [rule_based.py](evaluation/metrics/rule_based.py) | 5 | 接在链路上 |

**答法**："`BaselineEvaluator` 和 `ResearchMetrics` 没有接进主调用链，**只有 CLI 入口引用**。**仅自身入口引用 ≠ 一定是废代码**（可能是独立的 CLI 工具），**但它不参与主链路，被问到要说清。**"

### 6.12 假数据生成器 — 绝不能演示的东西

**出处**：[eval_layer_metrics.json](outputs/eval_layer_metrics.json) §8

**事实**：[evaluation/analyze_ablation.py:245-254](evaluation/analyze_ablation.py#L245-L254) 里有 **6 个硬编码演示数值**，代码里有"演示数据"字样。当 `main()` **未指定输入文件**时，会用这些数值渲染 **dpi=300 的对比图**：

```python
{"adv_0": 0.62, "adv_1": 0.71, "adv_2": 0.78, "adv_3": 0.80}   # 对抗随轮数上升
{"evo_off": 0.68, "evo_on": 0.76}                              # 演化开关对比
```

**图看起来像真实实验结果，但一个数都没测过。**

**🔴 绝对不要演示这个脚本的空输入模式。** 如果面试官要求"给我看消融图"，正确答法是："**那个脚本空输入时会渲染硬编码的演示数据，不是真结果。我可以给你看真实的产出侧度量（`outputs/*.json`），但消融图我不能给你——因为消融数据不存在。**"

**⚠️ 主动坦白这条的收益极高**：它证明你有代码审计能力，而且诚实。

---

## 第 7 章｜数字总账（每个数字的出处与档位）

**档位说明**：**A** = 仓库有实测 JSON 产物 · **B** = 代码/配置静态可查 · **C** = 端到端实测产出 · **D** = 仓库无源（需改写或备答法）

| 简历说法 | 值 | 出处 | 档位 |
|---|---|---|---|
| 9 状态状态机 | 9 | [schemas.py:30-46](src/orchestrator/schemas.py#L30-L46)（枚举 9 成员 + 9 handler） | **B** |
| Kahn 拓扑分层 | — | [dag.py:124-148](src/planner/dag.py#L124-L148) | **B** |
| 并发度 5 | 5 | [yaml:112](configs/default.yaml#L112) → [runner.py:308](src/core/runner.py#L308) → [orch:238](src/orchestrator/orchestrator.py#L238) | **B** |
| 单任务超时 | 30s | [orch:262-271](src/orchestrator/orchestrator.py#L262-L271) | **B** |
| 失败率超 50% | 0.5 | [orch:533](src/orchestrator/orchestrator.py#L533) | **B** |
| 重规划上限 3 轮 | 3 | [yaml:116](configs/default.yaml#L116) | **B** |
| 全局超时 | 600s | [yaml:114](configs/default.yaml#L114) | **B** |
| 保留高置信结果 | ≥0.6 | `_do_replanning` docstring | **B** |
| 五维评分 | 5 | [red_agent.py](src/adversarial/red_agent.py)（5 个 PROMPT） | **B** |
| 五维权重 | .30/.25/.20/.15/.10 | [m5_verdict_metrics.json](outputs/m5_verdict_metrics.json) | **A** |
| 三类修复动作 | 3 | [blue_agent.py](src/adversarial/blue_agent.py) | **B** |
| 收敛三条件（**生效值**） | **10 / 9.5 / 0.2** | `configs/default.yaml` 的 `adversarial` 段 → [runner.py:233-235](src/core/runner.py#L233-L235) | **B** |
| 收敛三条件（docstring 值） | 3 / 8.0 / 0.3 | [loop.py:9](src/adversarial/loop.py#L9)、[:53-55](src/adversarial/loop.py#L53-L55) | **B** |
| 解析失败保守分 | 5.0 | [red_agent.py:10](src/adversarial/red_agent.py#L10) | **B** |
| 三级压缩阈值 | .60/.80/.95 | [compressor.py:31-33](src/compressor/compressor.py#L31-L33) | **B** |
| 上下文预算 | 16000−2048=13952 | [compressor.py:36-37,68](src/compressor/compressor.py#L36-L37) | **B** |
| 记忆去重/矛盾阈值 | .92 / .65–.92 | [memory_store.py:31-33](src/memory/memory_store.py#L31-L33) | **B** |
| 来源权重 | 1.0/.8/.6 | [memory_store.py:36](src/memory/memory_store.py#L36) | **B** |
| ResearchBench | 35 题 / 11 领域 | [eval_layer_metrics.json](outputs/eval_layer_metrics.json) §7/§4 | **A** |
| HotpotQA 兜底题 | 5 | [hotpotqa.py:24-60](evaluation/benchmarks/hotpotqa.py#L24-L60) | **B** |
| Bootstrap 重采样 | 10000 | [stats.py:22,68](evaluation/metrics/stats.py#L22) | **B** |
| 五大模块行数 | **14,146 / 63 文件 / 410 函数** | [extra_metrics.json](outputs/extra_metrics.json) | **A** |
| 注解覆盖率 | 96.9% / 94.4% / 79.5% | 同上 | **A** |
| 7 个研究工具 | 7 | `src/tools/` 的 7 个工具类 | **B** |
| 4 类后端热切换 | 4 | [model_router.py:96](src/models/model_router.py#L96) + [yaml:99-107](configs/default.yaml#L99-L107) | **B** |
| 5 份报告 | 5 | `outputs/reports1..5` | **C** |
| 平均 2.1 万字 | **20,994.2 字符** | [extra_metrics.json](outputs/extra_metrics.json) | **A** |
| 规则口径幻觉率 | **3.34%**（池化 4.24%） | [report_metrics.json](outputs/report_metrics.json) | **A** |
| 跨报告相似度 | 均值 **0.272** / 最大 0.375 | [extra_metrics.json](outputs/extra_metrics.json) | **A** |
| 5 项指标区分度校验 | — | **仓库无同名产物**（内容在 [eval_layer_metrics.json](outputs/eval_layer_metrics.json) §1–§2） | **D** |

**D 档的唯一一条**：「5 项指标区分度校验」。**"5 项"就是 composite 权重表里那 5 个键**，而 `eval_layer_metrics.json` 里**确实做了这 5 项的鉴别力检查**（`logical_consistency` 无鉴别力、`factual_accuracy` 静默清零、`bias` 产不出键）。**所以这件事你做了，只是叫法不同。** 建议改称「5 项指标口径审计」——**一个词的差别，且产物就在那。**

### 7.1 效果类数字必须成对报（红线）

**压缩比必须和保留率一起报**：L2 压缩比 0.011 听起来很好，但**保留率 0.000**（[compressor_metrics.json](outputs/compressor_metrics.json)）。**只报压缩比 = 误导。**

**同理**：幻觉率必须带口径（逐份平均 3.34% vs 池化 4.24%）。

---

## 第 8 章｜诚实边界总表（能报 / 不能报）

### 8.1 ✅ 能报（有 JSON 产物或代码可查）

| 能报的 | 证据 |
|---|---|
| 规则层真跑了 | 幻觉率 3.34%、平均 20,994 字符、跨报告相似度 0.272 |
| 产出侧画像 | 5 份报告、1,581 句、段落近重复率 0.0 |
| 代码规模 | 14,146 行 / 63 文件 / 410 函数 |
| 机制实现 | 9 状态机、三级降级、三级压缩、五维对抗、三种消解 |
| 运行时证据 | 搜索 18–35 轮、对抗 2/2/10/10/5 轮 |
| **所有缺陷** | **每个缺陷都有产物或代码证据，全都能报** |

### 8.2 ❌ 不能报（没有数据）

| 不能报的 | 原因 |
|---|---|
| **消融对照结果**（去掉对抗掉多少分） | 4 个臂里 3 个没接线；`composite_score` 三口径 |
| **Bootstrap CI 具体数值** | 工具就绪，无输入数据 |
| **Cohen's d 具体数值** | 同上 |
| **Judge 与人工评分的相关性** | 没做过 |
| **成本金额** | 无 token 落盘 |
| **Red-Blue 的收益（幻觉率 3.34%→?）** | 没有对照组 |

### 8.3 ⚠️ 三条最容易被追穿的（提前准备）

1. **"去掉对抗掉多少分？"** → 没跑成对照。**给三条理由 + 一个补法**（见面试官版 Q3.7）。
2. **"CI 是多少？"** → 没有数值。**说清"工具就绪 ≠ 有数据"**。
3. **"Judge 和人类打分的相关性？"** → 没做过。**直说，并说明这是最大缺陷。**

---

## 第 9 章｜降级话术库（不懂/没做过怎么答）

**总原则：三段式 = 承认边界 → 说清为什么 → 给补救方案。**

> ⛔ **绝对不说**："这个我做过" / 编一个数 / 含糊过去。

### 9.1 万能三段式模板

> **① 边界**："这个我没做 / 这个数我给不了。"
> **② 原因**："因为 [具体的技术原因，不是'没时间']。"
> **③ 补救**："要做的话，正确路径是 [具体方案]，代价是 [成本]。"

**为什么有效**：第 ② 步把"缺失"变成"判断"，第 ③ 步把"没做"变成"知道怎么做"。**面试官考的是判断力，不是完成度。**

### 9.2 逐题降级话术

| 被问 | 降级答法 |
|---|---|
| 消融收益 | "没跑成，因为 4 个臂里 3 个没接线、composite 有三个口径。**缺的是一个 arm + 一轮跑，不是缺能力。** 补法是 DAG 压单节点 + `max_concurrent=1` + 其余全关，3 题就够出方向性结论。" |
| CI 数值 | "工具实现了（10,000 次重采样），但**至今没有输入**——消融没跑成。**工具就绪不等于有证据。**" |
| Judge 对齐度 | "**没做，这是我知道的最大缺陷。** 如果重来我会先做这一步——**没有对齐度的 Judge 等于用没校准的尺子量所有模块的收益。**" |
| 人工标注 | "没做过人工标注，所以规则口径的幻觉率我只敢叫它'无引用可疑表述密度'，不敢叫事实错误率。" |
| 成本 | "没做 token 落盘，给不了准数。能估的量级是单份数元到十几元。**修法很便宜**：policy 层统计 usage 落盘。" |
| Prompt injection | "**会被打穿，三层防护一行没写。** 正确做法是来源隔离 + 工具参数白名单 + 注入模式检测。" |
| 测试 | "**几乎没有 pytest 用例**，靠端到端跑批 + JSON 度量验证。这验证不了回归。" |
| 为什么不用 LangGraph | 三个不满足点 + "**如果上生产我会先用 LangGraph 打原型**，只在降级和预算两处定制。" |
| 标定过程 | "**经验值，没标定。** 该做的是固定题目跑 1/3/5/10/20 五档看交叉点。" |
| 震荡实例 | "**给不了真实案例**——震荡检测只在 `stop_reason` 记字符串，没落盘 issue 内容。我能讲机制，讲不了实例。" |
| Red/Blue 同模型 | 承认；**但强调攻击方与被攻击方（Solver）已分离**。 |
| 断点续跑 | 不支持。"最小改动是把状态快照 + 已完成结果落盘，重启恢复 `DISPATCHING`。" |

### 9.3 反问式转移（把不会的引到会的）

**当问题落在你不会的地方，用一次反问把话题引到你有证据的地方**：

> "这个问题我是这么理解的：[给一个框架]。**您更关心的是 [A 机制] 还是 [B 效果]？** —— 如果是机制我可以讲得很细；**如果是效果，我手上没有对照数据，但我有产出侧的实测画像。**"

**注意**：反问必须**真的给出一个框架**，不能是空转。否则会显得在躲。

---

## 第 10 章｜手撕题完整参考

### C1 Kahn 分层拓扑

```python
from collections import defaultdict, deque

def kahn_layers(nodes, edges):
    """return (layers, cycle_nodes).  layers=[] 表示有环"""
    indeg = {n: 0 for n in nodes}
    adj = defaultdict(list)
    for u, v in edges:
        if u == v:
            raise ValueError(f"自环不允许: {u}")   # ← 早失败
        adj[u].append(v)
        indeg[v] = indeg.get(v, 0) + 1

    # 层内排序保证确定性
    cur = sorted([n for n in nodes if indeg[n] == 0])
    layers, seen = [], 0
    while cur:
        layers.append(cur)
        seen += len(cur)
        nxt = []
        for u in cur:
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    nxt.append(v)
        cur = sorted(nxt)

    if seen < len(nodes):                      # ← 环检测：剩余节点即环上节点
        return [], sorted(n for n in nodes if indeg[n] > 0)
    return layers, []
```

**必说**：① 自环早拒绝 ② `seen < len` 判环，**剩余 indeg>0 的节点就是环上节点** ③ `sorted()` 保证同 seed 可复现 ④ 复杂度 O(V+E)。

### C2 asyncio 限流 + 超时 + 部分失败

```python
async def run_layer(tasks, limit, timeout):
    sem = asyncio.Semaphore(limit)

    async def guarded(t):
        async with sem:                        # ← 限在途数，不限速率
            try:
                return await asyncio.wait_for(t.run(), timeout=timeout)
            except asyncio.TimeoutError:
                return TaskResult(status="TIMEOUT", output=None)
            except asyncio.CancelledError:
                raise                          # ← 不能吞！否则静默丢任务
            except Exception as e:
                return TaskResult(status="FAILED", output=str(e))

    results = await asyncio.gather(*(guarded(t) for t in tasks),
                                   return_exceptions=True)   # ← 收部分结果
    ok = [r for r in results if isinstance(r, TaskResult) and r.status == "SUCCESS"]
    return ok
```

**必说**：① Semaphore 限在途不限速率，要限速得令牌桶 ② **`CancelledError` 必须 re-raise** ③ `return_exceptions=True` 是"不阻塞整层"的实现。

### C3 Bootstrap 95% CI + Cohen's d（配对样本）

```python
import numpy as np

def bootstrap_paired(diffs, n_boot=10000, alpha=0.05, seed=42):
    rng = np.random.default_rng(seed)
    n = len(diffs)
    boots = np.array([rng.choice(diffs, size=n, replace=True).mean()
                      for _ in range(n_boot)])          # ← 有放回，且重采样的是"配对差异"
    lo, hi = np.percentile(boots, [100*alpha/2, 100*(1-alpha/2)])
    return float(lo), float(hi)

def cohens_d_paired(diffs):
    return float(np.mean(diffs) / np.std(diffs, ddof=1))
```

**必说**：① **配对样本要重采样"差异"，不是两组各自重采样** ② `ddof=1` 是样本标准差 ③ **5 个指标一起检验必须做 Holm/BH 校正**，否则假阳性率 23%。

### C4 TextRank 关键句抽取

```python
def textrank(sents, sim_fn, damping=0.85, iters=30, tol=1e-4, top_ratio=0.3):
    n = len(sents)
    W = np.array([[max(sim_fn(a, b), 0) if i != j else 0
                   for j, b in enumerate(sents)] for i, a in enumerate(sents)])
    W[W < 0.1] = 0                                  # ← 边阈值，去噪
    rs = W.sum(axis=1, keepdims=True); rs[rs == 0] = 1
    M = W / rs                                      # 列随机归一化
    s = np.ones(n) / n
    for _ in range(iters):
        ns = (1 - damping) / n + damping * M.T.dot(s)
        if np.abs(ns - s).sum() < tol:
            s = ns; break
        s = ns
    k = max(1, int(n * top_ratio))
    idx = sorted(np.argsort(s)[::-1][:k])           # ← 选出后按原序重排
    return [sents[i] for i in idx]
```

**必说**：① 阻尼 0.85 / 30 次 / 1e-4 / 边阈值 0.1 ② **选出 top-k 必须按原序重排**，否则句子乱序不可读 ③ 复杂度 O(n²) 建图 + O(n²·iters) 迭代，**n 大时要 `to_thread`**。

### C5 状态转移校验

```python
ALLOWED = {
    "idle": {"planning"},
    "planning": {"dispatching", "failed"},
    "dispatching": {"collecting"},
    "collecting": {"synthesizing", "replanning", "failed"},
    "replanning": {"dispatching", "synthesizing", "failed"},
    "synthesizing": {"adversarial", "done"},
    "adversarial": {"done", "failed"},
    "done": set(),          # ← 终态无出边
    "failed": set(),
}

def transition(cur, nxt):
    if nxt not in ALLOWED[cur]:
        raise ValueError(f"非法转移 {cur} → {nxt}")
    return nxt
```

**必说**：① **终态必须有空出边集**，否则主循环 `while state not in (DONE, FAILED)` 不会退出 ② 用白名单而不是"检查状态名是否存在"——**能到但非法的转移才是 bug 来源**。

### C6 矛盾检测 + 投票消解

```python
DEDUP, CONFLICT_LOW, CONFLICT_HIGH = 0.92, 0.65, 0.92

def classify(sim):
    if sim > DEDUP:                      return "duplicate"     # 合并，留 confidence 高者
    if CONFLICT_LOW < sim <= CONFLICT_HIGH: return "maybe_conflict"  # 再查语义对立
    return "unrelated"

def resolve(claims):                     # claims: [(text, votes, evidence_type)]
    if len({c[1] for c in claims}) > 1:
        return max(claims, key=lambda c: c[1])[0]        # ① 多数投票
    W = {"primary": 1.0, "secondary": 0.8, "inference": 0.6}
    if len({c[2] for c in claims}) > 1:
        return max(claims, key=lambda c: W[c[2]])[0]     # ② 来源权重
    return llm_judge(claims)                             # ③ LLM 裁定（失败回退 ②）
```

**必说**：① **区间是开闭要定死**（`>0.92` 去重、`(0.65, 0.92]` 查矛盾） ② 三级**成本递增**，能用确定性解的绝不调模型 ③ LLM 判定失败**回退到来源权重**，所以第三级是加成不是必需。

---

## 第 11 章｜红线（绝不能说的）

| ⛔ 不能说 | 为什么 |
|---|---|
| **消融前后具体分值**（"去掉对抗掉 12%"） | 数据不存在。会被追到 CI、样本量、口径，**必崩** |
| **Bootstrap CI 具体数值** | 没有输入数据 |
| **Cohen's d 具体数值** | 同上 |
| **Judge 与人工的 Spearman 相关系数** | 没做过 |
| **成本金额** | 无 token 落盘 |
| **演示 `analyze_ablation.py` 的空输入图** | 那是**硬编码的演示数据**（6 个数值），一个都没测过 |
| **"报告是 6 月之后跑的"** | 文件名写着 `20260509`，**一眼矛盾，反而像在编** |
| **"85% → 95%"这类提升数字** | 仓库里不存在这个数据，只有标注为"无出处"的引用 |
| **声称消融跑过** | 4 个臂里 3 个没接线，跑了也是噪声 |
| **含糊过去某个不会的点** | 面试官会继续追。**三段式承认比含糊安全十倍** |

---

## 附｜一页速查

**三条最亮的素材（一定要找机会主动说）**：
1. **压缩闸门 12.3% vs L1 阈值 60%，差 4.9 倍** → 三级压缩从未触发，日志在撒谎
2. **Δscore 阈值 0.2 占理论最大 22.36 的 0.89%** → 收敛条件 ③ 形同虚设（实证：对抗轮数 2/2/10/10/5，两次撞上限）
3. **消融 4 臂只有 1 个生效** → 这才是"消融没跑"的真正原因

**三条最需要准备的诚实答**：
1. 消融收益 → 三条理由 + 补法
2. CI 数值 → "工具就绪 ≠ 有证据"
3. Judge 对齐度 → "最大缺陷，重来会先做"

**一条元教训（可以当收尾）**：
> **静态审计会产生"理论风险"，运行时数据才是裁判。** 我推断过 `confidence >= 0.8` 那道门会稀释消融差异，后来翻 5 份报告的元数据才发现**它从未触发过**（置信度全是 0.46–0.47）。**这件事让我在讲任何结论前都先去翻一遍实际产物。**
