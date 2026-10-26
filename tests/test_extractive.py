"""ExtractiveCompressor 测试（TextRank + query-biased，Embedder fallback 模式）。"""
import pytest

from src.compressor.extractive import ExtractiveCompressor, _tokenize_sentences

TEXT = (
    "深度研究代理需要先对问题进行拆解。拆解后的子任务构成有向无环图。"
    "编排器按拓扑顺序调度这些子任务。每个子任务调用搜索工具收集证据。"
    "收集到的证据写入共享记忆库。记忆库负责去重和矛盾检测。"
    "最后由摘要器合成研究报告。报告经过红蓝对抗循环降噪。"
)


@pytest.fixture()
def compressor():
    return ExtractiveCompressor()


class TestTokenize:
    def test_splits_multiple_sentences(self):
        sentences = _tokenize_sentences("这是第一条足够长的句子。这是第二条足够长的句子！这是第三条足够长的句子？")
        assert len(sentences) == 3

    def test_short_sentences_filtered(self):
        # 分句器会过滤长度 <= 8 的短句
        assert _tokenize_sentences("短句。太短了！") == []

    def test_empty(self):
        assert _tokenize_sentences("") == []


class TestCompress:
    def test_short_text_untouched(self, compressor):
        short = "只有一句话的短文本不压缩。"
        assert compressor.compress(short, "查询") == short

    def test_long_text_compresses(self, compressor):
        compressed = compressor.compress(TEXT, "编排器如何调度子任务")
        assert len(compressed) < len(TEXT)
        # 保留的句子应来自原文
        for sent in compressed.split("。"):
            if sent.strip():
                assert sent.strip() in TEXT

    def test_target_ratio_controls_output(self, compressor):
        aggressive = compressor.compress(TEXT, "研究", target_ratio=0.2)
        conservative = compressor.compress(TEXT, "研究", target_ratio=0.8)
        assert len(aggressive) <= len(conservative)

    def test_get_stats(self, compressor):
        compressed = compressor.compress(TEXT, "研究")
        stats = compressor.get_stats(TEXT, compressed)
        assert stats["original_chars"] == len(TEXT)
        assert stats["compressed_chars"] == len(compressed)
        assert stats["compression_ratio"] == pytest.approx(
            len(compressed) / len(TEXT), abs=1e-3
        )
