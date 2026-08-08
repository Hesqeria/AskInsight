from app.core.scope_guard import filter_tables_by_scope, validate_table_scope

class TestFilterTablesByScope:
    def test_star_returns_all(self):
        tables = [{"name":"a","scope":["s"]},{"name":"b","scope":["h"]}]
        assert len(filter_tables_by_scope(tables,["*"])) == 2
    def test_exact_match(self):
        tables = [{"name":"a","scope":["s"]},{"name":"b","scope":["h"]}]
        r = filter_tables_by_scope(tables,["s"])
        assert len(r) == 1 and r[0]["name"] == "a"
    def test_no_scope_visible(self):
        tables = [{"name":"a","scope":[]},{"name":"b","scope":["h"]}]
        r = filter_tables_by_scope(tables,["f"])
        assert len(r) == 1 and r[0]["name"] == "a"
    def test_multiple_scopes(self):
        tables = [{"name":"a","scope":["s"]},{"name":"b","scope":["f"]},{"name":"c","scope":[]}]
        assert len(filter_tables_by_scope(tables,["s","f"])) == 3
    def test_empty_user_scope(self):
        tables = [{"name":"a","scope":["s"]}]
        assert len(filter_tables_by_scope(tables,[])) == 1

class TestValidateTableScope:
    def test_star_user(self):
        assert validate_table_scope("a",["s"],["*"]) is True
    def test_match(self):
        assert validate_table_scope("a",["s"],["s"]) is True
    def test_no_match(self):
        assert validate_table_scope("a",["s"],["h"]) is False
    def test_no_table_scope(self):
        assert validate_table_scope("a",[],["s"]) is True
