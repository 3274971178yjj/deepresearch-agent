"""M5 对抗循环（verdict.py / loop.py）体检脚本。

零 API、零网络、零新依赖。只做静态读取 + 纯函数调用 + 对 outputs/ 的实测扫描，
用来复现 `learn-notes/面试知识库-项目二-对抗循环M5.md` 里的每一个数字。

用法：
    PYTHONIOENCODING=utf-8 python scripts/measure_m5_verdict.py
    PYTHONIOENCODING=utf-8 python scripts/measure_m5_verdict.py --json outputs/m5_verdict_metrics.json
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.adversarial.verdict import (  # noqa: E402
    Dimension,
    FixOperation,
    FixType,
    Issue,
    RedVerdict,
    Severity,
    VerdictEngine,
)

M5_FILES = [
    REPO / "src/adversarial/verdict.py",
    REPO / "src/adversarial/loop.py",
    REPO / "src/adversarial/red_agent.py",
    REPO / "src/adversarial/blue_agent.py",
]


def _all_py() -> list[Path]:
    return [
        p
        for p in REPO.rglob("*.py")
        if ".venv" not in p.parts and "site-packages" not in p.parts
    ]


def _ship_py() -> list[Path]:
    """只扫上线代码（src/ + evaluation/），排除本脚本自身，避免自证式计数。"""
    return [
        p
        for p in _all_py()
        if p.relative_to(REPO).parts[0] in ("src", "evaluation")
    ]


# ---------------------------------------------------------------------------
# 1 · Issue 的相等性机制
# ---------------------------------------------------------------------------
def check_issue_hash() -> dict:
    """Issue 为什么要手写 __hash__：dataclass 在 eq=True/frozen=False 时置 None。"""

    @dataclass
    class OnlyEq:
        a: int

        def __eq__(self, o: object) -> bool:
            return isinstance(o, OnlyEq) and self.a == o.a

    hashable_ok = True
    try:
        {OnlyEq(1)}  # type: ignore[arg-type]
    except TypeError:
        hashable_ok = False

    # evidence 不参与相等性：同一问题、不同论据，应判等
    a = Issue(Severity.MAJOR, Dimension.FACTUAL, "数字错了", "para3", FixType.IN_PLACE, "来源A原文")
    b = Issue(Severity.MAJOR, Dimension.FACTUAL, "数字错了", "para3", FixType.IN_PLACE, "完全不同的证据")

    # description 是自由文本：同一问题、不同措辞，判不等 → 震荡检测漏报
    i1 = Issue(Severity.MAJOR, Dimension.FACTUAL, "2024年市场规模应引用IDC数据", "para2")
    i2 = Issue(Severity.MAJOR, Dimension.FACTUAL, "市场规模数字缺少 IDC 来源引用", "para2")

    fields_in_eq = [f for f in ("severity", "dimension", "description", "location", "fix_type", "evidence")]
    src = (REPO / "src/adversarial/verdict.py").read_text(encoding="utf-8")
    eq_block = src[src.index("    def __eq__") : src.index("    def __eq__") + 700]

    return {
        "dataclass_无hash时__hash__": str(OnlyEq.__hash__),
        "dataclass_无hash时可入set": hashable_ok,
        "Issue可入set": len({Issue(Severity.MINOR, Dimension.COVERAGE, "x")}) == 1,
        "evidence不同是否判等": a == b,
        "evidence不同的两条去重后剩": len({a, b}),
        "措辞不同是否判等": i1 == i2,
        "相等性涉及的字段": [f for f in fields_in_eq if f in eq_block],
    }


# ---------------------------------------------------------------------------
# 2 · 枚举：名字 vs 值
# ---------------------------------------------------------------------------
def check_enums() -> dict:
    out: dict = {}
    for enum_cls in (Severity, FixType, Dimension):
        rows = []
        for m in enum_cls:
            # 只把"语义错位"标出来：值不是名字的小写形式
            mismatch = m.value != m.name.lower()
            rows.append({"名": m.name, "值": m.value, "语义错位": mismatch})
        out[enum_cls.__name__] = rows

    # 证明错位的后果：用枚举名当值去反序列化会炸
    try:
        Issue.from_dict({"severity": "minor", "dimension": "fact_check",
                         "description": "x", "fix_type": "supplementary"})
        supplementary_by_name = "未报错"
    except ValueError as e:
        supplementary_by_name = f"ValueError: {e}"

    # 未知枚举值反序列化的行为
    unknown: dict[str, str] = {}
    for label, fn in (
        ("severity=blocker", lambda: Issue.from_dict({"severity": "blocker", "dimension": "fact_check", "description": "x"})),
        ("dimension=mind_reading", lambda: RedVerdict.from_dict({"dimension_scores": {"mind_reading": 5.0}})),
    ):
        try:
            fn()
            unknown[label] = "未报错"
        except ValueError as e:
            unknown[label] = f"ValueError: {e}"

    out["_用枚举名当值的后果"] = supplementary_by_name
    out["_未知枚举值反序列化"] = unknown
    return out


# ---------------------------------------------------------------------------
# 3 · compute_overall：缺失维度怎么处理
# ---------------------------------------------------------------------------
def check_overall() -> dict:
    full = {d: 8.0 for d in Dimension}
    three = {Dimension.FACTUAL: 8.0, Dimension.HALLUCINATION: 8.0, Dimension.LOGICAL: 8.0}
    mixed = {Dimension.FACTUAL: 10.0, Dimension.COVERAGE: 0.0}
    return {
        "五维全8.0": round(VerdictEngine.compute_overall(full), 4),
        "只给三维都8.0": round(VerdictEngine.compute_overall(three), 4),
        "缺失维度时_重新归一化": round(VerdictEngine.compute_overall(three), 4) == 8.0,
        "只给FACTUAL10_COVERAGE0": round(VerdictEngine.compute_overall(mixed), 4),
        "空字典": VerdictEngine.compute_overall({}),
        "越界输入99_被clamp": round(VerdictEngine.compute_overall({Dimension.FACTUAL: 99.0}), 4),
        "权重表": {d.value: w for d, w in VerdictEngine.DIMENSION_WEIGHTS.items()},
        "权重和": round(sum(VerdictEngine.DIMENSION_WEIGHTS.values()), 6),
    }


# ---------------------------------------------------------------------------
# 4 · compute_delta 的量纲 vs 配置阈值
# ---------------------------------------------------------------------------
def check_delta() -> dict:
    def d(a: float, b: float) -> float:
        return round(VerdictEngine.compute_delta({x: a for x in Dimension}, {x: b for x in Dimension}), 4)

    max_delta = VerdictEngine.compute_delta({x: 0.0 for x in Dimension}, {x: 10.0 for x in Dimension})
    yaml_thr, default_thr = 0.2, 0.3

    # 阈值反推：五维等量变化时，每维能变多少
    def per_dim_for(thr: float) -> float:
        return round(thr / math.sqrt(len(Dimension)), 4)

    return {
        "全等": d(5.0, 5.0),
        "每维+0.1_共5维": d(5.0, 5.1),
        "每维+0.2_共5维": d(5.0, 5.2),
        "每维+1.0_共5维": d(5.0, 6.0),
        "理论最大_全0到全10": round(max_delta, 4),
        "实际配置阈值0.2": yaml_thr,
        "函数默认阈值0.3": default_thr,
        "阈值0.2占理论最大百分比": round(yaml_thr / max_delta * 100, 2),
        "阈值0.2等价于每维变化": per_dim_for(yaml_thr),
        "阈值0.3等价于每维变化": per_dim_for(default_thr),
        "每维+0.1是否触发0.2阈值": d(5.0, 5.1) < yaml_thr,
        "每维+0.2是否触发0.2阈值": d(5.0, 5.2) < yaml_thr,
    }


# ---------------------------------------------------------------------------
# 5 · compute_priority 的取值范围与乘数
# ---------------------------------------------------------------------------
def check_priority() -> dict:
    rows = []
    for s in Severity:
        for dm in Dimension:
            for f in FixType:
                rows.append((VerdictEngine.compute_priority(Issue(s, dm, "", "", f)), s, dm, f))
    rows.sort(key=lambda r: r[0])
    lo, hi = rows[0], rows[-1]
    return {
        "最低": {"分": round(lo[0], 4), "组合": f"{lo[1].name}/{lo[2].name}/{lo[3].name}"},
        "最高": {"分": round(hi[0], 4), "组合": f"{hi[1].name}/{hi[2].name}/{hi[3].name}"},
        "跨度倍数": round(hi[0] / lo[0], 1),
        "SEVERITY_WEIGHTS": {k.name: v for k, v in VerdictEngine.SEVERITY_WEIGHTS.items()},
        "FIX_DIFFICULTY": {k.name: v for k, v in VerdictEngine.FIX_DIFFICULTY.items()},
        "难度名与乘数方向相反": (
            VerdictEngine.FIX_DIFFICULTY[FixType.IN_PLACE]
            > VerdictEngine.FIX_DIFFICULTY[FixType.SUPPLEMENTARY]
        ),
        "公式": "severity_weight × dimension_weight × fix_difficulty（连乘，非相加）",
    }


# ---------------------------------------------------------------------------
# 6 · 序列化对称性 + 调用点统计
# ---------------------------------------------------------------------------
def check_serialization_and_callers() -> dict:
    symmetric = {
        cls.__name__: {"to_dict": hasattr(cls, "to_dict"), "from_dict": hasattr(cls, "from_dict")}
        for cls in (Issue, RedVerdict, FixOperation)
    }

    # 全仓统计：哪些 verdict.py 的公开 API 从来没被外部调用
    targets = {
        "VerdictEngine.to_json": 0,
        "VerdictEngine.from_json": 0,
        "VerdictEngine.compute_overall": 0,
        "VerdictEngine.compute_delta": 0,
        "VerdictEngine.compute_priority": 0,
    }
    where: dict[str, list[str]] = {k: [] for k in targets}
    for py in _ship_py():
        rel = py.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("to_json", "from_json"):
                key = f"VerdictEngine.{node.attr}"
                if key in targets:
                    where[key].append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr in (
                "compute_overall", "compute_delta", "compute_priority"
            ):
                key = f"VerdictEngine.{node.attr}"
                where[key].append(f"{rel}:{node.lineno}")
    for k, locs in where.items():
        targets[k] = len(locs)

    return {
        "序列化对称性": symmetric,
        "API调用点数": targets,
        "零调用的API": [k for k, v in targets.items() if v == 0],
        "各API出现位置": {k: v for k, v in where.items()},
    }


# ---------------------------------------------------------------------------
# 7 · 死配置 / 审计链是否接线
# ---------------------------------------------------------------------------
def check_dead_config_and_audit() -> dict:
    # adversarial 段所有配置键，以及它们在 .py 里的读点
    yaml_path = REPO / "configs/default.yaml"
    keys: list[str] = []
    in_block = False
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^adversarial:\s*$", line):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line):  # 离开该段
                break
            m = re.match(r"^\s{2}([a-z_]+):", line)
            if m:
                keys.append(m.group(1))

    def read_points(key: str) -> list[str]:
        pat = re.compile(rf'get\(\s*["\']{re.escape(key)}["\']')
        hits = []
        for py in _all_py():
            rel = py.relative_to(REPO).as_posix()
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line) and "runner.py" in rel:
                    hits.append(f"{rel}:{i}")
        return hits

    cfg = {k: read_points(k) for k in keys}

    # history 在 orchestrator 里的去向
    orch = (REPO / "src/orchestrator/orchestrator.py").read_text(encoding="utf-8")
    hist_lines = [
        f"orchestrator.py:{i}: {ln.strip()}"
        for i, ln in enumerate(orch.splitlines(), 1)
        if "history" in ln
    ]
    hist_stored = any(
        ("memory_store" in ln or "=" in ln.split("history")[0]) and "history]" in ln
        for ln in hist_lines
    )

    # outputs/ 里有没有对抗日志
    adv_files = [
        p.relative_to(REPO).as_posix()
        for p in (REPO / "outputs").rglob("*")
        if p.is_file() and re.search(r"advers|round|verdict", p.name, re.I)
    ]

    return {
        "adversarial段配置键": keys,
        "各键在runner.py的读点": cfg,
        "死配置_无读点": [k for k, v in cfg.items() if not v],
        "orchestrator里history的用法": hist_lines,
        "history被落盘": hist_stored,
        "outputs里的对抗日志": adv_files,
        "对抗轮数是否会落盘说明": (
            "loop.py 构造了含 dimension_scores/delta/fix_operations/stop_reason 的每轮记录，"
            "但 orchestrator 只取 len(history)，记录本身不落盘"
        ),
    }


# ---------------------------------------------------------------------------
# 8 · 实测：真实报告里的对抗轮数 / 置信度 / final_score
# ---------------------------------------------------------------------------
def check_real_reports() -> dict:
    out: dict = {"每份报告": []}
    for rdir in sorted((REPO / "outputs").glob("reports*")):
        for md in sorted(rdir.glob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace")
            def grab(label: str) -> str | None:
                m = re.search(rf"-\s*\*\*{label}\*\*:\s*([0-9.]+)", text)
                return m.group(1) if m else None

            out["每份报告"].append({
                "文件": md.relative_to(REPO).as_posix(),
                "对抗轮数": grab("对抗轮数"),
                "置信度": grab("置信度"),
                "final_score_是否出现在报告里": "final_score" in text,
            })
    rounds = [
        int(r["对抗轮数"]) for r in out["每份报告"] if r["对抗轮数"] is not None
    ]
    if rounds:
        out["轮数汇总"] = {
            "样本数": len(rounds),
            "各次轮数": rounds,
            "命中上限10的次数": sum(1 for r in rounds if r >= 10),
            "命中上限比例": f"{sum(1 for r in rounds if r >= 10)}/{len(rounds)}",
            "最小值": min(rounds),
            "最大值": max(rounds),
        }
    out["final_score在报告中出现"] = any(
        r["final_score_是否出现在报告里"] for r in out["每份报告"]
    )
    return out


# ---------------------------------------------------------------------------
# 9 · 收敛判据的检查顺序
# ---------------------------------------------------------------------------
def check_convergence_order() -> dict:
    max_rounds, score_thr, delta_thr = 10, 9.5, 0.2

    def check(round_idx: int, overall: float, delta: float) -> str:
        if round_idx >= max_rounds:
            return f"max_rounds_reached({max_rounds})"
        if overall >= score_thr:
            return f"score_threshold_met({overall:.2f}>={score_thr})"
        if round_idx > 1 and delta < delta_thr:
            return f"delta_converged({delta:.3f}<{delta_thr})"
        return ""

    probes = [
        (1, 9.6, 0.0),
        (2, 9.6, 0.05),
        (2, 5.0, 0.05),
        (2, 5.0, 1.20),
        (9, 9.9, 0.0),
        (10, 9.9, 0.0),
    ]
    return {
        "实际配置": {"max_rounds": max_rounds, "score_threshold": score_thr, "delta_threshold": delta_thr},
        "探针": [{"round": r, "score": s, "delta": d, "stop_reason": check(r, s, d)} for r, s, d in probes],
        "round10必报max_rounds": check(10, 9.9, 0.0).startswith("max_rounds_reached"),
        "score与delta在早停时不可区分": (
            "早停时 stop_reason 会写明是哪一个；但轮数记录不落盘，"
            "所以从 outputs/ 只能看到轮数，无法反推早停原因"
        ),
    }


# ---------------------------------------------------------------------------
# 10 · 收敛判据用的是修复前还是修复后的分数
# ---------------------------------------------------------------------------
def check_score_semantics() -> dict:
    return {
        "loop_attack": "loop.py:96   verdict = await red_agent.attack(current)",
        "loop_defend": "loop.py:118  fixed_report, operations = await blue_agent.defend(current, verdict)",
        "loop_convergence_input": "loop.py:135  _check_convergence(round_idx, verdict.overall_score, delta)",
        "结论": (
            "verdict 在 defend 之前产生，所以收敛判据用的是【本轮修复前】的分数；"
            "final_score = history[-1]['overall_score'] 同样是修复前分数，是保守下界"
        ),
        "delta比的是哪两轮": (
            "compute_delta(prev_scores, verdict.dimension_scores) —— 两个输入都是'攻击时'的分数，"
            "中间隔着的是上一轮的修复动作，所以 delta 衡量的是上一轮修复的边际收益"
        ),
        "schemas_docstring说": "schemas.py:124  final_score: 最终综合评分（由外部评测模块写入）",
        "实际写入点": "loop.py:157  current.final_score = history[-1]['overall_score']",
        "docstring与实现是否一致": False,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    report = {
        "1_Issue相等性机制": check_issue_hash(),
        "2_枚举名与值": check_enums(),
        "3_compute_overall": check_overall(),
        "4_compute_delta量纲": check_delta(),
        "5_compute_priority": check_priority(),
        "6_序列化与调用点": check_serialization_and_callers(),
        "7_死配置与审计链": check_dead_config_and_audit(),
        "8_真实报告实测": check_real_reports(),
        "9_收敛判据顺序": check_convergence_order(),
        "10_分数语义": check_score_semantics(),
    }

    for section, body in report.items():
        print(f"\n{'=' * 72}\n{section}\n{'=' * 72}")
        print(json.dumps(body, ensure_ascii=False, indent=2, default=str))

    if args.json_out:
        out = REPO / args.json_out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n[written] {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
