# 面试知识库 · 项目二「评测体系 + 多 Agent 价值」深挖

> 用途：面试官看完简历后提的五个问题，逐条准备到「能指着行号说话」的程度。
> 原则：**每一句话都要么能指到行号，要么能指到 `outputs/` 里的产物。** 两者都指不到的，就不说 —— 见 §9。
> 本文档不重复 [面试知识库-项目二-DeepResearch.md](面试知识库-项目二-DeepResearch.md) 的架构内容，只补评测层与「多 agent 凭什么更好」这两块。
> 行号链接从本文档所在目录（`learn-notes/`）起算，形如 `../src/...`。

---

## 0 · 五个问题 · 30 秒速答表（先背这张）

| 面试官问 | 一句话答 | 最强证据 | ⚠️ 红线 |
|---|---|---|---|
| **比单 agent 好在哪里** | 机制上：三级降级把长链路的单点故障隔离掉、对抗循环把幻觉往外挤、三级压缩让上下文不爆。**但量化对照我没做过** | 三级降级有完整行号+代码 | **绝对不能说"提升 X%"** —— 全仓没有单 agent 基线（§1.1） |
| **幻觉率怎么测的** | 规则口径：把报告切成句，统计「**既含可疑特征、又不带引用标记**」的句子占比。5 份实测 **3.34%** | [outputs/report_metrics.json](../outputs/report_metrics.json) | 它测的是**无引用可疑表述密度**，**不是事实错误率**（§2.5） |
| **数据集怎么构建** | 自建 ResearchBench：**11 领域 / 35 题**，每题带 `expected_topics`（覆盖面锚点）+ `ground_truth`（关键事实锚点） | [research_bench.py:19](../evaluation/benchmarks/research_bench.py#L19) | `ground_truth` 是**启发式锚点不是标注答案**；HotpotQA 变体只有 **5 条**兜底数据（§3.4） |
| **规则怎么构建** | 5 个纯正则/子串指标 + 一个加权 `composite_score` | [rule_based.py:26-303](../evaluation/metrics/rule_based.py#L26-L303) | `composite_score` 有个**天花板 bug**，实跑口径最高只能到 **0.75**（§4.2） |
| **评估覆盖了什么** | 规则层**真跑了**（5 份报告有产物）；Judge 层实现完整但**没跑**；统计层参数齐全但 **n=5 报不了区间** | 覆盖矩阵见 §5.1 | [analyze_ablation.py:246-254](../evaluation/analyze_ablation.py#L246-L254) **会生成假的实验数据并画成图**（§5.4） |

**把这五条读三遍。** 下面每一条展开成「被追问三层也答得住」的深度。

---

## 1 · 「比单 agent 好在哪里」

### 1.1 先说三条我不能说的话（**这是本节最重要的部分**）

面试官问这句时，期待的是一个数字。**你要先主动把那个数字的可能性掐掉** —— 因为如果等他挖出来，性质就变了。

**① 全仓没有单 agent 基线。**
[ablation.py:34-40](../src/core/ablation.py#L34-L40) 的五臂是 `full / no_adversarial / no_compressor / no_memory / no_evolution` —— **全是「把多 agent 系统的某个模块关掉」，没有一臂是「换成一个单 agent 跑」**。

> 口播：*"我这套评测做到的是**模块级消融**，不是**架构级对照**。也就是说它能回答『对抗循环有用吗』，回答不了『多 agent 比单 agent 好吗』—— 因为我没有一个单 agent 的 arm。"
>
> 这个区分一出口，面试官对你的评价就从"学生做了个 demo"变成"知道自己在测什么"。*

**② 消融实验从来没跑过。** `outputs/` 下只有 `reports1-5/` 与三个指标 JSON（[report_metrics.json](../outputs/report_metrics.json) / [extra_metrics.json](../outputs/extra_metrics.json) / [eval_layer_metrics.json](../outputs/eval_layer_metrics.json)）—— **没有任何消融结果 JSON**。

**③ 就算跑了，四条臂里只有一条真的接线。** 这是本次体检最硬的发现，详细见 §4.5 与 [outputs/eval_layer_metrics.json](../outputs/eval_layer_metrics.json) 的 `5_消融开关接线`：

| 消融臂 | override 键 | src/ 里的读点 | 消费点 | 真实效果 |
|---|---|---|---|---|
| `no_adversarial` | `adversarial.enabled` | [runner.py:312](../src/core/runner.py#L312) | [orchestrator.py:429](../src/orchestrator/orchestrator.py#L429) | ✅ **真生效** |
| `no_compressor` | `compressor.enable_multilevel` | **无** | 无 | ❌ 空操作 |
| `no_memory` | `memory.enabled` | **无** | 无 | ❌ 空操作 |
| `no_evolution` | `evolution.enabled` | [runner.py:264](../src/core/runner.py#L264)、[runner.py:313](../src/core/runner.py#L313) | **无** | ⚠️ 空壳（只打日志） |

怎么证「无读点」的：`initialize_modules` 构造压缩器时只读两个键 —— [runner.py:196-197](../src/core/runner.py#L196-L197) 的 `max_context_length` 与 `output_reserve_tokens`；构造记忆库时只读 [runner.py:205](../src/core/runner.py#L205) 的 `memory.db_path`/`session_id`。`enable_multilevel` 与 `memory.enabled` 在 `src/` 全树没有出现。

> **面试官可能追问的反面**："那你把 `no_compressor` 那一行跑出来，看到分数没变，不就知道了吗？"
> 答："所以我准备的是：这两条臂要么补上接线再跑，要么直接从消融表里删掉 —— **跑一条开关没接线的消融臂，得到的差异全是 LLM 采样噪声，报了比不报更糟。**"

**顺带一个更隐蔽的坑**：[ablation.py:114](../src/core/ablation.py#L114) 是 `scores.append(1.0)  # 占位，实际分数由外部 evaluator 填充`，异常分支 [ablation.py:125](../src/core/ablation.py#L125) 是 `scores.append(0.0)`，然后 [ablation.py:131](../src/core/ablation.py#L131) 求平均叫 `average_composite_score`。
→ **这个字段名在撒谎：它是「成功率」，不是「质量分」。** 被问到这个字段，必须说清。

### 1.2 能站住的答案：讲三种「行为差异」，不讲「分数差异」

这个问题的正解不是"分数更高"，而是"**单 agent 在某个具体工况下会崩，而架构让它不崩**"。三种工况：

**工况 A · 长链路单点故障 → 三级降级**

单 agent 做深度研究是一条长链：拆解 → 检索 → 读 → 写。**中间任何一步失败，整条链断掉，前面全白做。** 这个项目用三层兜：单任务超时标记继续不阻塞整层、失败率超阈值触发增量重规划、全局超时强制以已有结果合成。

> 口播核心：*"单 agent 的失败是**全损**，多 agent 的失败是**局部降级**。这就是架构级差异，不依赖任何评测数字。"*

**工况 B · 长文幻觉漂移 → 对抗循环**

一份两万字的报告，单 agent 一次生成完，**没有任何机制回头质疑自己刚写的东西**。这里插了一个 Red-Blue 循环专门做这件事。

**但要主动补盲区**：[red_agent.py:289](../src/adversarial/red_agent.py#L289) 的 `report.content[:4000]` —— Red 只看前 4000 字符，而报告平均 20,994 字符。

> 口播：*"这一层我承认是**部分覆盖**：它只在报告开头那 4000 字符上做事实核查。所以「对抗降噪」这句话的准确表述是「对报告前段做了五维核查并驱动修正」，不是「整篇都被核查过」。"*

**工况 C · 上下文膨胀 → 三级压缩**

子任务越多，上下文越涨。单 agent 要么截断（丢信息）要么爆窗口。这里按预算占用率分级触发压缩。

> 三个工况的**共同结构**：单 agent 是「一个模型 + 一条链」，这个项目是「一个链路 + 三个可独立失效的机制」。**能失效才会被消融，能消融才谈得上工程。** —— 这句是可以主动送出去的观点，比数字有分量。

### 1.3 完整口播模板（三层递进，被打断在任何一层都完整）

> **第一层（先划边界）**："我先说清楚我能证明什么。我做的是**模块级消融**，不是**架构级对照** —— 仓库里没有一个「单 agent」的 arm，所以**我不能给你「多 agent 提升 X%」这个数**。"
>
> **第二层（讲机制差异）**："我能讲的是**失效模式的差异**。单 agent 是一条长链，任何一步失败是全损；这里用三级降级把失败限制在单个子任务上。单 agent 的长文没有自我质疑环节，这里加了 Red-Blue 循环，但我得说明它只读报告前 4000 字符，是部分覆盖。"
>
> **第三层（给可验证的替代证据）**："如果你要的是**可验证的量化证据**，我有的不是对照实验，而是**产出侧画像**：5 份报告平均 20,994 字符、跨报告相似度均值 0.272（证明不是同一模板换题目）、规则口径幻觉率 3.34%。这些能证明**系统真的在产出实质不同的长报告**，但确实不能证明它比单 agent 好。"

**第三层是这一节的灵魂**：主动说出"我有的证据支持什么、不支持什么"。面试官记住的是**这份克制**，不是那个缺席的数字。

### 1.4 追问链与落点

| 追问 | 落点 |
|---|---|
| "凭什么说多 agent 更好？" | §1.3 第一层 + 第二层。**不要滑到数字上** |
| "那你为什么不补一个单 agent baseline？" | 诚实答：缺的是**一个 arm + 一轮跑**，不是缺能力。补法是把 Planner 的 DAG 压成单节点、`max_concurrent=1`，其余模块全关 —— 这样对比才干净。**能说出补法，比说"没时间"强十倍** |
| "消融跑了吗？跑出来什么？" | 说真话：**没跑**。然后立刻给原因（完整一轮的调用量与时间）与你知道的两个前置 bug（开关没接线 / `average_composite_score` 是占位） |
| "没有对照，你怎么知道三级降级有用？" | 退到**代码级论证**：降级路径是显式代码而非配置（[orchestrator.py:519-537](../src/orchestrator/orchestrator.py#L519-L537) 的触发条件），且这条路径在本地用合成输入复现过 |
| "多 agent 的代价是什么？" | 主动给：**调用次数成倍**、**状态机复杂度**、**失败面更多**（规划阶段零降级，一次 LLM 调用失败就直接 FAILED） |

---

## 2 · 「幻觉率是怎么测的」

### 2.1 指标定义（逐步拆开，见 [rule_based.py:110-146](../evaluation/metrics/rule_based.py#L110-L146)）

```
输入：报告全文
① 切句   sentences = re.split(r"[。！？\n]", report)          ← L126
② 逐句判定，句子满足两个条件的**合取**才计数：
   ├─ 条件一（豁免）：句子中【没有】一对括号
   │   if not re.search(r"[\[【（(].*?[\]）)]", sentence)      ← L140
   └─ 条件二（可疑）：句子里命中 3 条正则中任意一条            ← L131-135
        A. 带单位的孤立数字  \d+[\d,]*\.?\d*\s*(%|倍|个|人|元|美元|亿|万)
        B. 绝对化表述        毫无疑问|绝对|必然|一定|众所周知
        C. 模糊引用开头      据我所知|据了解|研究显示[^【\[（(]
③ 输出  min(1.0, 可疑句数 / 总句数)                            ← L146
   （空报告直接返回 1.0，见 L121-124）
```

一句话：**"一段话里有数字或绝对化表述，却连个括号都没有" 的句子占比。**

### 2.2 实测数字（证据档位：**实测**）

出处：[outputs/report_metrics.json](../outputs/report_metrics.json)（5 份报告），本次用 [scripts/measure_eval_layer.py](../scripts/measure_eval_layer.py) 复核并拆解构成。

| 报告 | 句数 | 可疑句 | 占比 |
|---|---|---|---|
| reports1 · 大模型 Agent 技术 | 413 | 6 | 1.45% |
| reports2 · Transformer 7B 推理 | 388 | 10 | 2.58% |
| reports3 · 中国新能源汽车 | 410 | **48** | **11.71%** |
| reports4 · 检索近一年综述 | 60 | 0 | 0.00% |
| reports5 · 生成式 AI 对比 | 310 | 3 | 0.97% |
| **合计** | **1,581** | **67** | — |

**⚠️ 两个口径不是同一个数，被追问必须立刻答对用的是哪个：**

| 口径 | 算法 | 值 |
|---|---|---|
| **逐份比率的算术平均** | `mean(1.45%, 2.58%, 11.71%, 0%, 0.97%)` | **3.34%** ← **简历上写的就是这个** |
| **池化（pooled）** | `67 / 1581` | **4.24%** |

> 口播：*"简历上那个 3.34% 是**五份报告各自比率的算术平均**。如果按**总可疑句除以总句数**来算，是 4.24%。两个都是对的，但口径不同，我报的时候会用前者并说明是逐份平均。"*
>
> **这个细节的价值**：绝大多数候选人报指标时说不清分子分母。能主动区分「比率的平均」和「平均的比率」，是硬信号。

### 2.3 这个数字由什么构成（**95% 来自一条正则**）

| 模式 | 命中句数 | 占比 |
|---|---|---|
| A 带单位的孤立数字 | **64** | **95.5%** |
| B 绝对化表述 | 3 | 4.5% |
| C 模糊引用开头 | **0** | 0% |

**三个直接结论：**
1. **这个指标实质上在测「不带引用的数字密度」**，不是三类特征的综合判断。说"幻觉率"时心里要清楚这一点。
2. **模式 C 一次都没命中**，说明它要么写得过窄（`研究显示[^【\[（(]` 只在没有紧邻括号时才成立），要么这段语料确实不用这类句式。**这是一个应该被 remove 或重写的指标**。
3. **reports3 的 11.71% 极可能是风格差异而非质量问题**：48 句里 45 句是带单位数字 —— 它只是一份**数字密度高、且习惯不用括号标注来源**的报告。这暴露了指标对**写作风格**敏感。

### 2.4 四个实现缺陷（挑最强的讲）

**缺陷 ①【最值得讲】豁免正则的括号类不配对 —— 开有 `【`，闭没有 `】`**

[rule_based.py:140](../evaluation/metrics/rule_based.py#L140)：

```python
if not re.search(r"[\[【（(].*?[\]）)]", sentence):
#                    ↑ 开括号类：半角[ / 全角【 / 全角（ / 半角(      4 种
#                                ↑ 闭括号类：半角] / 全角） / 半角)      3 种 —— 缺 】
```

**后果**：项目**自己倡导**的 `【来源: ...】` 引用格式（[rule_based.py:158](../evaluation/metrics/rule_based.py#L158) 的 docstring 明确列了它，`citation_coverage` 也认它）**恰好不能构成豁免**。实测（[scripts/measure_eval_layer.py](../scripts/measure_eval_layer.py) 的 `2b_幻觉率探针`）：

| 句子（分母=1 句） | hallucination_rate | 说明 |
|---|---|---|
| `2025 年市场规模达到 100 亿美元。` | 1.0000 | 无括号 → 计为可疑 ✅ 符合预期 |
| `2025 年市场规模达到 100 亿美元（约合人民币 700 亿）。` | 0.0000 | ⚠️ **括号里是补充说明，也豁免了** |
| `2025 年市场规模达到 100 亿美元[1]。` | 0.0000 | ✅ 真引用，正确豁免 |
| `2025 年市场规模达到 100 亿美元(来源: IDC)。` | 0.0000 | ✅ 真引用，正确豁免 |
| `2025 年市场规模达到 100 亿美元【来源: IDC】。` | **1.0000** | ❌ **真引用却被判为幻觉** |

**两个方向都错**：说明性括号 `（约合…）` 被当成引用豁免了（**假阴性，指标偏低**），规范引用 `【来源: IDC】` 反而不豁免（**假阳性，指标偏高**）。两个错误方向相反，在混合语料上互相抵消一部分 —— 这也是为什么真实报告上看起来"数字还挺正常"。

> 提示：真实 5 份报告主要用 `（Result N）` 格式（`report_metrics.json` 的 `citation_markers` 显示 `（Result N）` 命中 15/15/0/0/0），所以**这 5 份的 3.34% 没被缺陷 ① 污染**。要**强调这一点**，否则会被认为在自曝无法解释的数字。

**缺陷 ② 豁免条件是「任意一对括号」，不是「真的引用标记」**
和缺陷 ① 同源。`（如 GPT-4o 等主流模型）` 这类纯说明性插入语，会让整句话脱离检查。

**缺陷 ③ `logical_consistency` 在真实语料上零区分度**

[rule_based.py:216-221](../evaluation/metrics/rule_based.py#L216-L221) 的判定是 `if a in sentence and b in sentence`，而矛盾对 [L202-208](../evaluation/metrics/rule_based.py#L202-L208) 里 `("是","不是")/("可以","不可以")/("会","不会")` 三对是**子串包含关系** → 任何含"不是"的句子必然同时命中"是"和"不是"。实测：

| 句子 | logical_consistency |
|---|---|
| `这不是一个好方案。` | **0.0000** |
| `公司不可以这样做。` | **0.0000** |
| `这个问题并不会消失。` | **0.0000** |
| `报告共 3200 字符，引用 15 处。` | 1.0000 |

→ **单句上必然误报。** 但注意长期效应：5 份真实报告全部测得 **1.0**（分母是几百句，个别误报被摊薄了）。**所以这一维在真实语料上等于没测 —— 零区分度**。
> **诚实版口播**：*"这个指标我审出两个问题：单句上会误报（子串嵌套），整篇上又零区分度（5 份全是满分）。结论是它现在不具备鉴别力，应该重写成基于断言的检查 —— 比如先抽主语-谓语-宾语三元组，再判同一主语的同一谓词是否被同时断言正反。"*

**缺陷 ④ 空报告返回 1.0**（[L121-124](../evaluation/metrics/rule_based.py#L121-L124)）—— 方向是保守的（宁可判最差），但要能说出这是**刻意的保守值**而不是 bug。

### 2.5 ⚠️ 必须主动说清：**它不是事实错误率**

这是本节最容易被问翻的地方。上面所有的判定**只用到报告自身的文本特征**，**没有拿任何外部事实做校验**。

| | 这个指标测的 | 事实错误率测的 |
|---|---|---|
| 判据 | 句内特征（数字/绝对化/模糊引用 + 有无括号） | 与外部真值逐项比对 |
| 反例 | 一句**完全编造但带 `[1]` 引用标记**的话 → 豁免，判为"无幻觉" | 会命中 |
| 反例 | 一句**完全正确但没有标注括号**的数字 → 判为"可疑" | 会通过 |

> 口播：*"这个数我从来不叫它『事实错误率』，它是**无引用可疑表述密度**。它的作用不是判断对错，而是**定位需要人工复核的句子** —— 把两万字报告里 67 句可疑的话挑出来，人只需要看这 67 句。这是一个**降采样的筛查指标**，不是一个质量判决。"*
>
> **这一段的杀伤力很大**：它同时满足了①知道自己指标的局限 ②给出了指标的真实用途 ③不给自己贴金。

### 2.6 完整口播（30 秒版）

> "幻觉率是**规则口径**的：报告切句后，统计『有数字或绝对化表述、却不带任何括号引用』的句子占比。5 份报告实测**逐份平均 3.34%**，池化口径 4.24%，1,581 句里 67 句。构成上 **95% 来自『不带引用的数字』**这一条正则，所以它本质是**无引用可疑表述密度**，我会拿它做**人工复核的降采样**，不会称它为事实错误率。审计时我还发现两个实现缺陷：豁免正则的闭括号类漏了全角 `】`，导致规范引用格式反而被判为幻觉；以及 `logical_consistency` 因为子串嵌套在单句上必然误报、在整篇上又全是满分，目前不具备鉴别力。"

---

## 3 · 「数据集是怎么构建的」

### 3.1 结构：一题四字段

出处 [research_bench.py:19-...](../evaluation/benchmarks/research_bench.py#L19)。每题是一个 dict：

```python
{
  "id":             "tech_001",                       # 域前缀_序号
  "domain":         "科技",                            # ⚠️ 中文值，见 §3.6
  "query":          "对比分析 2024 年主流大语言模型…",   # 直接喂给系统的研究问题
  "expected_topics": ["中文推理","代码生成",...],       # 覆盖面锚点 → comprehensiveness
  "ground_truth":   {"GPT-4o": "OpenAI 发布于 2024 年 5 月，原生多模态", ...},  # 关键事实锚点 → fact_accuracy
}
```

**构建逻辑（这是要讲的设计意图）**：
- `query` 是**开放研究题**，不是问答题 —— 因为要测的是"能不能产出一份报告"，不是"能不能答对"。
- `expected_topics` 和 `ground_truth` 是**两个不同维度的锚点**：前者测**覆盖面**（有没有漏掉该讲的子话题），后者测**事实命中**（有没有讲到该出现的实体）。**这两个锚点的分工，是这套题的设计核心。**
- `ground_truth` 的 value 是**描述性文字而非答案**（`"GPT-4o": "OpenAI 发布于 2024 年 5 月，原生多模态"`）。**注意：`fact_accuracy` 只用它的 key 做子串匹配，value 从来没被用过**（[rule_based.py:46-48](../evaluation/metrics/rule_based.py#L46-L48)）—— 被问到要说清。

### 3.2 规模与领域分布（**实测**，出处 [outputs/eval_layer_metrics.json](../outputs/eval_layer_metrics.json)）

**35 题 / 11 领域**：

| 领域 | 题数 | 领域 | 题数 |
|---|---|---|---|
| 科技 | 8 | 能源 | 2 |
| 医疗 | 6 | 消费 | 2 |
| 金融 | 6 | 汽车 | 2 |
| 教育 | 2 | 游戏 | 2 |
| 法律 | 2 | 传媒 | 1 |
| 交叉 | 2 | | |

> ⚠️ **代码注释里写「包含 20 道」**（[research_bench.py:8](../evaluation/benchmarks/research_bench.py#L8)、class docstring 里还有一处「内置 20 道」）—— **过期注释，别照抄**。真实条数是 35。**这个差异本身可以主动讲**：*"题库从 20 题扩到 35 题时没同步改 docstring，我后来发现了。这是个典型的注释腐化案例。"*

### 3.3 已知脏数据（**实测**）

`fin_005` 的 `ground_truth` 里有两个键**带前导空格**（[research_bench.py:197-198](../evaluation/benchmarks/research_bench.py#L197-L198)）：

```python
"ground_truth": {
    " Lemonade": "AI 驱动的保险理赔",      # ← 前导空格
    " telematics": "UBI 基于使用的保险",   # ← 前导空格
},
```

**后果**：`fact_accuracy` 走 `if key_fact.lower() in report_lower`（[rule_based.py:47](../evaluation/metrics/rule_based.py#L47)），带空格的键**永远匹配不到** → **这道题的事实准确率恒为 0**，无论报告写得多好。

> 主动说破：*"题库里我发现两处脏数据：`fin_005` 的 ground_truth 有两个键前面多了空格，导致这道题的事实准确率恒为 0。这是我自己造的题自己踩的坑 —— 匹配前没做 strip。"*
> **一个能自己找出题库脏数据的候选人，比一个题库完美的候选人可信。**

### 3.4 第二个数据集：HotpotQA 变体（**要说清它有多小**）

[evaluation/benchmarks/hotpotqa.py](../evaluation/benchmarks/hotpotqa.py) 是适配器，**不是数据集**：
- 真实加载走 [hotpotqa.py:96](../evaluation/benchmarks/hotpotqa.py#L96) 的 `load_dataset("hotpot_qa", "distractor", ...)` → **需要联网 + 需要 `datasets` 包**。
- 离线时回退到内置 `_MOCK_DATA`（[hotpotqa.py:26](../evaluation/benchmarks/hotpotqa.py#L26)），**实测只有 5 道题**。

> **必须主动说**：*"HotpotQA 那一半我在离线环境只跑到**兜底数据 5 道**，真实数据集要联网下载。所以简历上写『HotpotQA 变体』准确的含义是**适配器写完了、在 5 道兜底题上验证过流程**，不是『在完整 HotpotQA 上评测过』。"*
> 这一句如果等面试官问"多少道题"，性质就从**诚实**变成**被拆穿**。

### 3.5 ⚠️ `--domain` 过滤目前选不中任何题

[evaluation/run_baseline.py:211](../evaluation/run_baseline.py#L211) 和 [scripts/run_ablation.py:293](../scripts/run_ablation.py#L293) 的参数是：

```python
parser.add_argument("--domain", type=str, default=None, choices=["tech", "med", "fin"], ...)
```

而数据里的 `domain` 值是**中文**（`"科技" / "医疗" / "金融"`…）。实测：`--domain tech` → **0 道题**；`--domain 科技` → **8 道题**。

**下游后果**（[run_baseline.py:232](../evaluation/run_baseline.py#L232)）：`get_questions` 返回空列表 → `scores = []` → [run_baseline.py:131](../evaluation/run_baseline.py#L131) `avg_score = 0.0` → **评测报告的五大系统全部报 0.0000，且不报错。** 一个静默的全零。

> 这条的价值：**它是「静默失败」的第二个实例**（第一个是规划器的二次解析静默降级）。能讲出"我的系统里有两处静默降级/静默全零"，说明你在**主动找这类 bug**，而不是只测 happy path。

### 3.6 口播（40 秒版）

> "数据集是我自建的 ResearchBench：**11 个领域 35 道题**。每题四个字段 —— `query` 是开放研究问题，`expected_topics` 是**覆盖面锚点**，`ground_truth` 是**关键事实锚点**，两者分管完备性和事实命中两个维度。领域上科技 8 题、医疗和金融各 6 题，其余领域 2 题左右。
> 我要说明三点局限：第一，`ground_truth` 是**启发式锚点不是标注答案**，`fact_accuracy` 只拿它的 key 做子串匹配；第二，题库里有已知脏数据，`fin_005` 两个键带前导空格导致该题事实准确率恒为 0；第三，另一个数据集的 HotpotQA 我离线只跑到 **5 道兜底题**，真实加载需要联网。"

---

## 4 · 「规则指标是怎么构建的」

### 4.1 五个指标逐条（判定式 + 行号 + 已知缺陷）

| # | 指标 | 判定逻辑 | 行号 | 已知缺陷 |
|---|---|---|---|---|
| 1 | `fact_accuracy` | `ground_truth` 的 **key** 逐个做 `key.lower() in report.lower()` 子串匹配，命中比例 | [L26-42](../evaluation/metrics/rule_based.py#L26-L42) | 只看 key 不看 value；前导空格即失效（§3.3） |
| 1b | `semantic_fact_accuracy` | 报告按 `[。！？\n]` 切块 → embedder 编码 → 与 ground_truth 算余弦，**阈值 0.65** 计为命中 | [L45-105](../evaluation/metrics/rule_based.py#L45-L105) | 注释自称「**面试强化版**」；**且它算完不进 composite**（§4.2） |
| 2 | `hallucination_rate` | 见 §2.1 | [L110-146](../evaluation/metrics/rule_based.py#L110-L146) | 4 个缺陷，见 §2.4 |
| 3 | `citation_coverage` | 非空行里含 `[N]` / `[来源:` / `【来源:` / `(来源:` / `https?://` / `arxiv\.org` 的行占比 | [L151-184](../evaluation/metrics/rule_based.py#L151-L184) | **容忍裸 URL**，而 `hallucination_rate` 的豁免只认括号 → 两个指标对"引用"的定义**不一致** |
| 4 | `logical_consistency` | 关键词矛盾对 + 连接词奖励（上限 0.1） | [L189-229](../evaluation/metrics/rule_based.py#L189-L229) | 子串嵌套误报 + 真实语料零区分度 |
| 5 | `comprehensiveness` | `expected_topics` 逐项子串匹配比例 | [L234-248](../evaluation/metrics/rule_based.py#L234-L248) | 只认字符串，同义改写不算覆盖 |

**加一个没进主流程的**：`efficiency_score`（[L290-303](../evaluation/metrics/rule_based.py#L290-L303)）= `max_bonus * sigmoid(slope*(target_turns - num_turns))`，target 8 轮、slope 0.5、上限 0.5。**它在 composite 的权重表里没有位置**（见下），是个孤立的辅助分。

**这五个指标的共同设计哲学（要能一句话说清）**：
> **全部是「零依赖的确定式启发式」** —— 不需要模型、不需要联网、不需要标注语料，跑一次是毫秒级且**完全可复现**。代价是**粗糙**：它们测的是"表面特征"，不是"语义正确性"。
> 所以这套指标的正确用法是**快速筛查 + 人工复核降采样**，不是最终质量判决 —— 判决要靠 Judge 层。

### 4.2 ⚠️ `composite_score` 的天花板 bug（**本节最硬的发现**）

权重表在 [rule_based.py:268-274](../evaluation/metrics/rule_based.py#L268-L274)：

```python
default_weights = {
    "factual_accuracy":    0.25,
    "logical_consistency": 0.20,
    "citation_coverage":   0.20,
    "bias":                0.20,
    "comprehensiveness":   0.15,
}
```

而 [L280-283](../evaluation/metrics/rule_based.py#L280-L283) 的累加方式是**「缺失的键按 0.0 计入分子，但权重照旧计入分母」**：

```python
for key, weight in w.items():
    value = metrics.get(key, 0.0)     # ← 键名对不上就是 0.0
    total_score += value * weight
    total_weight += weight            # ← 权重照样进分母
return total_score / total_weight
```

**于是「同一个 `composite_score`，三个调用方三个天花板」**（实测，[scripts/measure_eval_layer.py](../scripts/measure_eval_layer.py) §1）：

| 口径 | 谁这么调 | 传进去的键 | 全满分时 composite |
|---|---|---|---|
| A | [run_ablation.py:62-69](../scripts/run_ablation.py#L62-L69) | `factual_accuracy` / `logical_consistency` / `citation_coverage` / `bias` / `comprehensiveness` | **1.00** ✅ |
| B | [research_bench.py:490-499](../evaluation/benchmarks/research_bench.py#L490-L499) | `factual_accuracy_str` / `factual_accuracy_sem` / … | **0.75** ❌ |
| C | 直接拿 `rule_based.py` 自己的产出 | 没有 `bias` 这个键 | **0.80** ❌ |

**口径 B 的两个后果**：
1. **`factual_accuracy` 权重 0.25 被静默清零** —— 事实准确率最高的报告，也拿不到这 0.25。
2. **`semantic_fact_accuracy`（被注释称为「面试强化版」的那个指标）根本不在权重表里** —— 算了，但永远进不了总分。

**在真实报告上量化这个蒸发**（对照题 `tech_001`，仅用于暴露口径差；实测数据见 [outputs/eval_layer_metrics.json](../outputs/eval_layer_metrics.json) 的 `9_真实报告上的键名落差`）：

| 报告 | 口径A | 口径B | 蒸发 |
|---|---|---|---|
| reports2 | 0.7248 | 0.5373 | **−0.1875** |
| 其余四份 | — | — | 0.0000（因为该题事实命中为 0，两边都是 0） |

→ reports2 的 `factual_accuracy_str = 0.75`，于是 `0.25 × 0.75 = 0.1875` **凭空消失**。

> **怎么讲这件事（关键在措辞）**：不要说"我的评测有 bug"就完了，要说**发现过程**：
> *"我是在做指标区分度校验的时候发现的：当时想看看五维里哪一维对总分影响最大，就逐个把权重置零做敏感性分析 —— 结果发现把 `factual_accuracy` 权重从 0.25 调到 1.0，分数**完全不变**。一查是调用方传的键名是 `factual_accuracy_str`，而权重表里写的是 `factual_accuracy`，`metrics.get(key, 0.0)` 就静默吃了个 0。"
>
> **这段叙述的价值**：它证明你①做了敏感性分析（超出"跑通脚本"的水平）②会追查反常现象③知道 `dict.get` 的默认值是静默失败的常见来源。

**修法（要能说出来）**：把权重表的键与产出键统一（推荐在 `composite_score` 里加**键名不匹配即 raise**，或者返回 `(score, missing_keys)`），**而不是继续用 `0.0` 兜底** —— 因为兜底正是这个 bug 能藏这么久的原因。

### 4.3 ⚠️ 评测层有三份重复实现，其中两份没接在链路上

**实测**（[outputs/eval_layer_metrics.json](../outputs/eval_layer_metrics.json) §6）：

| 实现 | 位置 | 全仓引用情况 | 判定 |
|---|---|---|---|
| `RuleBasedMetrics` | [evaluation/metrics/rule_based.py](../evaluation/metrics/rule_based.py) | 外部 5 处 | ✅ **唯一在用的规则指标** |
| `ResearchMetrics` | [evaluation/metrics.py](../evaluation/metrics.py)（370 行） | **仅自身 `__main__` 自测** | ❌ **死实现** |
| `compute_composite_score` | [evaluation/metrics/composite.py](../evaluation/metrics/composite.py) | 仅 `__init__.py` 导出，**无人调用** | ❌ **死实现** |

`evaluation/metrics.py` 和 `evaluation/metrics/rule_based.py` **是同一套指标的两次实现**（`fact_accuracy` / `hallucination_rate` / `citation_coverage` / `logical_consistency` / `comprehensiveness` / `composite_score` 一一对应），且**默认权重表一模一样**。旧版 [metrics.py:239-245](../evaluation/metrics.py#L239-L245) vs 新版 [rule_based.py:268-274](../evaluation/metrics/rule_based.py#L268-L274)。

**另一份被弃用的设计**：[composite.py:33-57](../evaluation/metrics/composite.py#L33-L57) 走的是「规则 60% + Judge 40%」的两层合成，且 [L42-43](../evaluation/metrics/composite.py#L42-L43) 把 `rule_metrics.values()` **不加权求平均**：

```python
rule_vals = [v for v in rule_metrics.values() if isinstance(v, (int, float))]
rule_score = sum(rule_vals) / len(rule_vals)
```

→ **如果谁把这个 `metrics` 字典里塞进 `hallucination_rate`，它会当成"越高越好"参与平均** —— 这是一个极性反向的陷阱，因为这一层没有权重表来区分极性。

> 口播：*"评测层我清过一次，发现有两份历史实现没接在链路上：一份 370 行的 `evaluation/metrics.py` 是旧版指标，现在只有自己的自测在调它；另一份 `composite.py` 是「规则 60% + Judge 40%」的两层合成设计，后来没采用 —— 因为它对 `rule_metrics` 做的是**不加权平均**，一旦有人把 `hallucination_rate` 塞进去，就会按『越高越好』参与平均。现在实际在跑的是 `rule_based.py` 那一份。"*
>
> **这一段的作用**：展示你**做过代码清理审计**，并且能解释"为什么弃用某个设计"（不是"忘了"）。

### 4.4 口播（40 秒版）

> "规则层是 5 个指标：事实准确率（ground_truth 键的子串命中）、幻觉率（无引用的可疑表述密度）、引用覆盖率（含引用标记的行占比）、逻辑一致性（矛盾关键词对 + 连接词奖励）、完备性（expected_topics 命中率）。共同点是**零依赖确定式启发式** —— 不调模型、不联网、毫秒级、完全可复现；代价是只测表面特征，所以定位是**筛查 + 人工复核降采样**。
> 这层我审出几个问题：`composite_score` 的权重键和 `research_bench` 传的键名不一致，导致 **0.25 的事实准确率权重被静默清零、天花板只有 0.75**；`logical_consistency` 因为子串嵌套单句必然误报、整篇又全满分，目前没有鉴别力；还有两份历史实现没接在链路上。"

---

## 5 · 「评估覆盖了哪些内容」

### 5.1 覆盖矩阵（**这一张表要能画出来**）

| 层 | 实现 | 真跑过？ | 产物 | 覆盖范围 |
|---|---|---|---|---|
| **规则指标** | [rule_based.py](../evaluation/metrics/rule_based.py) | ✅ **跑过** | [report_metrics.json](../outputs/report_metrics.json) · [extra_metrics.json](../outputs/extra_metrics.json) · [eval_layer_metrics.json](../outputs/eval_layer_metrics.json) | 5 份真实报告，**1,581 句 / 1,150 段 / 104,971 字符** |
| **产出画像** | [measure_extra_metrics.py](../scripts/measure_extra_metrics.py) | ✅ **跑过** | [extra_metrics.json](../outputs/extra_metrics.json) | 字符分布、近重复率、跨报告相似度、代码静态分析 |
| **LLM-as-Judge** | [core/judge.py](../src/core/judge.py) · [evolution/judge.py:158](../src/evolution/judge.py#L158) | ❌ **没跑** | 无 | 实现完整，缺一次批量执行 |
| **消融实验** | [ablation.py](../src/core/ablation.py) · [run_ablation.py](../scripts/run_ablation.py) | ❌ **没跑** | 无 | 5 模块臂 + 4 轮数臂，**但 4 臂里只有 1 臂接线** |
| **统计检验** | [stats.py](../evaluation/metrics/stats.py) | ❌ **没跑** | 无 | Bootstrap CI / Cohen's d / t 检验，参数齐全 |
| **数据集** | [research_bench.py](../evaluation/benchmarks/research_bench.py) · [hotpotqa.py](../evaluation/benchmarks/hotpotqa.py) | 部分 | — | 35 题（自建）+ 5 题（HotpotQA 兜底） |

**一句话总结**：**「规则层真跑了、Judge 层没跑、消融层没跑也跑不通、统计层是空转的」。**

**这个总结要主动说。** 简历上「配套可复现评测体系」这句话，准确的含义是**「评测体系的实现是完整的、并且在规则层真实产出了结果」**，不是「三层都跑出了实验数据」。

### 5.2 三份 Judge 的关系（容易答混）

| Judge | 位置 | 维度 | 谁调用 |
|---|---|---|---|
| `LLMJudge` | [core/judge.py:24](../src/core/judge.py#L24) | `score_single` 五维（[L45-125](../src/core/judge.py#L45-L125)）；`compare_two` 四维 1-5 分（[L130-189](../src/core/judge.py#L130-L189)） | 通用评测 |
| `Judge`（Ensemble） | [evolution/judge.py:158](../src/evolution/judge.py#L158) | M6 进化的裁判 | 只经 [engine.py](../src/evolution/engine.py) / [run_evolution.py](../scripts/run_evolution.py) |
| `JudgeBasedMetrics` | [metrics/judge_based.py:18](../evaluation/metrics/judge_based.py#L18) | 无自己逻辑，**直接委托**给 `LLMJudge` | 指标门面 |

**两个必须知道的缺陷**：
1. **Judge 也只看前 4000 字符** —— [core/judge.py:78](../src/core/judge.py#L78) 的 `report[:4000]`（对比报告更长，[L153](../src/core/judge.py#L153)/[L156](../src/core/judge.py#L156) 是 `[:3000]`）。而报告平均 **20,994 字符** → **Judge 实际只看到约 19% 的正文**。
2. **平均分把 `overall` 重复计入** —— [core/judge.py:113](../src/core/judge.py#L113) 把 `overall` 从 `dimensions` 里剔掉，但 [L107](../src/core/judge.py#L107) 算 `scores` 时**把 `overall` 一起放进去求均值** → `overall` 既作为独立维度参与、又隐含了其他四维的信息，属于**重复计权**。

> **这两条合起来是很强的口播**：*"Judge 层的两个偏差我都知道：一是它只读前 4000 字符，也就是平均报告的 19%，所以它的评分代表的是报告**开头部分**的质量；二是它的平均分算了 `overall` 这一维，而 `overall` 本身又是其他四维的综合，属于重复计权。真要用 Judge 分数做结论，这两处都得先修。"*

### 5.3 统计检验：参数齐全，但 **n=5 报不了区间**

实现见 [stats.py](../evaluation/metrics/stats.py)：`bootstrap_ci_paired`（[L20-62](../evaluation/metrics/stats.py#L20-L62)）、`bootstrap_ci_two_sample`（[L65-105](../evaluation/metrics/stats.py#L65-L105)）、`cohens_d`（[L108-115](../evaluation/metrics/stats.py#L108-L115)）、`paired_t_test`（[L118-132](../evaluation/metrics/stats.py#L118-L132)）。

**两个坑**：

**坑 ① Bootstrap 没有固定随机种子** —— [stats.py:44](../evaluation/metrics/stats.py#L44) 直接 `np.random.choice(...)`，没传 `seed`。→ **同一个输入跑两次 CI 端点不同**，与"可复现"直接冲突。

**坑 ② 报告侧只有 n=5，不足以报置信区间** —— 规则层实测只有 5 份报告。Bootstrap 在 n=5 上重采样，CI 宽度会大到没有信息量。
**所以：5 份报告的数字只能报「均值/中位/极差」，不能报「95% CI」** —— 报了就是误导。

> 这是**纪律问题**，和 [面试知识库-项目二-DeepResearch.md](面试知识库-项目二-DeepResearch.md) §2.4 里「段落近重复率 0.000 不能报（缺对照组）」是同一条原则：**没有对照 / 样本不足，就不下结论。**

### 5.4 🚨 红线：`analyze_ablation.py` 会**生成假的实验数据并画成图**

[evaluation/analyze_ablation.py:246-254](../evaluation/analyze_ablation.py#L246-L254)：

```python
# 如果没有提供文件，生成示例数据进行演示
if adv_report is None and evo_report is None:
    print("[main] 未提供输入数据，使用示例数据生成演示图表...")
    adv_report = analyzer.analyze_adversarial_rounds(
        {"adv_0": 0.62, "adv_1": 0.71, "adv_2": 0.78, "adv_3": 0.80}
    )
    evo_report = analyzer.analyze_evolution(
        {"evo_off": 0.68, "evo_on": 0.76}
    )
```

**这 6 个数字是硬编码的，一个都没测过**，但它们会被渲染成 dpi=300 的对比图 —— 图上会呈现「对抗轮数增加 → 分数从 0.62 单调升到 0.80」「开启进化 → 0.68 升到 0.76」。**图看起来完全是真实实验结果。**

> 🚨 **绝对不要展示这两张图。** 这不是"数据不严谨"，是**展示未经测量的数据**。
> **主动说破反而是强加分项**：*"我还发现一个必须处理的问题：`analyze_ablation.py` 在没有输入文件时，会用硬编码的示例数值画图 —— 图上会是「对抗轮数越多分数越高」的漂亮曲线，但那些数一个都没测过。我已经标记了，要么删掉这个 fallback 改成直接报错，要么在图上强制标注『DEMO DATA』。"*
>
> **为什么要主动讲**：面试官如果自己跑到这个脚本、看到那两张图，然后意识到数字是假的 —— **你整份简历的可信度都会崩**。主动说破的成本极低，收益极高。

### 5.5 口播（覆盖内容 · 40 秒版）

> "分三层说，我按**真跑过的**和**只写完实现的**分开讲。
> **真跑过的**是规则层和产出画像：5 份真实报告，1,581 句、1,150 段、10.5 万字符，规则口径幻觉率 3.34%，跨报告相似度 0.272，都有 JSON 产物。
> **只写完实现没跑的**是两个：LLM-as-Judge 和消融实验 —— 消融那边还发现 4 个臂里只有 1 个的开关真的接线了，所以我没把它跑出来的数当结果。
> **统计层**参数齐全（Bootstrap CI、Cohen's d、配对 t 检验），但报告侧只有 n=5，我不会拿它报置信区间。
> 另外我**不建议**你运行 `analyze_ablation.py` 不带参数 —— 它会用硬编码的示例数画图，看起来像真结果。"

---

## 6 · 新增口径陷阱（编号接 [面试知识库-项目二-DeepResearch.md](面试知识库-项目二-DeepResearch.md) §2.5 的 1–11）

| # | 陷阱 | 一句话 | 证据 |
|---|---|---|---|
| **12** | **`composite_score` 天花板 0.75** | 权重键 `factual_accuracy` 与调用方传的 `factual_accuracy_str` 名字不一致，0.25 权重被 `metrics.get(key, 0.0)` 静默清零；`semantic_fact_accuracy` 根本不在权重表里 | [rule_based.py:268-283](../evaluation/metrics/rule_based.py#L268-L283) · [research_bench.py:490-499](../evaluation/benchmarks/research_bench.py#L490-L499) |
| **13** | **消融 4 臂只有 1 臂接线** | `compressor.enable_multilevel` 与 `memory.enabled` 在 `src/` 无读点，`evolution.enabled` 只驱动日志 → 这三臂的产出与 `full` 相同 | [ablation.py:34-40](../src/core/ablation.py#L34-L40) · [runner.py:196-197](../src/core/runner.py#L196-L197) |
| **14** | **`average_composite_score` 是成功率** | [ablation.py:114](../src/core/ablation.py#L114) 写死 `1.0`、[L125](../src/core/ablation.py#L125) 写死 `0.0`，字段名却叫 composite | [ablation.py:131](../src/core/ablation.py#L131) |
| **15** | 🚨 **`analyze_ablation.py` 造假图** | 无输入时用 6 个硬编码数值渲染对比图 | [analyze_ablation.py:246-254](../evaluation/analyze_ablation.py#L246-L254) |
| **16** | **没有单 agent 基线** | 全仓无一臂是"单 agent"，所以"多 agent 更好"无量化证据 | [ablation.py:34-40](../src/core/ablation.py#L34-L40) |
| **17** | **幻觉率两个口径** | 逐份比率平均 3.34% ≠ 池化 4.24% | [outputs/report_metrics.json](../outputs/report_metrics.json) |
| **18** | **豁免正则 `【` 无配对** | 闭括号类缺 `】` → 规范引用格式反被判为幻觉 | [rule_based.py:140](../evaluation/metrics/rule_based.py#L140) |
| **19** | **`--domain` 全零静默** | choices 是英文、数据是中文 → 选 0 题 → 报告全 0.0000 且不报错 | [run_baseline.py:211](../evaluation/run_baseline.py#L211) · [run_baseline.py:232](../evaluation/run_baseline.py#L232) |

**陷阱 7（原版「没有任何实验结果 JSON」需要更新）**：现在**规则层有产物**（`report_metrics.json` / `extra_metrics.json` / `eval_layer_metrics.json`），所以「幻觉率 3.34%」「平均 2.1 万字」「跨报告相似度 0.272」**都可以报**。但**消融层依然一份结果都没有** —— 所以"模块有没有用"仍然不能报。

---

## 7 · 速记卡（面试前扫一遍）

| 问 | 答 |
|---|---|
| 多 agent 好在哪里 | 讲**失效模式差异**（全损 vs 局部降级）；**不给数字**，因为没有单 agent 基线 |
| 为什么没有单 agent 基线 | 做的是**模块级消融**不是**架构级对照**；补法：DAG 压成单节点 + `max_concurrent=1` |
| 消融跑了吗 | **没跑**。且 4 臂里 `no_compressor`/`no_memory` 未接线、`no_evolution` 是空壳，只有 `no_adversarial` 生效 |
| `average_composite_score` 是什么 | **成功率**（1.0/0.0 占位求和平均），不是质量分 |
| 幻觉率多少 | **3.34%**（5 份逐份比率算术平均）/ 池化 **4.24%** / 1,581 句里 67 句 |
| 幻觉率是什么 | **无引用的可疑表述密度**，不是事实错误率；作用是**人工复核降采样** |
| 幻觉率构成 | **95.5% 来自「带单位数字」一条正则**；模糊引用模式命中 **0** 次 |
| 幻觉率几个缺陷 | 豁免正则 `【` 无配对；说明性括号被误豁免；`logical_consistency` 零区分度 |
| 数据集多大 | **11 领域 35 题**（注释写 20 是过期）；HotpotQA **只有 5 题兜底** |
| 数据集脏数据 | `fin_005` 两个 ground_truth 键带**前导空格** → 该题事实准确率恒 0 |
| 规则指标几个 | **5 个** + 一个没接进流程的 `efficiency_score` |
| 规则指标共同点 | **零依赖确定式启发式**；毫秒级可复现；测表面特征 → 定位是筛查 |
| composite 天花板 | **0.75**（research_bench 口径），因 `factual_accuracy` 权重静默清零 |
| 怎么发现的 | **权重敏感性分析**：把 `factual_accuracy` 权重调到 1.0，分数不变 |
| 评测层几份实现 | 3 份，**2 份没接在链路上**（[metrics.py](../evaluation/metrics.py) 370 行死实现、[composite.py](../evaluation/metrics/composite.py) 未被调用） |
| Judge 偏差 | 只读**前 4000 字符**（≈平均报告的 19%）；平均分**重复计入 `overall`** |
| Bootstrap 的坑 | **没固定 seed** → 不可复现；且 **n=5 报不了 CI** |
| 🚨 红线 | **不要展示 `analyze_ablation.py` 的图**，那是硬编码假数据 |
| `--domain` | choices 英文 vs 数据中文 → **永远选 0 题**，下游静默全零 |

---

## 8 · 跟读自检（能顺口答才算过）

1. 面试官问「多 agent 比单 agent 好在哪里」，你的**第一句**是什么？
2. 为什么不能报「多 agent 提升 X%」？缺的是什么（**一个 arm 还是一轮跑**）？
3. 消融实验的五臂分别是什么？哪几臂的开关没接线？
4. `average_composite_score` 实际是什么含义？
5. 幻觉率的分子分母分别是什么？豁免条件是什么？
6. 幻觉率 3.34% 和 4.24% 差在哪？
7. 为什么说幻觉率「不是事实错误率」？它的正确用途是什么？
8. 豁免正则的 bug 是什么？（**开括号类有几个字符、闭括号类有几个**）
9. ResearchBench 每题有哪四个字段？两个锚点分别服务哪个指标？
10. 题库的真实题数是多少？注释写的是多少？哪个领域的题最多？
11. `fin_005` 的脏数据是什么？后果是什么？
12. HotpotQA 那一半你实际跑了多少题？为什么？
13. 五个规则指标的**共同设计哲学**是什么？代价是什么？
14. `composite_score` 的天花板 bug 是怎么产生的？（**说清 `metrics.get(key, 0.0)` 和 `total_weight` 两行**）
15. 你是怎么发现这个 bug 的？
16. 三个天花板分别是多少？分别是谁调出来的？
17. Judge 层的两个偏差是什么？各在哪一行？
18. Bootstrap 的两个坑是什么？
19. 为什么 n=5 不能报 95% CI？
20. 🚨 哪一个脚本绝对不能演示？为什么？

---

## 9 · 三处讲不清时的诚实路径（**不许编**）

**① 被问"多 agent 到底好多少"**
→ 不编数字。答："**我没有这个数，因为我没有单 agent 的对照 arm。** 我能给的是机制层论证 + 产出侧画像，你要的话我可以讲这两块。" 然后**主动转进** §1.2 的三种工况。**转进要快，不要停在"对不起"。**

**② 被问"消融跑出来"**
→ 不编曲线。答："**没跑。** 而且我审计后知道**有 4 条臂现在跑出来也不能用** —— 开关没接线，差异只会是采样噪声。要跑得先修接线。" 然后给**修法**（§1.4 追问链第 2 行）。

**③ 被问某个具体指标值（比如 Judge 分数、各级压缩比）**
→ 只在三个指标 JSON（[report_metrics.json](../outputs/report_metrics.json) / [extra_metrics.json](../outputs/extra_metrics.json) / [eval_layer_metrics.json](../outputs/eval_layer_metrics.json)）里出现过的数字才报。**没在 JSON 里的，一律说"我没有可复现的测量"**。
报数字时带上出处，例如："幻觉率 3.34%，出自 `outputs/report_metrics.json`，5 份报告逐份比率的算术平均。"

> **这三条的共同点**：都是**用"缺什么 + 怎么补"替代"编一个数"**。这是本文件存在的全部意义。

---

## 10 · 与其它文件的关系

| 文件 | 关系 |
|---|---|
| [面试知识库-项目二-DeepResearch.md](面试知识库-项目二-DeepResearch.md) | 架构 + 全套口径陷阱的**主文件**；本文档是它的评测层深挖，新增陷阱 **12–19** 已回填 |
| [03-编排引擎.md](03-编排引擎.md) | 三级降级的完整带读（§1.2 工况 A 的细节在那） |
| [04-规划器与JSON解析.md](04-规划器与JSON解析.md) | 「静默降级」的第一个实例；与本文档 §3.5 的静默全零是同一类 bug |
| [简历修订对照与追问索引.md](简历修订对照与追问索引.md) | 简历逐句体检；评测句（第 ⑤ 句）的修订理由在那里 |

**三份可复现的测量脚本**（都是零 API、零联网）：

```
scripts/measure_report_metrics.py   → outputs/report_metrics.json     指标逐份原始值
scripts/measure_extra_metrics.py    → outputs/extra_metrics.json      产出画像 + 代码静态分析
scripts/measure_eval_layer.py       → outputs/eval_layer_metrics.json 本篇全部结论  ← 新增
```

**复现命令**（Windows 下必须带 `PYTHONIOENCODING`，否则 GBK 控制台会崩）：

```bash
PYTHONIOENCODING=utf-8 python scripts/measure_eval_layer.py --json outputs/eval_layer_metrics.json
```

---

> **下一讲预告**：`05-上下文压缩与记忆.md`（M3/M4）—— 三级压缩各自做什么、阈值是**占用率**不是相似度、保底策略，锚点 [compressor.py:92-168](../src/compressor/compressor.py#L92-L168)。
> **上一篇**：[04-规划器与JSON解析.md](04-规划器与JSON解析.md)
