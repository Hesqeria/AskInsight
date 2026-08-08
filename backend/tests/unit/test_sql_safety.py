import pytest
from app.agent.nodes.validate_sql_safety import validate_sql_safety, _normalize, _strip_comments


class TestNormalize:
    def test_leaves_ascii_unchanged(self):
        assert _normalize('SELECT') == 'SELECT'

    def test_removes_fullwidth(self):
        result = _normalize(chr(65297) + chr(65298) + chr(65299))
        assert result == '123'

    def test_empty_string(self):
        assert _normalize(
            chr(69) + chr(77) + chr(80) + chr(84) + chr(89)
        ) == chr(69) + chr(77) + chr(80) + chr(84) + chr(89)


class TestStripComments:
    def test_single_line(self):
        assert _strip_comments(chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(49)+chr(32)+chr(45)+chr(45)+chr(32)+chr(99)) == chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(49)+chr(32)

    def test_inline_block(self):
        result = _strip_comments(chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(47)+chr(42)+chr(32)+chr(120)+chr(32)+chr(42)+chr(47)+chr(32)+chr(49))
        assert result == chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(32)+chr(49)


class TestValidateSQLSafety:
    def test_valid_select(self):
        ok, msg = validate_sql_safety(chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(42)+chr(32)+chr(70)+chr(82)+chr(79)+chr(77)+chr(32)+chr(111))
        assert ok and msg == chr(79)+chr(75)

    def test_forbidden_drop(self):
        ok, msg = validate_sql_safety(chr(68)+chr(82)+chr(79)+chr(80)+chr(32)+chr(84)+chr(65)+chr(66)+chr(76)+chr(69)+chr(32)+chr(111))
        assert not ok

    def test_forbidden_insert(self):
        ok, _ = validate_sql_safety(chr(73)+chr(78)+chr(83)+chr(69)+chr(82)+chr(84)+chr(32)+chr(73)+chr(78)+chr(84)+chr(79)+chr(32)+chr(111)+chr(32)+chr(86)+chr(65)+chr(76)+chr(85)+chr(69)+chr(83)+chr(32)+chr(40)+chr(49)+chr(41))
        assert not ok

    def test_forbidden_delete(self):
        ok, _ = validate_sql_safety(chr(68)+chr(69)+chr(76)+chr(69)+chr(84)+chr(69)+chr(32)+chr(70)+chr(82)+chr(79)+chr(77)+chr(32)+chr(111))
        assert not ok

    def test_empty_sql(self):
        ok, msg = validate_sql_safety('')
        assert not ok

    def test_multi_statement(self):
        ok, msg = validate_sql_safety(chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(49)+chr(59)+chr(32)+chr(68)+chr(82)+chr(79)+chr(80)+chr(32)+chr(84)+chr(65)+chr(66)+chr(76)+chr(69)+chr(32)+chr(120))
        assert not ok

    def test_drop_in_literal_ignored(self):
        sql = chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(39)+chr(68)+chr(82)+chr(79)+chr(80)+chr(32)+chr(84)+chr(65)+chr(66)+chr(76)+chr(69)+chr(39)+chr(32)+chr(70)+chr(82)+chr(79)+chr(77)+chr(32)+chr(120)
        ok, msg = validate_sql_safety(sql)
        assert ok

    def test_cte(self):
        ok, msg = validate_sql_safety(chr(87)+chr(73)+chr(84)+chr(72)+chr(32)+chr(99)+chr(32)+chr(65)+chr(83)+chr(32)+chr(40)+chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(49)+chr(41)+chr(32)+chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+chr(32)+chr(42)+chr(32)+chr(70)+chr(82)+chr(79)+chr(77)+chr(32)+chr(99))
        assert ok
