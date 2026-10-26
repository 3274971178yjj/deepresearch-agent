"""BudgetTracker 测试。"""
import pytest

from src.planner.budget_tracker import BudgetTracker


class TestBudgetTracker:
    def test_track_accumulates(self):
        bt = BudgetTracker(budget_limit=1000)
        bt.track(300)
        bt.track(200)
        assert bt.get_usage() == 500
        assert bt.get_history() == [300, 200]

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            BudgetTracker().track(-1)

    def test_over_budget(self):
        bt = BudgetTracker(budget_limit=100)
        assert not bt.is_over_budget()
        bt.track(100)
        assert bt.is_over_budget()

    def test_near_budget_threshold(self):
        bt = BudgetTracker(budget_limit=100)
        bt.track(79)
        assert not bt.is_near_budget()      # 0.79 < 0.8
        bt.track(1)
        assert bt.is_near_budget()          # 0.80 >= 0.8

    def test_ratio_unbounded_above_one(self):
        bt = BudgetTracker(budget_limit=100)
        bt.track(250)
        assert bt.get_usage_ratio() == 2.5

    def test_dynamic_limit_adjust(self):
        bt = BudgetTracker(budget_limit=100)
        bt.track(90)
        assert bt.is_near_budget()
        bt.set_budget_limit(1000)
        assert not bt.is_near_budget()

    def test_reset(self):
        bt = BudgetTracker(budget_limit=100)
        bt.track(90)
        bt.reset()
        assert bt.get_usage() == 0
        assert bt.get_history() == []

    def test_snapshot_fields(self):
        bt = BudgetTracker(budget_limit=200)
        bt.track(100)
        snap = bt.snapshot()
        assert snap.total_tokens == 100
        assert snap.budget_limit == 200
        assert snap.usage_ratio == 0.5
        assert snap.is_over_budget is False

    def test_minimum_limit_clamped(self):
        assert BudgetTracker(budget_limit=0)._budget_limit >= 1
