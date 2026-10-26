#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_kb_pdf.py
================================================================================
把两个项目的备战材料合并成一份《双项目面试知识库》Markdown，并渲染为 PDF。

输入（三份知识库 + 两份简历原文）：
  ../shopping-grpo-longhorizon/learn-notes/面试知识库-项目一-Shopping-GRPO.md
  ../shopping-grpo-longhorizon/learn-notes/面试知识库-项目一-简历反追问手册.md
  learn-notes/面试知识库-项目二-DeepResearch.md
  ../shopping-grpo-longhorizon/learn-notes/shoppinggrpo简历描述.txt
  learn-notes/简历项目描述-修订版v5-对齐版.txt

输出：
  learn-notes/面试知识库-双项目汇总.md
  learn-notes/面试知识库-双项目汇总.pdf

用法：
  python scripts/build_kb_pdf.py            # 生成 md + pdf
  python scripts/build_kb_pdf.py --md-only  # 只生成 md

源文件本身不会被修改；本脚本只读它们。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent                      # .../qiqihezh/deepresearch-agent
NOTES = PROJ / "learn-notes"
# 同级仓库：.../projects/shopping-grpo-longhorizon（与 .../projects/qiqihezh 平级）
SIBLING = PROJ.parent.parent / "shopping-grpo-longhorizon" / "learn-notes"

MD_OUT = NOTES / "面试知识库-双项目汇总.md"
PDF_OUT = NOTES / "面试知识库-双项目汇总.pdf"

SRC = {
    "kb1": SIBLING / "面试知识库-项目一-Shopping-GRPO.md",
    "kb2": SIBLING / "面试知识库-项目一-简历反追问手册.md",
    "kb3": NOTES / "面试知识库-项目二-DeepResearch.md",
    "resume_grpo": SIBLING / "shoppinggrpo简历描述.txt",
    "resume_dr": NOTES / "简历项目描述-修订版v5-对齐版.txt",
}


def read(p: Path) -> str:
    if not p.exists():
        sys.exit(f"[错误] 缺少源文件：{p}")
    return p.read_text(encoding="utf-8")


def unwrap_resume(txt: str) -> list[str]:
    """简历 .txt 是硬换行的：按 '·' 项目符重新聚合成完整条目，去掉行内断行。"""
    out: list[str] = []
    cur: str | None = None
    for line in txt.strip().splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("·"):
            if cur:
                out.append(cur)
            cur = s
        elif cur is None:
            out.append(s)        # 首行（项目经历 … 时间）
        else:
            cur += s             # 续行直接拼接（中文句子，不加空格）
    if cur:
        out.append(cur)
    return out


def demote(md: str) -> str:
    """标题整体下降一级；感知代码围栏，不动代码块里的 #。"""
    out: list[str] = []
    fence = False
    for line in md.splitlines():
        if re.match(r"^\s*```", line):
            fence = not fence
            out.append(line)
            continue
        if not fence and re.match(r"^#{1,4} ", line):
            out.append("#" + line)
            continue
        out.append(line)
    return "\n".join(out)


def body(path: Path) -> str:
    """去掉源文件的一级标题（由本文档的 Part 标题承担）。"""
    text = demote(read(path))
    return text.split("\n", 1)[1].lstrip("\n") if "\n" in text else text


def quote(lines: list[str]) -> str:
    return "\n".join("> " + l for l in lines)


HEAD = """# 双项目面试知识库 · 尤俊杰

> **怎么用这份文档**
>
> 这是把两个项目的**简历原文 + 代码级机制 + 关键数字 + 口径陷阱 + 反追问**汇总成的**备战知识库**。
>
> - ✅ **面试前**：通读一遍；重点吃透每部分的「口径陷阱」和「高危追问」两节。
> - ✅ **准备时**：当索引用——「这个数字能不能报」「被追问怎么答」直接翻对应小节。
> - ⚠️ **原则**：每一句都要能在**没有辅助的情况下自己讲出来**。讲不出来的就是薄弱点。
>
> **可信度分级**（每个数字都标了）：
> **实测** = 有脚本有产物，可主动报 ｜ **代码可查** = 能指到文件行号，可主动讲 ｜ **无出处** = 仓库里找不到证据，必须删或改口。

---

# 第一部分 · Shopping-GRPO：长程购物 Agent 的可复现后训练与评测系统

## 简历原文（投放版）

"""

PART2 = """

---

# 第二部分 · DeepResearch Agent：面向复杂深度研究任务的多智能体协作系统

## 简历原文（投放版）

"""

TAIL = """

---

# 第三部分 · 统一口径速查（两项目红线）

## Shopping-GRPO —— 可以说 / 不能说

| 数字 | 档位 | 用法 |
|---|---|---|
| 2498 条轨迹 → 1026 采纳（41.07%）→ 重筛 1192（1073 训练 / 119 验证） | 实测 | ✅ 主动报 |
| 严格成功率 0% → 60.5%（SFT）→ 62.0%（GRPO） | 实测 | ✅ 报，**但必须同时说 +1.5pp 的 McNemar p≈0.66 不显著** |
| 完成终局率 18.0% → 96.5% | 实测 | ✅ 主动报（这条最强） |
| 平均 Reward −0.1105 → 0.5158 | 实测 | ✅ 主动报 |
| Guard 非法动作拒绝 752 → 52 → 38 | 实测 | ✅ 主动报 |
| SFT 峰值显存 89GiB / 单卡 96GB / 500 步 | 实测 | ✅ 报 |
| 六模型同协议对比（本模型 62.0%） | ⚠️ 口径 | ❌ **不能说「超过」**：Final-183 分母 vs Final-200 Clean 分母，**不可比** |
| TRACE 回合级信用分配 | ⚠️ 口径 | ❌ **不在任何主结果里**，只能讲「探索过」 |

## DeepResearch Agent —— 可以说 / 不能说

| 数字 | 档位 | 用法 |
|---|---|---|
| 14,146 行 / 63 文件 | 实测 | ✅ 主动报（原简历「约 1.4 万行」被独立验证） |
| 参数类型注解覆盖率 96.9% | 实测 | ✅ 主动报 |
| 跨报告相似度 均值 0.272 / 最大 0.375 | 实测 | ⭐ 主动报，堵「套模板」质疑 |
| 报告字符数 均值 20,994 / 中位 23,005 / σ 4,757 | 实测 | ✅ 报 |
| 11 领域 / 35 题 | 代码可查 | ✅ 报（文件注释写「20」是过期注释） |
| 对抗轮数/阈值 **10 / 9.5 / 0.2** | 代码可查 | ✅ 报**配置值**，不是函数默认值 3/8.0/0.3 |
| Blue 修复 **3 类** | 代码可查 | ✅ 必须说 3 类（**无 VERIFY**） |
| 并发度 5 / 重规划上限 3 轮 / 阈值 >0.5 或 (<0.3 且 failed>0) | 代码可查 | ✅ 报 |
| `85% → 95%` JSON 解析率 | ❌ 无出处 | ❌ **必须删** |
| Judge 得分等评测分 | ❌ 无出处 | ❌ **不要主动报**，被问就说「没跑完大规模实验」 |
| `L3 原文保留` | ❌ 与代码矛盾 | ❌ 改为 `L3 LLM 摘要` |
| `L1 cosine < 0.6` | ❌ 概念混淆 | ❌ 0.6 是 budget 占用率；L1 相似度是自适应 0.25/0.20/0.15 |

## 两个项目都要准备好的一句话

| 场景 | 答法要点 |
|---|---|
| 「跑出来多少分？」 | 先说**已实测**的数字 → 再说**没测的部分**（12 小时/轮）→ **转向机制** |
| 「哪些是你做的？」 | 讲**设计决策**（比敲键盘更难被质疑）+ 主动指出**自己系统的缺陷** |
| 「为什么不用现成框架？」 | 先承认框架能做，再讲**你真正需要而它不直接给的**（见讲义 03 附加题） |
| 「是不是 AI 写的？」 | 2026 年不是减分项；**说不清自己代码的才是** → 用「主动指出盲区」证明所有权 |

## 绝对不能说出口的六句话（红线）

1. 「我完全没碰 LangChain 生态」 —— 你用了 **LangSmith**
2. 「networkx 不支持 / 性能差」 —— 它是依赖，且分层排序它也能做
3. 「我的系统拿了 X 分」 —— 仓库里**没有一份实验结果 JSON**
4. 「这个提升很显著」 —— McNemar p≈0.66，**不显著**
5. 「我的 2B 模型超过了 DeepSeek-V4-Pro」 —— 分母不同，**不可比**
6. 任何 `85%→95%` 相关的展开 —— **无出处**

---

*文档结束。祝面试顺利。*
"""


def build_md() -> str:
    grpo = unwrap_resume(read(SRC["resume_grpo"]))
    dr = unwrap_resume(read(SRC["resume_dr"]).split("═══════")[0])
    return "".join([
        HEAD, quote(grpo),
        "\n\n## 项目机制与关键数字\n\n", body(SRC["kb1"]),
        "\n\n## 简历反追问手册\n\n", body(SRC["kb2"]),
        PART2, quote(dr),
        "\n\n## 项目机制与关键数字\n\n", body(SRC["kb3"]),
        TAIL,
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description="合并双项目知识库并渲染 PDF")
    ap.add_argument("--md-only", action="store_true", help="只生成 Markdown，不渲染 PDF")
    args = ap.parse_args()

    doc = build_md()
    MD_OUT.write_text(doc, encoding="utf-8")
    print(f"✓ {MD_OUT}\n  {len(doc):,} 字符 / {doc.count(chr(10)):,} 行")

    # 链接自检：只校验相对路径存在性
    bad = []
    for m in re.finditer(r"\]\((\.[^)#]+)(?:#L\d+(?:-L\d+)?)?\)", doc):
        if not (MD_OUT.parent / m.group(1)).resolve().exists():
            bad.append(m.group(1))
    print(f"  相对链接失效：{len(bad)}" + (f"  {sorted(set(bad))}" if bad else "  ✓"))

    if args.md_only:
        return 0

    sys.path.insert(0, str(HERE))
    from md2pdf import build_tex, esc  # noqa: E402

    title, author = "双项目面试知识库", "尤俊杰"
    md2pdf = HERE / "md2pdf.py"
    cmd = [sys.executable, str(md2pdf), str(MD_OUT), "-o", str(PDF_OUT),
           "--title", title, "--author", author, "--toc", "--no-secnum"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
