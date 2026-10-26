#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/measure_eval_layer.py
================================================================================
评测层体检脚本 —— 零 API、零联网、零新依赖。

回答五个面试高频问题，全部以「可复现的数字 + 文件行号」作答，不靠记忆：

  1. 综合分 composite_score 的权重键是否对得上？（→ 天花板是多少）
  2. logical_consistency 会不会误报？
  3. 题库里有没有脏数据？
  4. --domain 过滤能不能选中题目？
  5. 消融实验的 4 个模块开关，有几个真的接线了？

外加两项清点：
  6. 评测/裁判实现有几份，哪几份是死代码？
  7. 两个数据集的真实规模。

用法：
  PYTHONIOENCODING=utf-8 python scripts/measure_eval_layer.py
  PYTHONIOENCODING=utf-8 python scripts/measure_eval_layer.py --json outputs/eval_layer_metrics.json

设计原则与 measure_report_metrics.py 一致：只读源码文本 + 调用纯函数，
不触发任何 LLM 调用。凡是需要模型才能得出的结论，本脚本一律不下结论。
================================================================================
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# =============================================================================
# 1. 综合分权重键对齐
# =============================================================================
def check_composite_weights() -> dict[str, Any]:
    """对比两条评测路径喂给 composite_score 的 metrics 键名。

    - scripts/run_ablation.py::evaluate_with_rules 用 "factual_accuracy"
    - evaluation/benchmarks/research_bench.py::evaluate_report 用
      "factual_accuracy_str" / "factual_accuracy_sem"

    而 composite_score 的默认权重表里写的是 "factual_accuracy"。
    """
    from evaluation.metrics.rule_based import RuleBasedMetrics

    weights = {
        "factual_accuracy": 0.25,
        "logical_consistency": 0.20,
        "citation_coverage": 0.20,
        "bias": 0.20,
        "comprehensiveness": 0.15,
    }

    # 口径 A：run_ablation.py 的键（与权重表一致）
    metrics_ablation = {
        "factual_accuracy": 1.0,
        "logical_consistency": 1.0,
        "citation_coverage": 1.0,
        "bias": 1.0,
        "comprehensiveness": 1.0,
    }
    # 口径 B：research_bench.evaluate_report 实际产出的键
    metrics_bench = {
        "factual_accuracy_str": 1.0,
        "factual_accuracy_sem": 1.0,
        "logical_consistency": 1.0,
        "citation_coverage": 1.0,
        "bias": 1.0,
        "comprehensiveness": 1.0,
    }

    # 口径 C：直接拿 rule_based.py 的产出（没有 bias 这个键）
    metrics_native = {
        "factual_accuracy": 1.0,
        "logical_consistency": 1.0,
        "citation_coverage": 1.0,
        "comprehensiveness": 1.0,
    }

    all_perfect = RuleBasedMetrics.composite_score(metrics_ablation, weights)
    bench_perfect = RuleBasedMetrics.composite_score(metrics_bench, weights)
    native_perfect = RuleBasedMetrics.composite_score(metrics_native, weights)

    return {
        "权重表": weights,
        "口径A_run_ablation_用键": sorted(metrics_ablation),
        "口径A_全满分时composite": round(all_perfect, 6),
        "口径B_research_bench_用键": sorted(metrics_bench),
        "口径B_全满分时composite": round(bench_perfect, 6),
        "口径C_rule_based原生产出用键": sorted(metrics_native),
        "口径C_全满分时composite": round(native_perfect, 6),
        "结论": (
            f"同一个 composite_score，三个调用方三个天花板："
            f"A={all_perfect:.2f}（键全对）、B={bench_perfect:.2f}（research_bench 口径，"
            f"factual_accuracy 权重 0.25 静默清零）、C={native_perfect:.2f}"
            f"（rule_based 自己产不出 bias 键，0.20 权重静默清零）"
        ),
    }


# =============================================================================
# 2. logical_consistency 误报
# =============================================================================
def check_logical_consistency() -> dict[str, Any]:
    """contradiction_pairs 里 "是"/"不是" 是子串包含关系 →
    任何含 "不是" 的句子都被判为自相矛盾。"""
    from evaluation.metrics.rule_based import RuleBasedMetrics

    cases = [
        "这不是一个好方案。",
        "公司不可以这样做。",
        "这个问题并不会消失。",
        "该方案支持增量更新，反对全量重跑。",   # 真矛盾对，理应命中
        "报告共 3200 字符，引用 15 处。",        # 干净句，理应满分
    ]
    return {
        "对照句": [
            {"句子": s, "logical_consistency": round(RuleBasedMetrics.logical_consistency(s), 4)}
            for s in cases
        ],
        "机理": (
            'rule_based.py 的 contradiction_pairs 含 ("是","不是")、("可以","不可以")、'
            '("会","不会")；判定是 `if a in sentence and b in sentence`，'
            '子串包含 → 含否定形式的句子必然同时命中 a 与 b'
        ),
    }


# =============================================================================
# 2b. 幻觉率探针
# =============================================================================
def check_hallucination_probe() -> dict[str, Any]:
    """拆开 hallucination_rate 的两个部件：

    (a) 豁免条件：句子只要含【任意一对括号】就跳过检查。
    (b) 触发条件：带单位数字 / 绝对化表述 / 模糊引用开头。

    关键发现：豁免正则的开括号类含【，但闭括号类 [\\]）)] 里没有】 →
    项目自己倡导的【来源: ...】格式反而不被豁免。
    """
    from evaluation.metrics.rule_based import RuleBasedMetrics

    cases = [
        ("裸数字_无括号", "2025 年市场规模达到 100 亿美元。"),
        ("括号内是补充说明_非引用", "2025 年市场规模达到 100 亿美元（约合人民币 700 亿）。"),
        ("方括号真引用_N格式", "2025 年市场规模达到 100 亿美元[1]。"),
        ("圆括号真引用", "2025 年市场规模达到 100 亿美元(来源: IDC)。"),
        ("全角方头括号真引用_项目推荐格式", "2025 年市场规模达到 100 亿美元【来源: IDC】。"),
    ]
    rows = [
        {"情形": n, "hallucination_rate": round(RuleBasedMetrics.hallucination_rate(s), 4),
         "句子": s}
        for n, s in cases
    ]

    # 真实报告的构成拆解
    import glob as _glob

    pats = {
        "带单位数字": r"\d+[\d,]*\.?\d*\s*(%|倍|个|人|元|美元|亿|万)",
        "绝对化表述": r"毫无疑问|绝对|必然|一定|众所周知",
        "模糊引用开头": r"据我所知|据了解|研究显示[^【\[（(]",
    }
    tot_sent = tot_sus = 0
    per_pattern = {k: 0 for k in pats}
    per_report = []
    for p in sorted((REPO / "outputs").glob("reports*/report_*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        sents = [s.strip() for s in re.split(r"[。！？\n]", text) if s.strip()]
        hits = {k: 0 for k in pats}
        sus = 0
        for s in sents:
            if re.search(r"[\[【（(].*?[\]）)]", s):
                continue
            for k, pat in pats.items():
                if re.search(pat, s):
                    hits[k] += 1
                    sus += 1
                    break
        tot_sent += len(sents)
        tot_sus += sus
        for k in pats:
            per_pattern[k] += hits[k]
        per_report.append({"报告": p.parent.name, "句数": len(sents), "可疑句": sus,
                           "可疑占比": round(sus / max(len(sents), 1), 4), "各模式": hits})

    return {
        "探针": rows,
        "豁免正则": r"[\[【（(].*?[\]）)]",
        "豁免正则_开括号类": r"[\[【（(]",
        "豁免正则_闭括号类": r"[\]）)]",
        "缺陷": "开括号类含【，闭括号类缺】→【来源: ...】无法构成豁免匹配",
        "真实报告构成": {
            "逐份": per_report,
            "句数合计": tot_sent,
            "可疑句合计": tot_sus,
            "池化口径": round(tot_sus / max(tot_sent, 1), 4),
            "逐份比率的算术平均": round(
                sum(r["可疑占比"] for r in per_report) / max(len(per_report), 1), 4
            ),
            "各模式贡献": per_pattern,
            "模式集中度": round(
                max(per_pattern.values()) / max(sum(per_pattern.values()), 1), 4
            ),
        },
        "口径分歧": (
            "『逐份比率的算术平均』与『池化(总可疑/总句数)』不是同一个数："
            "简历上的 3.34% 是前者，池化口径是 4.24%。被追问要能立刻说出用的哪个"
        ),
    }


# =============================================================================
# 3. 题库脏数据
# =============================================================================
def check_dataset_dirt() -> dict[str, Any]:
    from evaluation.benchmarks.research_bench import ResearchBench

    b = ResearchBench()
    dirty: list[dict[str, Any]] = []
    for q in b.questions:
        for field in ("ground_truth", "expected_topics"):
            v = q.get(field)
            keys = list(v.keys()) if isinstance(v, dict) else (v or [])
            for k in keys:
                if k != k.strip():
                    dirty.append({"题目": q["id"], "字段": field, "值": repr(k)})

    q = next((x for x in b.questions if x["id"] == "fin_005"), None)
    return {
        "脏数据": dirty,
        "脏数据条数": len(dirty),
        "fin_005_ground_truth键": list(q["ground_truth"].keys()) if q else None,
        "影响": (
            "fact_accuracy 用 `key_fact.lower() in report_lower` 逐键子串匹配，"
            "键名带前导空格 → 永远匹配不到 → 该题 factual_accuracy 恒为 0"
        ),
    }


# =============================================================================
# 4. --domain 过滤
# =============================================================================
def check_domain_filter() -> dict[str, Any]:
    from evaluation.benchmarks.research_bench import ResearchBench

    b = ResearchBench()
    real = {}
    for q in b.questions:
        real[q.get("domain")] = real.get(q.get("domain"), 0) + 1

    cli_choices = ["tech", "med", "fin"]
    cli_result = {}
    for c in cli_choices:
        cli_result[c] = len(b.get_questions(domain=c, n=99))

    return {
        "题库真实domain取值": real,
        "域数量": len(real),
        "CLI_choices": cli_choices,
        "CLI_choices命中题数": cli_result,
        "结论": (
            "run_baseline.py:211 / run_ablation.py:293 的 --domain choices 是英文，"
            "数据里的 domain 是中文 → 传任何合法值都选 0 道题 → 下游全部 0.0"
        ),
    }


# =============================================================================
# 5. 消融开关接线审计（AST 静态扫描）
# =============================================================================
def _collect_key_reads() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """扫描 src/，收集两样东西：

    paths: 两级配置路径，如 "adversarial.enabled" -> ["file:line"]
    literals: 所有出现在 .get("x") / ["x"] / setdefault("x") 位置的字面量 -> ["file:line"]

    只读 `src/`（运行时真正消费配置的地方），不读 eval 脚本。
    """
    paths: dict[str, list[str]] = {}
    literals: dict[str, list[str]] = {}

    def _s(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _add(d: dict[str, list[str]], key: str, where: str) -> None:
        d.setdefault(key, [])
        if where not in d[key]:
            d[key].append(where)

    for py in sorted((REPO / "src").rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = py.relative_to(REPO).as_posix()

        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"

            # a.get("X", {}).get("Y", d)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get" and node.args:
                    key = _s(node.args[0])
                    if key:
                        _add(literals, key, where)
                    inner = node.func.value
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "get"
                        and inner.args
                    ):
                        a, b = _s(inner.args[0]), key
                        if a and b:
                            _add(paths, f"{a}.{b}", where)

            # d["X"]["Y"]  /  d.setdefault("X", {})["Y"]
            if isinstance(node, ast.Subscript):
                outer = _s(node.slice)
                inner_v = node.value
                if outer and isinstance(inner_v, ast.Subscript):
                    inner = _s(inner_v.slice)
                    if inner:
                        _add(paths, f"{inner}.{outer}", where)
                if outer and isinstance(inner_v, ast.Call):
                    f = inner_v.func
                    if (
                        isinstance(f, ast.Attribute)
                        and f.attr == "setdefault"
                        and inner_v.args
                    ):
                        a = _s(inner_v.args[0])
                        if a:
                            _add(paths, f"{a}.{outer}", where)

    return paths, literals


def check_ablation_wiring() -> dict[str, Any]:
    """4 个消融臂各自的开关，是否在 src/ 里被读。

    判定标准（两个都要）：该键在 src/ 里以「读取位置」出现，且该处上下文
    指向对应模块段。只出现于 ablation.py / run_baseline.py 自身的 = 未接线。
    """
    paths, literals = _collect_key_reads()

    # 4 个消融臂的 override 键 + 它们的权威读取点（人工核验过的锚点）
    #   read_anchors: 该 override 键在 src/ 里被读的行
    #   effect:       读到之后是否真的改变了行为
    #     - effective : 有消费点，改了行为（附消费点锚点）
    #     - stub      : 读到了，但下游只用于打日志 / 字段无人消费
    #     - unwired   : 字面量在 src/ 里根本没被读
    targets = {
        "no_adversarial": {
            "override": "adversarial.enabled",
            "sub_key": "enabled",
            "section": "adversarial",
            "read_anchors": ["src/core/runner.py:312"],
            "effect": "effective",
            "consumed_at": ["src/orchestrator/orchestrator.py:429"],
            "note": "读进 RunConfig.enable_adversarial，orchestrator 据此决定是否进 ADVERSARIAL 状态",
        },
        "no_compressor": {
            "override": "compressor.enable_multilevel",
            "sub_key": "enable_multilevel",
            "section": "compressor",
            "read_anchors": [],
            "effect": "unwired",
            "consumed_at": [],
            "note": (
                "initialize_modules 只读 compressor.max_context_length / "
                "output_reserve_tokens；enable_multilevel 全仓出现 3 次且都在配置与"
                "消融脚本自身，src/ 无消费点 → ContextCompressor 照常构造"
            ),
        },
        "no_memory": {
            "override": "memory.enabled",
            "sub_key": "enabled",
            "section": "memory",
            "read_anchors": [],
            "effect": "unwired",
            "consumed_at": [],
            "note": (
                "initialize_modules 只读 memory.db_path / session_id，"
                "SharedMemoryStore 无条件构造并注入 Orchestrator → memory.enabled 无消费点"
            ),
        },
        "no_evolution": {
            "override": "evolution.enabled",
            "sub_key": "enabled",
            "section": "evolution",
            "read_anchors": ["src/core/runner.py:264", "src/core/runner.py:313"],
            "effect": "stub",
            "consumed_at": [],
            "note": (
                "两处读到的分支体都只有 logger.info（『预留接口』）；"
                "RunConfig.enable_evolution 在 orchestrator 里无任何消费点 → "
                "M6 是空壳，开关拨动不改变流水线行为"
            ),
        },
    }

    out: dict[str, Any] = {}
    for arm, t in targets.items():
        sub_sites = literals.get(t["sub_key"], [])
        section_sites = literals.get(t["section"], [])
        # 该段内「除开关之外」还有哪些键被读（说明模块本身确实被构造了）
        other_keys = sorted(
            p.split(".", 1)[1]
            for p in paths
            if p.startswith(t["section"] + ".") and p != t["override"]
        )
        out[arm] = {
            "override键": t["override"],
            "src内该配置段读取点": section_sites,
            "段内其它被读键": other_keys,
            "接线状态": t["effect"],
            "读点": t["read_anchors"],
            "消费点": t["consumed_at"],
            "说明": t["note"],
        }

    effective = [k for k, v in out.items() if v["接线状态"] == "effective"]
    stub = [k for k, v in out.items() if v["接线状态"] == "stub"]
    unwired = [k for k, v in out.items() if v["接线状态"] == "unwired"]
    return {
        "逐臂审计": out,
        "生效臂": effective,
        "空壳臂": stub,
        "未接线臂": unwired,
        "总臂数": len(out),
        "结论": (
            f"{len(out)} 个模块消融臂：{len(effective)} 个真正生效 {effective}，"
            f"{len(stub)} 个是空壳 {stub}，{len(unwired)} 个未接线 {unwired}；"
            "空壳臂与未接线臂的产出与 full 相同（同 seed 下逐字节），"
            "跑出来的『差异』只会是 LLM 采样噪声"
        ),
    }


# =============================================================================
# 6. 重复实现 / 死代码清点
# =============================================================================
def check_duplicate_impls() -> dict[str, Any]:
    """找出同一职责的多份实现，并判断是否被定义文件之外引用。"""
    defs: dict[str, list[str]] = {}

    for base in ("src", "evaluation"):
        for py in sorted((REPO / base).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(REPO).as_posix()
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and re.search(
                    r"Metrics$|Judge$|Benchmark$|Evaluator$", node.name
                ):
                    defs.setdefault(node.name, []).append(rel)

    # 全仓引用扫描（排除自身定义文件）
    all_py = [
        p for p in REPO.rglob("*.py")
        if "__pycache__" not in p.parts and "_v" not in p.parts
    ]
    text = {}
    for p in all_py:
        text[p.relative_to(REPO).as_posix()] = p.read_text(
            encoding="utf-8", errors="replace"
        )

    report = {}
    for name, files in sorted(defs.items()):
        pat = re.compile(rf"(?<![\w.]){name}(?![\w])")
        external, same_file = [], []
        for rel, src in text.items():
            hits = pat.findall(src)  # noqa: F841  (存在性判断即可)
            if not hits:
                continue
            if rel in files:
                # 排掉 class X: 这一行本身的定义
                body = "\n".join(
                    ln
                    for ln in src.splitlines()
                    if ln.strip() != f"class {name}:" and not ln.strip().startswith(f"class {name}(")
                )
                if pat.search(body):
                    same_file.append(rel)
            else:
                external.append(rel)
        report[name] = {
            "定义处": files,
            "外部引用文件": external,
            "仅同文件引用": same_file,
            # 判据：有外部引用 = 接在链路上；只有同文件引用 = 仅自身 __main__/CLI 入口
            "判定": "接在链路上" if external else "仅自身入口引用",
        }

    only_self = [k for k, v in report.items() if not v["外部引用文件"]]
    return {
        "实现清点": report,
        "仅自身入口引用的实现": only_self,
        "结论": (
            "判据是『定义文件之外是否有人引用』。仅自身入口引用 ≠ 一定是废代码"
            "（可能是 CLI 入口），但 = 没有接进主调用链，被问到时要说清。"
        ),
    }


# =============================================================================
# 7. 数据集规模
# =============================================================================
def check_dataset_scale() -> dict[str, Any]:
    from evaluation.benchmarks.research_bench import ResearchBench

    b = ResearchBench()
    src = (REPO / "evaluation/benchmarks/research_bench.py").read_text(encoding="utf-8")
    doc_claims = re.findall(r"包含 (\d+) 道", src)

    hp_src = (REPO / "evaluation/benchmarks/hotpotqa.py").read_text(encoding="utf-8")
    i = hp_src.index("_MOCK_DATA")
    j = hp_src.index("def ", i)
    hp_mock = hp_src[i:j].count('"question"')
    hp_needs_net = "load_dataset" in hp_src

    return {
        "ResearchBench_题目数": len(b.questions),
        "ResearchBench_docstring声明": doc_claims,
        "ResearchBench_每题结构": sorted(b.questions[0].keys()),
        "HotpotQA_内置兜底题数": hp_mock,
        "HotpotQA_真实加载需联网": hp_needs_net,
        "HotpotQA_兜底触发条件": "datasets 未安装 / 加载超时 / 离线",
    }


# =============================================================================
# 8. 假数据生成器检测
# =============================================================================
def check_fake_demo_data() -> dict[str, Any]:
    """analyze_ablation.py 无参运行时会不会造数据画图？"""
    src = (REPO / "evaluation/analyze_ablation.py").read_text(encoding="utf-8")
    lines = src.splitlines()

    # 先定位「示例数据」演示块所在行区间，再在区间内抓全部键值对
    demo_lines = [
        i for i, ln in enumerate(lines, 1)
        if "示例数据" in ln or re.search(r'"(adv_\d+|evo_\w+)"\s*:\s*[0-9]', ln)
    ]
    lo = (min(demo_lines) - 1) if demo_lines else 0
    hi = (max(demo_lines) + 1) if demo_lines else 0
    block = "\n".join(lines[lo:hi]) if demo_lines else ""

    hits = []
    for m in re.finditer(r'"(adv_\d+|evo_\w+)"\s*:\s*([0-9.]+)', block):
        hits.append({"line": lo + block[: m.start()].count("\n") + 1,
                     "键": m.group(1), "值": float(m.group(2))})

    return {
        "硬编码演示数值": hits,
        "硬编码数值个数": len(hits),
        "出现演示数据字样": "示例数据" in src,
        "代码行区间": [lo, hi] if demo_lines else [],
        "危害": (
            "evaluation/analyze_ablation.py 的 main() 在未指定输入文件时，"
            "会用这些硬编码数值渲染 dpi=300 的对比图（adv_0→adv_3 上升、evo_off→evo_on 上升）。"
            "图看起来像真实实验结果，但一个数都没测过。"
        ),
    }


# =============================================================================
# 9. 用真实报告量化「键名错配」的分数蒸发
# =============================================================================
def check_composite_on_real_reports() -> dict[str, Any]:
    """拿 5 份真实报告跑两条口径，看错配键到底吃掉多少分。

    ⚠️ 注意：所有报告都对照 tech_001 的 ground_truth / expected_topics 评分。
    这不是「报告的真实得分」（题目并不对应），
    这么做只是为了在真实文本上暴露两条口径之间的落差。
    """
    from evaluation.metrics.rule_based import RuleBasedMetrics as R
    from evaluation.benchmarks.research_bench import ResearchBench

    q = ResearchBench().questions[0]
    gt, et = q["ground_truth"], q["expected_topics"]
    W = {
        "factual_accuracy": 0.25,
        "logical_consistency": 0.20,
        "citation_coverage": 0.20,
        "bias": 0.20,
        "comprehensiveness": 0.15,
    }

    rows = []
    for p in sorted((REPO / "outputs").glob("reports*/report_*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        f_str = R.fact_accuracy(text, gt)
        f_sem = R.semantic_fact_accuracy(text, gt, threshold=0.65)
        h = R.hallucination_rate(text)
        l = R.logical_consistency(text)
        c = R.citation_coverage(text)
        cp = R.comprehensiveness(text, et)
        bias = max(0.0, 1.0 - h)

        A = {"factual_accuracy": f_str, "logical_consistency": l,
             "citation_coverage": c, "bias": bias, "comprehensiveness": cp}
        B = {"factual_accuracy_str": f_str, "factual_accuracy_sem": f_sem,
             "logical_consistency": l, "citation_coverage": c,
             "bias": bias, "comprehensiveness": cp}

        ca, cb = R.composite_score(A, W), R.composite_score(B, W)
        rows.append({
            "报告": p.parent.name,
            "factual_accuracy_str": round(f_str, 4),
            "factual_accuracy_sem": round(f_sem, 4),
            "口径A_composite": round(ca, 4),
            "口径B_composite": round(cb, 4),
            "蒸发": round(ca - cb, 4),
            "理论蒸发": round(0.25 * f_str, 4),
        })

    return {
        "对照题": q["id"],
        "口径A_天花板": 1.0,
        "口径B_天花板": 0.75,
        "逐份": rows,
        "口径分歧": (
            "口径B 下报告分数被硬性压到 0.75 以下；"
            "且 factual_accuracy_sem（语义事实准确率，被注释标为『面试强化版』）"
            "根本不在权重表里，算了也不进 composite"
        ),
    }


# =============================================================================
# 主流程
# =============================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="评测层体检（零 API）")
    ap.add_argument("--json", type=str, default=None, help="把结果写入 JSON")
    args = ap.parse_args()

    result: dict[str, Any] = {
        "1_综合分权重键": check_composite_weights(),
        "2_逻辑一致性误报": check_logical_consistency(),
        "2b_幻觉率探针": check_hallucination_probe(),
        "3_题库脏数据": check_dataset_dirt(),
        "4_domain过滤": check_domain_filter(),
        "5_消融开关接线": check_ablation_wiring(),
        "6_实现清点": check_duplicate_impls(),
        "7_数据集规模": check_dataset_scale(),
        "8_假数据生成器": check_fake_demo_data(),
        "9_真实报告上的键名落差": check_composite_on_real_reports(),
    }

    print("=" * 74)
    print("评测层体检（零 API / 零联网）")
    print("=" * 74)

    a = result["1_综合分权重键"]
    print("\n[1] 综合分权重键 —— 同一函数三个天花板")
    print(f"    口径A run_ablation   全满分 composite = {a['口径A_全满分时composite']}  键={a['口径A_run_ablation_用键']}")
    print(f"    口径B research_bench 全满分 composite = {a['口径B_全满分时composite']}  键={a['口径B_research_bench_用键']}")
    print(f"    口径C rule_based原生 全满分 composite = {a['口径C_全满分时composite']}  键={a['口径C_rule_based原生产出用键']}")
    print(f"    {a['结论']}")

    b = result["2_逻辑一致性误报"]
    print("\n[2] logical_consistency 误报")
    for c in b["对照句"]:
        print(f"    {c['logical_consistency']:>6.4f}  {c['句子']}")

    b2 = result["2b_幻觉率探针"]
    print("\n[2b] hallucination_rate 探针（分母=1 句）")
    for row in b2["探针"]:
        print(f"    {row['hallucination_rate']:>6.4f}  {row['情形']}")
    rc = b2["真实报告构成"]
    print(f"    真实报告：{rc['句数合计']} 句 → {rc['可疑句合计']} 可疑句")
    print(f"      逐份比率算术平均 = {rc['逐份比率的算术平均']}（← 简历口径）"
          f"  池化 = {rc['池化口径']}")
    print(f"      模式贡献 = {rc['各模式贡献']}  集中度 {rc['模式集中度']}")

    c = result["3_题库脏数据"]
    print(f"\n[3] 题库脏数据：{c['脏数据条数']} 条")
    for d in c["脏数据"]:
        print(f"    {d['题目']} · {d['字段']} = {d['值']}")

    d = result["4_domain过滤"]
    print(f"\n[4] domain 过滤：真实域 {d['域数量']} 个 = {d['题库真实domain取值']}")
    print(f"    CLI --domain {d['CLI_choices']} → 命中 {d['CLI_choices命中题数']}")

    e = result["5_消融开关接线"]
    print(f"\n[5] 消融开关接线：生效 {len(e['生效臂'])} / 空壳 {len(e['空壳臂'])} / 未接线 {len(e['未接线臂'])}")
    tagmap = {"effective": "✅生效  ", "stub": "⚠️空壳  ", "unwired": "❌未接线"}
    for arm, v in e["逐臂审计"].items():
        print(f"    {tagmap[v['接线状态']]} {arm:18s} {v['override键']}")
        if v["读点"]:
            print(f"            读点 = {v['读点']}  消费点 = {v['消费点'] or '无'}")

    f = result["6_实现清点"]
    print("\n[6] 实现清点")
    for name, v in f["实现清点"].items():
        ext = v["外部引用文件"]
        flag = f"外部引用 {len(ext)} 处" if ext else "仅自身入口引用"
        print(f"    [{flag:14s}] {name:22s} 定义于 {v['定义处']}")

    g = result["7_数据集规模"]
    print("\n[7] 数据集规模")
    print(f"    ResearchBench: {g['ResearchBench_题目数']} 题 (docstring 声明 {g['ResearchBench_docstring声明']})")
    print(f"    HotpotQA 兜底: {g['HotpotQA_内置兜底题数']} 题, 真实加载需联网={g['HotpotQA_真实加载需联网']}")

    h = result["8_假数据生成器"]
    print(f"\n[8] 假数据生成器：{h['硬编码数值个数']} 个硬编码数值（行 {h['代码行区间']}）")
    for x in h["硬编码演示数值"]:
        print(f"    analyze_ablation.py:{x['line']}  {x['键']} = {x['值']}")

    i = result["9_真实报告上的键名落差"]
    print(f"\n[9] 真实报告上的键名落差（对照题 {i['对照题']}，仅用于暴露口径差）")
    print(f"    {'报告':<9}{'A口径':>9}{'B口径':>9}{'蒸发':>9}")
    for r in i["逐份"]:
        print(f"    {r['报告']:<9}{r['口径A_composite']:>9.4f}"
              f"{r['口径B_composite']:>9.4f}{r['蒸发']:>9.4f}")
    print(f"    口径A 天花板 {i['口径A_天花板']} / 口径B 天花板 {i['口径B_天花板']}")

    if args.json:
        out = REPO / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
