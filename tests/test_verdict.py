"""VerdictEngine 评分引擎测试（M5 纯逻辑，无 LLM 调用）。"""
import math

import pytest

from src.adversarial.verdict import (
    Dimension,
    FixType,
    Issue,
    RedVerdict,
    Severity,
    VerdictEngine,
)


def make_issue(severity=Severity.MAJOR, dimension=Dimension.FACTUAL, fix_type=FixType.IN_PLACE):
    return Issue(
        severity=severity,
        dimension=dimension,
        description="测试问题",
        location="第1段",
        fix_type=fix_type,
    )


class TestComputeOverall:
    def test_uniform_scores(self):
        scores = {d: 8.0 for d in Dimension}
        assert VerdictEngine.compute_overall(scores) == pytest.approx(8.0)

    def test_weighted_average(self):
        # 权重: FACTUAL .30 / HALLUCINATION .25 / LOGICAL .20 / SOURCE .15 / COVERAGE .10
        scores = {d: 10.0 for d in Dimension}
        scores[Dimension.COVERAGE] = 0.0
        expect = 10.0 * (1.0 - 0.10)
        assert VerdictEngine.compute_overall(scores) == pytest.approx(expect)

    def test_scores_clamped_to_range(self):
        scores = {Dimension.FACTUAL: 15.0, Dimension.LOGICAL: -3.0}
        clamped = VerdictEngine.compute_overall(scores)
        assert 0.0 <= clamped <= 10.0

    def test_empty_returns_zero(self):
        assert VerdictEngine.compute_overall({}) == 0.0


class TestComputeDelta:
    def test_identical_is_zero(self):
        scores = {Dimension.FACTUAL: 7.0, Dimension.LOGICAL: 8.0}
        assert VerdictEngine.compute_delta(scores, dict(scores)) == 0.0

    def test_euclidean_distance(self):
        prev = {Dimension.FACTUAL: 6.0}
        curr = {Dimension.FACTUAL: 8.0, Dimension.LOGICAL: 6.0}
        expect = math.sqrt((8 - 6) ** 2 + 6 ** 2)
        assert VerdictEngine.compute_delta(prev, curr) == pytest.approx(expect)

    def test_missing_dim_filled_with_zero(self):
        prev = {Dimension.FACTUAL: 5.0}
        curr = {Dimension.FACTUAL: 5.0, Dimension.COVERAGE: 3.0}
        assert VerdictEngine.compute_delta(prev, curr) == pytest.approx(3.0)

    def test_both_empty(self):
        assert VerdictEngine.compute_delta({}, {}) == 0.0


class TestComputePriority:
    def test_critical_factural_outranks_minor_coverage(self):
        critical = make_issue(severity=Severity.CRITICAL, dimension=Dimension.FACTUAL)
        minor = make_issue(severity=Severity.MINOR, dimension=Dimension.COVERAGE)
        assert VerdictEngine.compute_priority(critical) > VerdictEngine.compute_priority(minor)

    def test_formula(self):
        issue = make_issue(severity=Severity.MAJOR, dimension=Dimension.FACTUAL, fix_type=FixType.IN_PLACE)
        expect = 5.0 * 0.30 * 1.0
        assert VerdictEngine.compute_priority(issue) == pytest.approx(expect)


class TestSerialization:
    def test_verdict_json_roundtrip(self):
        verdict = RedVerdict(
            dimension_scores={Dimension.FACTUAL: 7.5, Dimension.LOGICAL: 6.0},
            overall_score=7.0,
            issues=[make_issue()],
            raw_feedback="原始输出",
        )
        raw = VerdictEngine.to_json(verdict)
        restored = VerdictEngine.from_json(raw)
        assert restored.overall_score == 7.0
        assert restored.dimension_scores[Dimension.FACTUAL] == 7.5
        assert restored.issues[0].description == "测试问题"
        assert restored.raw_feedback == "原始输出"

    def test_issue_dict_roundtrip(self):
        issue = make_issue()
        restored = Issue.from_dict(issue.to_dict())
        assert restored.severity == Severity.MAJOR
        assert restored.fix_type == FixType.IN_PLACE
