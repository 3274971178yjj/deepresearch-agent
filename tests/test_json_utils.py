"""json_utils 单元测试：覆盖各模块此前自实现的全部解析场景。"""
import pytest

from src.utils.json_utils import (
    JsonExtractionError,
    extract_json,
    extract_json_dict,
)


class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_json_array(self):
        assert extract_json("[1, 2, 3]") == [1, 2, 3]

    def test_markdown_code_block_with_language_tag(self):
        text = '好的，以下是结果：\n```json\n{"score": 8}\n```\n以上。'
        assert extract_json(text) == {"score": 8}

    def test_markdown_code_block_without_tag(self):
        text = "```\n{'k': 1}\n```"
        assert extract_json(text) == {"k": 1}

    def test_surrounding_prose(self):
        text = '分析如下 {"sub_tasks": [{"task_id": "t1"}]} 请查收'
        assert extract_json(text) == {"sub_tasks": [{"task_id": "t1"}]}

    def test_trailing_comma_repair(self):
        text = '{"a": [1, 2, 3,], "b": {"c": 1,},}'
        assert extract_json(text) == {"a": [1, 2, 3], "b": {"c": 1}}

    def test_line_comment_repair(self):
        text = '{\n  "a": 1, // 这是注释\n  "b": 2\n}'
        assert extract_json(text) == {"a": 1, "b": 2}

    def test_comment_stripping_spares_urls(self):
        # 字符串字面量内的 // 不应被当成注释
        text = '{"url": "http://example.com/a//b", "n": 1}'
        assert extract_json(text) == {"url": "http://example.com/a//b", "n": 1}

    def test_single_quote_repair(self):
        assert extract_json("{'name': 'value'}") == {"name": "value"}

    def test_nested_object_first_match(self):
        text = '噪声 {"a": {"b": 2}} 中间 {"c": 3} 结尾'
        assert extract_json(text) == {"a": {"b": 2}}

    def test_braces_inside_string_values(self):
        text = '{"desc": "包含 {大括号} 的文本", "n": 1}'
        assert extract_json(text)["n"] == 1

    def test_empty_input_raises(self):
        with pytest.raises(JsonExtractionError):
            extract_json("")
        with pytest.raises(JsonExtractionError):
            extract_json("   \n  ")

    def test_no_json_raises(self):
        with pytest.raises(JsonExtractionError):
            extract_json("这里完全没有 JSON 内容")

    def test_none_input_raises(self):
        with pytest.raises(JsonExtractionError):
            extract_json(None)  # type: ignore[arg-type]


class TestExtractJsonDict:
    def test_returns_dict(self):
        assert extract_json_dict('{"a": 1}') == {"a": 1}

    def test_default_on_failure(self):
        assert extract_json_dict("not json", default={}) == {}

    def test_default_on_non_dict(self):
        assert extract_json_dict("[1,2]", default={"x": 1}) == {"x": 1}

    def test_raises_without_default(self):
        with pytest.raises(JsonExtractionError):
            extract_json_dict("not json")
        with pytest.raises(JsonExtractionError):
            extract_json_dict("[1,2]")

    def test_fenced_output_with_default(self):
        text = "```json\n{'has_new_issue': false}\n```"
        assert extract_json_dict(text) == {"has_new_issue": False}
