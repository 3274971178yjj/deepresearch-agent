#!/usr/bin/env python3
"""验证 learn-notes 讲义里的代码链接(文件路径 + 行号)是否真的打得开。

讲义的读者会直接点链接跳代码,所以每条链接必须实测。这个脚本做三件事:
  1. 相对路径能否解析到真实文件
  2. 行号是否落在文件范围内
  3. 打印该行号对应的代码,供人眼核对"链接指向的内容"和"正文讲的内容"是否一致

用法:
    python .claude/skills/lecture-notes/check_links.py learn-notes/03-编排引擎.md
    python .claude/skills/lecture-notes/check_links.py learn-notes/*.md   # 检查全部

退出码:0 全部有效;1 存在失效链接。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 匹配 markdown 里所有 `](../path)` 形式的链接,再单独解析行号片段。
LINK_PATTERN = re.compile(r"\]\((\.\./[^)]+)\)")
LINE_FRAGMENT = re.compile(r"^(?P<path>.+?)#L(?P<start>\d+)(?:-L(?P<end>\d+))?$")


def check_file(md_path: Path) -> tuple[int, int]:
    """检查一份讲义,返回 (有效链接数, 失效链接数)。"""
    if not md_path.is_file():
        print(f"✗ 讲义不存在: {md_path}")
        return 0, 1

    text = md_path.read_text(encoding="utf-8")
    base = md_path.parent  # 链接相对于讲义所在目录解析
    ok_count = 0
    bad_count = 0

    print(f"\n=== {md_path} ===")
    for raw in sorted(set(LINK_PATTERN.findall(text))):
        match = LINE_FRAGMENT.match(raw)
        rel_path = match.group("path") if match else raw
        target = (base / rel_path).resolve()
        label = f"{Path(rel_path).name}"

        if not target.is_file():
            print(f"✗ 文件不存在  {rel_path}")
            bad_count += 1
            continue

        if match is None:
            # 不带行号的链接(如某个数据文件),只验证文件存在
            print(f"✓ {label}")
            ok_count += 1
            continue

        start = int(match.group("start"))
        end = int(match.group("end")) if match.group("end") else start
        lines = target.read_text(encoding="utf-8").splitlines()

        if start > len(lines) or end > len(lines):
            print(f"✗ 行号越界    {label} L{start}-L{end}(文件只有 {len(lines)} 行)")
            bad_count += 1
            continue
        if end < start:
            print(f"✗ 行号颠倒    {label} L{start}-L{end}")
            bad_count += 1
            continue

        snippet = lines[start - 1].strip()[:72]
        span = f"L{start}" if start == end else f"L{start}-L{end}"
        print(f"✓ {label:28s} {span:12s} {snippet}")
        ok_count += 1

    return ok_count, bad_count


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    total_ok = total_bad = 0
    for arg in sys.argv[1:]:
        ok, bad = check_file(Path(arg))
        total_ok += ok
        total_bad += bad

    print(f"\n汇总: {total_ok} 条有效, {total_bad} 条失效")
    if total_bad:
        print("⚠️  讲义里有打不开的链接,修好再交付。")
        return 1
    print("✅ 所有链接都能点开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
