"""SharedMemoryStore 测试（SQLite 用 tmp_path，Embedder 用确定性 fallback，无网络/无 API）。"""
import time

import pytest

from src.memory.memory_store import SharedMemoryStore, _is_semantically_opposite
from src.memory.long_term import MemoryEntry


def make_entry(claim: str, confidence: float = 0.9, topic: str = "测试主题") -> MemoryEntry:
    return MemoryEntry(
        entry_id=f"e_{abs(hash(claim)) % 10**8}",
        claim=claim,
        source="https://example.com/source",
        confidence=confidence,
        agent_id="agent_test",
        timestamp=time.time(),
        evidence_type="primary",
        embedding=[],  # 留空触发自动编码（fallback 为确定性向量）
        topic=topic,
    )


LONG_CLAIM = "这是一条足够长的记忆内容用于通过垃圾过滤器并进入记忆库进行测试。"


class TestPutAndDedup:
    def test_put_returns_id_and_counts(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        entry_id = store.put(make_entry(LONG_CLAIM))
        assert len(store) == 1

    def test_duplicate_claim_merged(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        first_id = store.put(make_entry(LONG_CLAIM, confidence=0.8))
        second_id = store.put(make_entry(LONG_CLAIM, confidence=0.6))
        # 完全相同的 claim → fallback embedding 完全一致 → cosine 1.0 > 0.92 → 合并
        assert second_id == first_id
        assert len(store) == 1

    def test_higher_confidence_replaces_lower(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        first_id = store.put(make_entry(LONG_CLAIM, confidence=0.5))
        store.put(make_entry(LONG_CLAIM, confidence=0.95))
        # 新条目置信度更高，应覆盖旧条目（数量仍为 1）
        assert len(store) == 1

    def test_junk_too_short_rejected(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        store.put(make_entry("太短", confidence=0.9))
        assert len(store) == 0

    def test_junk_low_confidence_rejected(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        store.put(make_entry(LONG_CLAIM, confidence=0.1))
        assert len(store) == 0


class TestQuery:
    def test_query_by_topic(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        store.put(make_entry(LONG_CLAIM, topic="AI"))
        store.put(make_entry("另外一条完全不同的长记忆内容用于话题过滤查询测试验证。", topic="经济"))
        ai_entries = store.query_by_topic("AI")
        assert len(ai_entries) == 1
        assert ai_entries[0].topic == "AI"

    def test_get_context_returns_text(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        store.put(make_entry(LONG_CLAIM))
        context = store.get_context_for_query(LONG_CLAIM, max_tokens=1000)
        assert isinstance(context, str)

    def test_persistence_across_reopen(self, tmp_path):
        db = str(tmp_path / "mem.db")
        store = SharedMemoryStore(db_path=db, session_id="s1")
        store.put(make_entry(LONG_CLAIM))
        store2 = SharedMemoryStore(db_path=db, session_id="s1")
        assert len(store2) == 1


class TestSemanticOppositeHeuristic:
    def test_increase_vs_decrease(self):
        assert _is_semantically_opposite("公司利润大幅增加", "公司利润大幅减少")

    def test_same_direction_not_opposite(self):
        assert not _is_semantically_opposite("收入持续增长", "营收稳步提高")

    def test_unrelated_not_opposite(self):
        assert not _is_semantically_opposite("今天天气晴朗", "模型参数规模很大")


class TestEviction:
    def test_evict_respects_limit(self, tmp_path):
        store = SharedMemoryStore(db_path=str(tmp_path / "mem.db"), session_id="s1")
        for i in range(5):
            store.put(make_entry(f"第{i}条彼此完全不同的记忆内容，长度足够通过过滤测试。", confidence=0.5 + i * 0.1))
        removed = store.evict(max_entries=3)
        assert len(store) <= 3
        assert isinstance(removed, int)
