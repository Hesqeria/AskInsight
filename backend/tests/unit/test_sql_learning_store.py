"""dash-style learnings: format + save gating (no DB in unit tests)."""
from app.services.sql_learning_store import format_for_prompt


def test_format_empty():
    assert format_for_prompt([]) == "(none)"


def test_format_lines():
    rows = [{"error": "Unknown column 't.gmv'", "wrong": "SELECT t.gmv",
             "fixed": "SELECT t.total_amount"}]
    out = format_for_prompt(rows)
    assert "Unknown column" in out
    assert "t.total_amount" in out
    assert out.count("1.") == 1
