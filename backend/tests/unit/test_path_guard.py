from app.core.path_guard import validate_filename, sanitize_table_name, safe_temp_path

class TestValidateFilename:
    def test_valid(self):
        ok, name = validate_filename("report.csv")
        assert ok and name == "report.csv"
    def test_path_traversal(self):
        ok, msg = validate_filename("../etc/passwd")
        assert not ok
    def test_empty(self):
        ok, msg = validate_filename("")
        assert not ok
    def test_forbidden_ext(self):
        ok, msg = validate_filename("a.exe")
        assert not ok
    def test_backslash(self):
        ok, msg = validate_filename("a\b.csv")
        assert not ok

class TestSanitizeTableName:
    def test_valid(self):
        ok, name = sanitize_table_name("orders")
        assert ok and name == "orders"
    def test_sql_keyword_wrapped(self):
        ok, name = sanitize_table_name("select")
        assert ok
    def test_invalid(self):
        ok, msg = sanitize_table_name("drop table")
        assert not ok

class TestSafeTempPath:
    def test_creates_temp(self):
        path = safe_temp_path(".csv")
        assert "tmp" in path and path.endswith(".csv")
