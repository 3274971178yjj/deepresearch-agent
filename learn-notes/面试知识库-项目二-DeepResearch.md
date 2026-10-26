# 面试知识库 · 项目二 DeepResearch Agent

> 用途：喂给豆包 / 面试前自检 / 被追问时查。
> 原则：**只写代码里能指到行号的事实**。凡是"简历说了但代码不支持"的，集中在 §2.5，必须主动说破，不要等面试官挖。
> 行号链接从本文件所在目录（`learn-notes/`）起算，形如 `../src/...`。

---

## 项目二 · 简历原文（v5 投放版）

```
项目经历    DeepResearch Agent:面向复杂深度研究任务的多智能体协作系统    2026年6月-2026年9月
· 项目介绍:面向深度研究任务中信息过载与幻觉漂移的问题,基于asyncio自研多Agent编排引擎,搭建规划→并发执行→对抗降噪→报告合成全链路流水线,并配套可复现评测体系。
· 编排引擎:针对长链路单点故障与失败归因问题,基于asyncio+Semaphore自研:9状态状态机管理任务生命周期,Kahn算法对子任务DAG拓扑分层并发(并发度5);设计三级降级——单任务超时标记继续不阻塞整层、失败率超50%触发增量重规划(保留高置信结果,上限3轮)、全局超时强制以已有结果合成。
· 对抗降噪:针对长文幻觉漂移与来源不可信,设计Red-Blue对抗循环:Red Agent从事实核查/幻觉检测/逻辑一致性/来源可信度/覆盖完整度五维评分,Blue Agent执行就地修正/补充检索/整段移除;以收敛与震荡双重刹车终止,输出经多级JSON兜底、失败降级为保守分,保证循环不中断。
· 上下文治理:针对上下文膨胀与跨Agent结论打架,按预算占用率分级触发三级压缩(向量粗筛→TextRank关键句抽取→LLM摘要,超限由滑动窗口兜底),前两级为抽取式;以SQLite+NumPy向量检索构建共享记忆,写入前做语义去重与矛盾检测,冲突由多数投票/来源权重/LLM裁定消解。
· 评测体系:针对"提升是真实还是波动"的问题,自建ResearchBench(11领域/35题)与HotpotQA变体;三层指标由规则指标(事实准确率/幻觉率/引用覆盖率/逻辑一致性/完备性)、LLM-as-Judge评分与统计检验(Bootstrap 95%CI、Cohen's d)构成,配一键消融脚本。
· 项目成果:完成编排/压缩/记忆/对抗/评测五大模块约1.4万行实现,集成7个研究工具与4类LLM后端(DeepSeek/MiMo/vLLM/OpenAI)热切换;已产出5份实际研究报告(平均2.1万字),规则口径幻觉率3.34%,完成5项指标区分度校验。

正文 818 字符 / 6 条 / 表头另计 65 字符。
```

---

## 2.1 一句话定位与全链路

**一句话**：用 asyncio 手写了一套**多智能体编排引擎**，把"复杂问题研究"拆成 9 个状态、DAG 分层并发执行、再用 Red-Blue 对抗压幻觉，最后合成带引用的长报告。

**全链路**：

```
用户问题
  ↓
[PLANNING]   Planner 拆成 6–10 个子任务 + 构建 DAG
  ↓
[DISPATCHING] 按 DAG 拓扑分层（get_parallel_groups），每层并发
  ↓
[COLLECTING]  Researcher 跑子任务（7 个工具），单任务超时标记继续
  ↓           ↓ 失败率超阈值
  ↓        [REPLANNING] 增量重规划，最多 3 轮，清空旧结果
  ↓
[SYNTHESIZING] 合成报告（压缩器在这一步之前介入）
  ↓
[ADVERSARIAL] 如果 report.confidence < 0.8，跑 Red-Blue 对抗
  ↓
[DONE]
```

**这条链上你要能脱口而出的关键文件**：

| 模块 | 文件 |
|---|---|
| 状态机 / 并发 / 降级 | [orchestrator.py](../src/orchestrator/orchestrator.py) |
| 任务拆解 / 置信度过滤 | [planner.py](../src/planner/planner.py) |
| DAG / Kahn | [dag.py](../src/planner/dag.py) |
| Red-Blue 对抗 | [adversarial/red_agent.py](../src/adversarial/red_agent.py) / [blue_agent.py](../src/adversarial/blue_agent.py) / [loop.py](../src/adversarial/loop.py) |
| 三级压缩 | [compressor/compressor.py](../src/compressor/compressor.py) / [extractive.py](../src/compressor/extractive.py) |
| 共享记忆 | [memory/memory_store.py](../src/memory/memory_store.py) |
| LLM-as-Judge | [core/judge.py](../src/core/judge.py) |
| Agent 对象池 | [orchestrator/agent_pool.py](../src/orchestrator/agent_pool.py) |
| 指标 / 统计检验 | [rule_based.py](../evaluation/metrics/rule_based.py) / [judge_based.py](../evaluation/metrics/judge_based.py) / [stats.py](../evaluation/metrics/stats.py) |
| 基准 | [evaluation/benchmarks/research_bench.py](../evaluation/benchmarks/research_bench.py) / [hotpotqa.py](../evaluation/benchmarks/hotpotqa.py) |
| 可观测性 | [tracing.py](../src/utils/tracing.py) |

---

## 2.2 九个状态是怎么实现的（**别答错**）

**不是 `if/elif` 链，是字典映射。** [orchestrator.py:84](../src/orchestrator/orchestrator.py#L84) 处 `self._state_handlers = {...}`，每个 handler **返回下一个状态**，主循环用返回值驱动转移。

九个状态 = **7 个正常流转 + REPLANNING + FAILED**：

```
IDLE → PLANNING → DISPATCHING → COLLECTING → SYNTHESIZING → ADVERSARIAL → DONE
                       ↑                                       
                  REPLANNING ──────────┘（回到 DISPATCHING）
任意状态 → FAILED（不可恢复错误）
```

**被追问"为什么用字典不用 if/elif"**：状态到处理器的映射是**静态的**，字典是 O(1) 查表且新增状态不用改主循环；if/elif 是 O(n) 且每加一个状态就得动那个函数。**但真正的理由是**：handler 返回 next state 让**状态转移本身变成了数据**，而不是控制流——这样 REPLANNING 这种"回边"不需要特殊处理。

---

## 2.3 核心机制逐条（对应简历 6 句）

### ② 编排引擎 —— 三级降级（**这是全项目最强的部分，讲透它**）

**第一级 · 单任务超时标记继续** — [orchestrator.py:263](../src/orchestrator/orchestrator.py#L263)

```python
await asyncio.wait_for(agent.run(...), timeout=per_task_timeout)
```

超时不抛穿整层，而是记为 `TIMEOUT` 状态、**继续**。语义是"一个子任务失败不阻塞同层其他任务"。

**第二级 · 增量重规划** — 触发条件在 [orchestrator.py:332](../src/orchestrator/orchestrator.py#L332)，判定函数 [orchestrator.py:519](../src/orchestrator/orchestrator.py#L519)

精确阈值（**必背**）：

```
failure_rate > 0.5
  或
(success / total < 0.3  且  failed > 0)

→ 触发 REPLANNING，上限 3 轮
```

注意第二个条件里的 `failed > 0` 是**必要的**：没有它，`total = 0` 时 `success/total` 会除零，而且全成功时 `0 < 0.3` 也会误触发。

**重规划做的事**（[orchestrator.py:492](../src/orchestrator/orchestrator.py#L492) 附近）：

1. **保留** confidence 达标的结果（保留在 memory 中，新任务通过 `context_keys` 引用）
2. `self._results = []` —— **清空**当前结果列表
3. 重新规划剩余子任务

**"为什么清空，不是追加？"** —— 因为重规划会**重新生成任务 DAG**，旧的 DAG 已经不成立了。追加会让结果列表里混进两套任务体系的产物，去重和合成都没法做。**旧结果没有丢，它在 memory 里，通过 `context_keys` 被新任务引用**——这是"清空但不清失"。

**第三级 · 全局超时强制合成** — [orchestrator.py:125](../src/orchestrator/orchestrator.py#L125)

全局 600s 预算耗尽 → 不再跑后续状态，**直接跳 SYNTHESIZING**，用已有结果出报告。宁可要一份不完整的报告，也不要一个超时异常。

> ⚠️ **这一级有个你必须主动说破的真相，见 §2.5 陷阱 6。**

**并发的三层防护**（简历没写，但这是加分点）：

| 层 | 位置 | 作用 |
|---|---|---|
| 全局并发上限 | [orchestrator.py:238](../src/orchestrator/orchestrator.py#L238) `asyncio.Semaphore(max_concurrent)` | 建在**层循环之外** = 全局上限，不是每层重置 |
| 单任务超时 | [orchestrator.py:263](../src/orchestrator/orchestrator.py#L263) `asyncio.wait_for` | 第一道 |
| 层内异常隔离 | [orchestrator.py:286](../src/orchestrator/orchestrator.py#L286) `asyncio.gather(*coros, return_exceptions=True)` | 第二道 |
| Agent 归还 | `finally: await release_agent` | 第三道（防泄漏） |

**"`gather` 里一个任务抛异常会怎样？"** —— `return_exceptions=True` 把它**变成返回值**而不是抛出来，所以同层其他任务不受影响；异常对象在结果遍历时被识别、记为失败。**如果面试官追问"那 `return_exceptions=True` 会不会吞掉本该中断的致命错误"** —— 会，这是**有意的取舍**：研究任务里"一个子任务挂了"不该让整个研究失败，代价是无法区分"子任务失败"和"系统级故障"。

**时间用 `time.monotonic()`**（[orchestrator.py:113](../src/orchestrator/orchestrator.py#L113)），不用 `time.time()` —— 超时判定必须单调，墙上时钟会被 NTP 校时往回拨。

### DAG 与 Kahn（两个排序的区别，容易答混）

| 方法 | 行号 | 返回 | 谁在用 |
|---|---|---|---|
| `topological_sort()` | [dag.py:85](../src/planner/dag.py#L85) | **全序**（一个平铺的列表） | 需要严格顺序时 |
| `get_parallel_groups()` | [dag.py:115](../src/planner/dag.py#L115) | **分层**（list of list） | ⭐ **orchestrator 用的是这个** |

`get_parallel_groups()` 每轮取出 `_in_degree == 0` 的节点作为一层，同时扣减后继的入度——这就是 **Kahn 算法**。

**三层保护**：

1. **环检测** — [dag.py:109-112](../src/planner/dag.py#L109-L112)：入度不为 0 的节点就是环上的节点，`DAGCycleError` **把节点名报出来**（不是只说"有环"）
2. **状态无副作用** — [dag.py:91](../src/planner/dag.py#L91) 和 [dag.py:124](../src/planner/dag.py#L124) 都用 `dict(...)` **拷贝** `_in_degree`，所以同一个 DAG 可以反复排序，不污染内部状态
3. **稳定排序** — [dag.py:130-131](../src/planner/dag.py#L130-L131) `current.sort()`：同层内按字典序排，保证**同样的输入产出同样的层序**（可复现性）

**"为什么不用 `networkx`？"** —— ⚠️ **注意：项目里 `networkx` 是依赖**，用在 [compressor/extractive.py](../src/compressor/extractive.py) 的 TextRank 上。**绝对不能说"我没用 networkx"**。正确答法见讲义 03 附加 4，四个理由：① `networkx.algorithms.dag` 报环时报的是**环上的边**，我需要的是**剩下的节点名**（好定位）② 我有邻接字典，构 `DiGraph` 是多余的搬运 ③ 我要"重复调用无副作用"，自己写反而更短 ④ `networkx` 不保证同层顺序稳定。**收尾一定要加**："纯算法层面 networkx 完全能做，我是为了报错信息和确定性排序自己写的，不是性能问题。"

### ③ Red-Blue 对抗

**五维**（[red_agent.py](../src/adversarial/red_agent.py)，顺序即权重从高到低）：

```
fact_check > hallucination > logical_consistency > source_credibility > coverage
```

**"有时效性维度吗？"** —— **没有。** 简历 v3 曾写过，是错的。

**Blue 的修复类型 —— 只有 3 种，不是 4 种**（`FixType` 枚举）：

```
IN_PLACE        原地修正
SUPPLEMENTARY   补充检索
REMOVAL         整段移除
```

简历里那个 `VERIFY`（或 `ADD/DELETE/MODIFY/VERIFY` 四种）**不存在**。面试官问"VERIFY 具体改什么"就露馅。**v5 已经改成"就地修正/补充检索/整段移除"，是对的。**

**两个刹车**：

1. **收敛** — 三个条件同时满足才停（分数够高 + 提升够小 + `round > 1`）。那个 `round > 1` 是防止**第一轮就误判收敛**（第一轮没有前一轮可比）
2. **震荡** — 把"上一轮被改的问题集合"和"这一轮新增的问题集合"求**交集**，交集非空 = 改一个冒一个 = 停

**轮数/阈值：函数默认值 ≠ 实际配置！**

```
函数签名默认值:  3  /  8.0  /  0.3
实际配置文件值:  10 /  9.5  /  0.2     ← 面试答这一组
```

**为什么答配置值**：面试官问的是"你的系统实际怎么跑的"。答默认值会暴露"你没看过自己的配置"。

**"多级 JSON 兜底"** —— 清洗是**四步**，兜底是**多级 fallback 链**。**但不要说 `85%→95%`**（见 §2.5 陷阱 1）。

### ④ 上下文治理（三级压缩 + 共享记忆）

**三级压缩**（**必须按这个顺序答**）：

```
L1  向量粗筛       （抽取式）
L2  TextRank 关键句抽取 / 图算法   （抽取式）
L3  LLM 摘要       （生成式）
超限则由滑动窗口兜底
```

**"前两级为抽取式"**是简历原文，**对的**：L1/L2 只是选句子，不改写；只有 L3 是生成。

**触发按 `budget 占用率`分级**，不是相似度。

> ⚠️ 两个高危叙述见 §2.5 陷阱 3、陷阱 4。

**共享记忆**（SQLite + NumPy 向量检索）三段链：

```
写入前：  语义去重  →  矛盾检测
冲突时：  多数投票  →  来源权重  →  LLM 裁定
```

**"numpy 向量索引"的真相**：是**全量暴力余弦**，不是 ANN 索引（没有 faiss/hnswlib）。**被问到要如实说**："数据量在千级，暴力检索够用，引入 ANN 库是过度设计。" —— 这是**正确的工程判断**，不是缺点。

**⚠️ Embedder 会静默降级**：拿不到 embedding 时回落到某种默认/哈希方式，**不报错**。所以"向量检索"在某些运行下实际是退化的。这是你自己该知道的坑。

### ⑤ 评测体系

**ResearchBench：11 领域 / 35 题**（✅ 准确。文件注释里写"20 题"是**过期注释**，别照抄）。出处：[evaluation/benchmarks/research_bench.py](../evaluation/benchmarks/research_bench.py)，HotpotQA 变体在 [hotpotqa.py](../evaluation/benchmarks/hotpotqa.py)。

五个规则指标 + LLM-as-Judge + 统计检验：

```
规则指标：事实准确率 / 幻觉率 / 引用覆盖率 / 逻辑一致性 / 完备性
Judge：  LLM-as-Judge 打分
统计：   Bootstrap 95% CI、Cohen's d
```

**"为什么要 Bootstrap 不用 t 检验？"** —— 样本量小（35 题量级）、指标分布未知、且**不假设正态**。Bootstrap 直接从经验分布重采样，对分布形状无要求。**Cohen's d 报的是效应量不是 p 值**——p 值只回答"有没有差异"，d 回答"差多少"，小样本下必须两个都给。

**"消融脚本"** — [scripts/run_all_experiments.py](../scripts/run_all_experiments.py) 会调用 [scripts/run_ablation.py](../scripts/run_ablation.py)，**5 个模块配置**（full / no_adversarial / no_compressor / no_memory / no_evolution）+ **4 个对抗轮数配置**。

⚠️ **但注意**：脚本注释里的"165 次"是**计划执行次数**，不是**已跑完的次数**。见 §2.5 陷阱 7。

⚠️⚠️ **更硬的一条（2025-10 体检新增）**：这 5 个模块臂里，**只有 `no_adversarial` 的开关真的接线了**。`compressor.enable_multilevel` 与 `memory.enabled` 在 `src/` 全树**没有任何读点**（`initialize_modules` 只读 [runner.py:196-197](../src/core/runner.py#L196-L197) 的 `max_context_length`/`output_reserve_tokens` 和 [runner.py:205](../src/core/runner.py#L205) 的 `memory.db_path`），`evolution.enabled` 读到了但只驱动 [runner.py:264](../src/core/runner.py#L264) 的日志。
**→ 这三条臂的产出与 `full` 相同，跑出来的"差异"全是采样噪声。** 详见新增陷阱 13 与 §评测层深挖文件。

---

## 📌 评测层深挖在另一个文件

评测体系、幻觉率口径、数据集构建、规则指标构建、多 Agent 价值，这五个高频问题的**逐条行号级准备**在：
👉 [面试知识库-项目二-评测与多Agent价值.md](面试知识库-项目二-评测与多Agent价值.md)（50 KB，77 条已验证链接）

它同时新增了口径陷阱 **12–19**（composite 天花板 0.75、消融 4 臂 3 臂未接线、`analyze_ablation.py` 造假图、无单 agent 基线、幻觉率两口径、`【】` 豁免正则缺配对、`--domain` 静默全零），下面的 §2.4 / §2.5 已按需更新。

### 🎯 新增的三行速记（不点开上面那个文件也必须记住）

| 一句话 | 后果 |
|---|---|
| **`composite_score` 天花板 = 0.75** | 权重键 `factual_accuracy` 与调用方传的 `factual_accuracy_str` 名字不一致，0.25 权重被 `metrics.get(key, 0.0)` 静默清零；[rule_based.py:280-283](../evaluation/metrics/rule_based.py#L280-L283) |
| **全仓没有单 agent 基线** | "多 agent 更好"**不能报数字**，只能讲失效模式差异；[ablation.py:34-40](../src/core/ablation.py#L34-L40) 五臂全是模块开关 |
| 🚨 **不能演示 `analyze_ablation.py`** | 无输入时用 6 个硬编码数值画对比图，看起来像真结果；[analyze_ablation.py:246-254](../evaluation/analyze_ablation.py#L246-L254) |

---

## 📌 M5 对抗循环深挖在另一个文件

简历第 ③ 句那九个字「**内置评分收敛与震荡检测**」，是整份简历里**被问穿代价最高**的一处。它的逐行机制、七个坑、以及实测数字在：
👉 [面试知识库-项目二-对抗循环M5.md](面试知识库-项目二-对抗循环M5.md)

它同时**修正了 [02 第 3.3 节](02-核心模块细节笔记.md) 对震荡检测的乐观评价**。下面这三行是**不点开也必须记住的**：

| 一句话 | 后果 |
|---|---|
| 🚨 **震荡检测几乎失效** | 判等包含模型生成的自然语言描述，**换个措辞就判不等** → 实际检出率远低于设计意图；[verdict.py:98-111](../src/adversarial/verdict.py#L98-L111) + [loop.py:104](../src/adversarial/loop.py#L104) |
| 🚨 **整套审计链运行时被丢弃** | 循环构造了完整每轮记录，但编排器**只取 `len(history)`**；`to_json`/`from_json` **零调用**；`save_logs: true` 是**死配置**；[orchestrator.py:456-459](../src/orchestrator/orchestrator.py#L456-L459) |
| ⚠️ **`FIX_DIFFICULTY` 名字是反的** | 值越大＝越**容易**修；导致「需重新检索」那类（＝无来源断言、幻觉高发区）被乘 0.6 排到最后，实测 **2/5 次跑满 10 轮上限**；[verdict.py:204-208](../src/adversarial/verdict.py#L204-L208) |

复现命令：`PYTHONIOENCODING=utf-8 python scripts/measure_m5_verdict.py --json outputs/m5_verdict_metrics.json`

---

## 2.4 关键数字及出处

### 实测数字（有脚本、有产物，**可以主动报**）

出处：[scripts/measure_extra_metrics.py](../scripts/measure_extra_metrics.py) → [outputs/extra_metrics.json](../outputs/extra_metrics.json)
测量方式：不联网、不调模型、不装新包。只读 5 份报告文本 + 对 `src/` 和 `evaluation/` 做 `ast` 静态分析。**证据档位：实测。**

| 指标 | 值 | 怎么用 |
|---|---|---|
| 源文件数 / 总行数 | 63 个 / **14,146 行** | ✅ 与简历"约 1.4 万行"吻合（`src 11703 + evaluation 2443 = 14146`），**独立验证了** |
| 函数总数 / async 数 | 410 / **62（15.1%）** | 佐证"真的是异步编排"，不是同步套壳 |
| **参数类型注解覆盖率** | **96.9%** | 工程规范度硬证据，学生简历里少见 |
| 返回值注解覆盖率 | 94.4% | 同上 |
| docstring 覆盖率 | 79.5% | 同上 |
| 报告字符数（5 份） | 均值 20,994 / **中位 23,005** / 极差 13,509–25,185 / **σ 4,757** | 比干说"平均 2.1 万字"更能答"稳定性如何" |
| ⭐ **跨报告两两相似度** | 均值 **0.272** / 最大 0.375 | **最好用的一条**，见下 |
| 引用密度 | 3.1–4.1 个/千字 | 证明检索确实在用 |
| 引用段落覆盖率 | 0.023–0.167 | ← 解释了 `citation_coverage = 0.386` 为什么这么低 |

**⭐ 为什么"跨报告相似度 0.272"最好用**：它预先堵住一个面试官**一定会怀疑但不会明说**的问题——"你这些报告是不是同一个模板换个题目生成的？"

> 口播："我额外测了一个数字：5 份报告两两的字符级相似度均值 0.27，最高 0.38。题面完全不同（Agent 技术趋势 / Transformer 7B 推理成本 / 新能源汽车 / 检索综述 / 生成式 AI 对比），产出也确实实质性不同。这直接回答了『产出是不是模板套用』。"

**测法**（被追问要能讲）：字符 **3-gram TF-IDF 余弦**（中文不需要分词器，`sklearn` 默认 `token_pattern` 对中文无效）。静态分析用 Python 标准库 `ast` 遍历函数节点。

**新增的实测数字（2025-10 评测层体检，出处 [scripts/measure_eval_layer.py](../scripts/measure_eval_layer.py) → [outputs/eval_layer_metrics.json](../outputs/eval_layer_metrics.json)）**：

| 指标 | 值 | 怎么用 |
|---|---|---|
| **规则口径幻觉率** | **3.34%**（5 份**逐份比率的算术平均**） | ✅ 简历上那个数的出处就在这。⚠️ **池化口径是 4.24%**（67 可疑句 / 1,581 句），**被追问要立刻说清用哪个** |
| 幻觉率构成 | **95.5% 来自"带单位数字"一条正则**（64/67）；"模糊引用"命中 **0** 次 | 主动说破："它实质在测**不带引用的数字密度**，不是三类特征的综合判断" |
| 逐份幻觉率 | 1.45% / 2.58% / **11.71%** / 0% / 0.97% | reports3 那 11.71% 里 45 句是数字 → **更可能是写作风格差异，不是质量问题** |
| 语料规模 | 1,581 句 / 1,150 段 / 104,971 字符 | 说明分母是实的，不是"跑了一两份" |

### ❌ 不能报的数字

| 数字 | 问题 |
|---|---|
| `85% → 95%` | **全仓库无出处，唯一纯凭空的数据** |
| **任何消融 / 对照实验结果** | 消融**从未跑过**，`outputs/` 下无消融 JSON；且 5 臂里 3 臂开关未接线（陷阱 13） |
| **任何 Judge 分数** | Judge 实现完整但**没跑过**，无产物；且它只看报告前 4000 字符（陷阱 10） |
| **95% 置信区间 / p 值** | 报告侧只有 **n=5**；且 `bootstrap_ci_paired` **没固定 seed**（[stats.py:44](../evaluation/metrics/stats.py#L44)）不可复现 |
| 段落近重复率 0.000 | 没有对照组，无法区分"去重做得好"还是"本来就不重复" |
| 单 Agent 对比提升幅度 | **全仓没有单 agent 基线**，这个数不存在（陷阱 16） |

**"段落近重复率 0.000 为什么不用"** —— 这就是纪律：**没有对照，就不下结论**。写上去反而给面试官送靶子。

**"n=5 为什么不能报置信区间"** —— 同一条纪律的第二个应用。5 个点的 Bootstrap 重采样，CI 宽度会大到没有任何信息量。**只能报均值/中位/极差。**

---

## 2.5 必须主动说破的口径陷阱（**全篇最重要**）

面这份简历，你会被问到下面的东西。**被动被挖 = 露馅；主动说破 = 加分。** 顺序按危险程度排。

### 陷阱 1（最高危）：`85%→95%` JSON 解析率

**这是唯一一个纯凭空数字**，而且长得特别像编的。仓库里**没有任何测量脚本或结果**支持它。
面试官只要问一句"怎么测的、样本量多少"，第二层就崩。

**✅ 改法**：删数字，说"**多级 JSON 解析兜底链**"，讲机制：四步清洗 + 多级 fallback + 失败降级为**保守分**（不中断循环）。
**如果 v5 已删除该数字——很好，但你要准备好"为什么之前的版本有"。** 标准答法："那个数字我没有可靠测量，所以从简历里删掉了，只保留机制描述。" —— 这个回答本身就是**诚实信号**，比数字值钱。

### 陷阱 2：`VERIFY` 这个修复类型不存在
v5 已改对。**别在口述时又滑回去**。

### 陷阱 3：`L3 原文保留` —— 错，且逻辑自相矛盾
L3 是 **LLM 摘要**（生成式）。"原文保留"既与代码不符，也和"L1/L2 是抽取式、L3 才是生成"这个分级逻辑矛盾。
**✅ 说**：`L1 向量粗筛 → L2 TextRank 抽取 → L3 LLM 摘要`。

### 陷阱 4：`L1 cosine < 0.6 丢弃` —— 两个东西混了
`0.6` 是 **budget 占用率**（压缩触发阈值）；L1 的 cosine 阈值是**自适应**的 `0.25 / 0.20 / 0.15`。
**✅ 说**："触发看 budget 占用率；L1 内部的相似度阈值是自适应三档。"

### 陷阱 5：Red 五维里的"时效性"不存在
五维是 `fact_check / hallucination / logical_consistency / source_credibility / coverage`。v5 已改对。

### 陷阱 6 ⚠️：**全局超时是"软超时"**（这条最能体现你真读过自己的代码）

[orchestrator.py:125](../src/orchestrator/orchestrator.py#L125) 的 `_is_global_timeout()` **只在主循环顶部检查**，而 [orchestrator.py:141](../src/orchestrator/orchestrator.py#L141) 的 `await handler()` **外面没有 `wait_for`**。

**后果**：如果某个状态自己跑很久（比如 SYNTHESIZING 调一个大模型），600s 到点时**拦不住它**——只能在它结束后、下一次循环顶部才发现超时。

**✅ 主动说破的口播**：

> "这里有个我知道的盲区：全局超时是软超时，只在循环顶部检查，单个长状态拦不住。要硬保证的话得在 `await handler()` 外面再包一层 `wait_for`，代价是被打断的状态要做清理。我当时选软超时，是因为研究任务比硬 SLA 更看重出结果。"

**这一段的说服力极强**：它证明你不仅写过，还**想过它的失败模式**。

### 陷阱 7 ⚠️：**有规则层产物，但没有消融/对照结果 → 分清楚再报**

（2025-10 更新：这条的原始版本是"没有任何实验结果 JSON"，现在已经不准确了。）

**能报的**：规则层真跑了，有产物 —— 幻觉率 3.34%、平均 20,994 字符、跨报告相似度 0.272、代码 14,146 行。见 §2.4。

**不能报的**：任何**消融值 / 对照值 / Judge 分数**。消融从未跑过（`outputs/` 下无消融 JSON），Judge 层实现完整但没跑。

> **被问"跑出来多少分"就如实说**："规则层跑过，5 份报告有产物；**消融和 Judge 层没跑**——完整跑一轮的成本很高，而且我在审计时发现**消融的 5 条臂里有 3 条开关根本没接线**，跑出来的差异只会是采样噪声，所以我把优先级放在先修接线。"
>
> **然后立刻转向你能讲的**：指标怎么设计的、Judge 有什么偏差、Bootstrap 为什么用它。

**"先修接线再跑"这个答案比"时间不够"强得多** —— 它证明你做了**前置审计**，而不是在凑数据。**主动承认比含糊过去安全得多。**

### 陷阱 8 ⚠️：`max_sub_questions` 是**死配置**
配置里有，但**不生效**。真正的任务是 [planner.py:229](../src/planner/planner.py#L229) 硬编码的提示词 `"generate 6-10 sub_tasks"` 驱动的。
**被问"子任务数量怎么控制"** → 答这个真相，别答配置字段。

### 陷阱 9 ⚠️：**对抗不是无条件跑的**
[orchestrator.py:446-448](../src/orchestrator/orchestrator.py#L446-L448)：`if report.confidence >= 0.8: skip adversarial`。

**后果**：高置信报告**根本不进对抗**。所以"对抗降低了幻觉"这个说法，**只对 confidence < 0.8 的那部分样本成立**。做消融实验时这个门会稀释对照组的差异。

### 陷阱 10 ⚠️：**Red Agent 只看报告前 4000 字符**
[red_agent.py:289](../src/adversarial/red_agent.py#L289) `report.content[:4000]`。而报告**平均 20,994 字符**。

**后果**：360 个引用 URL 里只有约 **1.9%** 落在可见窗口内 → **`source_credibility` 这一维（权重 0.15）实际上接近失明**。

**同一模式出现在多处**：[core/judge.py:78](../src/core/judge.py#L78) `report[:4000]`、[core/judge.py:153-156](../src/core/judge.py#L153-L156) `[:3000]`、[compressor.py:193](../src/compressor/compressor.py#L193) `encode(text[:1000])`。

**✅ 主动说破的口播**：

> "我知道一个真实盲区：Red Agent 只读报告前 4000 字符，而报告平均 2 万字，所以在来源可信度这一维上它基本是盲的。当时是为了控制单次调用成本做的截断，代价是五维里的四维覆盖不全。要做对的话得按段落切分后分批评分，再聚合。"

### 陷阱 11 ⚠️：**AgentPool 的两处实现问题**
1. [agent_pool.py:127](../src/orchestrator/agent_pool.py#L127) `_create_agent` 对 SEARCH / ANALYZE / VERIFY 三种类型**都返回 `ResearcherAgent`**（[L138](../src/orchestrator/agent_pool.py#L138) / [L140](../src/orchestrator/agent_pool.py#L140) / [L142](../src/orchestrator/agent_pool.py#L142) 三行是同一个类）—— 类型标签是装饰性的
2. [agent_pool.py:149](../src/orchestrator/agent_pool.py#L149) `_infer_type_key` 对所有 `ResearcherAgent` 都返回 `"search"`（[L155](../src/orchestrator/agent_pool.py#L155) 的注释自己写了"统一归到 search"）→ `_idle["analyze"]` **永远是空的** → **池化对 2/3 的任务类型实际失效**

**"对象池的意义是什么"** —— 复用昂贵对象（LLM client、连接）；[agent_pool.py:39](../src/orchestrator/agent_pool.py#L39) `max_idle=3` 控制常驻上限，[L108](../src/orchestrator/agent_pool.py#L108) 是取用判定。**然后主动补上面两条**，说明你知道它没完全生效。

---

### 陷阱 12–19 ⚠️（2025-10 评测层体检新增 · 完整版在[深挖文件](面试知识库-项目二-评测与多Agent价值.md) §6）

| # | 陷阱 | 一句话 | 证据 |
|---|---|---|---|
| **12** | **`composite_score` 天花板 0.75** | 权重键 `factual_accuracy` 与调用方传的 `factual_accuracy_str` 不一致，0.25 权重被 `metrics.get(key, 0.0)` 静默清零。**怎么发现的**：把权重调到 1.0 分数不变（敏感性分析） | [rule_based.py:268-283](../evaluation/metrics/rule_based.py#L268-L283) · [research_bench.py:490-499](../evaluation/benchmarks/research_bench.py#L490-L499) |
| **13** | **消融 5 臂只有 1 臂接线** | `compressor.enable_multilevel` / `memory.enabled` 在 `src/` **无读点**；`evolution.enabled` 只驱动日志（空壳） | [ablation.py:34-40](../src/core/ablation.py#L34-L40) · [runner.py:196-197](../src/core/runner.py#L196-L197) |
| **14** | **`average_composite_score` 实际是成功率** | 写死 `1.0` / `0.0` 占位再求平均，字段名却叫 composite —— **名字在撒谎** | [ablation.py:114](../src/core/ablation.py#L114) · [L125](../src/core/ablation.py#L125) · [L131](../src/core/ablation.py#L131) |
| **15** | 🚨 **`analyze_ablation.py` 造假图** | 无输入时用 6 个硬编码数值（`adv_0..3 = 0.62/0.71/0.78/0.80`、`evo_off/on = 0.68/0.76`）画 dpi=300 对比图。**绝对不要演示** | [analyze_ablation.py:246-254](../evaluation/analyze_ablation.py#L246-L254) |
| **16** | **全仓没有单 agent 基线** | 五臂全是"关掉某模块"，无一是"换成单 agent" → **"多 agent 更好"不能报数字** | [ablation.py:34-40](../src/core/ablation.py#L34-L40) |
| **17** | **幻觉率两个口径** | 逐份比率算术平均 **3.34%**（← 简历口径）≠ 池化 **4.24%**（67/1581） | [outputs/report_metrics.json](../outputs/report_metrics.json) |
| **18** | **豁免正则 `【` 无配对** | 闭括号类 `[\]）)]` **缺全角 `】`** → 项目自己倡导的 `【来源: ...】` 反被判为幻觉；而说明性括号 `（约合…）` 却被误豁免 | [rule_based.py:140](../evaluation/metrics/rule_based.py#L140) |
| **19** | **`--domain` 静默全零** | choices 是英文（tech/med/fin）、数据 `domain` 是中文 → 永远选 **0 题** → 评测报告**全 0.0000 且不报错** | [run_baseline.py:211](../evaluation/run_baseline.py#L211) · [L232](../evaluation/run_baseline.py#L232) |

**陷阱 17–19 的讲法建议**：不要一次性倒出来。**被问到哪个讲哪个**，且每个都要带"我怎么发现的"——
陷阱 12 是**敏感性分析**发现的，陷阱 13 是**审计开关读点**发现的，陷阱 19 是**真跑了一次 CLI** 发现的。**发现路径比缺陷本身值钱。**

---

## 2.6 高危追问清单

| 简历句 | 追问 | 答案落点 |
|---|---|---|
| ① 项目定位 | **"为什么自研不用 LangGraph？"** | 讲义 03 附加题（全篇背下来）。**红线：别说"我完全没碰 LangChain 生态"——你用了 LangSmith**（[tracing.py](../src/utils/tracing.py)）。也别说 networkx 做不了 |
| ① | "1.4 万行是你一个人写的？" | 见 §2.7 |
| ② 编排 | "`gather` 里一个任务抛异常会怎样？" | §2.3 三层防护 |
| ② | "三级降级的**精确阈值**？" | `>0.5` 或（`<0.3` 且 `failed>0`），**3 轮** |
| ② | "重规划为什么清空结果？" | §2.3 "清空但不清失" |
| ② | "全局超时能中断当前状态吗？" | **不能**，软超时，陷阱 6 |
| ② | "`confidence >= 0.6` 在哪过滤的？" | ⚠️ **在 planner，不在 orchestrator**（[planner.py:177-186](../src/planner/planner.py#L177-L186)）——这是最容易被问翻的一处 |
| ③ 对抗 | "**85%→95% 怎么测的？**" | 陷阱 1 |
| ③ | "对抗能覆盖全文吗？" | 陷阱 10，**主动承认盲区** |
| ③ | "轮数阈值多少？" | **10 / 9.5 / 0.2**（配置值） |
| ④ 压缩 | "0.6 / 0.8 / 0.95 是相似度还是占用率？" | **占用率**；L1 相似度是自适应 0.25/0.20/0.15 |
| ④ | "L3 具体做什么？" | LLM 摘要，陷阱 3 |
| ⑤ 评测 | "**跑出来多少分？**" | 陷阱 7，如实说 + 转向 |
| ⑤ | **"比单 agent 好在哪里？"** | 深挖文件 §1。**先划边界**（没有单 agent 基线）→ 讲**失效模式差异** → 给产出侧画像。**绝不给百分比** |
| ⑤ | **"幻觉率怎么测的？"** | 深挖文件 §2。口诀：**切句 → 有可疑特征且无括号 → 占比**；主动说"**不是事实错误率**" |
| ⑤ | **"数据集怎么构建的？"** | 深挖文件 §3。**11 域 35 题 + 两个锚点（覆盖面/关键事实）**；主动说 HotpotQA **只有 5 题兜底** |
| ⑤ | **"规则指标怎么构建的？"** | 深挖文件 §4。**5 个零依赖确定式启发式** + 主动说 composite **天花板 0.75** |
| ⑤ | **"评估覆盖了哪些？"** | 深挖文件 §5。**规则层真跑了、Judge/消融没跑、统计层 n=5 报不了 CI** |
| ⑤ | "消融实验做了吗？" | 陷阱 7 + 陷阱 13：**没跑，且 3 条臂开关未接线** |
| ⑤ 🚨 | **"能演示一下消融分析吗？"** | **不要！** 陷阱 15：`analyze_ablation.py` 会用硬编码假数据画图 |
| ⑤ | "Judge 用什么模型？为什么？" | 要能指名并说明理由 |
| ⑤ | "Bootstrap 为什么不用 t 检验？" | §2.3 评测体系 |
| 全篇 | "哪些是你做的？" | 见 §2.7 |

**风险信号自检**（答案里出现这些词就是失分点）：

- 用"负责 / 优化 / 提升"却**没有对象 + 动作 + 证据**
- 报了百分比却**说不清统计口径**（← 就是那个 85%→95%）
- 只讲 happy path，**讲不了失败与回滚**（← 所以三级降级要讲透）
- **背得出术语定义，却说不出它在项目里的作用**

---

## 2.7 "哪些是你做的" + "代码是不是 AI 写的"

这两问在 2026 年几乎是必问，且**必须提前想好、平静讲完**。

**准备三句话，具体、不心虚、不辩解**：

1. **讲你做了什么**（用"我定的"而不是"我写的"——设计决策比敲键盘更难被质疑）：
   "架构和降级策略是我定的，代码是我用 Claude Code 辅助实现的。9 状态机的状态划分、三级降级的阈值、DAG 分层并发这几个设计取舍是我做的，实现过程我逐模块审过。"

2. **讲证据**（比任何解释都硬）：
   "我可以现场讲任意一个模块的实现细节和它的缺陷——比如全局超时是软超时、Red Agent 只读前 4000 字符、AgentPool 的类型键算错导致池化失效。这三个都是我自己找出来的。"

3. **如果问 commit 为什么不在你名下**（如实、一句话、不展开）：
   "仓库在组里统一账号下推的。" —— **然后立刻转回技术**。**不要在这个问题上多解释**，越解释越像心虚。

> **核心逻辑**：AI 辅助写代码在 2026 年不是减分项，**说不清自己代码的才是**。所以 §2.5 那 11 个陷阱是这一节的弹药——**能主动指出自己系统的缺陷，是对代码所有权最强的证明**。

---

## 2.8 跟读自检（能顺口答出来才算过）

1. 九个状态分别是什么？状态机是用什么数据结构实现的？
2. 三级降级的**精确**触发条件各是什么？（阈值要数字）
3. 第二级重规划的上限是几轮？重规划后为什么要清空 `_results`？
4. `confidence >= 0.6` 的过滤在哪个文件？为什么这是个坑？
5. `topological_sort()` 和 `get_parallel_groups()` 的区别？orchestrator 用哪个？
6. 环检测报错时，报出来的是**节点**还是**边**？为什么这个选择重要？
7. 对抗的两个刹车是什么？`round > 1` 那个前提防的是什么？
8. 对抗的轮数/阈值，函数默认值和配置值分别是多少？答哪一组？
9. Blue 的修复类型有几种？分别是？
10. 三级压缩分别是什么技术？哪几级是抽取式？
11. `0.6` 是预算占用率还是相似度？L1 的相似度阈值是多少？
12. Red Agent 只看报告的多少字符？这导致哪一维失效？
13. `report.confidence` 到多少就跳过对抗？
14. 全局超时是硬超时还是软超时？为什么？
15. `max_sub_questions` 为什么是死配置？真正的驱动是什么？
16. AgentPool 的两个实现问题分别是什么？
17. 项目里用没用 `networkx`？用在哪？用没用 LangSmith？
18. "为什么不直接用 LangGraph？" —— 30 秒版怎么讲？
19. "跨报告相似度 0.272" 是用来回答哪个质疑的？
20. "跑出来多少分？" —— 怎么答才不掉分？

---

## 2.9 30 秒项目总述（背这一段）

> "这是一个面向复杂研究任务的多智能体系统，用 asyncio 手写了编排引擎。核心是三层设计：
> **第一层是状态机**——9 个状态管任务生命周期，每个 handler 返回下一个状态，所以状态转移是数据不是控制流；
> **第二层是执行**——用 Kahn 算法把子任务 DAG 拓扑分层，层内并发、层间串行，全局信号量控制并发度 5；
> **第三层是容错**——三级降级：单任务超时标记继续不阻塞整层，失败率超 50% 触发增量重规划（保留高置信结果、上限 3 轮），全局超时强制用已有结果合成。
>
> 后面接 Red-Blue 对抗压幻觉、三级压缩治理上下文、SQLite 共享记忆做跨 Agent 一致，最后是带统计检验的评测体系，35 题 11 领域，Bootstrap 置信区间加 Cohen's d。
>
> 有一个我自己最清楚的设计取舍可以讲：全局超时是**软超时**，只在循环顶部检查，单个长状态拦不住——我当时选了软超时，因为研究任务更看重出结果而不是硬 SLA。"

---

## 2.10 速记卡

| 项 | 值 | ⚠️ |
|---|---|---|
| 状态数 | 9（7 正常 + REPLANNING + FAILED） | 字典映射，非 if/elif |
| 并发度 | 5 | 信号量建在层循环**外** |
| 重规划阈值 | `>0.5` 或（`<0.3` 且 `failed>0`） | 上限 **3** 轮 |
| 重规划后 | `_results = []` | 旧结果在 memory，靠 `context_keys` 引用 |
| confidence 过滤 | **planner.py:177-186** | **不在 orchestrator** |
| 对抗跳过门 | `confidence >= 0.8` | 对抗非无条件 |
| 对抗轮数/阈值 | **10 / 9.5 / 0.2** | 函数默认 3/8.0/0.3 |
| Blue 修复 | **3 种** | 无 VERIFY |
| 三级压缩 | L1 向量 / L2 TextRank / L3 LLM | L1L2 抽取式，L3 生成 |
| 压缩触发 | budget **占用率** | L1 相似度自适应 0.25/0.20/0.15 |
| Red 可见窗口 | **前 4000 字符** | 报告均 20,994 → 来源维失明 |
| ResearchBench | 11 领域 / 35 题 | 文件注释"20"过期 |
| 代码量 | 14,146 行 / 63 文件 | 实测（ast） |
| 注解覆盖率 | 96.9% | 实测 |
| 跨报告相似度 | 0.272（max 0.375） | 实测，堵"套模板"质疑 |
| 实验结果 JSON | **无** | 不要主动报分数 |
| `85%→95%` | **无出处** | 必须删 |
| networkx | **用了**（压缩器 TextRank） | 别说没用 |
| LangSmith | **用了** | 别说没碰 LangChain 生态 |
| LangGraph | **没用**（编排自研） | 见讲义 03 附加题 |

---

## 2.11 主动讲出的三个架构盲区（**这是加分项，不是减分项**）

面试时如果讲到对应模块，**主动带出**这三条中的任意一条，效果远好于被问出来：

1. **全局超时是软超时** — 只检查循环顶部，长状态拦不住
2. **Red Agent 只读前 4000 字符** — 五维里来源可信度实际失明；同模式在 judge / compressor 也有
3. **AgentPool 池化失效** — 类型键算错，`_idle["analyze"]` 永远空

**为什么主动说是加分**：它证明 ① 你真读过每一行 ② 你能识别自己的失败模式 ③ 你知道正确的修法。**没有任何一个面试官会因为候选人指出自己系统的缺陷而扣分。**
