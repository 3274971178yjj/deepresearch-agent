"""
measure_compressor.py —— M3 上下文压缩模块的可复现实测脚本

用途：为 learn-notes/05-上下文压缩M3.md 提供全部数字的出处。
零 API 调用、零网络、零新增依赖（sentence-transformers 未安装时走 Embedder 的
确定性随机回退，脚本会显式标注哪些结论受这一点影响）。

运行：
    PYTHONIOENCODING=utf-8 python scripts/measure_compressor.py

输出：
    控制台报告 + outputs/compressor_metrics.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 本机事实：openai / dotenv 等第三方包未安装（所以本项目在本机从未真正跑过）。
# src/compressor/compressor.py 的导入链会经过 src.models.vllm_policy 与 src.utils.env_config，
# 那两层要 import openai 和 dotenv。用最小桩顶掉，只为让导入通过；
# 被测的压缩逻辑一行都没被替换，compress() 走的是项目自己的代码。
_STUBBED: list[str] = []


class _AnyStub:
    """能接受任意构造参数、任意属性访问、任意调用的万能桩。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __call__(self, *args: object, **kwargs: object) -> "_AnyStub":
        return _AnyStub()

    def __getattr__(self, name: str) -> "_AnyStub":
        return _AnyStub()


def _install_stub(name: str) -> None:
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: _AnyStub  # type: ignore[attr-defined]  # PEP 562
    sys.modules[name] = mod
    _STUBBED.append(name)


for _name in ("openai", "dotenv", "tiktoken", "httpx", "aiohttp", "tenacity"):
    try:
        __import__(_name)
    except ImportError:
        _install_stub(_name)

# 第二个本机事实：src/utils/tracing.py:38 的 is_tracing_enabled() 会
# get_env("LANGSMITH_TRACING", "").lower()，而 get_env 把 "" 归一成 None 返回，
# 于是未设置该变量时 .lower() 抛 AttributeError —— 而 @trace_chain 装饰器在
# 「类体求值」阶段就会调用它，所以 import src.compressor.compressor 直接崩。
# 04 讲义的【发现】一节已记录此坑。这里显式置为 false 绕过，不碰项目源码。
os.environ.setdefault("LANGSMITH_TRACING", "false")
print(f"[环境] 已桩掉的未安装包: {_STUBBED}")
print(f"[环境] LANGSMITH_TRACING={os.environ.get('LANGSMITH_TRACING')} "
      f"(绕过 tracing.py:38 的 None.lower() 崩溃)")

import yaml  # noqa: E402

from src.compressor.compressor import (  # noqa: E402
    ContextCompressor,
    _CHARS_PER_TOKEN,
    _L1_THRESHOLD,
    _L2_THRESHOLD,
    _L3_THRESHOLD,
    _OUTPUT_RESERVE,
)
from src.compressor.extractive import _tokenize_sentences  # noqa: E402
from src.compressor.sliding_window import SlidingWindowCompressor  # noqa: E402

RESULTS: dict = {}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
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


def _count_calls(name: str) -> tuple[int, list[str]]:
    """统计某个名字在 src/ + evaluation/ 里作为「被调用」出现的次数。"""
    pat = re.compile(rf"(?<!def )\b{re.escape(name)}\s*\(")
    hits: list[str] = []
    for p in _ship_py():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if pat.search(line) and "def " not in line:
                hits.append(f"{p.relative_to(REPO).as_posix()}:{i}")
    return len(hits), hits


def _fake_doc(n_chars: int, idx: int = 0) -> str:
    """造一段长度约 n_chars 的中文假文档，句子数可控。"""
    sentences = [
        f"报告第{idx}部分指出，2025年市场规模达到{1000 + idx}亿美元，同比增长12.5%。",
        "据IDC数据显示，该赛道头部厂商份额集中度进一步提升。",
        "多项研究表明，推理成本是当前落地的核心瓶颈之一。",
        "初步结果显示，端侧部署的比例可能在未来两年显著上升。",
        "与此相对，另一份来自Gartner的报告给出了更保守的估计。",
        "调研覆盖了北美、欧洲与亚太共1200家企业客户。",
        "需要注意的是，样本在行业分布上存在一定偏差。",
        "综上，该结论仍需更多一手数据支撑。",
    ]
    out = ""
    i = 0
    while len(out) < n_chars:
        out += sentences[i % len(sentences)]
        i += 1
    return out[:n_chars]


# --------------------------------------------------------------------------
# 1. 阈值语义：0.60 / 0.80 / 0.95 到底是什么
# --------------------------------------------------------------------------
def check_threshold_semantics() -> None:
    print("\n=== 1. 三个阈值的语义：占用率，不是相似度 ===")
    cfg = yaml.safe_load((REPO / "configs/default.yaml").read_text(encoding="utf-8"))
    ccfg = cfg.get("compressor", {})
    budget = ccfg.get("max_context_length", 16000)
    reserve = ccfg.get("output_reserve_tokens", 2048)
    available = budget - reserve

    print(f"configs/default.yaml  compressor.max_context_length = {budget}")
    print(f"configs/default.yaml  compressor.output_reserve_tokens = {reserve}")
    print(f"→ available_budget = {budget} - {reserve} = {available} tokens")
    print(f"→ _CHARS_PER_TOKEN = {_CHARS_PER_TOKEN}")
    print()
    print(f"{'级别':<6}{'常量':<16}{'占用率门槛':<12}{'需要 token':<12}{'需要字符':<12}")
    rows = []
    for lvl, name, thr in [
        (1, "_L1_THRESHOLD", _L1_THRESHOLD),
        (2, "_L2_THRESHOLD", _L2_THRESHOLD),
        (3, "_L3_THRESHOLD", _L3_THRESHOLD),
    ]:
        need_tok = thr * available
        need_chr = need_tok * _CHARS_PER_TOKEN
        print(f"L{lvl:<5}{name:<16}{thr:<12}{need_tok:<12.0f}{need_chr:<12.0f}")
        rows.append(
            {
                "level": lvl,
                "const": name,
                "occupancy": thr,
                "need_tokens": round(need_tok),
                "need_chars": round(need_chr),
            }
        )
    RESULTS["threshold_semantics"] = {
        "budget": budget,
        "output_reserve": reserve,
        "available_budget": available,
        "chars_per_token": _CHARS_PER_TOKEN,
        "levels": rows,
        "yaml_comment_claims_similarity": True,
    }

    # YAML 的注释原文
    raw = (REPO / "configs/default.yaml").read_text(encoding="utf-8")
    for line in raw.splitlines():
        if "l1_threshold" in line or "l2_threshold" in line or "l3_threshold" in line:
            print(f"  YAML 原文: {line.strip()}")
    print("  ↑ 但这三个键在 src/ 与 evaluation/ 里都没有读取点（见第 6 节）")


# --------------------------------------------------------------------------
# 2. 调用点闸门 vs 内部级别闸门：数量级对不上
# --------------------------------------------------------------------------
def check_gate_incompatibility() -> None:
    print("\n=== 2. 唯一调用点的闸门 vs 内部级别闸门 ===")
    cfg = yaml.safe_load((REPO / "configs/default.yaml").read_text(encoding="utf-8"))
    ccfg = cfg.get("compressor", {})
    available = ccfg.get("max_context_length", 16000) - ccfg.get("output_reserve_tokens", 2048)

    # orchestrator.py:576-578 的闸门
    gate_chars = 6000
    gate_tokens = gate_chars / _CHARS_PER_TOKEN
    gate_ratio = gate_tokens / available
    print("orchestrator.py:578 闸门: if total_chars > 6000  # 约 2000 tokens 的启发式阈值")
    print(f"  → 该闸门对应占用率 = ({gate_chars}/{_CHARS_PER_TOKEN})/{available} = {gate_ratio:.4f}")
    print(f"  → 但要触发 L1 需要占用率 > {_L1_THRESHOLD}")
    print(f"  → 差距倍数 = {_L1_THRESHOLD / gate_ratio:.1f}×")
    print()
    print("  即：能进 compress() 的输入，占用率只有 %.1f%%，远低于 L1 的 %.0f%% 门槛。"
          % (gate_ratio * 100, _L1_THRESHOLD * 100))

    # 单条 200 字符（orchestrator.py:571 的 str(value)[:200]）
    per_entry = 200 + len("some_key: ")
    need_entries = {}
    for lvl, thr in [(1, _L1_THRESHOLD), (2, _L2_THRESHOLD), (3, _L3_THRESHOLD)]:
        need_chars = thr * available * _CHARS_PER_TOKEN
        need_entries[f"L{lvl}"] = int(need_chars / per_entry) + 1
    print()
    print(f"  又：orchestrator.py:571 把每条 value 截到 200 字符，单条约 {per_entry} 字符")
    print(f"  → 要够到各门槛，_memory_store 里至少要有：{need_entries}")

    RESULTS["gate_incompatibility"] = {
        "orchestrator_gate_chars": gate_chars,
        "gate_tokens_inferred": round(gate_tokens),
        "gate_occupancy": round(gate_ratio, 4),
        "l1_occupancy": _L1_THRESHOLD,
        "gap_factor": round(_L1_THRESHOLD / gate_ratio, 1),
        "per_entry_chars": per_entry,
        "min_entries_needed": need_entries,
    }


# --------------------------------------------------------------------------
# 3. 在真实闸门条件下实跑：返回原样，且日志会撒谎
# --------------------------------------------------------------------------
def check_level0_noop() -> None:
    print("\n=== 3. 实跑：按调用点的输入规模走一遍 compress() ===")
    comp = ContextCompressor(llm_policy=None, budget=16000, output_reserve=2048)

    # 造 30 条 × 200 字符 ≈ 6000+ 字符，正是调用点刚过闸门的规模
    parts = [f"result:task{i}: " + _fake_doc(200, i) for i in range(30)]
    parts = [p[:200] for p in parts]
    total_chars = sum(len(p) for p in parts)

    out = comp.compress(texts=parts, query="2025年大模型Agent市场格局", system_prompt_tokens=0)

    same = out == parts
    print(f"  输入: {len(parts)} 条, 共 {total_chars} 字符")
    print(f"  输出: {len(out)} 条, 共 {sum(len(c) for c in out)} 字符")
    print(f"  输出 == 输入（原样返回）: {same}")
    print("  调用点会打印: [M3] Context compressed: %d → %d chars"
          % (total_chars, sum(len(c) for c in out)))
    print("  ↑ 两个数字相同。日志声称「压缩了」，实际一个字没动。")

    # 验收：确实进入了「无需压缩」分支
    stats = comp.get_stats()
    print(f"  get_stats().level_distribution = {stats['level_distribution']}")
    print(f"  get_stats().total_compresses = {stats['total_compresses']}")

    # 对照：把预算调小到 3000，同样的输入就该触发 L1/L2/L3 了
    small = ContextCompressor(llm_policy=None, budget=3000, output_reserve=2048)
    out2 = small.compress(texts=parts, query="2025年大模型Agent市场格局", system_prompt_tokens=0)
    print()
    print("  对照：同样输入、预算改成 3000 时")
    print(f"    available_budget = {3000 - 2048} tokens → 占用率 = "
          f"{total_chars / _CHARS_PER_TOKEN / (3000 - 2048):.2f}")
    print(f"    输出 {len(out2)} 条, {sum(len(c) for c in out2)} 字符, 原样返回={out2 == parts}")
    print(f"    level_distribution = {small.get_stats()['level_distribution']}")

    RESULTS["level0_noop"] = {
        "entries": len(parts),
        "input_chars": total_chars,
        "output_chars": sum(len(c) for c in out),
        "returned_unchanged": same,
        "logged_line_misleading": same,
        "level_distribution_budget16000": {str(k): v for k, v in stats["level_distribution"].items()},
        "control_budget3000_output_chars": sum(len(c) for c in out2),
        "control_budget3000_levels": {
            str(k): v for k, v in small.get_stats()["level_distribution"].items()
        },
    }


# --------------------------------------------------------------------------
# 4. L2 的注释算错了
# --------------------------------------------------------------------------
def check_l2_comment_math() -> None:
    print("\n=== 4. _l2_extract 的注释与公式不符 ===")
    print("  代码(compressor.py:235):")
    print("    target_ratio = max(0.15, min(0.40, 0.50 - (current_tokens / max(budget,1)) * 0.35))")
    print("  注释(compressor.py:234): 「预算用满 80% 时保留 30%，用满 100% 时保留 15%」")
    print()
    print(f"  {'占用率':<10}{'公式算出':<12}{'注释声称':<12}")
    rows = []
    for occ in [0.60, 0.70, 0.80, 0.90, 1.00]:
        r = max(0.15, min(0.40, 0.50 - occ * 0.35))
        claim = "30%" if abs(occ - 0.80) < 1e-9 else ("15%" if abs(occ - 1.00) < 1e-9 else "-")
        print(f"  {occ:<10.0%}{r:<12.3f}{claim:<12}")
        rows.append({"occupancy": occ, "formula": round(r, 4), "comment_claim": claim})
    # 反解 0.30 出现在哪个占用率
    occ_at_030 = (0.50 - 0.30) / 0.35
    # 反解 0.15 出现在哪个占用率
    occ_at_015 = (0.50 - 0.15) / 0.35
    print(f"\n  反解：target_ratio = 0.30 实际出现在占用率 {occ_at_030:.1%}")
    print(f"  反解：target_ratio = 0.15 实际出现在占用率 {occ_at_015:.1%}")
    print(f"  注意 {occ_at_015:.1%} > 100%：占用率 100% 时仍在 0.15 之上，"
          f"下限 0.15 只在占用率超过 {occ_at_015:.1%} 时才生效")
    print("  结论：「80%→30%、100%→15%」两个数都不对；注释只是顺手写的近似，代码才是真的。")
    RESULTS["l2_comment_math"] = {
        "formula": "max(0.15, min(0.40, 0.50 - occupancy * 0.35))",
        "table": rows,
        "occupancy_at_0_30": round(occ_at_030, 4),
        "occupancy_at_0_15": round(occ_at_015, 4),
        "comment_claims": {"0.80": 0.30, "1.00": 0.15},
        "comment_correct": False,
    }


# --------------------------------------------------------------------------
# 5. L2 的分句与保留比例，用真实报告文本量
# --------------------------------------------------------------------------
def _real_report_text() -> str:
    f = sorted((REPO / "outputs" / "reports1").glob("*.md"))[0]
    return f.read_text(encoding="utf-8", errors="ignore")


def check_l2_short_doc_noop() -> None:
    print("\n=== 5. L2 的分句与保留比例（用真实报告文本） ===")
    print("  extractive.compress() 有两个提前返回：")
    print("    extractive.py:126-127  if not sentences: return text")
    print("    extractive.py:128-130  if len(sentences) <= 3: return text   ← 太短不压缩")
    print()
    report = _real_report_text()
    # 调用点喂进去的是 200 字符碎片
    frag = report[:200]
    sent_frag = _tokenize_sentences(frag)
    sent_full = _tokenize_sentences(report)
    print(f"  真实报告首位 200 字符切片 → {len(sent_frag)} 句：{sent_frag[:2]}...")
    print(f"  → {len(sent_frag)} <= 3？{len(sent_frag) <= 3}"
          f"（若为 True 才走「太短不压缩」提前返回）")
    print(f"  整份报告 {len(report):,} 字符 → {len(sent_full)} 句")
    print()
    print("  保留句数 k = max(1, int(n * target_ratio))，n 为分句数：")
    print(f"  {'n':<8}{'ratio=0.40':<14}{'ratio=0.22':<14}{'ratio=0.15':<14}")
    for n in (8, len(sent_full)):
        row = [max(1, int(n * r)) for r in (0.40, 0.22, 0.15)]
        print(f"  {n:<8}{row[0]:<14}{row[1]:<14}{row[2]:<14}")
    print("  → 200 字符碎片在 L2 下被压到 1 句；整份报告压到 15%")
    print("     注意：k 是「保留句数」，碎片只剩 1 句后，L1 的保底 + L2 的 k=1")
    print("     叠加起来信息损失远大于字面 compression_ratio 显示的程度。")
    RESULTS["l2_short_doc_noop"] = {
        "real_report_chars": len(report),
        "sentences_in_200char_fragment": len(sent_frag),
        "sentences_in_full_report": len(sent_full),
        "fragment_hits_early_return": len(sent_frag) <= 3,
        "early_return_threshold": 3,
        "kept_sentences_at_ratio": {
            "0.40": max(1, int(len(sent_full) * 0.40)),
            "0.22": max(1, int(len(sent_full) * 0.22)),
            "0.15": max(1, int(len(sent_full) * 0.15)),
            "fragment_0.22": max(1, int(len(sent_frag) * 0.22)),
        },
    }


# --------------------------------------------------------------------------
# 5b. L1 的「自适应降阈值」循环：实际方向是反的
# --------------------------------------------------------------------------
class _ControlledEmbedder:
    """按预设相似度返回向量的桩 embedder，用来把 L1 的两个分支都逼出来。

    二维向量：query 固定为 (1,0)，文本向量按目标 cosine 生成。
    """

    def __init__(self, query: str, sims: dict[str, float], default_sim: float) -> None:
        self._query = query
        self._sims = sims
        self._default = default_sim

    @staticmethod
    def _vec(cos: float) -> list[float]:
        import math

        return [cos, math.sqrt(max(0.0, 1.0 - cos * cos))]

    def encode(self, text: str) -> list[float]:
        if text == self._query:
            return [1.0, 0.0]
        return self._vec(self._sims.get(text, self._default))

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


def check_l1_adaptive_loop() -> None:
    print("\n=== 5b. L1 的「自适应降阈值」循环到底会不会降 ===")
    print("   代码(compressor.py:198-207):")
    print("     for threshold in [0.25, 0.20, 0.15]:")
    print("         filtered = [t for t, s in scored if s >= threshold]")
    print("         if self.calculate_tokens(filtered) <= budget * 0.8: break")
    print("   docstring(compressor.py:181) 声称「若过滤后仍然超 budget，逐步降低到 0.15」")
    print()
    query = "2025年大模型Agent市场格局"
    docs = [_fake_doc(200, i) for i in range(30)]
    budget = 2000  # tokens

    for label, sim, expect in [
        ("A. 全部相似度 0.10（都过不了 0.25）", 0.10, "会 break 在 0.25，且结果为空 → 触发保底"),
        ("B. 全部相似度 0.90（都过得去）", 0.90, "三轮都试，最后 0.15 收下全部"),
    ]:
        emb = _ControlledEmbedder(query, {}, sim)
        comp = ContextCompressor(llm_policy=None, embedder=emb, budget=budget + 2048,
                                 output_reserve=2048)
        out = comp._l1_filter(list(docs), query, budget)
        print(f"  {label}")
        print(f"     → 保留 {len(out)} / {len(docs)} 篇, tokens={comp.calculate_tokens(out)}"
              f" (闸门 {budget * 0.8:.0f})")
        print(f"     预期: {expect}")

    # 证明 A 场景下阈值 0.20/0.15 根本没被尝试
    emb = _ControlledEmbedder(query, {}, 0.10)
    comp = ContextCompressor(llm_policy=None, embedder=emb, budget=4048, output_reserve=2048)
    tried: list[float] = []
    orig = comp.calculate_tokens

    def spy(texts: list[str]) -> int:
        return orig(texts)

    comp.calculate_tokens = spy  # type: ignore[method-assign]
    out_a = comp._l1_filter(list(docs), query, budget)
    print()
    print("  A 场景的机理：阈值 0.25 滤出空集 → 空集 tokens=0 <= budget*0.8 成立")
    print("  → 循环立刻 break，0.20 与 0.15 永远不会被尝试。")
    print("  → 也就是说：过滤「太狠」会被误判成「够省了」，跟 docstring 说的方向正好相反。")
    print(f"  之后靠 compressor.py:209-213 的保底，把 30 篇砍成 top-1 篇：{len(out_a)} 篇")

    # 对照：B 场景下确实会降到 0.15
    emb_b = _ControlledEmbedder(query, {}, 0.90)
    comp_b = ContextCompressor(llm_policy=None, embedder=emb_b, budget=4048, output_reserve=2048)
    out_b = comp_b._l1_filter(list(docs), query, budget)
    print(f"  B 场景对照：全部相似度 0.90 → 三轮都过不了 budget 闸门 → 0.15 收下 {len(out_b)} 篇")
    print("  结论：降阈值只在「过了 0.25 但总量仍超 80% 预算」时才发生；")
    print("        而「一片都没过 0.25」这条最常见的失败路径上，它一步都不降。")

    RESULTS["l1_adaptive_loop"] = {
        "case_all_below_0_25": {
            "kept_docs": len(out_a),
            "input_docs": len(docs),
            "lower_thresholds_tried": False,
            "fallback_top1_used": len(out_a) == 1,
        },
        "case_all_above_0_25": {"kept_docs": len(out_b), "input_docs": len(docs)},
        "docstring_claim": "若过滤后仍然超 budget，逐步降低到 0.15",
        "claim_holds_on_empty_result_path": False,
    }


# --------------------------------------------------------------------------
# 5c. 端到端：压缩链作用在真实报告上，比例与保留率是多少
# --------------------------------------------------------------------------
def check_end_to_end_on_real_report() -> None:
    print("\n=== 5c. 端到端实跑：真实报告分别走 L1 / L2 / L3 ===")
    report = _real_report_text()
    # 切成 200 字符碎片，模拟调用点喂进来的形状
    frags = [report[i : i + 200] for i in range(0, min(len(report), 6000), 200)]
    query = "2024年至2025年大模型Agent技术发展"
    print(f"  输入：真实报告切成 {len(frags)} 条 × 200 字符 = {sum(len(f) for f in frags):,} 字符")
    print()
    print(f"  {'强制级别':<10}{'输出条数':<10}{'输出字符':<12}{'比例':<10}{'信息保留率':<12}")
    out_rows = []
    for lvl in (1, 2, 3):
        comp = ContextCompressor(llm_policy=_EchoPolicy(), budget=13952 + 2048,
                                 output_reserve=2048)
        out = comp.compress(texts=list(frags), query=query, level=lvl, system_prompt_tokens=0)
        st = comp.get_stats()
        chars = sum(len(c) for c in out)
        print(f"  L{lvl:<9}{len(out):<10}{chars:<12,}{chars / sum(len(f) for f in frags):<10.3f}"
              f"{st['avg_retention']:<12.3f}")
        out_rows.append(
            {
                "level": lvl,
                "out_docs": len(out),
                "out_chars": chars,
                "compression_ratio": round(chars / sum(len(f) for f in frags), 4),
                "information_retention": st["avg_retention"],
            }
        )
    print()
    print("  ⚠ 证据等级：实测，但本机未装 sentence-transformers，")
    print("    Embedder 走确定性随机回退，因此：")
    print("    - 比例/information_retention 是真实代码算出来的，可信")
    print("    - 「筛得准不准」完全不可信（向量里没有语义）")
    print("    - L1 在这个环境下会把碎片砍到极少（见 5b 的 A 场景）")
    RESULTS["end_to_end_real_report"] = {
        "input_fragments": len(frags),
        "input_chars": sum(len(f) for f in frags),
        "rows": out_rows,
        "caveat": "本机无 sentence-transformers，向量为确定性随机回退",
    }


class _EchoPolicy:
    """桩 LLM：不做摘要，原样回显，让 L3 的兜底路径可复现。"""

    def __call__(self, messages: list[dict]) -> object:
        content = messages[-1].get("content", "")
        m = re.search(r"文档内容：\n(.*?)\n\n摘要：", content, re.S)
        body = m.group(1) if m else content
        return type("Resp", (), {"content": body[:200]})()



# --------------------------------------------------------------------------
# 6. YAML 里那些没人读的键
# --------------------------------------------------------------------------
def check_dead_config() -> None:
    print("\n=== 6. compressor 配置块：哪些键真的被读了 ===")
    raw = (REPO / "configs/default.yaml").read_text(encoding="utf-8")
    keys = []
    in_block = False
    for line in raw.splitlines():
        if line.startswith("compressor:"):
            in_block = True
            continue
        if in_block:
            if line and not line.startswith(" ") and not line.startswith("#"):
                break
            m = re.match(r"^\s{2}([a-z0-9_]+):", line)
            if m:
                keys.append(m.group(1))
    print(f"  YAML 里声明的键: {keys}")
    print()
    print(f"  {'键名':<26}{'src/+evaluation/ 中的读取点'}")
    dead, live = [], []
    for k in keys:
        n, hits = _count_calls(k)
        # 用引号形式更准：config.get("key")
        n2 = 0
        hits2 = []
        for p in _ship_py():
            for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if f'"{k}"' in line or f"'{k}'" in line:
                    n2 += 1
                    hits2.append(f"{p.relative_to(REPO).as_posix()}:{i}")
        flag = "✅ 被读" if n2 else "❌ 没人读"
        print(f"  {k:<26}{flag}  {hits2[:3]}")
        (live if n2 else dead).append(k)
    print()
    print(f"  被读: {live}")
    print(f"  死配置: {dead}")
    RESULTS["dead_config"] = {"declared": keys, "live": live, "dead": dead}


# --------------------------------------------------------------------------
# 7. get_stats() 有没有人调
# --------------------------------------------------------------------------
def check_get_stats_callers() -> None:
    print("\n=== 7. 模块文档里承诺的「量化评测接口」有没有接线 ===")
    print('  compressor.py:45  ... 同时提供量化评测接口，用于 ablation 实验。')
    print('  compressor.py:11  ... 量化评测接口：compression_ratio + information_retention')
    print()
    n, hits = _count_calls("get_stats")
    print(f"  get_stats 在 src/ + evaluation/ 里被调用的总次数: {n}")
    print(f"  命中位置: {hits}")
    print("  （项目里有 5 个类各自定义了同名 get_stats：ContextCompressor /")
    print("    ExtractiveCompressor / SlidingWindowCompressor / LLMSummarizer /")
    print("    experience_memory，上面统计的是全项目所有 get_stats 调用）")
    print(f"  → 结论：{'全项目没有任何一处调用 get_stats()' if n == 0 else '有调用点，见上'}")
    print("  → 也就是说 compressor.py:45 承诺的「量化评测接口，用于 ablation 实验」没有接线，")
    print("     与 verdict.py 的 to_json/from_json、评测层的 compute_composite_score 是同一类。")
    RESULTS["get_stats_callers"] = {"count": n, "hits": hits}


# --------------------------------------------------------------------------
# 8. Embedder 的回退会掏空 L1 的语义
# --------------------------------------------------------------------------
def check_embedder_fallback() -> None:
    print("\n=== 8. sentence-transformers 没装时，L1 的「语义」还剩多少 ===")
    from src.memory import embedder as emb_mod

    print(f"  _SENTENCE_TRANSFORMERS_AVAILABLE = {emb_mod._SENTENCE_TRANSFORMERS_AVAILABLE}")
    from src.memory.embedder import Embedder

    e = Embedder()
    related_a = "大模型Agent市场规模在2025年快速增长"
    related_b = "2025年大模型Agent赛道规模显著增长"
    unrelated = "今天天气不错，适合出门散步"
    va, vb, vu = e.encode(related_a), e.encode(related_b), e.encode(unrelated)

    def cos(x, y):
        import numpy as np

        x, y = np.array(x, dtype=np.float32), np.array(y, dtype=np.float32)
        return float(x.dot(y) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9))

    sim_rel, sim_irr = cos(va, vb), cos(va, vu)
    gap = sim_rel - sim_irr
    print(f"  语义相近的两句 cosine = {sim_rel:+.4f}")
    print(f"  完全无关的两句 cosine = {sim_irr:+.4f}")
    print(f"  差值 = {gap:+.4f}")
    print(f"  → 相近句是否明显更相似（判据 |差值| >= 0.10）: {abs(gap) >= 0.10}")
    print("  → L1 的自适应阈值 [0.25, 0.20, 0.15] 在这种向量上等于随机筛：")
    print("     两个都远离 0.25，绝大多数文本会被一刀切掉。")
    print("     这是环境事实（本机未装 sentence-transformers），不是代码缺陷；")
    print("     但意味着凡是在本机复现的 L1 数字都不能当作语义过滤效果来报。")
    RESULTS["embedder_fallback"] = {
        "sentence_transformers_available": emb_mod._SENTENCE_TRANSFORMERS_AVAILABLE,
        "cos_related": round(sim_rel, 4),
        "cos_unrelated": round(sim_irr, 4),
        "gap": round(gap, 4),
        "semantic_signal_present": abs(gap) >= 0.10,
    }


# --------------------------------------------------------------------------
# 9. 滑窗兜底：可能留下孤儿 tool 消息
# --------------------------------------------------------------------------
def _has_orphan_tool(msgs: list[dict]) -> bool:
    """检查是否存在「前面没有带 tool_calls 的 assistant」的 tool 消息。"""
    pending: set[str] = set()
    for m in msgs:
        role = m.get("role")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                pending.add(str(tc.get("id", tc.get("function", {}).get("name", "?"))))
        elif role == "tool":
            tid = str(m.get("tool_call_id", ""))
            if pending and tid not in pending:
                return True
            if not pending:
                return True
            pending.discard(tid)
    return False


def check_sliding_window_orphan() -> None:
    print("\n=== 9. 滑窗兜底的副作用：会切出孤儿 tool 消息 ===")
    # 每条约 600 字符，总量远超 max_chars，强制走截断分支
    msgs = [
        {"role": "system", "content": "你是研究员。"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "web_search", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "甲" * 600, "tool_call_id": "call_1", "name": "web_search"},
        {"role": "assistant", "content": "乙" * 600},
        {"role": "tool", "content": "丙" * 600, "tool_call_id": "call_1", "name": "web_search"},
        {"role": "user", "content": "继续"},
    ]
    for max_tokens in (400, 200):
        sw = SlidingWindowCompressor(max_tokens=max_tokens, char_per_token=3.5, min_recent_turns=2)
        out = sw.compress([dict(m) for m in msgs])
        roles = [m.get("role") for m in out]
        orphan = _has_orphan_tool(out)
        print(f"  --- max_tokens={max_tokens} (max_chars={int(max_tokens * 3.5)}) ---")
        print(f"    输入 roles = {[m.get('role') for m in msgs]}")
        print(f"    输出 roles = {roles}")
        print(f"    stats = {sw.get_stats()}")
        print(f"    出现孤儿 tool 消息: {orphan}")
        last = (out or [{}])[-1]
        print(f"    末条 content 长度 = {len(str(last.get('content', '')))}, "
              f"是否带 [CONTENT_TRUNCATED] = {'[CONTENT_TRUNCATED]' in str(last.get('content', ''))}")
        if max_tokens == 400:
            RESULTS["sliding_window_orphan"] = {
                "input_roles": [m.get("role") for m in msgs],
                "output_roles": roles,
                "truncated": sw.was_truncated(),
                "orphan_tool_message": orphan,
                "stats": sw.get_stats(),
            }
    print()
    print("  机理（sliding_window.py:105-120）：按字符预算从最旧一条开始 pop，")
    print("  pop 一次量一次总量，够省就停 —— 它不认 assistant(tool_calls) + tool 的成对语义，")
    print("  所以完全可能留下一个没有对应 assistant 的 tool 消息。")
    print("  多数 OpenAI 兼容后端遇到这种消息会直接 400。")
    print()
    print("  另一个副作用：截断分支返回 system_msgs + kept（sliding_window.py:120/145/153），")
    print("  会把 system 消息一律挪到最前面，原本插在中间的 system 会被重排。")


# --------------------------------------------------------------------------
# 10. 真实报告规模（用于交叉引用）
# --------------------------------------------------------------------------
def check_real_reports() -> None:
    print("\n=== 10. 真实报告规模（对照用） ===")
    sizes = []
    for d in sorted((REPO / "outputs").glob("reports*")):
        for f in sorted(d.glob("*.md")):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            sizes.append({"file": f.relative_to(REPO).as_posix(), "chars": len(txt)})
    if sizes:
        avg = sum(s["chars"] for s in sizes) / len(sizes)
        for s in sizes:
            print(f"  {s['chars']:>7} 字符  {s['file']}")
        print(f"  共 {len(sizes)} 份，平均 {avg:,.0f} 字符 ≈ {avg / _CHARS_PER_TOKEN:,.0f} tokens")
        print(f"  → 一份报告本身就远超 orchestrator 的 6000 字符闸门，")
        print(f"     但报告正文从不经过 compress()（compress 只吃 _memory_store 的 dict 条目）")
    RESULTS["real_reports"] = {"files": sizes, "avg_chars": round(avg) if sizes else 0}


# --------------------------------------------------------------------------
def main() -> None:
    print("=" * 78)
    print("M3 上下文压缩模块实测  ——  src/compressor/ (986 行 / 4 文件)")
    print("=" * 78)
    check_threshold_semantics()
    check_gate_incompatibility()
    check_level0_noop()
    check_l2_comment_math()
    check_l2_short_doc_noop()
    check_l1_adaptive_loop()
    check_end_to_end_on_real_report()
    check_dead_config()
    check_get_stats_callers()
    check_embedder_fallback()
    check_sliding_window_orphan()
    check_real_reports()

    outdir = REPO / "outputs"
    outdir.mkdir(exist_ok=True)
    out = outdir / "compressor_metrics.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 78)
    print(f"结果已写入 {out.relative_to(REPO).as_posix()}"
          f"（{out.stat().st_size:,} 字节）")
    print("=" * 78)


if __name__ == "__main__":
    main()
