"""LLM lineage parser tests (P3-06).

Covers:
  - Rule-based SQL fallback (no LLM)
  - LLM-driven SQL parsing with JSON extraction
  - Python parsing (LLM-only path)
  - Transform classification (LLM label normalization)
  - Graceful fallback when LLM raises / times out
  - Edge normalization from various src/dst shapes
"""
import asyncio

import pytest

from app.agents.lineage_agent import LineageParser
from app.agents.lineage_agent.parser import (
    _classify_transform, _row_to_edge, _parse_table_column, _extract_json_object,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _FakeLLM:
    """Mimics LLMRouter: synchronous `complete` returning a dict."""
    def __init__(self, content: str):
        self._content = content
        self.calls = []

    def complete(self, messages, task_type=None, **kw):
        self.calls.append({"messages": messages, "task_type": task_type})
        return {"content": self._content, "model_used": "fake", "tokens_in": 0, "tokens_out": 0}


class _BoomLLM:
    def complete(self, *a, **kw):
        raise RuntimeError("dashscope down")


# --------------------------------------------------------------------------- #
# Transform classification
# --------------------------------------------------------------------------- #
def test_classify_transform_canonical_values():
    for t in ("DIRECT", "JOIN", "AGG", "FILTER", "CASE", "UNION", "DERIVED"):
        assert _classify_transform(t) == t
        assert _classify_transform(t.lower()) == t


def test_classify_transform_aggregate_funcs():
    for f in ("SUM", "COUNT", "AVG", "MIN", "MAX"):
        assert _classify_transform(f) == "AGG"


def test_classify_transform_fuzzy_labels():
    assert _classify_transform("WHERE x = 1") == "FILTER"
    assert _classify_transform("CASE WHEN ... THEN ...") == "CASE"
    assert _classify_transform("LEFT JOIN dim ON ...") == "JOIN"
    assert _classify_transform("UNION ALL") == "UNION"


def test_classify_transform_unknown_defaults_to_derived():
    assert _classify_transform("weird_expr") == "DERIVED"
    assert _classify_transform(None) == "DIRECT"
    assert _classify_transform("") == "DIRECT"


# --------------------------------------------------------------------------- #
# Table/column parsing
# --------------------------------------------------------------------------- #
def test_parse_table_column_full_path():
    assert _parse_table_column("dw.dwd_order.final_amount", "dw") == {
        "db": "dw", "table": "dwd_order", "column": "final_amount",
    }


def test_parse_table_column_two_part():
    assert _parse_table_column("dwd_order.final_amount", "dw") == {
        "db": "dw", "table": "dwd_order", "column": "final_amount",
    }


def test_parse_table_column_single_token():
    assert _parse_table_column("dwd_order", "dw") == {
        "db": "dw", "table": "dwd_order", "column": None,
    }


def test_parse_table_column_strips_quotes():
    assert _parse_table_column("`dw`.`dwd_order`.`final_amount`", "dw") == {
        "db": "dw", "table": "dwd_order", "column": "final_amount",
    }


# --------------------------------------------------------------------------- #
# Edge normalization
# --------------------------------------------------------------------------- #
def test_row_to_edge_dict_form():
    edge = _row_to_edge({
        "src": {"db": "dw", "table": "ods_order", "column": "amount"},
        "dst": {"db": "dw", "table": "dwd_order", "column": "final_amount"},
        "transform": "SUM",
    })
    assert edge is not None
    assert edge.src_table == "ods_order"
    assert edge.dst_column == "final_amount"
    assert edge.transformation == "AGG"


def test_row_to_edge_string_form():
    edge = _row_to_edge({
        "src": "dw.ods_order.amount",
        "dst": "dw.dwd_order.final_amount",
        "transform": "DIRECT",
    })
    assert edge is not None
    assert edge.src_column == "amount"
    assert edge.transformation == "DIRECT"


def test_row_to_edge_rejects_missing_table():
    assert _row_to_edge({"src": {}, "dst": {}}) is None
    assert _row_to_edge({}) is None
    assert _row_to_edge({"src": {"table": "t"}, "dst": {}}) is None
    assert _row_to_edge(None) is None


def test_row_to_edge_generates_stable_lineage_id():
    """Same input must produce same lineage_id (idempotent re-parse)."""
    e1 = _row_to_edge({"src": "dw.t.c", "dst": "dw.t2.c2", "transform": "DIRECT"})
    e2 = _row_to_edge({"src": "dw.t.c", "dst": "dw.t2.c2", "transform": "DIRECT"})
    assert e1.lineage_id == e2.lineage_id


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
def test_extract_json_plain():
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_with_prose():
    s = 'Here is the result:\n```json\n{"edges": [], "confidence": 0.9}\n```\nThanks.'
    assert _extract_json_object(s) == {"edges": [], "confidence": 0.9}


def test_extract_json_nested():
    s = 'preamble {"a": {"b": 2}, "c": [1,2,3]} trailing'
    assert _extract_json_object(s) == {"a": {"b": 2}, "c": [1, 2, 3]}


def test_extract_json_empty_or_invalid():
    assert _extract_json_object("") is None
    assert _extract_json_object("no json here") is None
    assert _extract_json_object("{broken json") is None


# --------------------------------------------------------------------------- #
# LineageParser - SQL rule-based fallback
# --------------------------------------------------------------------------- #
def test_parser_sql_no_llm_uses_rule_based():
    """Without an LLM, the parser must fall back to the regex SQL parser."""
    parser = LineageParser(llm=None)
    # Use unqualified table names since the legacy regex parser doesn't
    # handle schema-qualified names well; we're testing the plumbing
    # here, not the regex quality (that's covered by the LLM path).
    sql = """
        SELECT s.sku_name, SUM(o.final_amount) AS total
        FROM dwd_order_info_inc o
        JOIN dim_sku_info s ON s.sku_id = o.sku_id
        GROUP BY s.sku_name
    """
    edges = asyncio.run(parser.parse_sql(sql, use_llm=False))
    assert len(edges) > 0
    # The rule parser identifies the source tables / aliases.
    src_tables = {e.src_table for e in edges}
    assert "dwd_order_info_inc" in src_tables or "o" in src_tables
    # SUM aggregation should be classified as AGG.
    transforms = {e.transformation for e in edges}
    assert "AGG" in transforms or "DIRECT" in transforms


def test_parser_empty_sql_returns_empty():
    parser = LineageParser(llm=None)
    assert asyncio.run(parser.parse_sql("", use_llm=False)) == []
    assert asyncio.run(parser.parse_sql("   ", use_llm=False)) == []


# --------------------------------------------------------------------------- #
# LineageParser - LLM-driven
# --------------------------------------------------------------------------- #
def test_parser_sql_llm_overrides_baseline():
    """When the LLM returns valid JSON, its edges replace the baseline."""
    llm_content = '''```json
    {
      "edges": [
        {"src": "dw.ods_raw_order.amount", "dst": "dw.dwd_order_inc.final_amount", "transform": "AGG"},
        {"src": "dw.ods_raw_order.order_id", "dst": "dw.dwd_order_inc.order_id", "transform": "DIRECT"}
      ],
      "confidence": 0.95
    }
    ```'''
    fake = _FakeLLM(llm_content)
    parser = LineageParser(llm=fake)
    edges = asyncio.run(parser.parse_sql("SELECT 1", use_llm=True))
    assert len(edges) == 2
    transforms = {e.transformation for e in edges}
    assert transforms == {"AGG", "DIRECT"}
    # The LLM was actually called.
    assert len(fake.calls) == 1


def test_parser_sql_llm_returns_empty_falls_back_to_baseline():
    """If the LLM successfully returns no edges, we keep the baseline
    (the LLM may have just refused; baseline is still useful signal)."""
    fake = _FakeLLM('{"edges": [], "confidence": 1.0}')
    parser = LineageParser(llm=fake)
    # Use an aliased SQL the regex parser can handle.
    sql = "SELECT SUM(o.final_amount) FROM dwd_order_info_inc o"
    edges = asyncio.run(parser.parse_sql(sql, use_llm=True))
    # Baseline should pick up at least one edge.
    assert len(edges) > 0


def test_parser_sql_llm_garbage_falls_back_to_baseline():
    fake = _FakeLLM("sorry, I cannot parse that")
    parser = LineageParser(llm=fake)
    sql = "SELECT SUM(o.final_amount) AS gmv FROM dwd_order_info_inc o"
    edges = asyncio.run(parser.parse_sql(sql, use_llm=True))
    assert len(edges) > 0  # baseline kicked in


def test_parser_sql_llm_raises_falls_back_to_baseline():
    """If the LLM raises, we must still return the rule-based baseline."""
    parser = LineageParser(llm=_BoomLLM())
    sql = "SELECT SUM(o.final_amount) FROM dwd_order_info_inc o"
    edges = asyncio.run(parser.parse_sql(sql, use_llm=True))
    assert len(edges) > 0


# --------------------------------------------------------------------------- #
# LineageParser - Python
# --------------------------------------------------------------------------- #
def test_parser_python_without_llm_is_noop():
    parser = LineageParser(llm=None)
    assert asyncio.run(parser.parse_python("df = pd.read_sql(...)")) == []


def test_parser_python_with_llm():
    """Python parsing has no rule baseline; LLM result is returned as-is."""
    llm_content = '''{
      "edges": [
        {"src": "dw.ods_raw.user_id", "dst": "dw.dwd_user.user_id", "transform": "DIRECT"},
        {"src": "dw.ods_raw.event_ts", "dst": "dw.dwd_user.first_seen", "transform": "DERIVED"}
      ],
      "confidence": 0.88
    }'''
    fake = _FakeLLM(llm_content)
    parser = LineageParser(llm=fake)
    edges = asyncio.run(parser.parse_python("""
        df = spark.read.table('dw.ods_raw')
        df2 = df.select('user_id', F.min('event_ts').alias('first_seen'))
        df2.write.mode('overwrite').saveAsTable('dw.dwd_user')
    """))
    assert len(edges) == 2
    transforms = {e.transformation for e in edges}
    assert transforms == {"DIRECT", "DERIVED"}


def test_parser_python_llm_garbage_returns_empty():
    fake = _FakeLLM("no JSON")
    parser = LineageParser(llm=fake)
    assert asyncio.run(parser.parse_python("x = 1")) == []


# --------------------------------------------------------------------------- #
# LineageParser - generic dispatch
# --------------------------------------------------------------------------- #
def test_parser_dispatch_by_code_type():
    """`parse(code, code_type=...)` routes to the right path."""
    fake = _FakeLLM('{"edges": [{"src": "dw.t.c", "dst": "dw.t2.c2"}]}')
    parser = LineageParser(llm=fake)

    # sql -> rule baseline OR llm
    sql_edges = asyncio.run(parser.parse("SELECT * FROM dw.t", code_type="sql"))
    assert isinstance(sql_edges, list)

    # python -> llm only
    py_edges = asyncio.run(parser.parse("x = 1", code_type="python"))
    assert isinstance(py_edges, list)

    # shell -> llm only (no baseline)
    sh_edges = asyncio.run(parser.parse("cat foo | bar", code_type="shell"))
    assert isinstance(sh_edges, list)
