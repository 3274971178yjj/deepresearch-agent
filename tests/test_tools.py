"""工具层测试：Calculator 安全求值、Notepad、FileReader 沙箱（全部本地，无网络）。"""
import asyncio

import pytest

from src.tools.calculator import CalculatorTool
from src.tools.notepad import NotepadTool
from src.tools.file_reader import FileReaderTool


def run(coro):
    return asyncio.run(coro)


class TestCalculator:
    def setup_method(self):
        self.calc = CalculatorTool()

    def test_basic_arithmetic(self):
        assert run(self.calc.execute("(150 + 230) * 0.15")) == "57.0"

    def test_percent_preprocess(self):
        # 15% → (15/100)
        result = run(self.calc.execute("200 * 15%"))
        assert float(result) == pytest.approx(30.0)

    def test_chinese_brackets(self):
        result = run(self.calc.execute("（1+2）*3"))
        assert float(result) == pytest.approx(9.0)

    def test_thousands_separator(self):
        result = run(self.calc.execute("1,000 + 500"))
        assert float(result) == pytest.approx(1500.0)

    def test_mean_function(self):
        result = run(self.calc.execute("mean([12, 15, 18, 21])"))
        assert float(result) == pytest.approx(16.5)

    def test_division_by_zero_handled(self):
        result = run(self.calc.execute("1 / 0"))
        assert "Error" in result

    def test_empty_expression(self):
        assert "Error" in run(self.calc.execute("   "))

    def test_arbitrary_name_rejected(self):
        result = run(self.calc.execute("__import__('os').getcwd()"))
        assert "Error" in result

    def test_dunder_rejected(self):
        assert "Error" in run(self.calc.execute("__builtins__"))

    def test_syntax_error_handled(self):
        assert "Error" in run(self.calc.execute("1 + + * 2"))


class TestNotepad:
    def setup_method(self):
        self.note = NotepadTool()

    def test_write_and_read(self):
        run(self.note.write("结论：A 方案更优", category="conclusion"))
        output = run(self.note.read(category="conclusion"))
        assert "结论：A 方案更优" in output

    def test_search(self):
        run(self.note.write("重要数据点：市场增速 15%", category="data"))
        output = run(self.note.search("市场增速"))
        assert "15%" in output

    def test_clear_category(self):
        run(self.note.write("待清除", category="temp"))
        run(self.note.clear(category="temp"))
        output = run(self.note.read(category="temp"))
        assert "待清除" not in output

    def test_categories_isolated(self):
        run(self.note.write("结论X", category="conclusion"))
        run(self.note.write("数据Y", category="data"))
        assert "结论X" in run(self.note.read(category="conclusion"))
        assert "数据Y" not in run(self.note.read(category="conclusion"))


class TestFileReaderSandbox:
    def test_read_allowed_file(self, tmp_path):
        target = tmp_path / "note.txt"
        target.write_text("文件内容", encoding="utf-8")
        reader = FileReaderTool(allowed_base_dir=str(tmp_path))
        result = run(reader.execute(str(target)))
        assert "文件内容" in result

    def test_read_outside_denied(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        reader = FileReaderTool(allowed_base_dir=str(base))
        result = run(reader.execute(str(outside)))
        assert "Access denied" in result

    def test_prefix_sibling_dir_denied(self, tmp_path):
        """同前缀目录不允许绕过沙箱（/base_evil 不是 /base 的子目录）。"""
        base = tmp_path / "base"
        base.mkdir()
        sibling = tmp_path / "base_evil"
        sibling.mkdir()
        evil = sibling / "secret.txt"
        evil.write_text("pwned", encoding="utf-8")
        reader = FileReaderTool(allowed_base_dir=str(base))
        result = run(reader.execute(str(evil)))
        assert "Access denied" in result

    def test_missing_file(self, tmp_path):
        reader = FileReaderTool(allowed_base_dir=str(tmp_path))
        result = run(reader.execute(str(tmp_path / "nope.txt")))
        assert "not found" in result.lower() or "Error" in result

    def test_unsupported_extension(self, tmp_path):
        target = tmp_path / "malware.exe"
        target.write_bytes(b"\x00\x01")
        reader = FileReaderTool(allowed_base_dir=str(tmp_path))
        result = run(reader.execute(str(target)))
        assert "Unsupported" in result

    def test_no_restriction_when_base_none(self, tmp_path):
        target = tmp_path / "open.txt"
        target.write_text("任意内容", encoding="utf-8")
        reader = FileReaderTool(allowed_base_dir=None)
        result = run(reader.execute(str(target)))
        assert "任意内容" in result
