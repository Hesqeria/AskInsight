"""EQ003 regression: count phrase in question must win over glossary
supplements carrying exclusion words."""
from app.agent.nodes.semantic_grounding import match_business_term


def test_count_phrase_in_question_wins():
    # glossary appended "平均订单金额" used to inject the exclusion word
    # "平均", vetoing order_count and landing on avg_order_amount.
    question = "今天有多少笔订单"
    augmented = question + " 今天 多少 笔 订单 平均订单金额"
    assert match_business_term(question) == "order_count"
    assert match_business_term(augmented) == "avg_order_amount"  # fallback path intact


def test_avg_question_still_maps_avg():
    assert match_business_term("上个月平均订单金额是多少") == "avg_order_amount"
    assert match_business_term("客单价趋势") == "avg_order_amount"


def test_plain_count_maps_order_count():
    assert match_business_term("昨天订单数") == "order_count"
    assert match_business_term("本月订单笔数") == "order_count"
