"""
鲁棒 JSON 提取工具（统一各模块的 LLM 输出解析）

LLM 返回的文本常夹杂 markdown 代码块围栏、解释性文字、尾随逗号等噪声。
此前 planner / red_agent / blue_agent / judge / evolution 等模块各自实现了
相似的解析逻辑（且细节不一致），现统一收敛到本模块。

提取策略（依次尝试，首个成功即返回）:
  1. 直接 json.loads 整段文本
  2. 提取 ```json ... ``` / ``` ... ``` 代码块
  3. 扫描第一个括号平衡的 {...} / [...] 子串（正确跳过字符串字面量内的括号）
  4. 修复常见噪声（去 // 行注释、去尾随逗号）后重试 1-3
  5. 激进修复（单引号 -> 双引号）后重试，仅作最后手段

对外接口:
    extract_json(text)                 -> Any    全部失败时抛 JsonExtractionError
    extract_json_dict(text, default)   -> dict   期望 JSON 对象；失败时返回 default（缺省则抛错）
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["JsonExtractionError", "extract_json", "extract_json_dict"]


class JsonExtractionError(ValueError):
    """所有提取策略均失败时抛出。"""


_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# 未指定 default 时 extract_json_dict 的哨兵对象
_SENTINEL: Any = object()


def extract_json(text: str) -> Any:
    """从 LLM 输出文本中提取并解析第一个 JSON 值。

    Args:
        text: 模型原始输出，可包含 markdown 围栏、前后噪声等。

    Returns:
        解析后的 Python 对象（dict / list / 标量）。

    Raises:
        JsonExtractionError: 所有策略均失败。
    """
    raw = (text or "").strip()
    if not raw:
        raise JsonExtractionError("empty text")

    last_error: Exception = JsonExtractionError("unknown")
    for candidate_iter in (_clean_candidates(raw), _clean_candidates(_repair(raw)), _clean_candidates(_repair_aggressive(raw))):
        for candidate in candidate_iter:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = e
                continue

    snippet = raw[:200].replace("\n", " ")
    raise JsonExtractionError(f"no parseable JSON found in: {snippet!r} ({last_error})")


def extract_json_dict(text: str, default: Any = _SENTINEL) -> dict:
    """提取 JSON 并要求结果为对象（dict）。

    Args:
        text: 模型原始输出。
        default: 提取失败或结果不是 dict 时的返回值；未提供则抛 JsonExtractionError。

    Returns:
        解析出的 dict，或 default。
    """
    try:
        data = extract_json(text)
    except JsonExtractionError:
        if default is _SENTINEL:
            raise
        return default
    if not isinstance(data, dict):
        if default is _SENTINEL:
            raise JsonExtractionError(f"expected JSON object, got {type(data).__name__}")
        return default
    return data


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

def _clean_candidates(raw: str) -> list[str]:
    """按优先级生成待尝试的候选字符串。"""
    candidates = [raw]
    candidates.extend(m.strip() for m in _CODE_BLOCK_RE.findall(raw))
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        balanced = _find_balanced(raw, open_ch, close_ch)
        if balanced:
            candidates.append(balanced)
    # 去重同时保持顺序
    seen: set[str] = set()
    ordered = [c for c in candidates if not (c in seen or seen.add(c))]
    return ordered


def _find_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    """返回 text 中第一个括号平衡的子串，正确跳过字符串字面量内的括号。

    相比贪婪正则 ``\\{.*\\}``，能正确处理 JSON 对象之后还有正文
    （正文中含 { } ）或字符串值内含括号的情况。
    """
    start = text.find(open_ch)
    while start != -1:
        depth = 0
        in_str = False
        quote = ""
        escaped = False
        i = start
        while i < len(text):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    in_str = False
            else:
                if ch in ('"', "'"):
                    in_str = True
                    quote = ch
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
            i += 1
        start = text.find(open_ch, start + 1)
    return None


def _strip_line_comments(text: str) -> str:
    """去掉 // 行注释，但不处理字符串字面量内部的 //（如 URL）。"""
    result = []
    for line in text.splitlines():
        in_str = False
        quote = ""
        escaped = False
        cut = len(line)
        i = 0
        while i < len(line) - 1:
            ch = line[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    in_str = False
            else:
                if ch in ('"', "'"):
                    in_str = True
                    quote = ch
                elif ch == "/" and line[i + 1] == "/":
                    cut = i
                    break
            i += 1
        result.append(line[:cut])
    return "\n".join(result)


def _repair(raw: str) -> str:
    """常规修复：去 // 注释 + 去尾随逗号。"""
    text = _strip_line_comments(raw)
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def _repair_aggressive(raw: str) -> str:
    """激进修复：在常规修复基础上把单引号换成双引号。

    可能破坏含撇号的英文文本，仅作最后手段。
    """
    return _repair(raw).replace("'", '"')
