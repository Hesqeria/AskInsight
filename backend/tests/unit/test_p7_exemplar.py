"""Exemplar store pure-function tests."""
from app.services.exemplar_store import (
    _norm, cosine, exemplar_id, format_for_prompt, _SOURCE_CORRECTION,
)


def test_norm_strips_case_and_whitespace():
    assert _norm("  GMV 是 多少 ") == "gmv是多少"


def test_exemplar_id_idempotent_on_normalized_question():
    assert exemplar_id("GMV 是多少") == exemplar_id("  gmv 是 多少 ")
    assert exemplar_id("订单数") != exemplar_id("GMV")


def test_cosine_basics():
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([], []) == 0.0


def test_format_for_prompt_blocks():
    out = format_for_prompt([
        {"question": "昨天GMV", "sql_text": "SELECT 1"},
        {"question": "上周GMV", "sql_text": "SELECT 2"},
    ])
    assert out.count("Q: ") == 2 and "SELECT 2" in out
    assert format_for_prompt([]) == "(none)"


def test_source_constants():
    assert _SOURCE_CORRECTION == "user_correction"
