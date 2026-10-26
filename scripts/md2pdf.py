#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
md2pdf —— 把 Markdown 渲染成中文 PDF（无需 pandoc）

背景：本机没有 pandoc / wkhtmltopdf / weasyprint，但有 MiKTeX 的 xelatex + ctex。
这个脚本做两件事：
  1. 把一个 Markdown 子集转成 LaTeX（够用就好：标题/表格/代码块/列表/粗体/行内代码/链接）
  2. 调 xelatex 编译两遍（第二遍是为了目录页码正确）

用法：
    python scripts/md2pdf.py input.md -o output.pdf
    python scripts/md2pdf.py input.md -o output.pdf --title "标题" --toc

依赖：xelatex（MiKTeX/TeXLive 均可）、ctex 宏包、系统中文字体。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# LaTeX 转义
# ---------------------------------------------------------------------------

_ESCAPE_MAP = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]


def esc(text: str) -> str:
    """转义 LaTeX 特殊字符。"""
    for old, new in _ESCAPE_MAP:
        text = text.replace(old, new)
    return text


# ---------------------------------------------------------------------------
# emoji 清理
# ---------------------------------------------------------------------------
# 中文字体（微软雅黑/思源）不含 emoji 字形，直接进 LaTeX 会渲染成空白，
# 在表格里还会留下难看的空位。这里做一次清理：
#   有语义的映射成 LaTeX 符号；纯装饰的直接删掉；变体选择符（U+FE0F）一律删。

_EMOJI_MAP = {
    "✅": r"\checkmark",
    "✔": r"\checkmark",
    "❌": r"$\times$",
    "✗": r"$\times$",
    "⚠": r"\textbf{!}",
    "⭐": r"$\star$",
    "★": r"$\star$",
    "☆": r"$\circ$",
    "🔴": r"$\bullet$",
    "🟡": r"$\circ$",
    "🟢": r"$\bullet$",
    "⚪": r"$\circ$",
    "➡": r"$\rightarrow$",
    "→": r"$\rightarrow$",
    "←": r"$\leftarrow$",
    "≥": r"$\geq$",
    "≤": r"$\leq$",
    "≠": r"$\neq$",
    "×": r"$\times$",
    "…": r"\ldots{}",
    "℃": r"$^\circ$C",
}

# 未被映射的 emoji / 符号区段（杂项符号、装饰符号、补充符号等）
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF️⃣]"
)
# 制表/方框绘制字符（有些文档用 ─ │ 画框图）
_BOXCHARS = {"─": "-", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+", "├": "+", "┤": "+"}


def strip_emoji(text: str) -> str:
    """纯文本清理（用于代码块）：emoji 删除，制表符转 ASCII。"""
    text = _EMOJI_RE.sub("", text)
    for k, v in _BOXCHARS.items():
        text = text.replace(k, v)
    return text


def _emoji_to_tex(text: str, stash) -> str:
    """把 emoji 换成 LaTeX 符号并通过 stash 保护起来（避免被 esc 二次转义）。"""
    for k, v in _EMOJI_MAP.items():
        if k in text:
            text = text.replace(k, stash(v + " "))
    text = _EMOJI_RE.sub("", text)
    return text


def esc_code(text: str) -> str:
    """转义并允许在长 token（路径、下划线标识符）里断行，避免溢出。"""
    out = esc(text)
    # 在斜杠和点号后允许断行（文件路径最常见的溢出源）
    out = out.replace("/", r"/\allowbreak{}")
    return out


# ---------------------------------------------------------------------------
# 行内元素
# ---------------------------------------------------------------------------

def inline(text: str) -> str:
    """处理行内 Markdown：行内代码、粗体、斜体、链接、删除线。

    顺序很重要：先把代码 span 抽出来占位，避免其中的 * 和 _ 被当成强调标记。
    """
    spans: list[str] = []

    def stash(tex: str) -> str:
        spans.append(tex)
        return f"\x00{len(spans) - 1}\x00"

    # 1) 行内代码 `...`
    text = re.sub(
        r"`([^`]+)`",
        lambda m: stash(r"\texttt{" + esc_code(m.group(1)) + "}"),
        text,
    )

    # 2) 链接 [文字](url) —— 先于转义处理，因为 url 里有特殊字符
    def latex_label(label: str) -> str:
        """链接文字：逐段处理，占位符还原成已转义好的 LaTeX，其余部分照常转义。

        必须逐段处理——标签里可能嵌了行内代码（如
        `[default.yaml:116 \\`max_replan_rounds: 3\\`](...)`），
        第 1 步已把它换成了 \\x00N\\x00 占位符；若整体 esc() 一遍，
        占位符会被原样带进 \\href 的参数里，而第 5 步的 unstash 不会再扫替换结果，
        NUL 字节就漏进 .tex 导致编译失败。
        """
        out: list[str] = []
        for i, part in enumerate(re.split(r"\x00(\d+)\x00", label)):
            if i % 2:                      # 奇数位 = 占位符编号
                out.append(spans[int(part)])
                continue
            piece = esc(part)
            for k, v in _EMOJI_MAP.items():        # 已转义，直接插 LaTeX 不会再被转义
                piece = piece.replace(k, v + " ")
            out.append(_EMOJI_RE.sub("", piece))
        return "".join(out)

    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if url.startswith("#"):
            return stash(latex_label(label))
        url_tex = url.replace("\\", r"\textbackslash{}").replace("%", r"\%").replace("#", r"\#")
        return stash(r"\href{" + url_tex + "}{" + latex_label(label) + "}")

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)

    # 3) emoji → LaTeX 符号（同样走 stash，避免下面 esc 把 \ 和 $ 转义掉）
    text = _emoji_to_tex(text, stash)

    # 4) 剩余文本整体转义
    text = esc(text)

    # 4) 强调（在转义后的文本上做，标记字符本身不受转义影响）
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\\textbf{\\textit{\1}}", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\\textit{\1}", text)
    text = re.sub(r"~~(.+?)~~", r"\\sout{\1}", text)

    # 5) 还原占位
    def unstash(m: re.Match) -> str:
        return spans[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", unstash, text)


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------

def is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    body = s.strip("|")
    cells = [c.strip() for c in body.split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c != "")


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def render_table(rows: list[str], colwidth_hint: list[int] | None = None) -> str:
    """渲染 Markdown 管道表格为 LaTeX longtable（longtable 可跨页）。"""
    header = split_row(rows[0])
    body = [split_row(r) for r in rows[2:]]  # rows[1] 是分隔行
    ncol = len(header)

    # 列宽：按各列最大内容长度分配，夹在 8%–40% 之间
    widths = []
    for i in range(ncol):
        lens = [len(header[i])] + [len(r[i]) if i < len(r) else 0 for r in body]
        # 用"最长单元格"开根号来压平极端值，避免一列吃掉全部宽度
        widths.append(max(lens) ** 0.5)
    total = sum(widths) or 1.0
    fracs = [w / total for w in widths]
    fracs = [min(max(f, 0.08), 0.42) for f in fracs]
    # 归一化到 1.0
    s = sum(fracs)
    fracs = [f / s for f in fracs]

    spec = "".join(r">{\raggedright\arraybackslash}p{" + f"{f:.3f}" + r"\textwidth}" for f in fracs)

    def row_tex(cells: list[str], bold: bool = False) -> str:
        cells = (cells + [""] * ncol)[:ncol]
        out = []
        for c in cells:
            c = c.replace("<br>", r"\\").replace("<br/>", r"\\")
            tex = inline(c)
            if bold:
                tex = r"\textbf{" + tex + "}"
            out.append(tex)
        return " & ".join(out) + r" \\"

    lines = [
        r"\begingroup\footnotesize\sloppy",
        r"\begin{longtable}{" + spec + "}",
        r"\toprule",
        row_tex(header, bold=True),
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        row_tex(header, bold=True),
        r"\midrule",
        r"\endhead",
        r"\bottomrule",
        r"\endfoot",
    ]
    for r in body:
        lines.append(row_tex(r))
    lines += [r"\end{longtable}", r"\endgroup", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 块级转换
# ---------------------------------------------------------------------------

def convert(md_text: str, h1_in_toc: bool = False) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # ---- 围栏代码块 ----
        m = re.match(r"^```(\w*)\s*$", line)
        if m:
            i += 1
            buf: list[str] = []
            while i < n and not re.match(r"^```\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            out.append(r"\begin{lstlisting}")
            out.extend(_EMOJI_RE.sub("", l) for l in buf)
            out.append(r"\end{lstlisting}")
            out.append("")
            continue

        # ---- 表格 ----
        if line.strip().startswith("|") and i + 1 < n and is_table_sep(lines[i + 1]):
            buf = [line, lines[i + 1]]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                buf.append(lines[i])
                i += 1
            out.append(render_table(buf))
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level, title = len(m.group(1)), inline(m.group(2).strip())
            cmd = {1: "section", 2: "section", 3: "subsection", 4: "subsubsection"}[level]
            # 默认：一级标题当文档标题用，加星号（不编号、不进目录）。
            # h1_in_toc=True 时保留为真 section，使其进入目录（配合 secnumdepth=0 即无编号）。
            star = "*" if (level == 1 and not h1_in_toc) else ""
            out.append(f"\\{cmd}{star}{{{title}}}")
            out.append("")
            i += 1
            continue

        # ---- 分隔线 ----
        if re.match(r"^-{3,}\s*$", line) or re.match(r"^\*{3,}\s*$", line):
            out.append(r"\vspace{2pt}\hrule\vspace{6pt}")
            out.append("")
            i += 1
            continue

        # ---- 引用块 ----
        if line.strip().startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            body = "\\\\\n".join(inline(b) for b in buf if b)
            out.append(r"\begin{quote}\small " + body + r"\end{quote}")
            out.append("")
            continue

        # ---- 无序列表 ----
        if re.match(r"^\s*[-*+]\s+", line):
            i = render_list(lines, i, out, ordered=False)
            continue

        # ---- 有序列表 ----
        if re.match(r"^\s*\d+[.)]\s+", line):
            i = render_list(lines, i, out, ordered=True)
            continue

        # ---- 空行 ----
        if not line.strip():
            out.append("")
            i += 1
            continue

        # ---- 普通段落 ----
        out.append(inline(line))
        out.append("")
        i += 1

    return "\n".join(out)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def render_list(lines: list[str], i: int, out: list[str], ordered: bool) -> int:
    """渲染（可能带缩进的）列表。用 indent 递归处理嵌套。"""
    pattern = r"^\s*\d+[.)]\s+(.*)$" if ordered else r"^\s*[-*+]\s+(.*)$"
    base_indent = _indent_of(lines[i])
    env = "enumerate" if ordered else "itemize"
    items: list[list[str]] = []

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            # 空行：如果后面还是同级列表项，继续；否则结束
            if i + 1 < len(lines) and _indent_of(lines[i + 1]) >= base_indent and re.match(
                pattern, lines[i + 1]
            ):
                i += 1
                continue
            break
        m = re.match(pattern, line)
        if m and _indent_of(line) == base_indent:
            items.append([m.group(1)])
            i += 1
            continue
        # 嵌套或续行
        if items and _indent_of(line) > base_indent:
            if re.match(pattern, line):
                # 嵌套列表：交给递归
                sub_out: list[str] = []
                i = render_list(
                    lines, i, sub_out,
                    ordered=bool(re.match(r"^\s*\d+[.)]\s+", line)),
                )
                items[-1].append("\n".join(sub_out))
                continue
            items[-1].append(inline(line.strip()))
            i += 1
            continue
        break

    out.append(f"\\begin{{{env}}}")
    for it in items:
        text = inline(it[0])
        rest = [x for x in it[1:] if x]
        if rest:
            out.append(r"\item " + text)
            out.extend(rest)
        else:
            out.append(r"\item " + text)
    out.append(f"\\end{{{env}}}")
    out.append("")
    return i


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------

PREAMBLE = r"""\documentclass[11pt,a4paper]{ctexart}

% ---- 页面 ----
\usepackage[margin=2.0cm,top=2.2cm,bottom=2.0cm]{geometry}
\usepackage{fancyhdr}
\usepackage{lastpage}

% ---- 表格 ----
\usepackage{longtable}
\usepackage{booktabs}
\usepackage{array}
\usepackage{ragged2e}
\usepackage{amssymb}

% ---- 代码 ----
\usepackage{listings}
\usepackage{xcolor}
\lstset{
  basicstyle=\ttfamily\small,
  breaklines=true,
  breakatwhitespace=false,
  postbreak=\mbox{\textcolor{gray}{$\hookrightarrow$}\space},
  frame=single,
  framerule=0.3pt,
  rulecolor=\color{gray!50},
  backgroundcolor=\color{gray!6},
  columns=flexible,
  keepspaces=true,
  showstringspaces=false,
  extendedchars=true,
}

% ---- 其它 ----
\usepackage[colorlinks=true,linkcolor=blue!60!black,urlcolor=blue!60!black]{hyperref}
\usepackage[normalem]{ulem}
\usepackage{enumitem}
\setlist{nosep,leftmargin=1.4em}
\usepackage{titlesec}
\titlespacing*{\section}{0pt}{10pt}{5pt}
\titlespacing*{\subsection}{0pt}{8pt}{4pt}
__SECNUM__

% ---- 表头/页脚 ----
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\color{gray}__HEADER__}
\fancyhead[R]{\small\color{gray}\thepage\,/\,\pageref{LastPage}}
\renewcommand{\headrulewidth}{0.3pt}

% ---- 中文排版微调 ----
\linespread{1.12}
\setlength{\parskip}{2pt}
\sloppy

\title{\vspace{-1.2cm}\textbf{__TITLE__}}
\author{__AUTHOR__}
\date{__DATE__}

\begin{document}
\maketitle
\thispagestyle{fancy}
__TOC__
"""

POSTAMBLE = r"""
\end{document}
"""


def build_tex(md_text: str, title: str, author: str, date: str, toc: bool,
              no_secnum: bool = False) -> str:
    body = convert(md_text, h1_in_toc=no_secnum)
    preamble = (
        PREAMBLE.replace("__TITLE__", esc(title))
        .replace("__AUTHOR__", esc(author))
        .replace("__DATE__", esc(date))
        .replace("__HEADER__", esc(title))
        .replace("__TOC__", r"\tableofcontents\newpage" if toc else "")
        .replace("__SECNUM__", r"\setcounter{secnumdepth}{0}" if no_secnum else "")
    )
    return preamble + "\n" + body + "\n" + POSTAMBLE


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown → 中文 PDF（xelatex/ctex）")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--author", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--toc", action="store_true")
    ap.add_argument("--no-secnum", action="store_true",
                    help="关闭章节自动编号（文档内已手写 1.1/2.3 这类编号时用）")
    ap.add_argument("--keep-tex", action="store_true", help="保留中间 .tex 便于调试")
    args = ap.parse_args()

    src = args.input.resolve()
    if not src.exists():
        print(f"[错误] 找不到 {src}", file=sys.stderr)
        return 1

    md_text = src.read_text(encoding="utf-8")
    # 去掉 Markdown 的 front-matter / 首行大标题（用 LaTeX 的 \title 代替）
    md_text = re.sub(r"\A#\s+.*?\n", "", md_text, count=1)

    title = args.title or src.stem
    out_pdf = (args.output or src.with_suffix(".pdf")).resolve()
    tex = build_tex(md_text, title, args.author, args.date, args.toc, args.no_secnum)

    if shutil.which("xelatex") is None:
        print("[错误] 找不到 xelatex。请安装 MiKTeX 或 TeX Live。", file=sys.stderr)
        return 1

    workdir = out_pdf.parent if args.keep_tex else Path(tempfile.mkdtemp(prefix="md2pdf_"))
    workdir.mkdir(parents=True, exist_ok=True)
    tex_path = workdir / (out_pdf.stem + ".tex")
    tex_path.write_text(tex, encoding="utf-8")

    # 目录本身跨页时，第二遍的 .toc 会让正文整体位移，页码需要再收敛一遍。
    passes = (1, 2, 3) if args.toc else (1, 2)
    print(f"[1/{len(passes)}] 生成 {tex_path}")
    for pass_no in passes:
        proc = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            log = (workdir / (tex_path.stem + ".log"))
            tail = ""
            if log.exists():
                content = log.read_text(encoding="utf-8", errors="replace")
                ls = content.splitlines()
                # 抓每个 "! ..." 及其后 4 行上下文（含 l.<行号> 定位）
                chunks: list[str] = []
                for idx, l in enumerate(ls):
                    if l.startswith("!"):
                        chunks.append("\n".join(ls[idx: idx + 5]))
                    if len(chunks) >= 6:
                        break
                tail = "\n---\n".join(chunks) or content[-2000:]
                # 额外报出第一个出错的行号，方便回 .tex 里找
                m = re.search(r"^l\.(\d+)", content, re.M)
                if m:
                    tail += f"\n\n[出错位置] .tex 第 {m.group(1)} 行"
            print(f"[错误] xelatex 第 {pass_no} 遍失败：", file=sys.stderr)
            print(tail, file=sys.stderr)
            if not args.keep_tex:
                print(f"[提示] 用 --keep-tex 可保留 .tex 与 .log 便于排查", file=sys.stderr)
            return 1
        print(f"      第 {pass_no} 遍编译通过")

    produced = workdir / (tex_path.stem + ".pdf")
    if not produced.exists():
        print("[错误] 没有生成 PDF", file=sys.stderr)
        return 1

    shutil.copyfile(produced, out_pdf)
    size_kb = out_pdf.stat().st_size / 1024
    print(f"[2/2] ✅ {out_pdf}  ({size_kb:.0f} KB)")
    if not args.keep_tex:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
