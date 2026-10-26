"""红/蓝 Agent 的 JSON 解析层测试（不需要真实 LLM，直接测解析方法）。"""
import pytest

from src.adversarial.red_agent import RedAgent
from src.adversarial.blue_agent import BlueAgent
from src.adversarial.verdict import Dimension


class TestRedAgentParse:
    @pytest.fixture()
    def red(self):
        return RedAgent(policy=None)

    def test_plain_json(self, red):
        score, issues = red._parse_json_output(
            '{"score": 8.2, "issues": []}', Dimension.FACTUAL
        )
        assert score == 8.2
        assert issues == []

    def test_fenced_json_with_prose(self, red):
        raw = '评分如下：\n```json\n{"score": 6.5, "issues": []}\n```\n完毕'
        score, issues = red._parse_json_output(raw, Dimension.FACTUAL)
        assert score == 6.5

    def test_empty_returns_conservative(self, red):
        score, issues = red._parse_json_output("", Dimension.FACTUAL)
        assert score == 5.0
        assert issues == []

    def test_garbage_returns_conservative(self, red):
        score, issues = red._parse_json_output("模型输出损坏了", Dimension.FACTUAL)
        assert score == 5.0
        assert issues == []


class TestBlueAgentParse:
    def test_parse_fix_json_plain(self):
        raw = '{"fixed_content": "修复后内容", "changes": [{"type": "MODIFY"}]}'
        content, changes = BlueAgent._parse_repair_json(raw, "changes")
        assert content == "修复后内容"
        assert changes == [{"type": "MODIFY"}]

    def test_parse_fix_json_fenced(self):
        raw = '```json\n{"fixed_content": "x", "changes": []}\n```'
        content, changes = BlueAgent._parse_repair_json(raw, "changes")
        assert content == "x"

    def test_parse_removal_json_uses_list_key(self):
        raw = '{"fixed_content": "y", "removed_segments": [{"seg": 1}]}'
        content, removed = BlueAgent._parse_repair_json(raw, "removed_segments")
        assert content == "y"
        assert removed == [{"seg": 1}]

    def test_invalid_returns_empty(self):
        content, changes = BlueAgent._parse_repair_json("无法解析", "changes")
        assert content == ""
        assert changes == []

    def test_list_key_not_a_list_coerced(self):
        content, changes = BlueAgent._parse_repair_json(
            '{"fixed_content": "z", "changes": "not-a-list"}', "changes"
        )
        assert changes == []

    def test_parse_fix_json_wrapper_methods(self):
        blue = BlueAgent.__new__(BlueAgent)  # 只测解析，不初始化 policy/tools
        content, changes = blue._parse_fix_json('{"fixed_content": "a", "changes": []}')
        assert content == "a"
        content, removed = blue._parse_removal_json('{"fixed_content": "b", "removed_segments": []}')
        assert content == "b"
