"""Unit tests for app.eval.utils.

Covers the "必做三件套" from NL2SQL其他测评方法-PRD.md:
  - FR-METHOD-02 Component matching
  - FR-METHOD-03 Schema Linking F1 (table + column)
  - FR-METHOD-04 AST similarity
  - FR-METHOD-07 Cost efficiency
Plus the EX/difficulty/aggregation primitives already in utils.py.
"""
import math

import pytest

from app.eval.utils import (
    # Schema extraction
    extract_referenced_tables,
    extract_referenced_columns,
    normalize_sql,
    # Equivalence
    result_sets_equal,
    ast_similarity,
    # F1
    schema_linking_f1,
    schema_linking_f1_columns,
    F1Result,
    # Component
    component_match,
    ComponentScores,
    # Cost
    cost_efficiency,
    # Difficulty
    classify_difficulty,
    DIFFICULTY_EASY,
    DIFFICULTY_MEDIUM,
    DIFFICULTY_HARD,
    # Verdict
    QueryVerdict,
    EvalReport,
    aggregate_verdicts,
)


# --------------------------------------------------------------------------- #
# Table extraction
# --------------------------------------------------------------------------- #
class TestExtractTables:
    def test_single_table(self):
        assert extract_referenced_tables("SELECT * FROM orders") == {"orders"}

    def test_multi_table_with_join(self):
        sql = "SELECT * FROM orders o JOIN users u ON o.uid = u.id"
        assert extract_referenced_tables(sql) == {"orders", "users"}

    def test_case_insensitive(self):
        assert extract_referenced_tables("select * from ORDERS") == {"orders"}

    def test_ignores_from_inside_string_literal(self):
        sql = "SELECT * FROM logs WHERE msg = 'select from foo'"
        assert extract_referenced_tables(sql) == {"logs"}

    def test_with_schema_prefix(self):
        sql = "SELECT * FROM dw.orders"
        assert extract_referenced_tables(sql) == {"orders"}

    def test_backtick_quoted(self):
        sql = "SELECT * FROM `order`"
        assert extract_referenced_tables(sql) == {"order"}

    def test_empty(self):
        assert extract_referenced_tables("") == set()
        assert extract_referenced_tables(None) == set()


# --------------------------------------------------------------------------- #
# Column extraction
# --------------------------------------------------------------------------- #
class TestExtractColumns:
    def test_select_columns(self):
        cols = extract_referenced_columns("SELECT name, age FROM users")
        assert "name" in cols
        assert "age" in cols

    def test_where_columns(self):
        cols = extract_referenced_columns(
            "SELECT id FROM orders WHERE amount > 100 AND region = 'cn'"
        )
        assert "amount" in cols
        assert "region" in cols

    def test_ignores_aggregates_as_columns(self):
        # SUM/COUNT are functions, not columns - they shouldn't pollute F1.
        cols = extract_referenced_columns("SELECT SUM(amount), COUNT(*) FROM orders")
        assert "amount" in cols
        assert "sum" not in cols
        assert "count" not in cols

    def test_strips_table_prefix(self):
        cols = extract_referenced_columns("SELECT o.amount, u.name FROM orders o JOIN users u")
        assert "amount" in cols
        assert "name" in cols

    def test_ignores_sql_keywords(self):
        cols = extract_referenced_columns(
            "SELECT a FROM t WHERE x > 1 ORDER BY a DESC LIMIT 10"
        )
        assert "from" not in cols
        assert "where" not in cols
        assert "order" not in cols
        assert "limit" not in cols


# --------------------------------------------------------------------------- #
# EX / result set equivalence
# --------------------------------------------------------------------------- #
class TestResultSetsEqual:
    def test_identical(self):
        assert result_sets_equal([(1, "a")], [(1, "a")]) is True

    def test_order_insensitive_default(self):
        assert result_sets_equal([(1,), (2,)], [(2,), (1,)]) is True

    def test_order_matters_when_flag(self):
        assert result_sets_equal([(1,), (2,)], [(2,), (1,)], order_matters=True) is False

    def test_different_length(self):
        assert result_sets_equal([(1,)], [(1,), (2,)]) is False

    def test_both_empty(self):
        assert result_sets_equal([], []) is True

    def test_float_tolerance(self):
        # 100.00004 rounds to 100.0 at 4dp - should match.
        assert result_sets_equal([(100.0,)], [(100.00004,)]) is True

    def test_float_outside_tolerance(self):
        assert result_sets_equal([(100.0,)], [(100.01,)]) is False

    def test_string_normalization(self):
        # Strip + lowercase so "A " == "a".
        assert result_sets_equal([("A ",)], [("a",)]) is True


# --------------------------------------------------------------------------- #
# AST similarity
# --------------------------------------------------------------------------- #
class TestAstSimilarity:
    def test_identical_sql(self):
        assert ast_similarity(
            "SELECT a FROM t", "SELECT a FROM t"
        ) == pytest.approx(1.0)

    def test_trivially_different(self):
        # Different whitespace/case should normalize to ~1.0.
        sim = ast_similarity(
            "select a from t",
            "SELECT  a  FROM  t",
        )
        assert sim >= 0.9

    def test_completely_different(self):
        sim = ast_similarity(
            "SELECT a FROM t",
            "DROP TABLE users",
        )
        assert sim < 0.9

    def test_empty(self):
        assert ast_similarity("", "") == 0.0

    def test_returns_float_in_unit_range(self):
        sim = ast_similarity(
            "SELECT a, b FROM t WHERE c > 10",
            "SELECT a FROM t WHERE c > 5",
        )
        assert 0.0 <= sim <= 1.0


# --------------------------------------------------------------------------- #
# Schema linking F1 (table-level)
# --------------------------------------------------------------------------- #
class TestSchemaLinkingF1:
    def test_perfect_match(self):
        r = schema_linking_f1({"orders", "users"}, {"orders", "users"})
        assert r.f1 == 1.0
        assert r.precision == 1.0
        assert r.recall == 1.0

    def test_partial_match(self):
        r = schema_linking_f1({"a", "b", "c"}, {"a", "b", "x"})
        # tp=2, p_pred=3, p_gold=3 -> P=R=2/3, F1=2/3
        assert r.f1 == pytest.approx(2 / 3, abs=0.01)

    def test_no_overlap(self):
        r = schema_linking_f1({"a", "b"}, {"c", "d"})
        assert r.f1 == 0.0
        assert r.precision == 0.0
        assert r.recall == 0.0

    def test_both_empty_is_perfect(self):
        r = schema_linking_f1(set(), set())
        assert r.f1 == 1.0

    def test_pred_empty_is_zero(self):
        r = schema_linking_f1({"a"}, set())
        assert r.f1 == 0.0

    def test_case_insensitive(self):
        r = schema_linking_f1({"ORDERS"}, {"orders"})
        assert r.f1 == 1.0


# --------------------------------------------------------------------------- #
# Schema linking F1 (column-level)
# --------------------------------------------------------------------------- #
class TestSchemaLinkingF1Columns:
    def test_identical_selects(self):
        r = schema_linking_f1_columns(
            "SELECT name, age FROM users",
            "SELECT name, age FROM users",
        )
        assert r.f1 >= 0.8  # not exactly 1.0 because 'users' table is stripped

    def test_missing_column_lowers_recall(self):
        r_full = schema_linking_f1_columns(
            "SELECT name, age FROM users",
            "SELECT name, age FROM users",
        )
        r_partial = schema_linking_f1_columns(
            "SELECT name, age FROM users",
            "SELECT name FROM users",
        )
        assert r_partial.recall < r_full.recall

    def test_extra_column_lowers_precision(self):
        r = schema_linking_f1_columns(
            "SELECT name FROM users",
            "SELECT name, age, email FROM users",
        )
        assert r.precision < 1.0


# --------------------------------------------------------------------------- #
# Component matching
# --------------------------------------------------------------------------- #
class TestComponentMatch:
    def test_identical_sql_scores_one_everywhere(self):
        sql = "SELECT a, b FROM t1 JOIN t2 ON t1.id = t2.id WHERE x > 5 GROUP BY a ORDER BY a"
        s = component_match(sql, sql)
        assert s.select == pytest.approx(1.0)
        assert s.from_ == pytest.approx(1.0)
        assert s.where == pytest.approx(1.0)
        assert s.group_by == pytest.approx(1.0)
        assert s.order_by == pytest.approx(1.0)
        assert s.overall == pytest.approx(1.0)

    def test_missing_join_lowers_from_score(self):
        gold = "SELECT a FROM t1 JOIN t2 ON t1.id = t2.id"
        pred = "SELECT a FROM t1"
        s = component_match(gold, pred)
        assert s.from_ < 1.0

    def test_missing_where_lowers_where_score(self):
        gold = "SELECT a FROM t WHERE x > 5"
        pred = "SELECT a FROM t"
        s = component_match(gold, pred)
        assert s.where == 0.0

    def test_select_columns_partial(self):
        gold = "SELECT a, b, c FROM t"
        pred = "SELECT a, b FROM t"
        s = component_match(gold, pred)
        # tp=2, pred_n=2, gold_n=3 -> P=1, R=2/3, F1=0.8
        assert s.select == pytest.approx(0.8, abs=0.01)

    def test_no_group_by_in_either_is_perfect(self):
        # If both have no GROUP BY, that clause scores 1.0 (vacuous match).
        s = component_match("SELECT a FROM t", "SELECT a FROM t")
        assert s.group_by == pytest.approx(1.0)

    def test_to_dict_includes_all_components(self):
        s = component_match("SELECT a FROM t", "SELECT a FROM t")
        d = s.to_dict()
        for key in ("select", "from", "where", "group_by",
                    "having", "order_by", "overall"):
            assert key in d


# --------------------------------------------------------------------------- #
# Cost efficiency
# --------------------------------------------------------------------------- #
class TestCostEfficiency:
    def test_basic_calculation(self):
        # 1000 in + 500 out at 0.001/1k in, 0.002/1k out
        # cost = (1 * 0.001) + (0.5 * 0.002) = 0.002
        # per 10 correct = 0.0002
        c = cost_efficiency(10, 1000, 500, 0.001, 0.002)
        assert c == pytest.approx(0.0002, abs=1e-6)

    def test_zero_correct_returns_none(self):
        assert cost_efficiency(0, 1000, 500, 0.001, 0.002) is None

    def test_negative_correct_returns_none(self):
        assert cost_efficiency(-5, 1000, 500, 0.001, 0.002) is None

    def test_zero_tokens_zero_cost(self):
        c = cost_efficiency(5, 0, 0, 0.001, 0.002)
        assert c == 0.0

    def test_distinguishes_cheap_and_expensive_models(self):
        cheap = cost_efficiency(80, 100_000, 50_000, 0.00007, 0.00015)
        expensive = cost_efficiency(82, 100_000, 50_000, 0.01, 0.03)
        # Even though the expensive model got 2 more right, cost/correct
        # should be much higher.
        assert expensive > cheap * 10


# --------------------------------------------------------------------------- #
# Difficulty classification
# --------------------------------------------------------------------------- #
class TestClassifyDifficulty:
    def test_easy_single_table(self):
        assert classify_difficulty("SELECT a FROM t WHERE x = 1") == DIFFICULTY_EASY

    def test_medium_join(self):
        sql = "SELECT a FROM t1 JOIN t2 ON t1.id = t2.id"
        assert classify_difficulty(sql) == DIFFICULTY_MEDIUM

    def test_medium_group_by(self):
        assert classify_difficulty("SELECT a, COUNT(*) FROM t GROUP BY a") == DIFFICULTY_MEDIUM

    def test_hard_four_tables(self):
        sql = "SELECT a FROM t1 JOIN t2 JOIN t3 JOIN t4"
        assert classify_difficulty(sql) == DIFFICULTY_HARD

    def test_hard_window_function(self):
        sql = "SELECT a, ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) FROM t"
        assert classify_difficulty(sql) == DIFFICULTY_HARD

    def test_hard_cte(self):
        sql = "WITH cte AS (SELECT a FROM t) SELECT * FROM cte"
        assert classify_difficulty(sql) == DIFFICULTY_HARD

    def test_empty_defaults_easy(self):
        assert classify_difficulty("") == DIFFICULTY_EASY


# --------------------------------------------------------------------------- #
# QueryVerdict
# --------------------------------------------------------------------------- #
class TestQueryVerdict:
    def _make(self, **overrides):
        defaults = dict(
            question="Q",
            gold_sql="SELECT a FROM t",
            pred_sql="SELECT a FROM t",
            difficulty="easy",
            sql_valid=True,
            ex_correct=True,
            ast_similarity=1.0,
            schema_f1=1.0,
        )
        defaults.update(overrides)
        return QueryVerdict(**defaults)

    def test_passed_when_ex_correct(self):
        assert self._make(ex_correct=True).passed is True

    def test_passed_when_ast_and_schema_match(self):
        v = self._make(ex_correct=None, ast_similarity=0.9, schema_f1=1.0)
        assert v.passed is True

    def test_not_passed_when_ex_wrong(self):
        v = self._make(ex_correct=False, ast_similarity=0.95, schema_f1=1.0)
        assert v.passed is False

    def test_to_dict_includes_extended_fields(self):
        v = self._make(
            schema_f1_columns=0.85,
            component_overall=0.78,
            component_select=0.92,
            component_where=0.65,
        )
        d = v.to_dict()
        assert d["schema_f1_columns"] == 0.85
        assert d["component_overall"] == 0.78
        assert d["component_select"] == 0.92
        assert d["component_where"] == 0.65


# --------------------------------------------------------------------------- #
# Aggregate verdicts
# --------------------------------------------------------------------------- #
class TestAggregateVerdicts:
    def _v(self, ex_correct=True, diff="easy", latency=100, tokens=50,
           col_f1=None, comp=None):
        return QueryVerdict(
            question="Q", gold_sql="", pred_sql="",
            difficulty=diff, sql_valid=True, ex_correct=ex_correct,
            ast_similarity=0.9, schema_f1=0.9,
            latency_ms=latency, tokens_used=tokens,
            schema_f1_columns=col_f1, component_overall=comp,
        )

    def test_empty_returns_empty_report(self):
        rep = aggregate_verdicts([])
        assert rep.total == 0
        assert rep.passed == 0
        assert rep.pass_rate == 0.0

    def test_counts_passes(self):
        vs = [self._v(True), self._v(True), self._v(False)]
        rep = aggregate_verdicts(vs)
        assert rep.total == 3
        assert rep.passed == 2
        assert rep.ex_correct == 2
        assert rep.ex_evaluable == 3

    def test_by_difficulty_groups(self):
        vs = [
            self._v(True, "easy"),
            self._v(False, "easy"),
            self._v(True, "hard"),
        ]
        rep = aggregate_verdicts(vs)
        assert rep.by_difficulty["easy"]["total"] == 2
        assert rep.by_difficulty["easy"]["passed"] == 1
        assert rep.by_difficulty["hard"]["passed"] == 1

    def test_p95_latency(self):
        # 20 queries with latency 1..20 - P95 at idx=int(0.95*20)=19 -> 20.
        vs = [self._v(latency=i) for i in range(1, 21)]
        rep = aggregate_verdicts(vs)
        assert rep.p95_latency_ms == 20

    def test_extended_fields_aggregate(self):
        vs = [
            self._v(col_f1=0.8, comp=0.7),
            self._v(col_f1=0.6, comp=0.5),
        ]
        rep = aggregate_verdicts(vs)
        assert rep.avg_schema_f1_columns == pytest.approx(0.7, abs=0.01)
        assert rep.avg_component_overall == pytest.approx(0.6, abs=0.01)

    def test_cost_per_correct_with_token_totals(self):
        vs = [self._v(True), self._v(False), self._v(True)]  # 2 correct
        rep = aggregate_verdicts(
            vs, tokens_in=1000, tokens_out=500,
            price_in_per_1k=0.001, price_out_per_1k=0.002,
        )
        # cost = (1 * 0.001) + (0.5 * 0.002) = 0.002
        # per 2 correct = 0.001
        assert rep.cost_per_correct == pytest.approx(0.001, abs=1e-6)

    def test_cost_per_correct_zero_correct_is_none(self):
        vs = [self._v(False), self._v(False)]
        rep = aggregate_verdicts(
            vs, tokens_in=1000, tokens_out=0,
            price_in_per_1k=0.001, price_out_per_1k=0.0,
        )
        assert rep.cost_per_correct is None

    def test_failures_collected(self):
        vs = [self._v(True), self._v(False)]
        rep = aggregate_verdicts(vs)
        assert len(rep.failures) == 1
