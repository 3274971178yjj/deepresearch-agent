"""Planner 测试：JSON 解析、DAG 校验、task_map 重建（使用 FakePolicy，无 API 调用）。"""
import json

import pytest

from src.planner.planner import Planner, PlanParseError
from src.planner.budget_tracker import BudgetTracker


VALID_PLAN = {
    "sub_tasks": [
        {"task_id": "t1", "task_type": "search", "description": "搜索背景资料", "dependencies": []},
        {"task_id": "t2", "task_type": "analyze", "description": "分析数据", "dependencies": ["t1"]},
    ]
}


class FakePolicy:
    """返回预置 content 的假 LLM 策略，接口与 VLLMPolicy.__call__ 一致。"""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        return {"content": self.content}


def make_planner(content: str) -> Planner:
    return Planner(policy=FakePolicy(content), budget_tracker=BudgetTracker())


class TestGeneratePlan:
    def test_valid_json(self):
        planner = make_planner(json.dumps(VALID_PLAN, ensure_ascii=False))
        dag = planner.generate_plan("测试问题")
        assert len(dag) == 2
        assert set(dag.get_dependencies("t2")) == {"t1"}

    def test_fenced_json_with_prose(self):
        raw = f"以下是规划结果：\n```json\n{json.dumps(VALID_PLAN)}\n```\n希望有帮助"
        dag = make_planner(raw).generate_plan("测试问题")
        assert len(dag) == 2

    def test_trailing_commas_and_comments(self):
        raw = """
        {
          "sub_tasks": [ // 任务列表
            {"task_id": "t1", "task_type": "search", "description": "x" * 1, "dependencies": [],},
          ]
        }
        """.replace('"x" * 1', '"搜索资料"')
        dag = make_planner(raw).generate_plan("测试问题")
        assert len(dag) == 1

    def test_garbage_raises_parse_error(self):
        with pytest.raises(PlanParseError):
            make_planner("完全不是 JSON 的输出").generate_plan("测试问题")

    def test_missing_sub_tasks_raises(self):
        with pytest.raises(PlanParseError):
            make_planner('{"tasks": []}').generate_plan("测试问题")

    def test_cyclic_plan_raises(self):
        cyclic = {
            "sub_tasks": [
                {"task_id": "a", "dependencies": ["b"]},
                {"task_id": "b", "dependencies": ["a"]},
            ]
        }
        with pytest.raises(PlanParseError):
            make_planner(json.dumps(cyclic)).generate_plan("测试问题")

    def test_unknown_task_type_falls_back_to_search(self):
        plan = {"sub_tasks": [{"task_id": "t1", "task_type": "weird_type", "dependencies": []}]}
        planner = make_planner(json.dumps(plan))
        dag = planner.generate_plan("q")
        task_map = planner.get_task_map_from_dag(dag, planner._last_raw_json)
        assert task_map["t1"].task_type.value == "search"

    def test_budget_tracked(self):
        planner = make_planner(json.dumps(VALID_PLAN))
        planner.generate_plan("测试问题")
        assert planner.budget_tracker.get_usage() > 0


class TestGetTaskMap:
    def test_roundtrip_preserves_fields(self):
        raw = json.dumps(VALID_PLAN, ensure_ascii=False)
        planner = make_planner(raw)
        dag = planner.generate_plan("测试问题")
        task_map = planner.get_task_map_from_dag(dag, planner._last_raw_json)
        assert set(task_map) == {"t1", "t2"}
        assert task_map["t1"].description == "搜索背景资料"
        assert task_map["t2"].dependencies == ["t1"]

    def test_fenced_output(self):
        raw = f"```json\n{json.dumps(VALID_PLAN)}\n```"
        planner = make_planner(raw)
        task_map = planner.get_task_map_from_dag(None, raw)
        assert set(task_map) == {"t1", "t2"}

    def test_invalid_returns_empty(self):
        assert make_planner("").get_task_map_from_dag(None, "垃圾输出") == {}
