#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第 0 层指标：零依赖、零 API、零成本。

只用两样东西：
  1. outputs/reports*/ 里已有的最终报告文本
  2. 仓库源码本身（ast 静态分析）

不需要 LLM，不需要 embedding，不需要联网。
只依赖 numpy / scikit-learn（均已安装）。

用法:
    PYTHONIOENCODING=utf-8 python scripts/measure_extra_metrics.py
    PYTHONIOENCODING=utf-8 python scripts/measure_extra_metrics.py --json outputs/extra_metrics.json
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parent.parent
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 引用口径
# 口径 A：论文式 / 通用式引用标记
PAT_GENERIC_CITE = re.compile(
    r"\[\d+\]"                 # [1]
    r"|【来源[:：][^】]*】"      # 【来源: xxx】
    r"|https?://[^\s)）]+"      # 裸 URL
    r"|arXiv[:：]\s*\d{4}\.\d{4,5}"  # arXiv:2401.12345
)
# 口径 B：本项目报告实际使用的引用格式
PAT_ACTUAL_CITE = re.compile(r"[（(]\s*(?:Result|结果|来源)\s*\d+[^）)]{0,40}[）)]")

# ---------------------------------------------------------------- 结构信号
SECTION_HINTS = {
    "摘要": ["摘要", "概述", "引言", "背景"],
    "结论": ["结论", "总结", "结语"],
    "参考": ["参考", "引用", "文献", "来源列表"],
    "分节": ["#"],
}


def load_reports() -> list[dict]:
    """读取 outputs/reports*/ 下的所有 .md 报告。"""
    reports = []
    for p in sorted(REPO.glob("outputs/reports*/**/*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        reports.append(
            {
                "name": p.parent.name,
                "file": str(p.relative_to(REPO)),
                "text": text,
            }
        )
    return reports


def split_paragraphs(text: str, min_len: int = 50) -> list[str]:
    """按空行切段，丢掉过短的（标题、列表碎片）。"""
    raw = re.split(r"\n\s*\n", text)
    return [p.strip() for p in raw if len(p.strip()) >= min_len]


def near_duplicate_rate(paras: list[str], threshold: float = 0.85) -> float:
    """段落级近重复率：有多少段落与「更靠前」的某段高度相似。

    用字符 3-gram TF-IDF 余弦（中文不需要分词器）。
    返回被判定为近重复的段落占比。
    """
    if len(paras) < 2:
        return 0.0
    try:
        vec = TfidfVectorizer(analyzer="char", ngram_range=(3, 4), min_df=1)
        mat = vec.fit_transform(paras)
    except ValueError:
        return 0.0
    sim = cosine_similarity(mat)
    np.fill_diagonal(sim, 0.0)
    # 只看上三角：第 i 段是否与某个更靠前的段重复
    dup = 0
    for i in range(len(paras)):
        if sim[i, :i].max(initial=0.0) >= threshold:
            dup += 1
    return dup / len(paras)


def citation_stats(text: str, paras: list[str], pattern: re.Pattern) -> dict:
    """引用密度 + 引用在段落间的分布（含前后半段对比）。"""
    hits = pattern.findall(text)
    total = len(hits)
    n_para = max(len(paras), 1)
    cited = sum(1 for p in paras if pattern.search(p))

    half = len(paras) // 2
    first_half = [p for p in paras[:half]] if half else []
    second_half = [p for p in paras[half:]]
    f_cited = sum(1 for p in first_half if pattern.search(p))
    s_cited = sum(1 for p in second_half if pattern.search(p))

    return {
        "引用标记总数": total,
        "引用密度_每千字": round(total / max(len(text), 1) * 1000, 3),
        "含引用段落占比": round(cited / n_para, 3),
        "前半段含引用占比": round(f_cited / max(len(first_half), 1), 3),
        "后半段含引用占比": round(s_cited / max(len(second_half), 1), 3),
    }


def section_stats(text: str) -> dict:
    """章节结构：标题数 + 摘要/结论/参考三类关键章节是否齐备。"""
    headings = re.findall(r"^#{1,6}\s+\S", text, flags=re.MULTILINE)
    present = {}
    for key, hints in SECTION_HINTS.items():
        if key == "分节":
            continue
        present[key] = any(h in text for h in hints)
    return {
        "标题数": len(headings),
        "关键章节齐备数": sum(present.values()),
        "关键章节明细": present,
    }


# ---------------------------------------------------------------- 代码静态分析
def code_stats(dirs: list[str]) -> dict:
    """对 src/ 与 evaluation/ 做 ast 静态分析。"""
    n_files = n_lines = n_func = n_async = n_doc = 0
    n_args = n_args_ann = n_ret = n_ret_ann = 0
    func_lengths: list[tuple[int, str]] = []

    for d in dirs:
        root = REPO / d
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            n_files += 1
            src = py.read_text(encoding="utf-8", errors="replace")
            lines = src.splitlines()
            n_lines += len(lines)
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    n_func += 1
                    if isinstance(node, ast.AsyncFunctionDef):
                        n_async += 1
                    if ast.get_docstring(node):
                        n_doc += 1
                    # 参数注解
                    a = node.args
                    all_args = (
                        list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
                    )
                    for arg in all_args:
                        if arg.arg in ("self", "cls"):
                            continue
                        n_args += 1
                        if arg.annotation is not None:
                            n_args_ann += 1
                    if a.vararg:
                        n_args += 1
                        n_args_ann += 1 if a.vararg.annotation else 0
                    if a.kwarg:
                        n_args += 1
                        n_args_ann += 1 if a.kwarg.annotation else 0
                    # 返回值注解
                    n_ret += 1
                    if node.returns is not None:
                        n_ret_ann += 1
                    # 函数体长度
                    if node.end_lineno and node.lineno:
                        func_lengths.append(
                            (
                                node.end_lineno - node.lineno + 1,
                                f"{py.relative_to(REPO).as_posix()}:{node.name}",
                            )
                        )

    func_lengths.sort(reverse=True)
    loc = n_lines
    return {
        "文件数": n_files,
        "总行数": loc,
        "函数总数": n_func,
        "async函数数": n_async,
        "async占比": round(n_async / max(n_func, 1), 3),
        "有docstring函数占比": round(n_doc / max(n_func, 1), 3),
        "参数注解覆盖率": round(n_args_ann / max(n_args, 1), 3),
        "返回值注解覆盖率": round(n_ret_ann / max(n_ret, 1), 3),
        "最长函数Top5": [{"行数": n, "位置": s} for n, s in func_lengths[:5]],
    }


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="零依赖指标测量（不需要任何 API Key）")
    ap.add_argument("--json", type=str, default=None, help="把结果写到指定 json 路径")
    args = ap.parse_args()

    reports = load_reports()
    if not reports:
        print("没有找到报告，检查 outputs/reports*/ 是否存在。")
        return 1

    print("=" * 78)
    print(f"找到 {len(reports)} 份报告")
    print("=" * 78)

    per_report = []
    for r in reports:
        text = r["text"]
        paras = split_paragraphs(text)
        row = {
            "报告": r["name"],
            "字符数": len(text),
            "段落数": len(paras),
            "段落近重复率": round(near_duplicate_rate(paras), 3),
            **{f"A_{k}": v for k, v in citation_stats(text, paras, PAT_GENERIC_CITE).items()},
            **{f"B_{k}": v for k, v in citation_stats(text, paras, PAT_ACTUAL_CITE).items()},
            **section_stats(text),
        }
        per_report.append(row)

    # ---- 报告侧汇总
    print("\n--- 单份报告 ---")
    hdr = f"{'报告':<10}{'字符':>7}{'段':>5}{'重复率':>8}{'B_引用数':>9}{'B_密度':>8}{'B_段落覆盖':>11}{'B_前/后半':>13}{'标题数':>7}"
    print(hdr)
    print("-" * len(hdr))
    for row in per_report:
        print(
            f"{row['报告']:<10}{row['字符数']:>7}{row['段落数']:>5}"
            f"{row['段落近重复率']:>8.3f}{row['B_引用标记总数']:>9}"
            f"{row['B_引用密度_每千字']:>8.3f}{row['B_含引用段落占比']:>11.3f}"
            f"  {row['B_前半段含引用占比']:.2f}/{row['B_后半段含引用占比']:.2f}"
            f"{row['标题数']:>9}"
        )

    chars = np.array([r["字符数"] for r in per_report], dtype=float)
    dups = np.array([r["段落近重复率"] for r in per_report], dtype=float)
    cov = np.array([r["B_含引用段落占比"] for r in per_report], dtype=float)

    summary = {
        "报告数": len(reports),
        "字符数均值": round(float(chars.mean()), 1),
        "字符数中位": round(float(np.median(chars)), 1),
        "字符数最小": int(chars.min()),
        "字符数最大": int(chars.max()),
        "字符数标准差": round(float(chars.std(ddof=1)), 1),
        "段落近重复率均值": round(float(dups.mean()), 3),
        "段落近重复率最大": round(float(dups.max()), 3),
        "引用段落覆盖率均值": round(float(cov.mean()), 3),
    }
    print("\n--- 报告侧汇总 ---")
    for k, v in summary.items():
        print(f"  {k:<18} {v}")

    # ---- 跨报告相似度（证明不是模板复读）
    print("\n--- 跨报告相似度（越低越说明不是同一套模板）---")
    vec = TfidfVectorizer(analyzer="char", ngram_range=(3, 4), min_df=1)
    mat = vec.fit_transform([r["text"] for r in reports])
    sim = cosine_similarity(mat)
    np.fill_diagonal(sim, 0.0)
    cross = {
        "跨报告相似度均值": round(float(sim[sim > 0].mean()), 3),
        "跨报告相似度最大": round(float(sim.max()), 3),
    }
    for i in range(len(reports)):
        for j in range(i + 1, len(reports)):
            print(f"  {reports[i]['name']} vs {reports[j]['name']}: {sim[i, j]:.3f}")
    for k, v in cross.items():
        print(f"  {k}: {v}")

    # ---- 代码侧
    print("\n--- 代码静态分析 ---")
    src_stats = code_stats(["src", "evaluation"])
    for k, v in src_stats.items():
        if k == "最长函数Top5":
            continue
        print(f"  {k:<20} {v}")
    print("  最长函数 Top5:")
    for item in src_stats["最长函数Top5"]:
        print(f"    {item['行数']:>5} 行  {item['位置']}")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "单份报告": per_report,
            "报告侧汇总": summary,
            "跨报告": cross,
            "代码侧": src_stats,
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
