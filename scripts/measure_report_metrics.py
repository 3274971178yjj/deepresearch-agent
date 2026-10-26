#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/measure_report_metrics.py
================================================================================
对 outputs/ 下已有的报告跑规则指标，产出真实数值。

特点：
  - 不需要 API Key、不需要联网、不加载 torch / sentence-transformers
  - 只依赖标准库 + evaluation/metrics/rule_based.py
  - 同时给出两套"引用覆盖率"口径：
      口径 A：RuleBasedMetrics.citation_coverage（原样，看它认哪些格式）
      口径 B：识别报告实际使用的 （Result N） 格式

用法：
    python scripts/measure_report_metrics.py
    python scripts/measure_report_metrics.py --dir outputs --json out.json
================================================================================
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parent.parent
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import importlib.util
import json
import re
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load_rule_based():
    """直接从文件加载 rule_based.py，绕开 evaluation.metrics.__init__ 的重依赖。"""
    path = ROOT / "evaluation" / "metrics" / "rule_based.py"
    spec = importlib.util.spec_from_file_location("_rb_standalone", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RuleBasedMetrics


# 报告里实际出现的引用标记形态
PAT_ACTUAL_CITE = re.compile(r"[（(]\s*(?:Result|结果|来源)\s*\d+[^）)]{0,40}[）)]")
PAT_BRACKET_NUM = re.compile(r"\[\d+\]")
PAT_URL = re.compile(r"https?://")
PAT_ARXIV = re.compile(r"arxiv\.org", re.IGNORECASE)


def count_citation_markers(report: str) -> dict[str, int]:
    """统计各种引用标记出现次数，用来看指标认不认得到。"""
    return {
        "（Result N）": len(PAT_ACTUAL_CITE.findall(report)),
        "[N]": len(PAT_BRACKET_NUM.findall(report)),
        "http(s)://": len(PAT_URL.findall(report)),
        "arxiv.org": len(PAT_ARXIV.findall(report)),
    }


def adjusted_citation_coverage(report: str) -> float:
    """口径 B：把 （Result N） 也算作有效引用。"""
    paragraphs = [p.strip() for p in report.split("\n") if p.strip()]
    if not paragraphs:
        return 0.0
    cited = 0
    for p in paragraphs:
        if (
            PAT_ACTUAL_CITE.search(p)
            or PAT_BRACKET_NUM.search(p)
            or PAT_URL.search(p)
            or PAT_ARXIV.search(p)
        ):
            cited += 1
    return cited / len(paragraphs)


def collect_reports(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if p.is_file() and p.stat().st_size > 0)


def analyze(report_path: Path, rb: Any) -> dict[str, Any]:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if s.strip()]
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    return {
        "file": str(report_path.relative_to(ROOT)).replace("\\", "/"),
        "chars": len(text),
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "citation_coverage_原文指标": round(rb.citation_coverage(text), 4),
        "citation_coverage_识别Result格式": round(adjusted_citation_coverage(text), 4),
        "hallucination_rate": round(rb.hallucination_rate(text), 4),
        "logical_consistency": round(rb.logical_consistency(text), 4),
        "citation_markers": count_citation_markers(text),
    }


def fmt_table(rows: list[dict[str, Any]]) -> str:
    header = (
        f"{'报告':<26}{'字符':>7}{'段':>5}{'句':>5}"
        f"{'引用覆盖A':>10}{'引用覆盖B':>10}{'幻觉率':>9}{'逻辑一致':>10}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        name = Path(r["file"]).name
        if len(name) > 24:
            name = name[:21] + "..."
        lines.append(
            f"{name:<26}{r['chars']:>7}{r['paragraphs']:>5}{r['sentences']:>5}"
            f"{r['citation_coverage_原文指标']:>10.3f}"
            f"{r['citation_coverage_识别Result格式']:>10.3f}"
            f"{r['hallucination_rate']:>9.3f}"
            f"{r['logical_consistency']:>10.3f}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="对已有报告跑规则指标")
    parser.add_argument("--dir", default="outputs", help="报告所在目录（默认 outputs）")
    parser.add_argument("--json", default=None, help="可选：把结果写入 JSON")
    args = parser.parse_args()

    rb = load_rule_based()
    root = (ROOT / args.dir) if not Path(args.dir).is_absolute() else Path(args.dir)
    reports = collect_reports(root)
    if not reports:
        print(f"[!] {root} 下没有找到 .md 报告")
        return

    rows = [analyze(p, rb) for p in reports]

    print()
    print("=" * 88)
    print("DeepResearch Agent · 已有报告规则指标实测")
    print("=" * 88)
    print(f"报告数: {len(rows)} | 目录: {root}")
    print()
    print(fmt_table(rows))

    # ---- 引用标记形态统计 ----
    print()
    print("-" * 88)
    print("引用标记形态统计（看指标认不认得到）")
    print("-" * 88)
    keys = ["（Result N）", "[N]", "http(s)://", "arxiv.org"]
    print(f"{'报告':<26}" + "".join(f"{k:>14}" for k in keys))
    for r in rows:
        name = Path(r["file"]).name
        if len(name) > 24:
            name = name[:21] + "..."
        print(f"{name:<26}" + "".join(f"{r['citation_markers'][k]:>14}" for k in keys))

    # ---- 汇总 ----
    def mean(key: str) -> float:
        return statistics.fmean(r[key] for r in rows)

    print()
    print("-" * 88)
    print("汇总（均值）")
    print("-" * 88)
    print(f"  引用覆盖率（原文指标口径）    : {mean('citation_coverage_原文指标'):.4f}")
    print(f"  引用覆盖率（识别 Result 格式）: {mean('citation_coverage_识别Result格式'):.4f}")
    print(f"  幻觉率                        : {mean('hallucination_rate'):.4f}")
    print(f"  逻辑一致性                    : {mean('logical_consistency'):.4f}")
    print(f"  平均字符数                    : {mean('chars'):.0f}")
    print()

    if args.json:
        out = Path(args.json)
        out.write_text(
            json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已写入: {out}")


if __name__ == "__main__":
    main()
