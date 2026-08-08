import pytest
from app.core.json_guard import safe_json_parse, _extract_code_block, _fix_common_issues


class TestExtractCodeBlock:
    def test_json_fence(self):
        bt = chr(96) * 3
        nl = chr(10)
        text = bt + chr(106)+chr(115)+chr(111)+chr(110) + nl + chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(125) + nl + bt
        result = _extract_code_block(text)
        assert result == chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(125)

    def test_no_fence(self):
        assert _extract_code_block(chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(125)) is None


class TestFixCommonIssues:
    def test_trailing_comma(self):
        assert _fix_common_issues(chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(44)+chr(125)) == chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(125)


class TestSafeJsonParse:
    def test_valid(self):
        result = safe_json_parse(chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(125))
        assert result == {chr(97): 1}

    def test_markdown_wrapped(self):
        bt = chr(96) * 3
        nl = chr(10)
        text = bt + chr(106)+chr(115)+chr(111)+chr(110) + nl + chr(123)+chr(34)+chr(120)+chr(34)+chr(58)+chr(34)+chr(121)+chr(34)+chr(125) + nl + bt
        result = safe_json_parse(text)
        assert result == {chr(120): chr(121)}

    def test_invalid_returns_none(self):
        assert safe_json_parse('not json') is None

    def test_empty(self):
        assert safe_json_parse('') is None

    def test_array(self):
        assert safe_json_parse('[1, 2, 3]') == [1, 2, 3]

    def test_truncated_repair(self):
        result = safe_json_parse(chr(123)+chr(34)+chr(97)+chr(34)+chr(58)+chr(32)+chr(49)+chr(44)+chr(32)+chr(34)+chr(98)+chr(34)+chr(58)+chr(32)+chr(91)+chr(49)+chr(44)+chr(32)+chr(50))
        assert result is not None
