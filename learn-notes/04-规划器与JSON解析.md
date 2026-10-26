# 04 · 规划器与 JSON 解析【带读讲义 · 第 4 讲】

> 本讲义属于"带读模式"系列:每讲 = 大白话 → 带读代码 → 对照样例 → 跟读复述。先读 [01_项目全貌&学习路线](01_项目全貌&学习路线.md) 建立前序认知,再读本讲义。
> 上一讲 [03-编排引擎](03-编排引擎.md) 讲的是**拿到计划之后**怎么跑:九个状态、三级降级、Semaphore 并发、DAG 分层。
> 本讲往回退一层,讲**计划本身从哪来**:规划器怎么把一句话拆成 DAG,怎么"逼"LLM 吐出机器能读的 JSON,以及 LLM 不听话时那套解析兜底链的**真实边界**。
> 本讲目标:**说得出**规划器在整个链路里的位置、**背得出**四步 JSON 清洗的顺序、**讲得清**这套解析在什么输入下会失效(而且失效时是大声报错还是悄悄降级)。
> 使用方式:VS Code 打开项目文件夹 → 本 md 按 `Ctrl+Shift+V` 预览 → 点击蓝色链接直接跳代码。行号已与当前代码核对。

---

## 📖 阅读导航

| 优先级 | 章节 | 时间 | 为什么 |
|---|---|---|---|
| 🔴 必读 | 第 1 步 大白话:规划器在解决什么问题 | 6 min | 没有这个"为什么",后面所有代码都是死记硬背 |
| 🔴 必读 | 第 2 步 带读 prompt:怎么"逼"LLM 吐 JSON | 12 min | **面试最高频考点**。prompt 是你唯一能"控制" LLM 的地方,讲得出规则编号才算真做过 |
| 🔴 必读 | 第 4 步 `generate_plan` 主流程 | 8 min | 全链路入口,六行代码串起 policy / 原始 JSON / 预算追踪 |
| 🔴 必读 | 第 5 步 `_parse_plan` 四步清洗(核心) | 18 min | 本讲**技术含金量最高**的一段,简历"多级解析兜底链"指的就是它 |
| 🔴 必读 | 第 7 步 第二次解析:静默降级成占位符 | 10 min | **本讲最重要的盲区**。链路"跑通了",但子任务描述全丢 |
| 🔴 必读 | 第 9 步 replan:保留什么、丢掉什么 | 8 min | 和保存结果、清空结果直接相关,追问必到 |
| 🔴 必读 | 第 10 步 四处实现真相 | 8 min | 口径陷阱集中区,背下来面试直接得分 |
| 🟡 选读 | 第 3 步 "更激进拆题"分支 | 5 min | 逻辑简单,但藏着三处数量口径冲突 |
| 🟡 选读 | 第 6 步 `_deserialize_subtask` 字段兜底 | 6 min | 字段清单值得过一遍,重点是 `task_type` 静默降级 |
| 🟡 选读 | 第 8 步 DAG 校验与失败后果 | 6 min | 和第 3 讲的 DAG 部分有重叠,只补"规划失败怎么办" |
| ⚪ 可跳过 | 第 0 步 语法工具包 | 5 min | Python 基础一般就扫一眼,够用就行 |
| 🔴 必读 | 第 12 步 跟读复述 + 面试速记卡 | 15 min | 复述一遍比读三遍有用 |

---

## 第 0 步 · Python 语法工具包（第 3 包）

> 第 1 包(枚举、`@classmethod`、`dict[...]` 注解、`copy.deepcopy`、集合 `&`、f-string `.2f`)见 [02 第 0 步](02-核心模块细节笔记.md)。
> 第 2 包(`asyncio` 那一套、`deque`、`defaultdict`、`raise X from e`、`field(default_factory=list)`)见 [03 第 0 步](03-编排引擎.md#L29-L50)。**`raise X from e` 和 `field(default_factory=list)` 本讲会反复用到,但已在第 2 包讲过,不再重复。**
> 本包是**"清洗脏文本"那一套**——正则 + JSON + 字符串切片。看不懂这 8 个,第 5 步的代码就会卡住。

| 语法 | 例子 | 人话 |
|---|---|---|
| `re.search(pat, s, re.DOTALL)` | `re.search(r"(\{.*\})", raw, re.DOTALL)` | **找第一个匹配**。`re.DOTALL` 让 `.` 也能匹配换行符——不加的话,多行 JSON 一定匹配不到 |
| `.*?`(非贪婪) | `` r"```(?:json)?\s*(\{.*?\})\s*```" `` | **吃到能收手就收手**。`.*` 会一路吃到最后一个 `}`,把两个 JSON 块粘成一个;`.*?` 吃到第一个 `}` 就停 |
| `(?:...)` | `(?:json)?` | **只分组不捕获**。`(?:json)?` = "json 这三个字可有可无",但不给它编号 |
| `re.sub(pat, repl, s)` | `re.sub(r",(\s*[}\]])", r"\1", raw)` | **查找并替换**。`\1` 是"把第 1 个括号里抓到的东西原样放回来"——这里用来**删掉逗号但保留后面的括号** |
| `[}\]` 和 `\s` | `r",(\s*[}\]])"` | **字符类**。`[}\]` = "`}` 或 `]` 之一"(`}` 在正则里有特殊含义,所以要写 `\}`);`\s*` = 任意个空白 |
| `str.splitlines()` | `raw.splitlines()` | **按行切开,得到一个列表**。比 `split("\n")` 更懂事,`\r\n` 也认 |
| `str.startswith()` / 切片 | `lines = lines[1:]` | **掐头去尾**。`lines[1:]` = 去掉第一行;`lines[:-1]` = 去掉最后一行 |
| `json.loads` / `json.dumps(..., ensure_ascii=False)` | `json.dumps(x, ensure_ascii=False, indent=2)` | **JSON 字符串 ⇄ Python 对象**。`ensure_ascii=False` 才能让中文原样输出成中文,否则变成 `为` 这种转义 |

### 补充:三个本讲会用到但容易看错的写法

```python
# 1. 带默认值的字典取值 + 立刻转类型
int(item.get("timeout_seconds", 120))
#   ^^^ item.get 拿到的可能是字符串 "120",也可能是缺失。int() 是"类型收口"

# 2. 字典推导式 + enumerate(一次遍历同时拿到下标和元素)
{item.get("task_id", f"task_{i}"): self._deserialize_subtask(item)
 for i, item in enumerate(sub_tasks_raw)}
#   ^ 下标 i 只在 key 缺失时用来拼一个兜底 id:task_0 / task_1 / ...

# 3. f-string 里塞三元表达式
f"... Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
#   ^ 是字典就列键名,不是字典就报类型。为了不因为报错信息本身再抛一次异常
```

> 💡 **一句话记住 `.*?` 的价值**:LLM 经常把 JSON 包在 ```` ```json ... ``` ```` 里,而且**可能吐两个代码块**。贪婪匹配会把两个块连成一段无法解析的文本,非贪婪才能只抓到第一个完整的 `{...}`。

---

## 第 1 步 · 大白话:规划器到底在解决什么问题

### 1.1 先讲问题:一句话研究问题,机器没法直接执行

用户输入是一句自然语言:

> "对比 GPT-4o、Claude 3.5 和 DeepSeek-V3 的推理能力,并给出选型建议。"

编排引擎拿到这句话**没法干活**。它需要的是:

- **几件事**(而不是一件事):查 A 的 benchmark、查 B 的 benchmark、查 C 的 benchmark、查第三方榜单、做对比
- **每件事谁先谁后**:"做对比"必须在"查完三家"之后
- **哪些能一起干**:查 A / 查 B / 查 C 互相不依赖,可以同时发出去
- **每件事的指令是什么**:不是"查 A",而是"查 A 官方公布的 benchmark 分数,重点看 GSM8K / MMLU / HumanEval"

所以**规划器(Planner)的职责就一句话**:

> **把一句自然语言问题,翻译成一张带依赖关系的任务图(DAG)。**

### 1.2 为什么是 DAG,不是列表

如果规划器只输出一个**列表**:

```text
1. 查 GPT-4o 的 benchmark
2. 查 Claude 3.5 的 benchmark
3. 查 DeepSeek-V3 的 benchmark
4. 对比三家
```

那编排器只能**串行**跑:1 跑完跑 2,2 跑完跑 3…… 四件事里前三件本来可以同时做,白白慢 3 倍。

DAG(有向无环图)多了一样东西:**边**。`A → D` 表示"D 依赖 A"。有了边,编排器就能算出"哪几个任务在同一层、可以并发"。这一步在第 3 讲讲过:

👉 **本地阅读代码**:[dag.py:115-149 `get_parallel_groups()(按层分组,同层可并发)](../src/planner/dag.py#L115-L149)
👉 **本地阅读代码**:[orchestrator.py:238-239 Semaphore + 按层 gather](../src/orchestrator/orchestrator.py#L238-L239)

> 📌 **本讲和第 3 讲的分工**:第 3 讲管"**拿到 DAG 之后**怎么调度";本讲管"**DAG 怎么被造出来** + 造歪了怎么办"。
> 两个讲合起来,才是简历里"多智能体异步编排 + DAG 拓扑并发"的完整故事。

### 1.3 规划器的三段结构

`src/planner/` 目录下只有三个文件,分工非常清楚:

| 文件 | 职责 | 本讲覆盖 |
|---|---|---|
| [planner.py](../src/planner/planner.py) | **核心**:prompt 构造 + LLM 调用 + JSON 解析 + 重规划 | 第 2~9 步 |
| [dag.py](../src/planner/dag.py) | DAG 数据结构 + Kahn 拓扑排序 + 分层 | 第 8 步(第 3 讲已详讲) |
| [budget_tracker.py](../src/planner/budget_tracker.py) | token 消耗计数 | 第 4 步 |

整个规划过程就是一条流水线:

```text
  query
    │
    ├─① _build_prompt()          拼 prompt(模板 + "更激进拆题"提示)
    │
    ├─② self.policy(messages)    真正调 LLM,拿回一段**文本**
    │                            ← 注意:到这里为止,还是不可信的字符串
    ├─③ budget_tracker.track()   记一笔 token
    │
    ├─④ _parse_plan()            四步清洗 + 兜底 → 变成 DAG 对象
    │
    └─⑤ get_task_map_from_dag()  **再来一遍**解析 → 变成 task_id → SubTask 字典
```

> ⚠️ **第 ④⑤ 步是两遍独立解析,这是本讲最重要的结构事实**。第 7 步会讲:这两遍**逻辑几乎一样但不完全一样**,而差异会导致一次静默降级。

### 1.4 规划器最难的地方不是"拆题",是"让 LLM 输出能被程序读"

直觉上你会觉得:规划器难在"怎么拆得准"。

**实际上工程上最难的是:LLM 是概率模型,它输出的东西不保证是合法 JSON。**

它会:

- 把 JSON 包在 ```` ```json ... ``` ```` 里(哪怕你明确写了 "no markdown")
- 在 JSON 后面加一句 "以上计划覆盖了该问题的三个主要方面。"
- 在 JSON 里加 `//` 注释(JSON 标准**不支持**注释)
- 数组最后一个元素后面留个逗号(JSON 标准**也不支持** trailing comma)
- 把 `task_type` 写成 `SEARCH` / `search_web` / `web_search`,而你的枚举只认 `search`
- 直接超时/挂掉,一个字符都不返回

**这六种都在真实 LLM 上出现过。** 所以规划器真正的"技术含量"落在一个地方:

> **你能容忍多脏的输入,还能救回来;救不回来的时候,是大声报错还是悄悄降级。**

这是本讲的主线。

---

## 第 2 步 · 带读 prompt:怎么"逼"LLM 吐 JSON

👉 **本地阅读代码**:[planner.py:34-76 `INITIAL_PLAN_PROMPT(初始规划提示词)](../src/planner/planner.py#L34-L76)

prompt 是规划器里**唯一一处你能直接控制 LLM 行为的地方**。面试问"你怎么保证 LLM 输出可解析",标准答案就是拿这个 prompt 逐块讲。

### 2.1 第 1 块:角色 + 任务(第 35 行)

```text
You are an expert research planner. Your task is to decompose a complex
research question into a directed acyclic graph (DAG) of sub-tasks.
```

**【人话翻译】** 这就是"给模型一个身份"。为什么有用:同一个模型,你说"你是研究员"和你说"你是规划器,输出 DAG",对**输出格式**的遵从一个明显更好。这是零成本的格式约束手段。

**注意这里已经埋了关键词**:`directed acyclic graph`。你在 prompt 里提了"无环",模型才不会生成 `A 依赖 B、B 依赖 A` 这种输出。**规则不是靠解析器兜的,第一条防线是在 prompt 里说清楚。**

👉 **第二道防线在解析器里**:[planner.py:317-320 拓扑排序检测环](../src/planner/planner.py#L317-L320)(第 8 步详讲)

### 2.2 第 2 块:输出格式(第 40-56 行)—— 逐字段讲

```text
## Output Format
Return a JSON object with this exact structure (no markdown, no extra text):
{
  "sub_tasks": [
    {
      "task_id": "task_1",
      "task_type": "search",
      "description": "What is ...",
      "dependencies": [],
      "context_keys": [],
      "timeout_seconds": 120,
      "priority": 1,
      "expected_type": "factual",
      "search_hints": ["keyword1", "keyword2"]
    }
  ]
}
```

**【人话翻译】** 这叫做 **"给一个好样例比写十条规则管用"**。

注意两个设计细节:

1. **`(no markdown, no extra text)` 是硬要求,但你知道它会被违反**。写这句不是因为它一定生效,而是因为**让"违反"变成模型的错**,而不是你的 prompt 没说清。实测里模型仍然会加代码块——所以第 ⑤ 步的解析兜底**必须存在**,不能指望 prompt。
2. **`timeout_seconds: 120` 和 `priority: 1` 给了具体数字**。不给数字,模型可能填 `"timeout": "2 minutes"` 这种没法直接用的值。

下面这张表是这八个字段的**真实去处**(每个都追到消费点,不是猜的):

| 字段 | 类型 | 谁在读它 | 读它干什么 |
|---|---|---|---|
| `task_id` | str | [dag.py:39-55](../src/planner/dag.py#L39-L55) | DAG 的节点名,也是 `dependencies` 的引用目标 |
| `task_type` | enum | [orchestrator.py:260](../src/orchestrator/orchestrator.py#L260) | `agent_pool.get_agent(subtask.task_type)` —— **决定派哪类 Agent** |
| `description` | str | 子 Agent 的 prompt | 这就是子 Agent 收到的"任务指令",**质量直接决定检索质量** |
| `dependencies` | list[str] | [dag.py:43-55](../src/planner/dag.py#L43-L55) | 转成边,决定执行层级与先后 |
| `context_keys` | list[str] | [orchestrator.py:590-599 `_build_task_context()`](../src/orchestrator/orchestrator.py#L590-L599) | 从共享 Memory 里按 key 取上下文 |
| `timeout_seconds` | int | [orchestrator.py:263-264](../src/orchestrator/orchestrator.py#L263-L264) | `asyncio.wait_for(..., timeout=...)` 的单任务超时 |
| `priority` | int | **读不到消费点** | 序列化进 `SubTask`,但**全仓库没有一处代码按它排序** |
| `expected_type` | str | [researcher.py:377](../src/agents/researcher.py#L377) | 拼进子 Agent 的 prompt:`"Expected output: {expected_type}"` |
| `search_hints` | list[str] | 子 Agent | 额外关键词提示 |

> ⚠️ **这里已经出现第一个口径陷阱**:`priority` 这个字段**在 prompt 里被要求输出、在 `SubTask` 里被存下来、在占位符重建时还会被复制一遍,但没有任何代码用它做任何决策**。
> 全仓库唯一按"优先级"排序的地方是 [blue_agent.py:206](../src/adversarial/blue_agent.py#L206),那排的是**对抗环节的 issue**,和 `SubTask.priority` 完全无关。
> 面试官如果问"你的子任务优先级调度怎么实现的",**答案是"字段有,调度没做"**,而不是硬编一个不存在的机制。
>
> 💡 **注意 `expected_type` 恰恰相反,它是真的被用上了** —— 它进了子 Agent 的 prompt。
> **这两个字段看起来对称,实际一个死一个活。** 这就是为什么每个字段都要**追到消费点**,不能靠"看起来像占位"来判断。这种较真是面试里很吃分的细节。

### 2.3 第 3 块:十条规则(第 58-68 行)—— 只挑三条重点讲

```text
## Rules
1. task_type must be one of: search, analyze, verify
2. dependencies must reference existing task_id values
3. The graph must be a DAG (no cycles)
4. Generate 3 to 8 sub_tasks
5. More fundamental/information-gathering tasks should have fewer dependencies
6. Verification tasks should depend on analysis tasks
7. Use concise but clear descriptions
8. CRITICAL — RELEVANCE CONSTRAINT: Each sub-task description MUST directly
   address the research question. ...
9. The search_hints field MUST contain keywords directly from the query. ...
10. Prefer specific, actionable queries over broad, vague ones.
```

十条里,只有三条是"格式约束",其余七条是"质量约束"。**规则 1/2/3 是给解析器兜底的,规则 4~10 是给结果质量兜底的。**

#### 规则 1:枚举白名单(第 59 行)

```text
1. task_type must be one of: search, analyze, verify
```

**【人话翻译】** 这是**封闭值域**。和一个枚举对齐:

👉 **本地阅读代码**:[schemas.py:48-52 `TaskType(三个取值)](../src/orchestrator/schemas.py#L48-L52)

但**照样会违反**——模型能编出 `SEARCH`、`web_search` 这些。所以还有第二道防线:

👉 **本地阅读代码**:[planner.py:326-330 `task_type` 非法时静默降级为 SEARCH](../src/planner/planner.py#L326-L330)

#### 规则 4:子任务数量(第 62 行)—— 这里是第一个坑

```text
4. Generate 3 to 8 sub_tasks
```

**记住 "3 to 8" 这个数字**,第 3 步会告诉你:**同一个函数里还有另两个数字,而且互相打架**。

#### 规则 8:相关性约束(第 66 行)—— 这条规则为什么存在

```text
8. CRITICAL — RELEVANCE CONSTRAINT: Each sub-task description MUST directly
   address the research question. If the user asks about 'internship/job
   application', do NOT generate tasks about 'technology trends',
   'annual news summary', or 'science breakthroughs'.
```

**【人话翻译】** 这条规则读起来很怪——为什么一个通用研究 Agent 的 prompt 里,会专门写"实习/求职"?

**因为这是踩过坑之后补的。** 这是**分析**(源码不会写动机,我按反例内容推断):这类小模型 + 通用研究模板的组合,有一个非常典型的失败模式 —— 你问"怎么找大厂实习",它给你生成"2025 年技术趋势""年度科技新闻盘点""最新 AI 突破"。看着每一条都像正经研究任务,**但和你的问题毫无关系**。这就是**主题漂移**。

对应证据是紧接着的反例块:

👉 **本地阅读代码**:[planner.py:70-72 Anti-examples(BAD / GOOD 对照)](../src/planner/planner.py#L70-L72)

```text
## Anti-examples (DO NOT do this)
- Query: "How to find an internship at a big tech company"
  → BAD tasks: "2025 technology trends", "annual science news", "latest AI breakthroughs"
- Query: "How to prepare for post-training LLM engineer internship"
  → GOOD tasks: "Big tech post-training intern JD requirements",
                "LLM post-training intern interview experience",
                "Resume tips for LLM algorithm intern"
```

**【人话翻译】** 这叫 **few-shot 反例**。为什么反例比正例有用:你只写"要相关",模型的理解是模糊的;你写"**不相关长这样**",模型才知道边界在哪。

> 💬 **这里要特别提醒你**:这套反例的措辞(`internship/job application`、`post-training engineer internship`)**明显带个人色彩**,读起来像作者自己在找工作那阵子加进去的。
> **这不是坏事,但你要准备好被问**。面试官看到这段可能会随口问一句"这个反例怎么来的"。
> 诚实且加分的答法:**"这是我做测试时发现的主题漂移问题。我拿自己的求职类 query 试,发现规划器会跑偏到泛科技新闻上,所以在 prompt 里补了约束和反例。这类约束是踩坑后加的,不是一开始设计出来的。"**
> ❌ 不要答"这是标准的 prompt 工程实践"——那是空话,面试官一追问细节就崩。

---

## 第 3 步 · "更激进拆题":`_build_prompt` 的两个分支

👉 **本地阅读代码**:[planner.py:221-240 `_build_prompt()(首次规划 vs 有记忆)](../src/planner/planner.py#L221-L240)

```python
def _build_prompt(self, query: str, memory: str) -> str:
    """构建初始规划 prompt。"""
    # 首次运行（无历史记忆）时，提示 Planner 更激进地拆解子任务
    has_memory = bool(memory and memory.strip() and memory != "None")
    if not has_memory:
        extra_hint = (
            "\n## Note\n"
            "No previous research memory is available for this topic. "
            "Please be MORE AGGRESSIVE in decomposition: generate 6-10 sub_tasks to thoroughly cover the topic, "
            "rather than the usual 3-5. Each sub-task should focus on a distinct angle or data source.\n"
            "IMPORTANT: Each sub-task description must directly reflect the user's original intent. "
            "If the user asks about 'internship application strategies', do NOT generate tasks about '2025 tech trends' or 'annual science summary'."
        )
    else:
        extra_hint = (
            "\n## Note\n"
            "Use the preserved successful results above to inform new sub-tasks. "
            "New tasks should fill gaps and avoid duplicating existing coverage."
        )
    return INITIAL_PLAN_PROMPT.format(query=query, memory_context=memory or "None") + extra_hint
```

### 3.1 `has_memory` 那个布尔表达式在防什么(第 224 行)

```python
has_memory = bool(memory and memory.strip() and memory != "None")
```

**【人话翻译】** 三个条件用 `and` 串起来,是**三层防御**,每层防一种"假的有记忆":

| 写法 | 防的是 |
|---|---|
| `memory` | 防 `None` 和 `""`(空字符串是 falsy) |
| `memory.strip()` | 防 `"   "`(纯空白也算 falsy,因为 strip 后是空串) |
| `memory != "None"` | 防**字符串 `"None"`** |

第三层最微妙,也最有意思。看第 240 行:

```python
return INITIAL_PLAN_PROMPT.format(query=query, memory_context=memory or "None") + extra_hint
```

【人话翻译】**调用方在没记忆时会**主动把 `memory_context` 填成字符串 `"None"`(为了让 prompt 里那个 `{memory_context}` 占位符不空着,不好看)。于是**读回来的时候**就得额外防一下这个哨兵值 `"None"` —— 否则 `memory = "None"` 会过了前两关(非空、strip 后还是 `"None"`)被判成"有记忆",**分支就走反了**。

> 💬 **这是一个很好的"讲设计细节"的素材**。它说明你考虑过哨兵值和真实值的区别。但**也是一个小设计瑕疵**(分析):用魔法字符串 `"None"` 当哨兵,不如让 `_build_prompt` 直接接收 `str | None`。如果被追问"这里有没有更干净的写法",可以答"有,直接传 `None` 而不是 `"None"` 字符串,避免哨兵值污染判断逻辑"。

### 3.2 两个分支的业务含义

| 分支 | 什么时候走 | 提示什么 | 意图 |
|---|---|---|---|
| **无记忆**(首次) | 首次规划,`memory` 是空 / `"None"` | "**更激进**:生成 6-10 个,而不是通常的 3-5" | **冷启动没有历史结果可参考,只能靠多加题来保证覆盖面** |
| **有记忆** | replan 或跨轮次 | "用保留的成功结果,新任务补空白、别重复" | **有已有产出了,应该收敛而不是铺开** |

这条"**冷启动铺开、有积累收敛**"的策略,是规划器里最容易被面试官认可的一个**设计判断**——它不是拍脑袋,是两种场景下的不同成本权衡:题目漏了的损失(信息不全)比题目多了的损失(多花几次搜索)大。

### 3.3 ⚠️ 数量口径三处打架(必须记住)

这是本讲**最硬的一个口径陷阱**,因为它有**确凿的代码证据**,而且是"同一件事在同一个模块里有三个不同说法"。

| 出处 | 说的数字 | 行号 | 性质 |
|---|---|---|---|
| prompt **规则 4** | "Generate **3 to 8** sub_tasks" | [planner.py:62](../src/planner/planner.py#L62) | 规则约束 |
| prompt **激进提示** | "generate **6-10** sub_tasks ... rather than the **usual 3-5**" | [planner.py:229](../src/planner/planner.py#L229) | 覆盖规则 4 的追加提示 |
| `Planner` 模块 **docstring** | "初始规划: 生成 **3-8** 个子问题的 DAG" | [planner.py:6](../src/planner/planner.py#L6) | 模块级说明,和规则 4 一致 |
| `RunConfig.max_sub_questions` **默认值** | **8** | [schemas.py:151](../src/orchestrator/schemas.py#L151) | 见第 10 步:**死配置** |
| `configs/default.yaml` | **10** | [default.yaml:118](../configs/default.yaml#L118) | 同上,**从来没被读过** |

**最要紧的一点**:那条激进提示说 "rather than the usual **3-5**"—— 但它要替换的规则 4 说的是 "**3 to 8**"。**提示词自己记错了自己的规则。**(`推断` 抱歉措辞:这句话大概是从更早的版本改过来的,改了规则 4 但没改追加提示。)

**面试怎么答**——这题答好是加分,答崩是减分。**不要装看不见,主动说**:

> "子任务数量在我这套提示里其实有三处口径,规则里写 3 到 8,首次无记忆的追加提示里又写 6 到 10,还说'通常 3 到 5'——这个'通常 3 到 5'和我规则里的 3 到 8 是冲突的,是改提示时没同步留下的。
> 更实质的问题是:**这三处都只是自然语言提示,不是硬约束**。模型完全可以吐 2 条或者 12 条,代码不会拦。真正能拦的配置项 `max_sub_questions` 我读了但没在规划器里用上,所以**它是死配置**。
> 如果重做,我会把数量约束做成**解析后的显式校验**,而不是靠提示词自觉。"

这段话里有三个明确的自我披露(prompt 内部矛盾 / 约束是软的 / 配置是死的),这正是最有力的一种"我确实做过"的证据。

---

## 第 4 步 · `generate_plan` 主流程

👉 **本地阅读代码**:[planner.py:126-156 `generate_plan()(全链路入口)](../src/planner/planner.py#L126-L156)

```python
@trace_chain(name="planner.generate_plan", tags=["m2", "planner"])
def generate_plan(self, query: str, memory_context: str = "") -> DAG:
    ...
    prompt = self._build_prompt(query, memory_context)
    messages = [
        {"role": "system", "content": "You are a research planning assistant. Output valid JSON only."},
        {"role": "user", "content": prompt},
    ]

    try:
        response = self.policy(messages)
    except RuntimeError as e:
        raise PlanParseError(f"LLM call failed during planning: {e}") from e

    content = response.get("content", "") or ""
    self._last_raw_json = content
    # 估算 planning token 消耗
    self.budget_tracker.track(len(content) // 3)

    return self._parse_plan(content)
```

### 4.1 逐段【人话翻译】

**① 装饰器(第 126 行)**

```python
@trace_chain(name="planner.generate_plan", tags=["m2", "planner"])
```

调用链追踪埋点。注意这是**整个方法**被装饰——不管成功失败,调用都会被记录。

👉 **本地阅读代码**:[tracing.py:175-177 `trace_chain()(不过是 traceable 的别名)](../src/utils/tracing.py#L175-L177)
👉 **本地阅读代码**:[tracing.py:95-98 追踪关闭时直接返回原函数(零开销)](../src/utils/tracing.py#L95-L98)

> ⚠️ **这里有个坑,第 10 步会讲**:`is_tracing_enabled()` 在**import 时**就会被调用,而且它在环境变量缺失时**会崩**。

**② `system` + `user` 两条消息(第 141-144 行)**

```python
messages = [
    {"role": "system", "content": "You are a research planning assistant. Output valid JSON only."},
    {"role": "user", "content": prompt},
]
```

**【人话翻译】** `system` 消息是"总纪律",`user` 消息是"这次的具体活儿"。**格式要求同时出现在两处**(`system` 说 "Output valid JSON only",prompt 里说 "(no markdown, no extra text)")——这叫**冗余约束**,因为格式违规是最常见的失败,值得说两遍。

**③ LLM 调用 + 异常翻译(第 146-149 行)**

```python
try:
    response = self.policy(messages)
except RuntimeError as e:
    raise PlanParseError(f"LLM call failed during planning: {e}") from e
```

**【人话翻译】** 这里做了一件很关键的事:**把"调用失败"统一翻译成"解析失败"**。

为什么?因为对上层编排器来说,**"LLM 挂了"和"LLM 吐了垃圾"是同一种结果:我没拿到可用的计划**。与其让上层分两个 `except` 分支,不如在规划器内部归一成一个异常类型。

👉 **本地阅读代码**:[planner.py:25-27 `PlanParseError(自定义异常)](../src/planner/planner.py#L25-L27)
👉 **本地阅读代码**:[orchestrator.py:211-216 上层只 catch 这一个类型](../src/orchestrator/orchestrator.py#L211-L216)

**④ `self.policy` 是什么(第 147 行)**

`policy` 是构造时传进来的**可调用对象**,不是硬编码的某个 client:

👉 **本地阅读代码**:[planner.py:117-120 `Planner.__init__(policy, budget_tracker)](../src/planner/planner.py#L117-L120)
👉 **本地阅读代码**:[runner.py:180-186 组装:Planner(policy=planner_policy, budget_tracker=...)](../src/core/runner.py#L180-L186)

它的真实类型是 `VLLMPolicy`,定义在 [../src/models/vllm_policy.py](../src/models/vllm_policy.py#L37-L140),接口就是一个 `__call__(messages) -> dict`。

> 💬 **这里有一个值得讲的解耦设计**:`Planner` **不 import 任何具体模型类**,只要求传进来一个 `__call__(messages)` 的东西。
> 好处:**换模型不用改规划器代码**。配置里 `planner: "deepseek"`([default.yaml:102](../configs/default.yaml#L102))选谁,是 [runner.py](../src/core/runner.py#L183) 决定的。
> 面试讲法:"**规划器对模型是依赖注入的,换后端不改业务代码。**"

**⑤ 拿回文本 + 存原始串(第 151-152 行)**

```python
content = response.get("content", "") or ""
self._last_raw_json = content
```

**【人话翻译】** 第 151 行有**两层兜底**:

1. `.get("content", "")` —— 万一返回的 dict 里压根没 `content` 这个键,给空串
2. `or ""` —— 万一 `content` 是 `None`(有些 API 会这么返回),也转成空串

**为什么非要转成空串**:这样 `_parse_plan` 拿到的永远是 `str`,不会在下游 `content.strip()` 的时候再炸一个 `AttributeError`。**这是"在系统边界把类型收干净"的典型做法。**

第 152 行把原始文本存进 `self._last_raw_json`。这个**私有属性**是给编排器用的:

👉 **本地阅读代码**:[orchestrator.py:205-207 编排器读 `self.planner._last_raw_json`](../src/orchestrator/orchestrator.py#L205-L207)

> ⚠️ **`orchestrator` 直接读 `planner` 的**私有**属性**(带下划线),这是本讲第二个设计瑕疵。技术上私有属性只是约定,能读;
> 但代价是:**`Planner` 被迫维护一个"上一次输出"的隐式状态**,而且 `generate_plan` 和 `replan` 共写同一个字段(第 152 行、第 212 行)。
> 面试讲法(诚实版):"编排器需要 SubTask 的完整信息,而 DAG 里只存了 task_id。我把原始 JSON 存在规划器上让编排器再解析一遍——**这个设计不好,更干净的做法是让 `generate_plan` 直接返回 `(DAG, task_map)` 元组**,不引入跨对象的私有状态。"

**⑥ 预算追踪(第 154 行)**

```python
self.budget_tracker.track(len(content) // 3)
```

**【人话翻译】** 这一行在**估算**这次调用花了多少 token,公式是 `字符数 ÷ 3`。

**为什么是 ÷3**(分析):英文大致 4 字符 ≈ 1 token,但这是中英混排,中文一个字符往往就接近 1 token,整体密度比纯英文高,取 3 是个折中。

> ⚠️ **必须说清楚:这是估算,不是实测。** 真实 token 数应该从 API 响应的 `usage` 字段读,这里没读。
> 也就是说,`BudgetTracker` 记录的是**"字符数除以 3"**,不是真实 token 消耗。**证据档位 = 估算/推断**。
> 面试官如果问"你的 token 预算是准的吗",**不要答"准"**,答:"是估算,按字符数除以 3 折算的,真实值在 API 的 usage 字段里,我没接。所以这个预算只能做**粗粒度的趋势监控**,不能拿来卡精确额度。"

顺带把 `BudgetTracker` 的真实能力交代清楚:

👉 **本地阅读代码**:[budget_tracker.py:33-41 `__init__(默认预算 100_000)](../src/planner/budget_tracker.py#L33-L41)
👉 **本地阅读代码**:[budget_tracker.py:47-52 `track()(负数会抛 ValueError)](../src/planner/budget_tracker.py#L47-L52)
👉 **本地阅读代码**:[budget_tracker.py:62-71 `is_over_budget()` / `is_near_budget()`](../src/planner/budget_tracker.py#L62-L71)

| 你可能会被问 | 实际答案 | 证据 |
|---|---|---|
| 预算上限是多少 | `100_000`,而且**只在这里传默认值**,config 里没有对应项 | [budget_tracker.py:33](../src/planner/budget_tracker.py#L33) |
| 超预算会怎样 | **什么都不会发生**。`is_over_budget()` 有定义,但**没人调用它做决策** | 全仓库搜索 `is_over_budget` 只在此文件内 |
| `reset()` 谁调 | **没人调**。注释写着"通常在 replan 后调用",但 replan 路径里没有这行 | [budget_tracker.py:77-80](../src/planner/budget_tracker.py#L77-L80) |

> 💬 **这三行合起来是一个必须主动承认的"未接线"事实**:`BudgetTracker` 是一个**完整的、带历史记录的、能算占用率的组件,但它接在链路上只做了 `track()`,没有任何"超预算就降级"的行为**。
> 面试讲法:"预算追踪我实现了计数和阈值判断两个能力,但**只接了计数,没接决策**。也就是说现在它是**可观测性**,不是**控制面**。要变成控制面,得在编排器的 `_do_planning` 和 replan 之间插一个判断。"

---

## 第 5 步 · `_parse_plan` 四步清洗(本讲核心)

👉 **本地阅读代码**:[planner.py:242-322 `_parse_plan()(四步清洗 + 兜底链)](../src/planner/planner.py#L242-L322)

这是整讲**技术含量最高**的一段。简历里那句"**多级 JSON 解析兜底链**"说的就是它。

### 5.1 首尾两个函数的 docstring 先矛盾了

```python
def _parse_plan(self, json_str: str) -> DAG:
    """健壮性 JSON 解析：处理 markdown 代码块、多余换行等噪声。

    解析策略:
      1. 先尝试直接 json.loads
      2. 失败则提取 markdown 代码块内容
      3. 清理常见噪声（尾部逗号、注释等）
      4. 验证 DAG 无环
    """
```

> ⚠️ **docstring 里写的顺序和代码里实际的顺序不一致。**
> docstring 说"**1. 先尝试直接 json.loads**";但代码实际是**先把 markdown 围栏剥掉、把最外层 `{...}` 抠出来、把尾逗号删掉,最后才 `json.loads`**。
> 也就是说,代码是"**先清洗再解析**",docstring 是"**先解析再清洗**"。`json.loads` 只在第 278 行出现一次,是三步清洗**之后**才跑的。
> **这不是功能 bug,是文档没跟上代码。** 面试时如果有人指着 docstring 问你顺序,答"docstring 写的是早期版本的思路,代码已经改成清洗前置了"。

### 5.2 带读:四步清洗 + 两级解析兜底

#### 【清洗 1】剥 markdown 围栏 —— 第 254-261 行

```python
raw = json_str.strip()

# 尝试提取 markdown 代码块
if raw.startswith("```"):
    # 去掉首行 ```json 或 ```
    lines = raw.splitlines()
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    raw = "\n".join(lines).strip()
```

**【人话翻译】** 只处理**围栏在最外层**的情况:

- `raw.startswith("```")` —— 只有整段以围栏开头才进来
- `lines[1:]` —— 砍掉第一行(可能是 ```` ``` ```` 或 ```` ```json ````)
- `lines[:-1]` —— 砍掉最后一行,但要先判 `if lines`(空列表切片不会错,但判一下语义更清楚)

> 💡 **`startswith("```")` 一个判断同时兼容了 ```` ``` ```` 和 ```` ```json ````**,因为两者都以三个反引号开头。不用写两次判断。这是个小技巧,面试可以提一句。

#### 【清洗 2】兜底:用正则从任意位置抠 JSON 块 —— 第 263-266 行

```python
# 尝试提取 ```json...``` 中间的内容（即使不在开头）
code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
if code_block_match:
    raw = code_block_match.group(1).strip()
```

**【人话翻译】** 这一步才是**真兜底**。为什么需要它——因为清洗 1 只能处理"围栏在开头"。如果 LLM 这么输出(很常见):

```text
好的，我来分析这个问题。以下是我的计划：

```json
{ "sub_tasks": [...] }
```

以上就是我的规划思路。
```

围栏**不在开头**,清洗 1 整个跳过。这时候 `re.search` 从**任意位置**找 ```` ```...``` ```` 并抠出里面的 `{...}`。

**逐个 token 拆解这个正则**:

| 片段 | 含义 |
|---|---|
| `` ``` `` | 字面三个反引号 |
| `(?:json)?` | "json" 可有可无(`?:` 表示只分组不捕获) |
| `\s*` | 中间可能有空白或换行 |
| `(\{.*?\})` | **捕获**从 `{` 到 `}` 的内容。`.*?` 非贪婪 → 吃到**第一个** `}` 就停 |
| `\s*` | 收尾的空白 |
| `` ``` `` | 字面三个反引号 |
| `re.DOTALL` | 让 `.` 能跨行匹配 |

> ⚠️ **这里的 `.*?` 有一个已知边界**(分析):如果 LLM 吐了**两个** JSON 代码块,非贪婪只会抓到第一个。**这是好事**,不是 bug。但如果**同一个 JSON 块内部还有嵌套的对象**,`.*?` 会在**内层的第一个 `}`** 就停,拿到一个残缺的 JSON。
> 好在规划器的输出结构是**扁平的**(`sub_tasks` 是个数组,每个元素是单层对象),`.*?` 停的位置通常刚好是正确的外层 `}`。**这是"碰巧够用",不是"设计上安全"**。讲的时候要老实说。

#### 【清洗 3】再兜底:找最外层大括号 —— 第 268-272 行

```python
# 尝试直接找最外层的 JSON 对象
if not raw.startswith("{"):
    obj_match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if obj_match:
        raw = obj_match.group(1).strip()
```

**【人话翻译】** 如果**没有** markdown 围栏(模型裸吐了一段带前言后语的文本),就退到最粗暴的一招:**从第一个 `{` 到最后一个 `}` 全抠出来**。

注意这里的 `.*` 是**贪婪**的(和清洗 2 相反),而且**故意如此**:要拿的是"从第一个 `{` 到最后一个 `}`",中间的东西全部保留。

> 💬 **这里有一个值得讲的非对称设计**:清洗 2 用 `.*?`(非贪婪,只取一块),清洗 3 用 `.*`(贪婪,取到最末)。**两处不是随手写的,是两种不同的意图**:
> - 清洗 2 面对的是"可能有多块,只取第一块"
> - 清洗 3 面对的是"只有一块,但要把它完整取出来,包括里面的换行"
> 面试可以提:**"这个非贪婪/贪婪的不对称是有意的,不是笔误。"**

#### 【清洗 4】删尾逗号 —— 第 274-275 行

```python
# 清理尾部逗号（JSON 不允许 trailing comma）
raw = re.sub(r",(\s*[}\]])", r"\1", raw)
```

**【人话翻译】** 把 `,}` 或 `,]` 替换成 `}` 或 `]`。

拆解 `r",(\s*[}\]])"` → `r"\1"`:

| 部分 | 含义 |
|---|---|
| `,` | 字面逗号 |
| `(\s*[}\]])` | **捕获**:任意个空白 + (`}` 或 `]`) |
| `\1` | 替换成"捕获到的那个括号"——**逗号被吃掉,括号原样留着** |

> ⚠️ **这个正则是"扫全文替换",不区分"是否在字符串内部"**(分析)。所以如果 LLM 在 `description` 里写了一句 `"用 x,]" 描述`…… 实际上不会触发,因为要求 `,` 后面直接跟括号。
> **真正的问题是它不处理"字符串里的逗号括号组合"**,不过对规划器这个扁平结构来说,风险很低。**知道有边界即可,不用展开讲。**

#### 【解析兜底链】两级 `json.loads` —— 第 277-293 行

```python
# 解析 JSON
try:
    data = json.loads(raw)
except json.JSONDecodeError as e:
    # 最后尝试：逐行修复（去掉注释）
    cleaned_lines = []
    for line in raw.splitlines():
        # 去掉 // 注释
        if "//" in line:
            line = line[: line.index("//")]
        cleaned_lines.append(line)
    try:
        data = json.loads("\n".join(cleaned_lines))
    except json.JSONDecodeError:
        raise PlanParseError(
            f"Failed to parse planner output as JSON. Raw snippet: {json_str[:500]}"
        ) from e
```

**【人话翻译】** 到这里是**最后两级**:

| 级别 | 手段 | 失败后 |
|---|---|---|
| 第一次 `json.loads` | 直接解析清洗后的文本 | 抛 `JSONDecodeError` → 进兜底 |
| 兜底 `json.loads` | **逐行**砍掉 `//` 之后的全部内容 | 再失败 → **抛 `PlanParseError`,规划终止** |

**逐行砍 `//`** 的实现:

```python
if "//" in line:
    line = line[: line.index("//")]
```

- `line.index("//")` —— 找到 `//` 第一次出现的**位置(数字)**
- `line[:位置]` —— 从开头切到这个位置**之前**,也就是把 `//` 和它后面的东西全丢了

> ⚠️ **这一行有一个真实的 bug,第 5.4 节用实测证明。** 先记住结论:**`//` 不一定是注释——`https://` 里就有 `//`。**

#### 【最后】结构校验 —— 第 295-300 行

```python
if not isinstance(data, dict) or "sub_tasks" not in data:
    raise PlanParseError(f"Planner output missing 'sub_tasks' key. "
                         f"Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
```

**【人话翻译】** 两种非法输入都在这儿拦:

1. **不是字典**(比如 LLM 吐了个 JSON 数组 `[...]`)→ 报类型
2. **是字典但没有 `sub_tasks` 键** → 列出实际有哪些键

第 2 种尤其重要:**报错信息里带上"实际有哪些键"**,调试时一眼就能看出模型理解偏成什么样了(比如它输出的是 `{"tasks": [...]}`)。**好的报错信息是工程质量的体现,这一点面试可以主动讲。**

> 💬 **注意 f-string 里那个三元表达式**:`list(data.keys()) if isinstance(data, dict) else type(data)`。
> 为什么写这个?因为**报错信息本身不能抛异常**。如果 `data` 是个 list,你写 `data.keys()` 就会在**报错的那一行再抛一个 `AttributeError`**,整个错误原因就被掩盖了。**这是很细的工程经验**——面试官问"你怎么写报错信息",这是个好例子。

### 5.3 四步清洗全景表(背下来)

```text
LLM 原始输出(一段脏字符串)
   │
   ├─ ① strip()                       去首尾空白
   ├─ ② 围栏在开头 → 掐掉首尾行              [planner.py:254-261]
   ├─ ③ 正则抠 ```json...``` 块(任意位置)   [planner.py:264]  .*? 非贪婪
   ├─ ④ 不以 { 开头 → 抠第一个{到最后一个}    [planner.py:270]  .*  贪婪
   ├─ ⑤ re.sub 删尾逗号                   [planner.py:275]
   │
   ├─ ⑥ json.loads  ← 大部分情况到这里就成功了
   │      │ 失败
   │      └─ ⑦ 逐行砍 // 注释 → 再 json.loads
   │             │ 失败
   │             └─ ✗ raise PlanParseError
   │
   └─ ⑧ 校验:是 dict 吗?有 sub_tasks 吗?sub_tasks 是 list 吗?
```

**一句话记忆**:**"剥围栏 → 抠小块 → 抠大块 → 删逗号 → 解析 → 砍注释再解析 → 校验结构"**。

### 5.4 ⚠️ 实测:`//` 剥离会把 URL 砍断

这是本讲**第一个我实际跑出来验证的问题**。证据档位:**实测(本地复现,合成输入)**。

**测试输入**(模拟 LLM 在 description 里带了论文链接,同时在末尾加了一句注释):

```json
{
  "sub_tasks": [
    {"task_id": "t1", "task_type": "search",
     "description": "参考 https://arxiv.org/abs/2401.1",
     "dependencies": []}
  ]
  // 备注
}
```

**实际结果**:

```text
_parse_plan: ✗ PlanParseError: Failed to parse planner output as JSON. Raw snippet: {
 "sub_tasks": [
 {"task_id":"t1","task_type":
```

**原因**:兜底那一级逐行砍 `//`,而 `"description": "参考 https://arxiv.org/abs/2401.1"` 这一行里,**`https://` 的前两个字符正好是 `//`**。于是这一行被砍成:

```text
     "description": "参考 https:
```

**字符串没闭合** → 第二次 `json.loads` 也失败 → **`PlanParseError` → 整次规划终止**。

**更完整的链条**:

| 步骤 | 结果 |
|---|---|
| 第一次 `json.loads` | 失败(有 `//` 备注) |
| 触发注释剥离 | **把 URL 一起砍了** |
| 第二次 `json.loads` | 仍然失败(字符串未闭合) |
| 最终 | `PlanParseError` → [orchestrator.py:211-213](../src/orchestrator/orchestrator.py#L211-L213) → **FAILED,零重试** |

> 🎯 **这是本讲最有价值的一个自我披露。** 面试讲法:
>
> "我的兜底链里有一级是'逐行去掉 `//` 注释'。这个策略有个缺陷:**它把 `//` 一律当注释,但 URL 里的 `https://` 也含 `//`**。我实测过:如果 LLM 在任务描述里写了论文链接,同时输出里又有一句真注释,这一级会把 URL 砍断,让 JSON 更坏而不是更好。
> 正确的做法是**只在字符串外部剥注释**——正式解析器里要么用带注释扩展的解析器,要么先做一次"记录字符串边界"的扫描,再判断 `//` 是否在引号内。
> 这个问题的实际后果还不小:因为**规划失败是零重试的**,一次坏输出会让整次研究直接 FAILED。"

**加分点**:这个 bug 你**自己发现、自己复现、自己说清正确做法**。这比"我的解析很健壮"强十倍——**能指出自己系统的具体缺陷,本身就是 ownership 的最强证据。**

---

## 第 6 步 · `_deserialize_subtask`:字段兜底与两个陷阱

👉 **本地阅读代码**:[planner.py:324-342 `_deserialize_subtask()(JSON dict → SubTask)](../src/planner/planner.py#L324-L342)

```python
def _deserialize_subtask(self, item: dict[str, Any]) -> SubTask:
    """将 JSON dict 反序列化为 SubTask。"""
    task_type_str = item.get("task_type", "search")
    try:
        task_type = TaskType(task_type_str)
    except ValueError:
        task_type = TaskType.SEARCH  # 默认值降级

    return SubTask(
        task_id=item.get("task_id", "unknown"),
        task_type=task_type,
        description=item.get("description", ""),
        dependencies=list(item.get("dependencies", [])),
        context_keys=list(item.get("context_keys", [])),
        timeout_seconds=int(item.get("timeout_seconds", 120)),
        priority=int(item.get("priority", 1)),
        expected_type=item.get("expected_type", "factual"),
        search_hints=list(item.get("search_hints", [])),
    )
```

### 6.1 这段的整体模式:"每个字段都给兜底值"

**【人话翻译】** 这个函数的哲学是 **"LLM 少给一个字段,不能让整个流程崩"**。

| 字段 | 缺省值 | 兜底方式 | 风险 |
|---|---|---|---|
| `task_id` | `"unknown"` | 直接给 | ⚠️ **多条缺 id 会重名**,DAG 节点会互相覆盖 |
| `task_type` | `"search"` | 先查枚举,失败降级 SEARCH | ⚠️ **静默降级**,见 6.2 |
| `description` | `""` | 直接给 | ⚠️ **空描述**,子 Agent 会瞎搜 |
| `dependencies` | `[]` | `list(...)` 包一层 | 无 |
| `context_keys` | `[]` | `list(...)` 包一层 | 无 |
| `timeout_seconds` | `120` | `int(...)` 转类型 | ⚠️ 见 6.3 |
| `priority` | `1` | `int(...)` 转类型 | ⚠️ **字段没人消费**(见 2.2) |
| `expected_type` | `"factual"` | 直接给 | ✅ 会被拼进子 Agent 的 prompt([researcher.py:377](../src/agents/researcher.py#L377)) |
| `search_hints` | `[]` | `list(...)` 包一层 | ⚠️ 丢了检索提示 |

**注意 `list(...)` 和 `int(...)` 这两层包转**(第 336-341 行):

- `list(item.get(...))` —— 万一 LLM 给了个**字符串** `"dependencies": "task_1"`,`list("task_1")` 会变成 `['t','a','s','k','_','1']` —— **字符级别的拆散**。这是个隐患(分析):应该判类型再包,而不是无条件 `list()`。
- `int(item.get(...))` —— 万一 LLM 给了 `"timeout": "2分钟"`,`int()` 会抛 `ValueError`,**而这个异常没有被捕获**,会一路冒到 [orchestrator.py:214-216](../src/orchestrator/orchestrator.py#L214-L216) 的 `except Exception` → **规划 FAILED**。
  也就是说:**`int()` 这两行是整个"兜底函数"里唯二会抛异常的地方**。兜底函数自己会抛异常,这是设计上的不一致。

> 💬 **这两条都是很好的加强项**:面试时可以主动说"我这个反序列化是**防御式**的,但有两处防御不够:`list()` 会把字符串拆成字符,`int()` 遇到非数字会抛。要更稳,应该写一个 `_safe_int(v, default)` 和类型判断。"

### 6.2 陷阱一:`task_type` 静默降级

```python
try:
    task_type = TaskType(task_type_str)
except ValueError:
    task_type = TaskType.SEARCH  # 默认值降级
```

**【人话翻译】** LLM 写 `web_search` / `SEARCH` / `query`,都会**静默变成 `search`**。

**这是"好的容错"还是"坏的掩盖"?**——**两者都是**,要分开讲(分析):

- **好的一面**:不会因为一个小拼写错误让整次研究崩掉。降级到 `search` 在多数情况下**语义上是对的**(规划器输出的任务里 search 占多数)。
- **坏的一面**:**这个降级没有任何日志**。没有 `print`,没有 warning。所以如果模型把本该是 `analyze` 的任务写错成了 `analyse`(英式拼写),它会**悄悄变成 search**,你不会知道。
- **后果**:`task_type` 决定派哪类 Agent([orchestrator.py:260](../src/orchestrator/orchestrator.py#L260)),所以颜色**错了会改变执行路径**,而且**留下了自己的痕迹吗?没有**。

**实测证据**:`{"task_type": "SEARCH_WEB"}` → 静默变成 `TaskType.SEARCH`,无任何提示。

> 🎯 **面试讲法**:"我这里做了静默降级,保护了鲁棒性,但**代价是丢失了可观测性**。
> 更正确的做法是:**降级的同时打一条 warning**,最好把"原始值 → 降级值"记进结果元数据。这样既能继续跑,又能事后统计'模型格式违规率'。我现在的实现只有前者,没有后者。"

### 6.3 陷阱二:`timeout_seconds` 的 **300 vs 120**

这是**第二个口径陷阱**,证据确凿、且和上一个(数量口径)不同类:

| 出处 | 值 | 行号 |
|---|---|---|
| `SubTask` **dataclass 默认值** | **300** | [schemas.py:86](../src/orchestrator/schemas.py#L86) |
| `_deserialize_subtask` **兜底值** | **120** | [planner.py:338](../src/planner/planner.py#L338) |
| prompt **示例值** | **120** | [planner.py:50](../src/planner/planner.py#L50) |

**实测确认**:让规划器解析一个**没写 `timeout_seconds`** 的合法 JSON,拿到的 `SubTask.timeout_seconds` 是 **120**,不是 300。

**这意味着什么**:

- **正常路径**(走规划器):`SubTask` 的超时**永远是 LLM 说了算**;LLM 不写,就是 **120 秒**。**dataclass 的 300 从来不会生效。**
- **旁路**(不经过规划器构造 `SubTask`):比如 [orchestrator.py:626-631 `_rebuild_task_map_from_dag()` 造占位符](../src/orchestrator/orchestrator.py#L626-L631),用 dataclass 默认 → **300 秒**。

**所以同一个系统里,两条路径的单任务超时默认值是 120 和 300,差了 2.5 倍。** 这不是 bug,但**是一个必须知道的口径不一致**。

> 🎯 **面试怎么用这个**:
> 如果面试官问"你的单任务超时是多少",**不能只答一个数**。正确答法:
> "分两种来源。**规划器正常产出的任务,超时由 LLM 在 JSON 里指定,我在反序列化时的兜底值是 120 秒**;但如果规划器的第二次解析失败、走了占位符降级路径,占位 `SubTask` 用的是数据类默认值 300 秒。**这两条路径不一致,是历史遗留**——更干净的做法是把超时统一从配置读,不让 LLM 决定也不让两处默认值各说各话。"

---

## 第 7 步 · 第二次解析:`get_task_map_from_dag` 的复制粘贴

👉 **本地阅读代码**:[planner.py:344-375 `get_task_map_from_dag()(第二次解析)](../src/planner/planner.py#L344-L375)

### 7.1 为什么会有"第二次解析"

先理解**为什么需要它**。

`_parse_plan` 返回的是 `DAG` 对象。而 `DAG` 里**只存节点名和边**:

👉 **本地阅读代码**:[dag.py:30-33 `DAG` 的内部结构只有 `_nodes`/`_edges`/`_in_degree`](../src/planner/dag.py#L30-L33)

也就是说 **DAG 丢掉了 `description`、`search_hints`、`timeout_seconds` 这些信息**。但编排器要派子 Agent 干活,**必须知道每个任务的描述**。

于是编排器在拿完 DAG 之后,**从原始 JSON 再解析一遍**,造一个 `task_id → SubTask` 的字典:

```python
# orchestrator.py:205-210
self._dag = self.planner.generate_plan(self._query, memory_ctx)
self._task_map = self.planner.get_task_map_from_dag(self._dag, self.planner._last_raw_json)
if not self._task_map:
    # 降级：如果解析失败，使用占位符
    self._task_map = self._rebuild_task_map_from_dag()
```

👉 **本地阅读代码**:[orchestrator.py:205-210 两次解析 + 降级](../src/orchestrator/orchestrator.py#L205-L210)

### 7.2 两次解析的差异:清洗逻辑被抄了一遍

```python
def get_task_map_from_dag(self, dag: DAG, raw_json: str) -> dict[str, SubTask]:
    """从 DAG 和原始 JSON 重建 task_id -> SubTask 映射。..."""
    # 复用 _parse_plan 中的解析逻辑，但返回映射
    # 这里重新解析 raw_json 以获取完整 SubTask 信息
    raw = raw_json.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if code_block_match:
        raw = code_block_match.group(1).strip()
    if not raw.startswith("{"):
        obj_match = re.search(r"(\{.*\})", raw, re.DOTALL)
        if obj_match:
            raw = obj_match.group(1).strip()
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    ...
```

**注释写着"复用 `_parse_plan` 中的解析逻辑"——但代码是原样抄了一遍。**

👉 **对比**:[planner.py:254-275 vs :351-366](../src/planner/planner.py#L254-L275)

**抄漏了什么?两处关键差异**:

| 步骤 | `_parse_plan`(第 254-293 行) | `get_task_map_from_dag`(第 351-371 行) |
|---|---|---|
| 剥围栏 | ✅ | ✅ |
| 抠代码块 | ✅ | ✅ |
| 抠最外层 `{}` | ✅ | ✅ |
| 删尾逗号 | ✅ | ✅ |
| **`//` 注释剥离重试** | ✅ **有** | ❌ **没有** |
| **解析失败时** | `raise PlanParseError` | **`return {}`** ← 静默 |

**这四行差异,制造了一条完整的静默降级路径。**

### 7.3 ⚠️ 实测:规划"成功"了,但子任务全变成占位符

**证据档位:实测(本地复现,合成输入)。**

**测试输入**(LLM 输出带 `//` 注释,**但这行注释里没有 URL**):

````text
```json
{
  "sub_tasks": [
    {"task_id": "t1", "task_type": "search", "description": "查 A 公司岗位",
     "dependencies": [], "search_hints": ["A公司", "岗位"]},
    {"task_id": "t2", "task_type": "analyze", "description": "分析 A 的面试流程",
     "dependencies": ["t1"], "search_hints": ["面经"]}
  ]  // 说明：两段式结构
}
```
````

**实际结果**:

```text
_parse_plan ✓ 成功, nodes = ['t1', 't2']        ← 第一遍解析:成功!
get_task_map_from_dag 条目数 = 0  -> 全变占位     ← 第二遍解析:返回 {}
```

**发生了什么**(逐环节):

| 环节 | 结果 |
|---|---|
| ① `_parse_plan` | 第一次 `json.loads` 失败(有 `//`)→ 走**注释剥离** → 成功 → **返回了正确的 DAG(2 个节点)** |
| ② `get_task_map_from_dag` | 同样第一次失败 → **但它没有注释剥离**那一级 → `JSONDecodeError` → **`return {}`** |
| ③ 编排器 | `if not self._task_map:` → **走降级分支** [orchestrator.py:210](../src/orchestrator/orchestrator.py#L210) |
| ④ `_rebuild_task_map_from_dag()` | 为每个节点造**占位 `SubTask`** |

**占位 `SubTask` 长什么样**:

👉 **本地阅读代码**:[orchestrator.py:612-634 `_rebuild_task_map_from_dag()(占位符生成)](../src/orchestrator/orchestrator.py#L612-L634)

```python
task_map[node_id] = SubTask(
    task_id=node_id,
    task_type=TaskType.SEARCH,
    description=f"Auto-generated task for {node_id}",
    dependencies=deps,
)
```

**于是子 Agent 收到的任务指令变成了**:

```text
"Auto-generated task for t1"
```

**我们来盘点一下这一刻系统"静默丢掉"了什么**:

| 丢的东西 | 从 | 到 |
|---|---|---|
| **任务描述** | `"查 A 公司岗位"` | `"Auto-generated task for t1"` |
| **任务类型** | 混合(`search` + `analyze`) | **全变 `SEARCH`** |
| **检索提示** | `["A公司", "岗位"]` | **丢失**(`search_hints` 默认 `[]`) |
| **上下文键** | 原文里的 `context_keys` | **丢失**(默认 `[]`) |
| **超时** | 原文指定 | **300**(dataclass 默认) |
| **期望类型** | 原文指定 | **丢失**(默认 `"factual"`) |

**关键的一点**:**这不报错。** 日志里只有一行 `[Planning] ✓ DAG 生成完成: 2 个子任务, 2 个执行层`——**看起来完全正常**。然后子 Agent 拿着 `"Auto-generated task for t1"` 这个描述去搜网页。

> 🎯 **这是整讲最重要的一个盲区,也是最好的面试素材。** 面试讲法:
>
> "我的解析链有个**结构性问题**:规划产物的解析被**分成了两遍**——一遍在规划器里生成 DAG,一遍在编排器里重建任务详情,都以同一个 LLM 原始输出为输入。
> 两遍的清洗逻辑是**复制粘贴**的,而且在**第二遍里少了一级兜底**——少了'剥 `//` 注释重试'那一级。
> 后果是:如果模型输出里带了注释,第一遍能靠兜底救回来、生成正确的 DAG,第二遍却失败、返回空字典,**触发'占位符降级'——所有任务描述变成 `Auto-generated task for t1` 这种字符串,任务类型全变成 search,检索提示全丢**。
> 最麻烦的是它**不报错**。DAG 的节点数是对的,日志看起来正常,但是下游检索质量会明显下滑。
> **正确的做法**:让 `generate_plan` 直接返回 `(DAG, task_map)`,一遍解析同时产出两个东西,从根上消掉'两份清洗逻辑'。"
>
> **这段话里有:结构诊断 + 具体到达行号的因果链 + 改进方案。** 这是架构类追问的标准满分模板。

### 7.4 还有一个更难发现的:`task_id` 重名覆盖

看第 374-375 行的字典推导:

```python
return {item.get("task_id", f"task_{i}"): self._deserialize_subtask(item)
        for i, item in enumerate(sub_tasks_raw)}
```

**【人话翻译】** key 是 `task_id`,**如果有两条任务用了同一个 `task_id`,后面的会静默覆盖前面的**——而且字典推导**不会有任何提示**。

配合 6.1 表里的第一条(`task_id` 缺省值是 `"unknown"`):如果 LLM 有**两条**任务都没写 `task_id`,那两条的 key 都是 `"unknown"` → **合并成一条**,`task_map` 只剩一条。

而 DAG 那一侧呢?[planner.py:305 `dag.add_node(task.task_id)`](../src/planner/planner.py#L305) 用的是 `set`,**重名也是去重**。所以**DAG 里只有 1 个节点,task_map 里也只有 1 条**——**两边一致地"少了"一条任务,你完全看不出来**。

> 💬 **这个是"分析"(我按代码推的,没有实测那个特定输入),但逻辑是确定的**。面试可以作为补充:"除了刚才那条静默降级,`task_id` 万一重复,`dict` 推导和 `set` 节点都会**静默去重**,不会报错。"

---

## 第 8 步 · DAG 校验:失败之后发生什么

### 8.1 环检测(第三道防线)

👉 **本地阅读代码**:[planner.py:302-320 建节点 → 建边 → 拓扑排序验环](../src/planner/planner.py#L302-L320)

```python
dag = DAG()
for item in sub_tasks_raw:
    task = self._deserialize_subtask(item)
    dag.add_node(task.task_id)

# 第二遍添加边
for item in sub_tasks_raw:
    task_id = item.get("task_id", "")
    for dep in item.get("dependencies", []):
        if not dag.has_node(dep):
            # 依赖指向不存在的任务，创建占位节点
            dag.add_node(dep)
        dag.add_edge(dep, task_id)  # dep -> task_id (task_id 依赖 dep)

# 验证无环
try:
    dag.topological_sort()
except DAGCycleError as e:
    raise PlanParseError(f"Planner generated a cyclic graph: {e}") from e
```

**【人话翻译】** 三件事:

1. **先加所有节点**,再加边。**顺序不能反** —— 因为 `add_edge` 会自动补节点,如果先加边,你会搞不清哪些节点是"真的任务"、哪些是"被自动带出来的"。
2. **依赖指向不存在的 id → 自动补占位节点**(第 311-313 行)。
3. **`topological_sort()` 不只为了排序,更是为了验环**。Kahn 算法如果排不完所有节点,就说明有环 → 抛 `DAGCycleError` → **转成 `PlanParseError`**。

👉 **本地阅读代码**:[dag.py:107-112 环检测:排不完就是有环,报出剩余节点](../src/planner/dag.py#L107-L112)

> 💡 **为什么是"跑一次拓扑排序"来验环,而不是专门写个环检测算法?**
> 因为 Kahn 算法**顺便**就验了:它的循环结束时,**如果结果长度 ≠ 节点数,剩下的就是环上的节点**。**零额外成本**。
> 而且报错信息里带上剩余节点名([dag.py:109-111](../src/planner/dag.py#L109-L111)),**日志里直接告诉你环上有谁**,比只报"有环"有用得多。**这个设计细节面试可以主动讲。**

### 8.2 ⚠️ 实测:依赖指向不存在的 id 会造出一个"幽灵任务"

**证据档位:实测(本地复现)。**

**输入**:`{"sub_tasks":[{"task_id":"t1","task_type":"search","description":"a","dependencies":["ghost"]}]}`

**结果**:

```text
DAG = <DAG nodes=2 edges=1>   nodes = ['ghost', 't1']
```

**问题链条**:

| 环节 | 发生什么 |
|---|---|
| ① 建边 | `ghost` 不存在 → [planner.py:311-313](../src/planner/planner.py#L311-L313) **补一个占位节点** |
| ② DAG | 有 **2 个节点**:`ghost` 和 `t1`。`ghost` 是**凭空多出来的** |
| ③ `task_map` | ⚠️ **`ghost` 不在里面!** 因为 [get_task_map_from_dag](../src/planner/planner.py#L374-L375) 只遍历 `sub_tasks_raw`,而 `ghost` 是建边时补的 |
| ④ 分发 | `self._task_map.get("ghost")` → `None` → [orchestrator.py:247-253](../src/orchestrator/orchestrator.py#L247-L253) 返回 `AgentResult(status=FAILED, output="SubTask 'ghost' not found in task_map")` |
| ⑤ 后果 | **`ghost` 自己占一个执行层,必然失败**;而它的失败会**计入失败率** → 可能触发 [重规划](../src/orchestrator/orchestrator.py#L519-L537) |

**【人话翻译】** LLM 写错了一个依赖名,系统**不报错**,而是**凭空造一个任务、让它跑、让它失败**。

**这个"幽灵任务"的两重代价**:

1. **浪费一次执行层的调度** —— 它必然失败,纯粹白跑
2. **污染失败率统计** —— 它会计入 `_should_replan` 的 `failed` 计数([orchestrator.py:529](../src/orchestrator/orchestrator.py#L529)),**可能触发一次本来不需要的重规划**(而重规划要再调一次 LLM、再解析一次,正是第 5 步那个 URL bug 的暴露机会)

> 🎯 **面试讲法**:"依赖指向不存在的 id 时,我选择**补占位节点**而不是报错——目的是不让一个笔误毁掉整个计划。
> 但实测下来这个降级**不够好**:补出来的节点**不在 task_map 里**,所以它分发时必然失败,而且会**污染失败率、可能误触发重规划**。
> 更好的做法:**建边时记下无效依赖,要么直接删掉这条边并在日志里 warning,要么给占位节点补一个合理的 SubTask**。现在这样是'看着容错,实际制造了一次必败任务'。"

### 8.3 规划失败的后果:零级降级(本讲最尖锐的对比)

**这是本讲和第 3 讲最重要的呼应点。**

| 阶段 | 降级层级 | 证据 |
|---|---|---|
| **执行阶段**(派子 Agent) | **三级降级**:单任务超时→标记继续;失败率超阈值→重规划(最多 3 轮);全局超时→强制合成 | [03-编排引擎 第 6 步](03-编排引擎.md), [orchestrator.py:519-537](../src/orchestrator/orchestrator.py#L519-L537) |
| **规划阶段**(生成 DAG) | **零级降级**:LLM 调用失败或 JSON 解析失败 → **直接 FAILED,没有任何重试** | [orchestrator.py:211-216](../src/orchestrator/orchestrator.py#L211-L216) |

```python
# orchestrator.py:211-216
except PlanParseError as e:
    print(f"[Planning] Failed: {e}")
    return OrchestratorState.FAILED
except Exception as e:
    print(f"[Planning] Unexpected error: {e}")
    return OrchestratorState.FAILED
```

**【人话翻译】** 两个 `except` **都直接返回 `FAILED`**。而 `FAILED` 是**终态**:

👉 **本地阅读代码**:[orchestrator.py:511-513 `_on_failed()` 是终态,不再转移](../src/orchestrator/orchestrator.py#L511-L513)

**也就是说**:

> **整个系统的容错能力,在"第一跳"上有一个单点:一次 LLM 调用的输出格式不合格 → 整次研究终止。**
> 后面那套精心设计的三级降级(重规划、超时、强制合成)**一个都用不上,因为流程根本走不到那里。**

> 🎯 **这是"讲得出失败与回滚"的最佳素材**(SKILL 第七节第 6 条明确点名"只讲 happy path"是失分点)。面试讲法:
>
> "我的编排层有一整套三级降级,但**规划这一跳是零降级**的——一次 LLM 调用、一次解析,失败就整个 FAILED,没有重试。
> 这是一个**不对称的容错设计**:执行阶段很宽容,入口阶段很严格。而入口阶段恰恰是**唯一一次纯 LLM 调用、格式风险最高**的地方。
> 如果要改,最简单有效的一步是**给规划加一次重试**——同 prompt 再问一次,成本一次 LLM 调用,但能把'格式抖动'导致的整轮失败挡掉。再进一步可以做'解析失败就把错误信息喂回去让模型修'的自我修复回路。"
>
> 💡 **注意这段的两个加分点**:① 承认不对称并解释为什么不好;② 给出**成本最小、收益明确**的第一步改进。**不吹牛、不空谈架构,这是最像"做过工程"的答法。**

---

## 第 9 步 · replan:保留什么、丢掉什么

### 9.1 触发条件与预算

👉 **本地阅读代码**:[orchestrator.py:519-537 `_should_replan()(失败率>50% 或 成功<30%)](../src/orchestrator/orchestrator.py#L519-L537)

```python
failure_rate = failed / total
if failure_rate > 0.5:
    return True
if success / total < 0.3 and failed > 0:
    return True
return False
```

**【人话翻译】** 主判据是 **失败率 > 50%**。第二条是补充:**成功率不到 30% 而且至少有 1 个失败**。

> 💬 **注意其中"至少 1 个失败"这个条件的作用**(分析):没有它,当 `total=1` 且唯一一个任务是 TIMEOUT 时,`success/total = 0 < 0.3` 会触发,这和失败率判据其实重合了。
> 但更要紧的是**它防的是另一件事**:如果 `total=0`,第一条 `failure_rate` 会除零——所以上面还有 `if not results: return False` 兜着([orchestrator.py:526-527](../src/orchestrator/orchestrator.py#L526-L527))。**除零保护是有的,细节做对了。**

### 9.2 `replan()` 里"保留"的实现

👉 **本地阅读代码**:[planner.py:177-186 筛选保留结果(confidence ≥ 0.6)](../src/planner/planner.py#L177-L186)

```python
# 筛选保留的结果（confidence >= 0.6 且状态为 SUCCESS）
preserved = [
    {
        "task_id": r.task_id,
        "output": str(r.output)[:500] if r.output else "",
        "confidence": r.confidence,
    }
    for r in existing_results
    if r.status.value == "success" and r.confidence >= 0.6
]
```

**【人话翻译】** 两个条件**同时**满足才算"成功结果":

1. `r.status.value == "success"` —— 状态是成功
2. `r.confidence >= 0.6` —— 置信度至少 0.6

**这段是"保留什么"的完整定义,面试必问。**

> ⚠️ **这里有一个口径细节需要主动澄清**:
> **`confidence` 是谁给的?** 是**子 Agent 自己报的**([schemas.py:92-109 `AgentResult.confidence`](../src/orchestrator/schemas.py#L92-L109))。
> 也就是说,"这条结果值不值得保留"这个判断,**依据是 Agent 的自我评估,不是外部评价**。
> **面试官很可能追问这一点。** 准备好的答法:"0.6 这个阈值判的是**子 Agent 自报的置信度**。
> 这是个**弱信号**——模型对自己的答案往往偏乐观。所以这个筛选的作用是**粗筛**,不是"质量门禁"。
> 更严的做法是引入外部校验(比如让 Judge 给分,或者用来源数量做交叉验证),但那样每轮要多花一次 LLM 调用。"

**还注意 `str(r.output)[:500]`**:

> ⚠️ **第 181 行的 `[:500]` 是一个必须承认的截断**。传给 replan 的"成功结果"只有**前 500 个字符**。
> 而第 2.2 节提过,报告类输出往往很长(项目里 Red Agent 那边按 4000 字符截断,单份报告平均两万字符量级 —— 见 [面试知识库-项目二 2.5 陷阱 10](面试知识库-项目二-DeepResearch.md))。
> **所以 replan 看到的"已有成果"是残缺的**,只能看到每条结果的**开头**。
> 面试讲法:"给重规划器的历史结果我截断到 500 字符,目的是**控制 prompt 长度**。代价是重规划器**看不到完整输出**,只能知道'这条做过了,大意是什么'。它本来也不需要全文——它的任务是'补空白、别重复',不需要读细节。"

### 9.3 `REPLAN_PROMPT` 授权了哪四件事

👉 **本地阅读代码**:[planner.py:78-106 `REPLAN_PROMPT(重规划提示词)](../src/planner/planner.py#L78-L106)

```text
## Output Format
Return a JSON object with new sub_tasks. You may:
1. Modify failed tasks (new task_id, same or different description)
2. Add new tasks to fill gaps
3. Remove tasks that are no longer needed
4. Keep dependencies consistent
```

**【人话翻译】** 这四条是给模型的**四种授权**,对应重规划时必须处理的四种情况:

| 授权 | 含义 | 对应现实场景 |
|---|---|---|
| ① 改失败任务 | 可以用新 id 重新描述 | 原来是"查 X 的营收",超时了 → 改成"查 X 的营收,限定近一年" |
| ② 加新任务补空白 | 允许扩大范围 | 发现"三家对比"少了第三方评测数据 |
| ③ 删掉不再需要的 | **允许缩小范围** | 某个任务依赖的前置失败了,它本身也就没意义了 |
| ④ 保持依赖一致 | 约束 | 防止改完之后出现悬空依赖 |

> 💬 **第 ③ 条"允许删除"是一个容易被忽略但重要的设计**:如果 prompt 只允许"改"和"加",重规划会**越滚越大**——每次重规划都只会加任务,任务数单调增长,最后资源全烧在补漏上。
> **明确写出"可以删",是给重规划一个"收敛"的出口。** 面试可以主动讲这一点:**"重规划最大的风险是越补越多,所以我在 prompt 里明确授权它可以删任务。"**

### 9.4 `self._results = []` —— 结果清空,但记忆不清空

👉 **本地阅读代码**:[orchestrator.py:491-492 重规划后清空结果列表](../src/orchestrator/orchestrator.py#L491-L492)

```python
# 清空上一轮结果（保留在 memory 中，新任务可通过 context_keys 引用）
self._results = []
```

**【人话翻译】** 这一行注释说清了设计意图,分两个存储:

| 存储 | 清空? | 为什么 |
|---|---|---|
| `self._results`(当前的 `AgentResult` 列表) | ✅ **清空** | 新 DAG 要重新跑一遍,旧的执行记录不再参与结果统计(否则失败率会被旧账污染) |
| `self._memory_store`(共享记忆) | ❌ **保留** | 新任务是**增量**的,它们需要通过 `context_keys` 引用上一轮已产出的内容 |

这是**第 3 讲"两套存储"设计的直接落点**。面试讲法:

> "重规划时我只清空'本轮结果列表',**不清共享记忆**。原因是重规划是**增量**的——新计划要引用旧产出,如果清了记忆,新任务就得从零重查,重规划就失去意义了。清空结果列表是为了让**失败率统计只反映新一轮**,不被上一轮的旧失败拖累。"

### 9.5 重规划失败的两条逃生路

👉 **本地阅读代码**:[orchestrator.py:493-503 重规划失败:有成功结果就去合成](../src/orchestrator/orchestrator.py#L493-L503)

```python
except PlanParseError as e:
    print(f"[Replan] Failed: {e}")
    # 重规划失败，如果已有部分成功结果，尝试直接合成
    if any(r.status == AgentStatus.SUCCESS for r in self._results):
        return OrchestratorState.SYNTHESIZING
    return OrchestratorState.FAILED
```

**【人话翻译】** 重规划失败了怎么办?**分两种情况:**

| 情况 | 去向 | 为什么 |
|---|---|---|
| **已有成功的部分结果** | → `SYNTHESIZING`(**降级到"有啥写啥"**) | 不浪费已经拿到的成果,用残缺材料出一份降级报告 |
| **一个成功结果都没有** | → `FAILED`(终止) | 没材料可合成,继续也没意义 |

> 📌 **这里要和第 8.3 节形成一个对照**:
> - **首次规划失败** → **无条件 FAILED**,因为**手上什么都没有**
> - **重规划失败** → **有结果就降级合成**,因为**手上有货**
>
> **同一个异常类型 `PlanParseError`,两个地方的处理完全不同,而差别只在"手上有没有成果"。** 这是很干净的一个设计逻辑,面试可以主动讲:**"我的规划失败处理是分级的:首次失败直接终止,重规划失败就降级到合成——判断依据是当前有没有可用的部分成果。"**
>
> 注意这与 8.3 节的"零降级"说法**并不矛盾**:8.3 说的是"规划这一跳没有**重试**",9.5 说的是"规划失败后**去向哪里**有分级"。**两句都要说,面试官会觉得你分得清"重试"和"降级"。**

---

## 第 10 步 · 五处必须知道的实现真相

这一节是本讲的**口径陷阱总表**。SKILL 第七节第 1 条要求的"源码与文档冲突要显式写出",都在这里。

### 真相 1:`max_sub_questions` 是死配置

| 排查步骤 | 证据 |
|---|---|
| 配置文件里有 | [default.yaml:118 `max_sub_questions: 10`](../configs/default.yaml#L118) |
| 读进了 `RunConfig` | [runner.py:311 从 config 读取,默认 8](../src/core/runner.py#L311) |
| `RunConfig` 定义了字段 | [schemas.py:151 `max_sub_questions: int = 8`](../src/orchestrator/schemas.py#L151) |
| **被消费了吗** | ❌ **全仓库搜索:没有任何一处读它去做判断** |

**【人话翻译】** 这个配置**从 yaml 一路读到 dataclass,然后就没有然后了**。真正驱动子任务数量的是 prompt 里的**自然语言**(第 3 步的三处口径)。

> ⚠️ **而且是三处默认值不一致**:yaml 写 **10**、`RunConfig` 默认 **8**、prompt 规则说 **3-8**、激进提示说 **6-10**。**四个数字,一个真在起作用(prompt),三个是摆设。**

🎯 **面试讲法**:"我有一个叫 `max_sub_questions` 的配置项,在 yaml 里是 10,在数据类默认值里是 8,但是从 yaml 读进配置对象之后**没有被任何代码消费**——它是**死配置**。真正的数量控制只写在提示词里,是**软约束**,模型可以不听。
这是我做复盘时自己查出来的:**'配置项存在'和'配置项生效'是两件事**。校验方法很简单——**全局搜这个字段名,只看有没有非定义的引用点**。"

### 真相 2:子任务数量三处口径打架(见 3.3 节)

| 出处 | 数字 |
|---|---|
| [planner.py:6](../src/planner/planner.py#L6) 模块 docstring | 3-8 |
| [planner.py:62](../src/planner/planner.py#L62) prompt 规则 4 | 3 to 8 |
| [planner.py:229](../src/planner/planner.py#L229) 激进提示 | 6-10,并说"通常 3-5" |

**要点**:激进提示里的"usual **3-5**"和规则 4 的"**3 to 8**"**互相矛盾**,是改 prompt 时没同步的残留。

### 真相 3:`timeout_seconds` 的 300 vs 120(见 6.3 节)

| 路径 | 默认超时 |
|---|---|
| 走规划器正常产出 | **120**(`_deserialize_subtask` 兜底值) |
| 走占位符降级 | **300**(`SubTask` dataclass 默认值) |

**要点**:同一系统内两条路径差 2.5 倍,问"你的单任务超时多少"不能只答一个数。

### 真相 4:第二遍解析少了兜底 → 静默降级成占位符(见第 7 步)

**要点**:**这是本讲最重要的盲区**。`_parse_plan` 有 5 级手段,`get_task_map_from_dag` 只有 4 级(缺注释剥离),失败时 `return {}` 而不是抛异常,导致**所有任务描述变成 `Auto-generated task for t1`**,且**不报错**。

### 真相 5:`is_tracing_enabled()` 在环境变量缺失时会崩(附带发现)

这一条是**在读代码过程中意外发现的跨模块问题**,证据:实测(我直接跑出来的 traceback)。

👉 **本地阅读代码**:[tracing.py:35-38 `is_tracing_enabled()(追踪开关判断)](../src/utils/tracing.py#L35-L38)

```python
def is_tracing_enabled() -> bool:
    """检查 LangSmith 追踪是否启用。"""
    from .env_config import get_env
    return get_env("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
```

**问题**在于 `get_env` 的语义:

👉 **本地阅读代码**:[env_config.py:47-56 `get_env()(空串和缺失一律返回 None)](../src/utils/env_config.py#L47-L56)

```python
def get_env(key: str, default: str | None = None) -> str | None:
    ensure_env_loaded()
    val = os.getenv(key, default)
    if val == "" or val is None:
        return None          # ← 空字符串和缺失,一律返回 None
    return val
```

**把两者接起来**:`get_env("LANGSMITH_TRACING", "")` 传了一个**默认值 `""`**,但 `get_env` 会把 `""` 归一成 `None` 返回。于是:

```text
get_env(...) 返回 None
None.lower()                      ← AttributeError!
```

**实测 traceback**(直接 import 规划器就炸):

```text
File "src/planner/planner.py", line 127, in Planner
    def generate_plan(self, query: str, memory_context: str = "") -> DAG:
File "src/utils/tracing.py", line 97, in decorator
    if not is_tracing_enabled():
File "src/utils/tracing.py", line 38, in is_tracing_enabled
    return get_env("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
AttributeError: 'NoneType' object has no attribute 'lower'
```

**为什么危险**:这个调用发生在**装饰器执行时,也就是 import 时**([tracing.py:95-98](../src/utils/tracing.py#L95-L98))。而 [planner.py:126](../src/planner/planner.py#L126)、[planner.py:158](../src/planner/planner.py#L158) 都挂了 `@trace_chain`。

**触发条件**:环境里 `LANGSMITH_TRACING` **未设置或为空**。而 `ensure_env_loaded()` 是按 `os.getcwd()` 找 `.env` 的([env_config.py:35-42](../src/utils/env_config.py#L35-L42)),所以:

| 场景 | 后果 |
|---|---|
| 从项目根目录跑、`.env.local` 里配了 `LANGSMITH_TRACING=true`(模板默认) | 正常 |
| 从**别的目录**跑(比如 `cd src && python ...`) | **找不到 `.env` → 变量缺失 → import 崩溃** |
| 把模板里那行**注释掉或者留空**(模板注释写着"false 则完全禁用",容易误操作) | **import 崩溃** |

> ⚠️ **注意 `get_env` 的两个设计问题**(分析):
> ① **默认值参数在传空串时失效** —— `get_env(k, "")` 的 `""` 会被归一成 `None`,调用方拿不到空串。
> ② **返回类型是 `str | None`,但调用方直接 `.lower()`** —— 类型标注已经提示了可能为 `None`,调用点却没有防御。
>
> **修法**(一行):`(get_env("LANGSMITH_TRACING", "") or "").lower()`。

🎯 **面试怎么用这一条**:
> 这是一个**很好的"顺带发现"素材**,但**要控制篇幅**。讲法:
> "项目里有 LangSmith 追踪埋点。我在做本地复现的时候发现了一个**跨模块的小缺陷**:追踪开关的判断函数 `get_env(...).lower()` 里,`get_env` 在变量缺失时会返回 `None`,导致 `.lower()` 崩。
> 而且这个调用在**装饰器执行时**就会跑,也就是 import 阶段——所以只要环境变量没配好,**import 规划器就失败**。一行 `or ""` 就能修。
> 我提这个不是为了说代码差,是说明**我把代码读过一遍、真的跑过**,不是只看了主流程。"

> 💬 **重要提醒**:这条虽然是真问题,但**不要当主角讲**。它是"附加发现",用来说明"我读过全链路"。**主线永远是前四条**,尤其是真相 4。

---

## 第 11 步 · 对照数字:本讲涉及的所有数字及证据档位

按 SKILL 第七节第 2 条,每个数字必须标档位。

| 数字 | 出处 | 证据档位 | 能不能在面试里报 |
|---|---|---|---|
| prompt 规则:生成 **3 到 8** 个子任务 | [planner.py:62](../src/planner/planner.py#L62) | 源码事实 | ✅ 是"提示词里这么写",不是"实际生成 3-8 个" |
| 激进提示:生成 **6-10** 个 | [planner.py:229](../src/planner/planner.py#L229) | 源码事实 | ✅ 必须连带说"和规则 4 冲突" |
| 配置 `max_sub_questions` = **10** | [default.yaml:118](../configs/default.yaml#L118) | 源码事实 | ⚠️ **必须说明"是死配置"** |
| `RunConfig.max_sub_questions` 默认 **8** | [schemas.py:151](../src/orchestrator/schemas.py#L151) | 源码事实 | ⚠️ 同上 |
| `timeout_seconds` 兜底 **120** / 数据类默认 **300** | [planner.py:338](../src/planner/planner.py#L338) / [schemas.py:86](../src/orchestrator/schemas.py#L86) | 源码事实 | ✅ 两个都报,说明来源差异 |
| 预算上限 **100_000** token | [budget_tracker.py:33](../src/planner/budget_tracker.py#L33) | 源码事实 | ⚠️ 补一句"但没有任何代码在超预算时做决策" |
| token 估算公式 **字符数 ÷ 3** | [planner.py:154](../src/planner/planner.py#L154) | **估算/推断** | ⚠️ 必须说"是估算,不是 API 实测值" |
| 保留结果阈值 **confidence ≥ 0.6** | [planner.py:185](../src/planner/planner.py#L185) | 源码事实 | ⚠️ 必须说"是 **Agent 自报**的置信度" |
| 保留结果截断 **500 字符** | [planner.py:181](../src/planner/planner.py#L181) | 源码事实 | ✅ 主动说,体现你考虑过 prompt 长度 |
| 重规划上限 **3 轮** | [schemas.py:150](../src/orchestrator/schemas.py#L150) | 源码事实 | ✅ 见第 3 讲 |
| 重规划触发:**失败率 > 0.5**,或 **成功 < 30% 且有失败** | [orchestrator.py:532-536](../src/orchestrator/orchestrator.py#L532-L536) | 源码事实 | ✅ 精确报,这是硬考点 |
| 报告截断 **4000 字符**(Red Agent) | red_agent.py:289 | 源码事实 | 见 [项目二知识库 陷阱 10](面试知识库-项目二-DeepResearch.md) |
| 简历里的 `85% → 95%` | **仓库中查无出处** | **无仓库证据** | ❌ **不要主动报**,被问就答"待确认" |

> ⚠️ **最后一行要特别记住**。SKILL 第七节第 2 条明确点名:简历里的 `85%→95%` **仓库里查无出处**。
> 本讲涉及的所有数字**都是源码里的常量或兜底值,不是实验结果**。**不要把任何一个包装成"性能提升"**。
> 面试里唯一站得住的表述是:**"这些数字在我的代码里是配置值/阈值/兜底值,不是评测结果。评测结果我这边没有可引用的产出。"**

---

## 第 12 步 · 跟读复述

**用法**:用 VS Code 打开 [planner.py](../src/planner/planner.py),对照着填空。**不用背,能指着代码说出来就够。** 括号里是提示。

### A. 结构类

1. 规划器的三个文件分别是 `planner.py`、`______`、`______`,分别负责 ______、______、______。
2. `_parse_plan` 的输入是 ______,输出是 ______;`get_task_map_from_dag` 的输入是 ______ 和 ______,输出是 ______。
3. 规划器对模型的依赖方式叫 ______(提示:构造时传进来一个 `__call__` 对象),好处是 ______。
4. `self._last_raw_json` 这个字段被 ______ 类读取,读它是因为 ______。

### B. 解析链类(本讲重点)

5. 四步清洗的顺序是:① ______ ② ______ ③ ______ ④ ______。
6. 清洗 ② 用 `.*?`(非贪婪),清洗 ③ 用 `.*`(贪婪)。为什么这么不对称?______
7. 解析兜底共 ______ 级。第二级是 ______,它的副作用是 ______(提示:想想 URL)。
8. 结构校验检查三件事:① ______ ② ______ ③ ______。
9. `PlanParseError` 里的报错信息为什么要写成 `list(data.keys()) if isinstance(data, dict) else type(data)`?______

### C. 降级类(本讲最重要的盲区)

10. 规划产物的解析一共发生 ______ 遍。第二遍比第一遍**少了** ______ 那一级兜底。
11. 第二遍解析失败时,代码做的是 `return ______`,而不是抛异常。这导致编排器走 ______ 分支。
12. 走那个分支之后,子任务描述的固定格式是 `______`,任务类型全变成 ______,`search_hints` 变成 ______。
13. 这条降级路径**不报错**。为什么你看日志发现不了?______(提示:想想日志打印的是什么)
14. 依赖指向不存在的 id 时,代码补了一个 ______ 节点。这个节点**不在** `______` 里,所以分发时必然 ______。
15. 首次规划失败 → 状态机进入 ______,没有任何 ______。
16. 重规划失败,但已有成功结果 → 状态机进入 ______。为什么这两处处理不同?______

### D. 口径类

17. `max_sub_questions` 在 yaml 里是 ______,在 `RunConfig` 默认值是 ______,它**被消费了吗**?______
18. 子任务数量一共有 ______ 处不同口径,其中"usual 3-5"和"3 to 8"的矛盾说明 ______。
19. `timeout_seconds` 在规划器路径下的兜底值是 ______ 秒,在占位符路径下是 ______ 秒。
20. `BudgetTracker` 实现了 `is_over_budget()`,但 ______(提示:谁调用它)。
21. 传给重规划器的"成功结果"被截断到 ______ 字符;筛选阈值 `confidence ≥ 0.6` 里的 confidence 是 ______ 给的。

### E. 口述类(不写,直接说出来)

22. 用 60 秒讲完:"为什么规划器要输出 DAG 而不是列表?"
23. 用 90 秒讲完:"我的 JSON 解析兜底链是什么?它在什么输入下会失效?"
24. 用 60 秒讲完:"为什么我说规划阶段是零降级的?这和后面三级降级矛盾吗?"

---

## 面试速记卡

按 SKILL 第七节第 6 条的**五类 Claim** 归类。**口播里内部名词 ≤ 2 次,每次紧跟通用翻译。**

| # | 类型 | 问 | 答(口语版) |
|---|---|---|---|
| 1 | **Architecture** | 为什么要输出 DAG,不输出列表? | 列表只能串行跑。DAG 多了依赖边,编排器能算出"哪些任务互不依赖、可以一起发出去"。同一个问题,列表要跑四轮,分层 DAG 可能两轮就够。 |
| 2 | **Technical** | 你怎么保证 LLM 输出能被程序解析? | 三层。**第一层在提示词里说清格式**,给完整样例、写"只输出 JSON 不要 markdown";**第二层是清洗**,剥 markdown 围栏、从任意位置抠 JSON、删掉 JSON 标准不支持的尾逗号;**第三层是解析兜底**,直解析失败就剥掉行注释再试一次。最后还是失败就抛异常,交给上层。 |
| 3 | **Technical** | 解析最终失败了会怎样? | 抛一个统一的解析异常,编排器把它当终态处理——**整次研究终止**。这里有个我在复盘时发现的问题:**首次规划这一步是零重试的**,一次坏输出就全盘失败,后面那套重试和降级都走不到了。 |
| 4 | **Architecture** | 你解析链里最大的问题是什么?(高频) | **解析被分成两遍**。一遍生成任务图,一遍重建任务详情,两遍的清洗逻辑是复制的,而且**第二遍少了一级兜底**。结果是:某些输入下第一遍成功、第二遍失败,第二遍返回空,触发占位符降级——**任务描述全变成 "Auto-generated task for xxx" 这种字符串,而且不报错**。正确的做法是让规划函数一次返回两个产物。 |
| 5 | **Technical** | "更激进拆题"是什么? | 首次规划时没有历史结果可参考,我在提示词里额外加一段,让它**多生成一些子任务**保证覆盖面;有过往成果的重规划则相反,提示它**补空白、别重复**。核心是一个判断:**冷启动要铺开,有积累要收敛**。 |
| 6 | **Metric** | 你单任务的超时是多少秒? | **分两个来源**:规划器正常产出的任务,超时由模型在 JSON 里指定,我反序列化时的兜底值是 120 秒;走占位符降级路径的任务,用的是数据类默认的 300 秒。这两个数不一致是历史遗留。 |
| 7 | **Technical** | 重规划保留哪些结果? | 两个条件:状态是成功,**并且**置信度至少 0.6。需要说清的是——**这个置信度是子 Agent 自报的**,是个弱信号,所以这个筛选是**粗筛**不是质量门禁。 |
| 8 | **Architecture** | 重规划时为什么清空结果列表? | 我分两个存储:结果列表清空,**共享记忆不清空**。清结果是为了让失败率统计只反映新一轮,不被旧账污染;不清记忆是因为重规划是增量的,新任务要引用上一轮的产出。 |
| 9 | **Ownership** | 规划器这块是你写的吗? | 是我写的。追溯得比较清楚的两个点:**提示词里那组反例是我自己测试时发现主题漂移问题后加的**;**解析链里"注释剥离会砍断 URL"这个缺陷也是我自己复现出来的**。 |
| 10 | **Technical** | 提示词里的反例为什么写"实习/求职"? | 因为我用它做过测试,发现规划器会把求职类问题跑偏成泛科技新闻盘点,所以补了相关性约束和正反例。**这类约束是踩坑后补的,不是一开始设计的。** |
| 11 | **Architecture** | 你配置里写了最大子问题数,生效吗? | **不生效,是死配置**。yaml 里是 10,数据类默认值 8,但从配置读进来之后**没有任何代码消费它**。真正的数量控制只写在提示词里,是**软约束。** 我复盘时全局搜字段名才发现的。 |
| 12 | **Result** | token 预算是怎么统计的? | **是估算,不是实测**——按输出字符数除以 3 折算的,真实值在 API 的 usage 字段里,我没接。所以它只能做**粗粒度趋势监控**。而且这个组件我**只接了计数,没接决策**:有超预算判断函数,但没人调用它。 |
| 13 | **Technical** | 依赖指向一个不存在的任务会怎样? | 我会**补一个占位节点**,不让一个笔误毁掉整个计划。但实测下来这个降级**不够好**:补出来的节点不在任务详情表里,所以分发时**必然失败一次**,而且会计入失败率、可能误触发重规划。更好的做法是删掉那条边并打警告。 |
| 14 | **Technical** | 输出里带了 URL 会怎样? | **会踩到我的一个 bug。** 我有一级兜底是"逐行去掉行注释",而 URL 里的 `//` 会被当成注释起始,把链接砍断、字符串不闭合,最终解析失败。正确做法是只在字符串外部剥注释。 |
| 15 | **Metric** | 子任务数量到底几个? | 提示词里有**三处不同口径**:规则写 3 到 8,首次规划的追加提示写 6 到 10 还说"通常 3 到 5"。最后一个和前两个是矛盾的,是改提示词没同步的残留。**而且它们全是自然语言,模型可以不听。** |
| 16 | **Result** | 规划质量有评测数字吗? | **没有可引用的评测产出。** 我能给的是源码里的配置值、阈值和兜底值,不是实验结论。 |

> 🎯 **口播提醒**:
> - **内部名词预算**:整场口播里 `Planner` / `SubTask` / `DAG` 这类项目内部名**最多 2 次**,且每次紧跟一句通用翻译,比如"DAG,也就是带依赖关系的任务图"。
> - **不要用"优化了""提升了"这种没有对象的动词。** 每个动作都要有**对象 + 具体做什么 + 证据**。比如不说"优化了解析",说"**加了一级兜底:解析失败后剥掉行注释再试一次**"。
> - 第 4、14、11、12 题是**你主动暴露缺陷**的题,这是**最强的一种 ownership 信号**。遇到 Architecture 类追问,**优先往这三条上引。**
> - 第 16 题是**最容易翻车**的。**不要报 `85%→95%` 这类数字**,仓库里查不到出处。答"没有可引用的评测产出"比报一个说不清口径的数安全得多。

---

## 本篇小结

| 步骤 | 一句话结论 |
|---|---|
| 第 0 步 | 本讲新语法是**"清洗脏文本"那一套**:正则、`json.loads`、字符串切片。 |
| 第 1 步 | 规划器 = **把一句话翻译成 DAG**。难点不在"拆得准",在**"让 LLM 输出能被程序读"**。 |
| 第 2 步 | 提示词里只有 3 条是格式约束,其余 7 条是质量约束。`priority` **有字段没消费**;`expected_type` 却真的进了子 Agent 的 prompt —— **两个看着对称的字段,一个死一个活**。 |
| 第 3 步 | 冷启动"**更激进**"铺开、有记忆收敛。数量口径**三处打架**,而且都是软约束。 |
| 第 4 步 | 规划器对模型是**依赖注入**的。token 预算是**估算**(÷3),而且**只计数不决策**。 |
| 第 5 步 | 四步清洗 + 两级解析兜底。**`//` 剥离会砍断 URL**——我实测复现了。 |
| 第 6 步 | `task_type` **静默降级**、`timeout` **300 vs 120** 两条路径不一致。 |
| 第 7 步 | **本讲最重要的盲区**:解析分两遍,第二遍少一级兜底 → **静默降级成占位符,不报错**。 |
| 第 8 步 | 环检测**顺便**用 Kahn 做,零成本。无效依赖造出"**幽灵任务**"。**首次规划零降级**。 |
| 第 9 步 | 重规划保留 `success + confidence≥0.6`,**截断 500 字符**。清结果、**不清记忆**。失败时**有货就降级合成**。 |
| 第 10 步 | 五处真相:死配置、数量三口径、超时 300/120、静默降级、追踪开关的 `None.lower()` 崩溃。 |
| 第 11 步 | **本讲所有数字都是常量/阈值/兜底值,没有一个是实验结果。** |

**面试最强的四句话**(都是主动暴露缺陷,不是背优点):

1. "我的解析链**分两遍**,第二遍少一级兜底,**失败时静默降级成占位符,不报错**——这是最大的结构问题。"
2. "**首次规划是零重试的**,一次坏输出整轮终止,后面那套三级降级根本走不到。"
3. "我的注释剥离会**砍断 URL**,我实测复现过。"
4. "`max_sub_questions` 是**死配置**——'配置项存在'和'配置项生效'是两件事。"

---

## 三处讲不清时的诚实路径

按 SKILL 第七节第 7 条,本讲有三个点**如果讲不清,只有三条路,不要编**:

| 讲不清的点 | ① 补真实事实 | ② 补相关知识(读哪段源码) | ③ 降低表述强度 |
|---|---|---|---|
| **为什么 `÷3` 估 token** | 没有实测依据,是经验折算 | [budget_tracker.py:47-52](../src/planner/budget_tracker.py#L47-L52),再看真实 token 该从 API 的 `usage` 字段读 | 别说"token 预算控制",说"**token 消耗的粗略计数**" |
| **重规划到底提升多少** | 仓库里**没有** ablation 对比数据 | [orchestrator.py:519-537](../src/orchestrator/orchestrator.py#L519-L537) 只看触发逻辑 | 别说"重规划提升成功率",说"**实现了失败率触发重规划的机制,收益没有单独测**" |
| **占位符降级实际多常见** | **不知道**。仓库里没有"发生过几次"的日志统计 | 读 [orchestrator.py:612-634](../src/orchestrator/orchestrator.py#L612-L634) 看清降级产物长什么样 | 别说"很少发生",说"**这条路径存在,我没有统计过它的触发频率**" |

> ⚠️ **第三行尤其重要**。我在第 7 步说"实测复现了这条降级路径"——**复现的是"路径存在且可达",不是"它经常发生"**。这两个结论强度完全不同,**不要在面试里被追问时从前者滑到后者**。

---

**下一讲预告:第 5 讲 · 上下文压缩 M3**——一份两万字符的报告塞进上下文会发生什么、三级压缩(**L1 过滤 → L2 抽取 → L3 摘要**)各自砍掉什么、为什么那组 `0.6 / 0.8 / 0.95` 是**占用率阈值而不是相似度阈值**(README 把这件事说反了)、以及压缩的保底策略。主读 [compressor.py:92-168](../src/compressor/compressor.py#L92-L168)。
