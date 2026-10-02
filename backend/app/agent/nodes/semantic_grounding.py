"""Semantic Grounding node — converts NL → ontology-grounded SemanticPlan.

This is the deterministic alternative to LLM-only SQL generation. For
well-structured queries like "华北 上个月的gmv", we can:
  1. Find the measure template (from lineage_business / glossary_matches)
  2. Find dimension values (from matched_dimension_values)
  3. Parse time expressions (上个月 / 昨天 / 本月)
  4. Resolve joins (from a registry; falls back to ont_relation)
  5. Render SQL deterministically (no LLM call)

LLM still kicks in for:
  - Ambiguous questions (multiple measure candidates)
  - Complex predicates / window functions
  - Plans with confidence < 0.7

The node writes `state.semantic_plan`. Downstream `generate_sql` can
choose to use the plan (when valid + confidence high) or fall through
to its LLM path.
"""
from __future__ import annotations

import re
from datetime import date, timedelta as _timedelta, timedelta
from typing import Optional

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.ontology.plan import (
    Measure, DimensionFilter, DimensionGroupBy, TimeRange, JoinSpec, SemanticPlan, OrderBy,
)


# --------------------------------------------------------------------------- #
# Join Registry — pragmatic shortcut for common dim tables
# --------------------------------------------------------------------------- #
# Long-term: derive from ont_relation + ont_instance once that data is
# complete. Short-term: hardcode the common paths so grounding works.
#
# Key: (fact_class_id, dim_class_id) → ordered list of JoinSpec.
# Each entry is one hop. Multi-hop (e.g. Order→Province→Region) needs
# multiple JoinSpec entries in order.
JOIN_REGISTRY: dict[tuple[str, str], list[dict]] = {
    # Order (C030) -> County (CC, desensitized): shares the SA chain
    ("C030", "CC"): [
        {
            "left_table": "t",
            "left_column": "service_area_id",
            "right_table": "sa",
            "right_table_real": "dw.dim_service_area",
            "right_column": "id",
        },
    ],
    # Order (C030) -> Service Area (CSA): the highway "场" dimension
    ("C030", "CSA"): [
        {
            "left_table": "t",
            "left_column": "service_area_id",
            "right_table": "sa",
            "right_table_real": "dw.dim_service_area",
            "right_column": "id",
        },
    ],
    # Order (C030) -> Province (C050P): same physical chain as C050
    ("C030", "C050P"): [
        {
            "left_table": "t",
            "left_column": "province_id",
            "right_table": "p",
            "right_table_real": "dw.dim_base_province",
            "right_column": "id",
        },
    ],
    # Order (C030) -> User (C011/C012 share the user dim)
    ("C030", "C012"): [
        {
            "left_table": "t",
            "left_column": "user_id",
            "right_table": "u",
            "right_table_real": "dw.dim_user_info",
            "right_column": "id",
        },
    ],
    # Order (C030) → Region (C050) via dim_base_province
    ("C030", "C050"): [
        {
            "left_table": "t",                       # fact table alias
            "left_column": "province_id",
            "right_table": "p",
            "right_column": "id",
            "right_table_real": "dw.dim_base_province",
        },
        {
            "left_table": "p",
            "left_column": "region_id",
            "right_table": "r",
            "right_column": "id",
            "right_table_real": "dw.dim_base_region",
        },
    ],
    # Order (C030) → User (C011) — direct FK
    ("C030", "C011"): [
        {
            "left_table": "t",
            "left_column": "user_id",
            "right_table": "u",
            "right_column": "id",
            "right_table_real": "dw.dim_user_info",
        },
    ],
    # Order (C030) → Category (C021) via order_detail → sku → category3.
    # Multi-hop: t.id → d.order_id (fact pk = detail fk), d.sku_id → s.id,
    #            s.category3_id → c.id
    ("C030", "C021"): [
        {
            "left_table": "t",
            "left_column": "id",
            "right_table": "d",
            "right_column": "order_id",
            "right_table_real": "dw.dwd_order_detail_inc",
        },
        {
            "left_table": "d",
            "left_column": "sku_id",
            "right_table": "s",
            "right_column": "id",
            "right_table_real": "dw.dim_sku_info",
        },
        {
            "left_table": "s",
            "left_column": "category3_id",
            "right_table": "c",
            "right_column": "id",
            "right_table_real": "dw.dim_base_category3",
        },
    ],
    # Order detail (C031) → SKU (C022) — direct FK
    ("C031", "C022"): [
        {
            "left_table": "t",
            "left_column": "sku_id",
            "right_table": "s",
            "right_column": "id",
            "right_table_real": "dw.dim_sku_info",
        },
    ],
}


# Dimension value column resolution: when "华北" is matched against
# dimension values, what column should it filter on?
DIM_VALUE_COLUMN_REGISTRY: dict[str, dict] = {
    "C050P": {
        "table": "dw.dim_base_province",
        "column": "p.name",
        "label": "北京",
    },
    "C012": {
        "table": "dw.dim_user_info",
        "column": "u.gender",
        "label": "gender",
    },
    "CSA": {
        "table": "dw.dim_service_area",
        "column": "sa.name",
        "label": "service area",
    },
    "CC": {
        "table": "dw.dim_service_area",
        "column": "sa.county",
        "label": "county",
    },
    "C050": {  # 地域
        "column": "r.region_name",          # using join aliases from JOIN_REGISTRY
        "table": "dw.dim_base_region",
    },
    "C021": {  # 品类
        "column": "c.name",
        "table": "dw.dim_base_category3",
    },
    "C011": {  # 用户
        "column": "u.gender",
        "table": "dw.dim_user_info",
    },
}


# --------------------------------------------------------------------------- #
# Time expression parser
# --------------------------------------------------------------------------- #
# Maps Chinese time phrases → (start_date, end_date) computed against today.
# Conservative: only handles common expressions; LLM covers the rest.
_TIME_PATTERNS = [
    # Today / yesterday
    (re.compile(r"今天|今日|当日"), lambda today: (today, today)),
    (re.compile(r"昨天|昨日"), lambda today: (today - timedelta(days=1),
                                              today - timedelta(days=1))),
    # This week (assume week starts Monday)
    (re.compile(r"本周|这周|这一周"), lambda today: (
        today - timedelta(days=today.weekday()),
        today - timedelta(days=today.weekday()) + timedelta(days=6),
    )),
    # Last week
    (re.compile(r"上周|上一周"), lambda today: (
        today - timedelta(days=today.weekday() + 7),
        today - timedelta(days=today.weekday() + 1),
    )),
    # This month
    (re.compile(r"本月|这个月|当月"), lambda today: (
        today.replace(day=1),
        _last_day_of_month(today),
    )),
    # Last month
    (re.compile(r"上月|上个月|上一个月份"), lambda today: _last_month_range(today)),
    # This quarter
    (re.compile(r"本季度|本季|这个季度|当季"), lambda today: _this_quarter_range(today)),
    # Last quarter
    (re.compile(r"上季度|上个季度|上一季度|上季"), lambda today: _last_quarter_range(today)),
    # This year / last year (rare in NL2SQL but cheap to support)
    (re.compile(r"今年|本年"), lambda today: (
        today.replace(month=1, day=1),
        today.replace(month=12, day=31),
    )),
    # Recent N days: 近7天 / 近30天
    (re.compile(r"近(?P<n>\d+)天"), lambda today, n=7: (
        today - timedelta(days=int(n) - 1), today,
    )),
    # 最近 N 天 / 最近N天（含空格变体，如"最近 7 天"）
    (re.compile(r"最近\s*(?P<n>\d+)\s*天"), lambda today, n=7: (
        today - timedelta(days=int(n) - 1), today,
    )),
    (re.compile(r"近\s*(?P<n>\d+)\s*天"), lambda today, n=7: (
        today - timedelta(days=int(n) - 1), today,
    )),
]


def _last_day_of_month(d: date) -> date:
    """Last calendar day of the month containing d."""
    if d.month == 12:
        return d.replace(month=12, day=31)
    nxt = d.replace(month=d.month + 1, day=1)
    return nxt - timedelta(days=1)


def _last_month_range(today: date) -> tuple[date, date]:
    """First and last day of the previous month."""
    first_of_this_month = today.replace(day=1)
    last_of_prev = first_of_this_month - timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)
    return first_of_prev, last_of_prev


def _quarter_start(d: date) -> date:
    """First day of the quarter containing d."""
    q_start_month = ((d.month - 1) // 3) * 3 + 1
    return d.replace(month=q_start_month, day=1)


def _this_quarter_range(today: date) -> tuple[date, date]:
    """First and last day of the current quarter."""
    start = _quarter_start(today)
    # Next quarter's start minus one day = last day of this quarter.
    if start.month == 10:
        next_q_start = start.replace(month=1, day=1)
        next_q_start = date(start.year + 1, 1, 1)
    else:
        next_q_start = start.replace(month=start.month + 3, day=1)
    end = next_q_start - timedelta(days=1)
    return start, end


def _last_quarter_range(today: date) -> tuple[date, date]:
    """First and last day of the previous quarter."""
    this_start = _quarter_start(today)
    if this_start.month == 1:
        prev_start = date(this_start.year - 1, 10, 1)
    else:
        prev_start = this_start.replace(month=this_start.month - 3, day=1)
    end = this_start - timedelta(days=1)
    return prev_start, end


def parse_time_expression(text: str, today: Optional[date] = None) -> Optional[tuple[date, date]]:
    """Parse Chinese NL time expressions into (start, end) date range.

    Returns None if no pattern matches. Caller falls back to LLM.
    """
    if not text:
        return None
    today = today or date.today()
    for pat, fn in _TIME_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                # Patterns with named groups (e.g. 近7天) need groupdict.
                if m.groupdict():
                    return fn(today, **m.groupdict())
                return fn(today)
            except Exception as e:
                logger.debug(f"time pattern {pat.pattern} failed: {e}")
                continue
    return None


# --------------------------------------------------------------------------- #
# Measure candidate matching
# --------------------------------------------------------------------------- #
# Maps business_term (lineage_business.term_name) → fixed column on
# dwd_order_info_inc. The SQL template in lineage_business has known
# bugs (e.g. GMV references `final_amount` which doesn't exist), so we
# override here with verified columns.
#
# Long-term: lineage_business.sql_template should be the source of
# truth; this is a temporary correctness patch.
MEASURE_REGISTRY: dict[str, dict] = {
    "GMV": {
        "class_id": "C030",
        "column": "dw.ads_gmv_total_day.gmv",
        "aggregation": "SUM",
        "pre_filters": [],
    },
    "cashier_anomaly_count": {
        "class_id": "C030",
        "column": "dw.dwd_cashier_audit_inc.id",
        "aggregation": "COUNT",
        "pre_filters": [],
    },
    "cart_item_count": {
        "class_id": "C030",
        "column": "dw.dwd_cart_info_inc.sku_num",
        "aggregation": "SUM",
        "pre_filters": [],
    },
    "cart_user_count": {
        "class_id": "C030",
        "column": "dw.dwd_cart_info_inc.user_id",
        "aggregation": "COUNT_DISTINCT",
        "pre_filters": [],
    },
    "favor_count": {
        "class_id": "C030",
        "column": "dw.dwd_favor_info_inc.id",
        "aggregation": "COUNT",
        "pre_filters": [],
    },
    "good_rate": {
        "class_id": "C030",
        "column": "dw.dws_sku_sku_comment_day.good_comment_count",
        "aggregation": "GOOD_RATE",
        "pre_filters": [],
    },
    "DAU": {
        "class_id": "C011",
        "column": "dw.dws_user_user_login_day.user_id",
        "aggregation": "COUNT_DISTINCT",
        "pre_filters": [],
    },
    "order_count": {
        "class_id": "C030",
        "column": "dw.ads_gmv_total_day.order_count",
        "aggregation": "SUM",
        "pre_filters": [],
    },
    "payer_count": {
        "class_id": "C030",
        "column": "dw.ads_gmv_total_day.payer_count",
        "aggregation": "SUM",
        "pre_filters": [],
    },
    "avg_order_amount": {
        "class_id": "C030",
        "column": "dw.ads_gmv_total_day.avg_order_amount",
        "aggregation": "AVG",
        "pre_filters": [],
    },
    "repurchase_rate": {
        "class_id": "C010",
        "column": "dw.ads_user_repurchase.repurchase_rate",
        "aggregation": "AVG",
        "pre_filters": [],
    },
    "retention_rate": {
        "class_id": "C010",
        "column": "dw.ads_user_retention_day.retention_rate",
        "aggregation": "AVG",
        "pre_filters": [],
    },
    "activity_order_amount": {
        "class_id": "C060",
        "column": "dw.ads_activity_conversion.order_amount",
        "aggregation": "SUM",
        "pre_filters": [],
    },
    "coupon_roi": {
        "class_id": "C100",
        "column": "dw.ads_coupon_roi.roi",
        "aggregation": "AVG",
        "pre_filters": [],
    },
}

# --------------------------------------------------------------------------- #
# Measure-source adaptation (JOIN correctness patch)
# --------------------------------------------------------------------------- #
# MEASURE_REGISTRY grounds GMV/order_count/... to dw.ads_gmv_total_day -
# a PRE-AGGREGATED daily-total table with NO province/region/user/sku
# columns. When the question also slices by region/category/user, the
# JOIN_REGISTRY chains (built for fact dwd_order_info_inc) render
# invalid SQL like `dw.ads_gmv_total_day AS t ON t.province_id ...`
# (EXPLAIN fails: Unknown column 'province_id' in 't').
#
# adapt_measure_source() re-sources the measure before rendering:
#   GMV + region-only grouping -> dw.ads_region_gmv_rank (region_name
#       lives on the table; zero joins)
#   any ads_total metric + category/user grouping -> raw
#       dw.dwd_order_info_inc (+ paid pre-filter); JOIN chains valid
_ADS_TOTAL_TABLE = "dw.ads_gmv_total_day"
_REGION_GMV_TABLE = "dw.ads_region_gmv_rank"
_RAW_FACT_TABLE = "dw.dwd_order_info_inc"
_DETAIL_FACT_TABLE = "dw.dwd_order_detail_inc"
_PAID_PRE_FILTER = "order_status = '1001'"
_REGION_JOIN_TABLES = ("dim_base_province", "dim_base_region")

_RAW_FALLBACK_SPEC: dict[str, dict] = {
    "GMV": {"column": _RAW_FACT_TABLE + ".total_amount", "aggregation": "SUM"},
    "order_count": {"column": _RAW_FACT_TABLE + ".id", "aggregation": "COUNT"},
    "payer_count": {"column": _RAW_FACT_TABLE + ".user_id",
                    "aggregation": "COUNT_DISTINCT"},
    "avg_order_amount": {"column": _RAW_FACT_TABLE + ".total_amount",
                         "aggregation": "AVG"},
}


def _category_join_chain():
    """detail -> sku -> category3 JOIN chain (aliases t/s/c)."""
    from app.ontology.plan import JoinSpec
    return [
        JoinSpec(left_table="t", left_column="sku_id",
                 right_table="s", right_column="id",
                 right_table_real="dw.dim_sku_info"),
        JoinSpec(left_table="s", left_column="category3_id",
                 right_table="c", right_column="id",
                 right_table_real="dw.dim_base_category3"),
    ]


def _region_join_chain():
    """Fallback province->region JOIN chain built directly from the
    registry specs used by the fact chain (only when plan.joins empty)."""
    from app.ontology.plan import JoinSpec
    return [
        JoinSpec(left_table="t", left_column="province_id",
                 right_table="p", right_column="id",
                 right_table_real="dw.dim_base_province"),
        JoinSpec(left_table="p", left_column="region_id",
                 right_table="r", right_column="id",
                 right_table_real="dw.dim_base_region"),
    ]


def adapt_measure_source(plan) -> None:
    """Re-source plan.measures[0] when slicing dims need columns the
    pre-aggregated source table doesn't have. Mutates `plan` in place.
    Only fires for ads_gmv_total_day-sourced measures."""
    if not plan.measures:
        return
    m = plan.measures[0]
    if not m.column or not m.column.startswith(_ADS_TOTAL_TABLE + "."):
        return
    classes = ({g.class_id for g in plan.group_by}
               | {d.class_id for d in plan.dimensions})
    needs_region = "C050" in classes
    needs_deep = bool(classes & {"C021", "C011", "C012", "CSA", "CC"})  # deep dims
    if not needs_region and not needs_deep:
        return
    if needs_region and not needs_deep and m.business_term == "GMV":
        # NOTE: ads_region_gmv_rank is dead data (single dt, all-zero gmv,
        # NULL region names) - route region GMV through the real dwd chain.
        # fact must be the info table (province_id lives there); the
        # province->region JOIN chain then resolves region_name.
        m.column = _RAW_FALLBACK_SPEC["GMV"]["column"]
        m.aggregation = "SUM"
        plan.fact_table_override = _RAW_FACT_TABLE
        if _PAID_PRE_FILTER not in m.pre_filters:
            m.pre_filters.append(_PAID_PRE_FILTER)
        for g in plan.group_by:
            if g.class_id == "C050" and g.column != "p.name":
                g.column = "r.region_name"
        for d in plan.dimensions:
            if d.class_id == "C050":
                d.column = "r.region_name"
        if not plan.joins:
            plan.joins = _region_join_chain()
        plan.notes.append(
            "measure re-sourced to dwd chain (region slice; "
            "ads_region_gmv_rank has no usable data)")
        return
    spec = _RAW_FALLBACK_SPEC.get(m.business_term)
    if spec:
        m.column = spec["column"]
        m.aggregation = spec["aggregation"]
        if _PAID_PRE_FILTER not in m.pre_filters:
            m.pre_filters.append(_PAID_PRE_FILTER)
        if ("C021" in classes and "C011" not in classes
                and "C050" not in classes):
            # Category slice sources the DETAIL fact directly:
            # demo-data defect - detail.order_id never matches info.id, so
            # the info->detail chain always yields empty. detail has its
            # own dt (date) + sku_id; measure is the per-detail amount
            # (fan-out safe by construction).
            m.column = _DETAIL_FACT_TABLE + ".split_total_amount"
            plan.fact_table_override = _DETAIL_FACT_TABLE
            plan.joins = _category_join_chain()
            for g in plan.group_by:
                if g.class_id == "C021":
                    g.column = "c.name"
            for d in plan.dimensions:
                if d.class_id == "C021":
                    d.column = "c.name"
        elif "C021" in classes:
            # Category+user: user_id lives on the info fact - keep the
            # info hop and aggregate the per-detail amount on alias 'd'.
            m.column = "d.split_total_amount"
            plan.fact_table_override = _RAW_FACT_TABLE
        plan.notes.append(
            "measure re-sourced to raw chain "
            f"(deep slice by {sorted(classes)})")
    else:
        # No raw mapping (e.g. retention/repurchase ads tables) - strip
        # the invalid fact-side joins and demote confidence so the LLM
        # path rebuilds joins from real DDL.
        plan.joins = [
            j for j in plan.joins
            if j.right_table_real.split(".")[-1] not in _REGION_JOIN_TABLES
        ]
        plan.confidence = min(plan.confidence, 0.5)
        plan.notes.append("incompatible slice for ads source -> LLM path")


# Synonyms so we match user wording to canonical terms.
_BUSINESS_TERM_ALIASES: dict[str, str] = {
    "gmv": "GMV", "成交": "GMV", "成交金额": "GMV", "成交额": "GMV",
    "销售额": "GMV", "销售金额": "GMV", "销售总额": "GMV", "销售": "GMV",
    "卖了多少": "GMV", "生意": "GMV", "卖得": "GMV", "销售数据": "GMV",
    # English aliases (e.g. "Top regions by sales", "monthly revenue")
    "sales": "GMV", "revenue": "GMV", "turnover": "GMV",
    "dau": "DAU", "日活": "DAU", "活跃用户": "DAU", "日活跃": "DAU",
    "用户活跃": "DAU", "活跃情况": "DAU", "活跃": "DAU", "活跃数": "DAU",
    "订单数": "order_count", "订单量": "order_count", "下单数": "order_count",
    "orders": "order_count", "order volume": "order_count",
    "笔订单": "order_count", "订单笔数": "order_count",
    "支付用户数": "payer_count", "付款用户数": "payer_count", "支付人数": "payer_count",
    "用户总数": "payer_count", "总用户数": "payer_count", "注册用户": "payer_count",
    "用户数": "payer_count", "下单用户数": "payer_count",
    "多少用户": "payer_count", "users count": "payer_count",
    "消费金额": "GMV", "消费总额": "GMV", "累计消费": "GMV",
    "营收": "GMV", "营业额": "GMV", "流水": "GMV", "进账": "GMV",
    "收银异常数": "cashier_anomaly_count", "收银异常": "cashier_anomaly_count",
    "稽核异常数": "cashier_anomaly_count", "收银违规数": "cashier_anomaly_count",
    "加购件数": "cart_item_count", "加购数量": "cart_item_count",
    "加购商品数": "cart_item_count", "购物车件数": "cart_item_count",
    "加购人数": "cart_user_count",
    "收藏数": "favor_count", "收藏量": "favor_count",
    "收藏次数": "favor_count", "收藏个数": "favor_count",
    "好评率": "good_rate",
    "客单价": "avg_order_amount", "平均订单金额": "avg_order_amount", "平均客单价": "avg_order_amount",
    "平均金额": "avg_order_amount", "平均消费": "avg_order_amount", "平均下单金额": "avg_order_amount",
    "复购率": "repurchase_rate", "复购": "repurchase_rate", "重复购买": "repurchase_rate",
    "留存率": "retention_rate", "用户留存": "retention_rate", "次日留存": "retention_rate",
    "活动GMV": "activity_order_amount", "活动订单金额": "activity_order_amount", "活动销售额": "activity_order_amount",
    "优惠券ROI": "coupon_roi", "券ROI": "coupon_roi", "ROI": "coupon_roi",
}


# 以下词若同时出现，禁止映射到 order_count（金额/费率类问题）
# 例:"平均订单金额""订单运费""优惠券订单" 都不是订单数
_POP_WINDOW_RESOLVER = {
    "本周": lambda today: (today - _timedelta(days=today.weekday()), today),
    "上周": lambda today: (today - _timedelta(days=today.weekday() + 7),
                          today - _timedelta(days=today.weekday() + 1)),
    "本月": lambda today: (today.replace(day=1), today),
    "这个月": lambda today: (today.replace(day=1), today),
    "上月": lambda today: ((today.replace(day=1) - _timedelta(days=1))
                          .replace(day=1), today.replace(day=1) - _timedelta(days=1)),
    "上个月": lambda today: ((today.replace(day=1) - _timedelta(days=1))
                           .replace(day=1), today.replace(day=1) - _timedelta(days=1)),
}

_ORDER_COUNT_EXCLUDE_WORDS = [
    "平均", "金额", "运费", "优惠券", "满减", "占比", "比例", "费率",
    "客单价", "ROI", "折扣", "均价",
]


def _exclude_order_count(text: str, canonical: str) -> bool:
    """判断是否应排除 order_count 映射（避免宽泛"订单"误判金额类题）。"""
    if canonical != "order_count":
        return False
    lower = text.lower()
    return any(w in lower for w in _ORDER_COUNT_EXCLUDE_WORDS)


def _mine_alias_candidates(question: str) -> None:
    """EvoOntology-style self-evolution seed: when no measure matches,
    fuzzy-compare question 2-4grams against known alias keys and append
    near-misses to logs/alias_candidates.jsonl for admin promotion.
    Failures are silent (mining must never break grounding)."""
    try:
        import json as _json
        import difflib
        keys = [a for a in _BUSINESS_TERM_ALIASES
                if len(a) >= 2 and not a.isascii()]
        grams = {question[i:i + n]
                 for n in (2, 3, 4)
                 for i in range(0, max(0, len(question) - n + 1))}
        best = []
        for g in grams:
            for k in keys:
                ratio = difflib.SequenceMatcher(None, g, k).ratio()
                if ratio >= 0.75:
                    best.append((ratio, g, k,
                                 _BUSINESS_TERM_ALIASES[k]))
        best.sort(reverse=True)
        top = best[0] if best else None
        rec = {"q": question,
               "gram": top[1] if top else "",
               "like": top[2] if top else "-",
               "term": top[3] if top else "-",
               "ratio": round(top[0], 2) if top else 0.0,
               "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
        import os as _os
        path = _os.path.join("logs", "alias_candidates.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def match_business_term(text: str) -> Optional[str]:
    """Find the canonical business term in `text` (case-insensitive)."""
    terms = match_business_terms(text, limit=1)
    return terms[0] if terms else None


def match_business_terms(text: str, limit: int = 3) -> list[str]:
    """Find ALL distinct canonical terms in `text` (multi-measure questions
    like "GMV和订单数"). Activity-rule first, then longest-alias-first scan.
    """
    if not text:
        return []
    lower = text.lower()
    found: list[str] = []

    # 活动优先（"活动GMV" / "活动带来的GMV" 应映射 activity_order_amount，而非 GMV）
    if "活动" in lower and ("活动gmv" in lower or "活动销售" in lower or "活动订单金额" in lower
                            or ("活动" in lower and "gmv" in lower and "带来的" in lower)):
        return ["activity_order_amount"]

    # Longest-alias-first so "平均订单金额" wins over "订单金额"-style
    # substrings; collect distinct canonicals, skip consumed substrings.
    alias_items = sorted(_BUSINESS_TERM_ALIASES.items(),
                         key=lambda kv: -len(kv[0]))
    consumed = lower
    for alias, canonical in alias_items:
        a = alias.lower()
        if a in consumed:
            if _exclude_order_count(lower, canonical):
                continue
            if canonical not in found:
                found.append(canonical)
                consumed = consumed.replace(a, " " * len(a))
        if len(found) >= limit:
            break
    if not found:
        for term in MEASURE_REGISTRY:
            if term.lower() in lower:
                found.append(term)
                break
    return found[:limit]

    # 活动优先（"活动GMV" / "活动带来的GMV" 应映射 activity_order_amount，而非 GMV）
    # 注意: "活动优惠金额" 是优惠力度，不是活动GMV，不映射
    if "活动" in lower and ("活动gmv" in lower or "活动销售" in lower or "活动订单金额" in lower
                            or ("活动" in lower and "gmv" in lower and "带来的" in lower)):
        return "activity_order_amount"

    # Direct alias match first (more specific).
    for alias, canonical in _BUSINESS_TERM_ALIASES.items():
        if alias.lower() in lower:
            if _exclude_order_count(lower, canonical):
                continue  # 排除后继续找下一个，不直接返回 None
            return canonical
    # Direct registry match (e.g. user typed "GMV" verbatim).
    for term in MEASURE_REGISTRY:
        if term.lower() in lower:
            return term
    return None


# --------------------------------------------------------------------------- #
# Dimension value matching
# --------------------------------------------------------------------------- #
def match_dimension_class(
    matched_values: list[dict],
) -> list[tuple[str, str, str]]:
    """From matched_dimension_values (from match_dimension_value node),
    return [(class_id, column, value), ...] for each dimension found.

    Uses DIM_VALUE_COLUMN_REGISTRY to resolve which class each matched
    value belongs to (by looking at the source table).
    """
    out: list[tuple[str, str, str]] = []
    if not matched_values:
        return out
    # Build reverse map: table_name → class_id (when known)
    table_to_class = {}
    for cid, info in DIM_VALUE_COLUMN_REGISTRY.items():
        # info["table"] is like "dw.dim_base_region"
        tbl = info["table"].split(".")[-1]
        table_to_class[tbl] = cid

    for mv in matched_values:
        tbl = (mv.get("table_name") or "").split(".")[-1]
        cid = table_to_class.get(tbl)
        if not cid:
            continue
        col = DIM_VALUE_COLUMN_REGISTRY[cid]["column"]
        val = mv.get("value", "")
        if val:
            out.append((cid, col, val))
    return out


# --------------------------------------------------------------------------- #
# Main grounding function (pure, testable)
# --------------------------------------------------------------------------- #
def _detect_ranking(question: str, plan: SemanticPlan) -> Optional[dict]:
    """OPT-M7: 检测排名/Top-N/最高最低类问题。

    返回 {"order_by": [OrderBy], "limit": int|None, "needs_group_by_dt": bool, "note": str}
    或 None（非排名问题）。

    优先级：Top N（更具体）> 最高/最低 extremum。
    """
    if not plan.measures:
        return None
    m = plan.measures[0]
    if m.aggregation.upper() == "COUNT_DISTINCT":
        agg_expr = f"COUNT(DISTINCT t.{m.column.split('.')[-1]})"
    else:
        agg_expr = f"{m.aggregation}(t.{m.column.split('.')[-1]})"
    q = question.lower()

    # 1. Top N（优先，因为更具体）
    # "top10" / "前10" / "排名前5" / "10名最高" / "前十个"
    m_top = (re.search(r'top\s*(\d+)', q)
             or re.search(r'前\s*(\d+)', q)
             or re.search(r'排名前\s*(\d+)', q)
             or re.search(r'(\d+)\s*(?:名|个)最高', q))
    if m_top:
        n = int(m_top.group(1))
        n = max(1, min(n, 100))
        return {
            "order_by": [OrderBy(column=agg_expr, direction="DESC")],
            "limit": n,
            "needs_group_by_dt": False,
            "note": f"top-{n} by {agg_expr}",
        }

    # 1b. English ranking: "top regions" / "regions ranked" (no number)
    if re.search(r'\b(top|ranked|ranking|rank)\b', q):
        return {
            "order_by": [OrderBy(column=agg_expr, direction="DESC")],
            "limit": None,
            "needs_group_by_dt": False,
            "note": "english ranking detected -> ORDER BY desc",
        }

    # 2. 最高/最低 extremum
    # 排行/榜单/排名 = full ranking (ORDER BY DESC, no limit), same as
    # English "ranked"; plain extremum words keep limit=1 semantics.
    if any(w in q for w in ("排行", "榜单", "排名")):
        return {
            "order_by": [OrderBy(column=agg_expr, direction="DESC")],
            "limit": None,
            "needs_group_by_dt": False,
            "note": "ranking detected -> ORDER BY desc",
        }

    m_cnt = re.search(r'(\d+)(\s*)?(?:个|名|位)', q)
    desc_words = ["最高", "最大", "最多", "最好", "峰值"]
    asc_words = ["最低", "最小", "最少", "最差", "谷值"]
    direction = None
    for w in desc_words:
        if w in q:
            direction = "DESC"
            if m_cnt:
                n = max(1, min(int(m_cnt.group(1)), 100))
                return {
                    "order_by": [OrderBy(column=agg_expr, direction="DESC")],
                    "limit": n,
                    "needs_group_by_dt": False,
                    "note": f"top-{n} by {agg_expr} (extremum+count)",
                }
            break
    if direction is None:
        for w in asc_words:
            if w in q:
                direction = "ASC"
                if m_cnt:
                    n = max(1, min(int(m_cnt.group(1)), 100))
                    return {
                        "order_by": [OrderBy(column=agg_expr, direction="ASC")],
                        "limit": n,
                        "needs_group_by_dt": False,
                        "note": f"bottom-{n} by {agg_expr} (extremum+count)",
                    }
                break
    if direction:
        has_time_dim = any(t in q for t in ["天", "日", "月", "周", "年"])
        return {
            "order_by": [OrderBy(column=agg_expr, direction=direction)],
            "limit": 1,
            "needs_group_by_dt": has_time_dim,
            "note": f"extremum ({direction}) by {agg_expr}",
        }

    return None


def _detect_period_over_period(question: str, plan: SemanticPlan) -> Optional[dict]:
    """OPT-M7: 检测环比/同比类问题（"本周相比上周"/"环比增长"）。

    返回 {"template": "pop", "note": str}，build_plan 在 confidence<0.7 时
    会标记为不适合规则渲染，交给 LLM 处理（带提示）。
    本期仅识别并标记，不直接渲染（窗口函数复杂度高，留给 LLM）。
    """
    q = question.lower()
    pop_words = ["环比", "同比", "相比上周", "相比上月", "相比去年同期", "增长多少",
                 "增长率", "比上周", "比上月", "比去年"]
    if any(w in q for w in pop_words):
        return {"template": "pop", "note": "period-over-period detected"}
    # "A和B对比/比较" where A,B are different periods -> pop too
    m2 = re.search(
        r"(上周|本周|上月|本月|上个月|这个月|今年|去年)\s*[和与跟]\s*"
        r"(上周|本周|上月|本月|上个月|这个月|今年|去年)", q)
    if m2 and m2.group(1) != m2.group(2):
        return {"template": "pop",
                "note": f"dual-period compare {m2.group(1)} vs {m2.group(2)}",
                "labels": (m2.group(1), m2.group(2))}
    if re.search(r"(上周|本周|上月|本月|上个月|这个月|今年|去年)[^.]{0,10}(对比|比较)", q)             and not re.search(r"(区域|品类|省份|地区|category|region|user)", q):
        return {"template": "pop", "note": "period compare detected"}
    return None


def build_plan(
    question: str,
    *,
    keywords: Optional[list[str]] = None,
    matched_dimension_values: Optional[list[dict]] = None,
    glossary_matches: Optional[list[dict]] = None,
    today: Optional[date] = None,
) -> SemanticPlan:
    """Build a SemanticPlan from question + grounding signals.

    This is the rule-based grounding path. Returns a plan with
    `confidence` reflecting how complete the grounding was:
      - 1.0: measure + time + all dims resolved
      - 0.7: measure resolved but join/dim partial
      - 0.3: measure not found (LLM should take over)
    """
    plan = SemanticPlan(question=question)
    notes: list[str] = []

    # 1. Measure
    # Match the raw question FIRST: glossary/keyword supplements can carry
    # exclusion words (e.g. term "平均订单金额" adds "平均") that would veto
    # an explicit count phrase present in the question itself ("笔订单").
    terms = match_business_terms(question)
    if not terms:
        text_for_match = question + " " + " ".join(keywords or [])
        if glossary_matches:
            for gm in glossary_matches:
                text_for_match += " " + (gm.get("term") or "")
        terms = match_business_terms(text_for_match)
    if terms:
        for term in terms:
            spec = MEASURE_REGISTRY[term]
            plan.measures.append(Measure(
                business_term=term,
                class_id=spec["class_id"],
                column=spec["column"],
                aggregation=spec["aggregation"],
                pre_filters=list(spec.get("pre_filters", [])),
            ))
            # multi-measure must stay on one fact table; stop on divergence
            if len(plan.measures) > 1 and (
                    plan.measures[0].column.split(".")[1]
                    != spec["column"].split(".")[1]):
                plan.measures.pop()
                plan.notes.append(
                    f"measure '{term}' on different fact table -> dropped")
                break
    else:
        plan.notes.append("no measure matched in registry")
        plan.confidence = 0.3
        _mine_alias_candidates(question)
        return plan  # nothing more we can do

    # 2. Time range
    time_range = parse_time_expression(question, today=today)
    if time_range:
        start, end = time_range
        # TODO: use the fact table's actual dt column. dwd_order_info_inc
        # uses dt; DWS tables also use dt. Hardcoded for now.
        plan.time = TimeRange(
            column="t.dt",
            start=start.isoformat(),
            end=end.isoformat(),
            grain="day",
        )
    else:
        notes.append("no time expression found")

    # 2.5 Trend detection → GROUP BY dt
    # 趋势类问题（"每天/每日/每周/每月 X 趋势/分布"）必须按时间分组，
    # 否则渲染出的 SQL 只有聚合值没有时间维，趋势题会缺 GROUP BY。
    _TREND_PATTERNS = [
        "趋势", "走势", "每天", "每日", "每周", "每月", "各天",
        "逐日", "逐天", "逐月", "daily", "trend", "by day",
    ]
    _ql = question.lower()
    # bare "分布" only implies a time distribution when it is NOT a
    # dimension-distribution phrase ("按性别分布"/"区域分布"/"用户分布")
    _dim_dist = re.search("(用户|区域|省份|品类|性别|男女|品牌|门店)分布"
                          "|(按|各|分)[一-龥]{1,6}分布", question)
    if any(p in _ql for p in _TREND_PATTERNS) or ("分布" in _ql and not _dim_dist):
        # 事实表 dt 列（measisre 的 fact_table 上的 dt）
        fact_short = plan.fact_table_short if hasattr(plan, "fact_table_short") else ""
        dt_col = f"t.dt"
        # 确保 plan.group_by 不含重复
        if not any(getattr(g, "column", "") == dt_col for g in plan.group_by):
            plan.group_by.append(DimensionGroupBy(class_id="C080", column=dt_col))
        notes.append("trend detected → GROUP BY dt")

    # 2.6 Slice-pattern group_by: "各区域/按品类"-style questions imply a
    # grouping WITHOUT any concrete dimension value. Without this, the plan
    # kept the pre-agg ads fact and the LLM invented province_id joins on
    # tables that lack the column (EXPLAIN: Unknown column 'province_id').
    _SLICE_PATTERNS = {
        "C050": ["各区域", "按区域", "分区域", "每个区域", "各地区", "按地区",
                 "区域分布", "区域排行", "by region", "per region",
                 "region", "regions"],
        "C050P": ["省份", "各省", "每省", "按省", "province", "by province"],
        "C021": ["各品类", "按品类", "分品类", "每个品类", "品类分布",
                 "品类排行", "by category", "per category",
                 "category", "categories"],
        "CC": ["各县市", "按县市", "每个县市", "县市分布", "县市排行",
                "县市排名", "个县市", "各县市区", "各县域",
                "by county", "per county"],
        "CSA": ["各服务区", "按服务区", "每个服务区", "服务区分布",
                 "服务区排行", "服务区排名", "个服务区", "各服务区经营",
                 "服务区", "by service area", "per service area"],
        "C012": ["性别", "按性别", "分性别", "男女比例", "男女分布",
                 "by gender", "per gender"],
        "C011": ["各用户", "按用户", "用户分布", "分用户",
                 "user", "users", "member", "members",
                 "客户", "各客户", "个客户", "按客户", "买家", "会员",
                 "customer", "customers"],
    }
    if plan.measures:
        m_class = plan.measures[0].class_id
        for cid, words in _SLICE_PATTERNS.items():
            if not any(w in question.lower() for w in words):
                continue
            if cid == "C050P":
                # province granularity: group on the province dim itself
                if not any(g.class_id == "C050" for g in plan.group_by):
                    plan.group_by.append(DimensionGroupBy(
                        class_id="C050", column="p.name"))
                    notes.append("slice pattern -> GROUP BY province (p.name)")
                col_info = DIM_VALUE_COLUMN_REGISTRY.get("C050")
            else:
                col_info = DIM_VALUE_COLUMN_REGISTRY.get(cid)
            if col_info is None:
                continue
            if cid != "C050P" and not any(g.class_id == cid for g in plan.group_by):
                # user slice groups on the display name (customers), not
                # the gender value used for value filtering
                gcol = ("u.nick_name" if cid == "C011"
                        else col_info["column"])
                plan.group_by.append(DimensionGroupBy(
                    class_id=cid, column=gcol))
                if cid == "C011":
                    # same-name users must stay separate entities
                    plan.group_by.append(DimensionGroupBy(
                        class_id="C011K", column="u.id", select=False))
                notes.append(f"slice pattern -> GROUP BY {cid}")
            key = (m_class, cid)
            if key in JOIN_REGISTRY and not _has_join_for(plan, cid):
                for j in JOIN_REGISTRY[key]:
                    plan.joins.append(JoinSpec(**j))

    # 3. Dimensions
    dim_matches = match_dimension_class(matched_dimension_values or [])
    for cid, col, val in dim_matches:
        excl = re.search(
            r"(除|除了|不包括|不含|去掉|排除)\s*了?\s*" + re.escape(val),
            question)
        plan.dimensions.append(DimensionFilter(
            class_id=cid, column=col,
            operator="!=" if excl else "=", value=val,
        ))
        if excl:
            notes.append(f"exclusion '{val}' -> negated filter")
        # Resolve joins for this dim class
        key = (plan.measures[0].class_id, cid)
        if key in JOIN_REGISTRY and not _has_join_for(plan, cid):
            for j in JOIN_REGISTRY[key]:
                plan.joins.append(JoinSpec(**j))

    if not plan.dimensions:
        notes.append("no dimension values matched")

    # 3.4b Numeric threshold -> HAVING ("GMV" + chr(0x8D85) ...)
    _HAVING_WORDS = ["超过", "大于", "高于", "多于", "至少", "不少于", "不低于"]
    if plan.group_by and plan.measures:
        m_h = None
        for w in _HAVING_WORDS:
            idx = question.find(w)
            if idx >= 0:
                rest = question[idx + len(w):]
                m_h = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(万|千|亿)?", rest)
                if m_h:
                    val = float(m_h.group(1))
                    unit = m_h.group(2) or ""
                    if unit == '万':
                        val *= 10000
                    elif unit == '千':
                        val *= 1000
                    elif unit == '亿':
                        val *= 100000000
                    m0 = plan.measures[0]
                    col = m0.column.split(".")[-1]
                    if m0.aggregation.upper() == "COUNT_DISTINCT":
                        expr0 = f"COUNT(DISTINCT t.{col})"
                    elif m0.aggregation.upper() == "GOOD_RATE":
                        expr0 = f"SUM(t.{col})"
                    else:
                        expr0 = f"{m0.aggregation}(t.{col})"
                    strict = w in ["超过", "大于", "高于", "多于"]
                    op = ">" if strict else ">="
                    plan.having.append(f"{expr0} {op} {val:g}")
                    notes.append(f"threshold -> HAVING {expr0} {op} {val:g}")
                    break

    # 3.5 Ranking / Top-N detection (OPT-M7)
    # 识别"最高/最低/最大/最小/Top N/前 N/第几" → ORDER BY + LIMIT
    rank_info = _detect_ranking(question, plan)
    if rank_info:
        plan.order_by.extend(rank_info["order_by"])
        if rank_info.get("limit"):
            plan.limit = rank_info["limit"]
        # 排名类问题通常按日聚合后排序，需要 GROUP BY dt（除非已有）
        if rank_info.get("needs_group_by_dt") and not any(
            getattr(g, "column", "") == "t.dt" for g in plan.group_by
        ):
            plan.group_by.append(DimensionGroupBy(class_id="C080", column="t.dt"))
        notes.append(f"ranking detected: {rank_info['note']}")

    # 3.6 Period-over-period detection (OPT-M7)
    # 环比/同比问题不交给规则渲染（窗口函数复杂），强制 confidence<0.7
    # 让 generate_sql 走 LLM 路径，但 plan.notes 会提示 LLM 用 LAG() 窗口。
    # 3.6 Topic-noun guard: a domain topic (coupon/refund/favor/...)
    # with only a generic metric matched means the real concept was NOT
    # grounded - fabricating a GMV/payer answer would be silently wrong.
    # Drop to no-measure so ask_clarification asks instead.
    _TOPIC_NOUNS = {
        "优惠券": "coupon_roi", "券": "coupon_roi",
        "退货": "refund", "退款": "refund", "收藏": "favor",
        "加购": "cart", "购物车": "cart", "登录": "DAU",
    }
    if plan.measures:
        _generic = {"GMV", "order_count", "payer_count", "avg_order_amount"}
        for noun, related in _TOPIC_NOUNS.items():
            if noun in question and plan.measures[0].business_term in _generic                     and plan.measures[0].business_term != related:
                plan.measures = []
                plan.confidence = 0.2
                notes.append(f"topic '{noun}' not grounded to a measure -> clarify")
                break

    pop_info = _detect_period_over_period(question, plan)
    if pop_info:
        plan.pop = True
        labels = pop_info.get("labels")
        simple = (labels and not plan.dimensions and not plan.group_by
                  and not plan.joins
                  and all(l in _POP_WINDOW_RESOLVER for l in labels))
        if simple:
            try:
                plan.pop_windows = [
                    (l,) + _POP_WINDOW_RESOLVER[l](today) for l in labels
                ]
                plan.confidence = 1.0  # deterministically renderable
                notes.append("dual-period compare -> rule UNION render")
            except Exception:
                plan.pop_windows = []
        if not plan.pop_windows:
            notes.append(pop_info["note"] + " → LLM 路径（窗口函数）")

    # 3.65 Default time window: a measure question without any time hint
    # used to trigger a clarify round just for the date range. Default to
    # the last 30 days (standard BI behavior; CLARIFY_WHEN_NO_TIME=1
    # restores the asking behavior).
    import os as _os
    if (plan.measures and plan.time is None and not pop_info
            and _os.getenv("CLARIFY_WHEN_NO_TIME", "") != "1"):
        _end = today or date.today()
        from app.ontology.plan import TimeRange as _TR
        plan.time = _TR(column="t.dt",
                        start=(_end - _timedelta(days=29)).isoformat(),
                        end=_end.isoformat(), grain="day")
        notes.append("no time specified -> default last 30 days")

    # 3.7 Source adaptation: re-source ads_total measures when region/
    # category/user slicing needs columns the pre-agg table lacks
    # (fixes EXPLAIN 'Unknown column province_id in t' JOIN failures).
    try:
        logger.info(f"adapt pre: measure={plan.measures[0].column if plan.measures else None} "
                    f"classes={sorted({g.class_id for g in plan.group_by} | {d.class_id for d in plan.dimensions})}")
        adapt_measure_source(plan)
        logger.info(f"adapt post: measure={plan.measures[0].column if plan.measures else None}")
    except Exception as _e:
        logger.warning(f"adapt_measure_source raised: {_e}")

    # 3.8 Re-point ORDER BY at the FINAL measure column: ranking/extremum
    # detection ran before adapt_measure_source, so the aggregate could
    # reference the pre-adapt column (e.g. SUM(t.gmv) on a table that was
    # re-sourced to total_amount) -> unknown-column error at execution.
    if plan.order_by and plan.measures:
        m = plan.measures[0]
        col = m.column.split(".")[-1]
        if m.aggregation.upper() == "COUNT_DISTINCT":
            new_expr = f"COUNT(DISTINCT t.{col})"
        else:
            new_expr = f"{m.aggregation}(t.{col})"
        for ob in plan.order_by:
            ob.column = new_expr

    # 4. Confidence scoring
    if pop_info:
        if plan.pop_windows:
            plan.confidence = 1.0  # deterministic UNION render, no LLM
        else:
            # 环比强制走 LLM，但保留 plan（LLM 可参考 measure/time）
            plan.confidence = 0.5
    elif plan.time and plan.measures and plan.dimensions:
        plan.confidence = 1.0
    elif plan.measures:
        plan.confidence = 0.7
    plan.notes = notes
    plan.grounding_source = "rule"
    return plan


def _has_join_for(plan: SemanticPlan, dim_class_id: str) -> bool:
    """Quick check if plan already has joins covering this dim class.
    Avoids duplicate JOIN clauses when multiple dims share a path."""
    # Simple check: are there any joins targeting a table in DIM_VALUE_COLUMN_REGISTRY for this class?
    expected_table = DIM_VALUE_COLUMN_REGISTRY.get(dim_class_id, {}).get("table", "")
    if not expected_table:
        return False
    return any(expected_table.split(".")[-1] in j.right_table_real for j in plan.joins)


# --------------------------------------------------------------------------- #
# LangGraph node wrapper
# --------------------------------------------------------------------------- #
async def semantic_grounding(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """LangGraph node: produces state.semantic_plan via rule-based grounding.

    Sets state.semantic_plan when grounding succeeds (confidence ≥ 0.7).
    Lower-confidence plans are still attached (for inspection) but
    downstream generate_sql will use the LLM path instead.
    """
    writer = runtime.stream_writer
    writer({"stage": "Semantic Grounding"})
    try:
        # 从 state.date_info 读取"今天"（add_extra_context 注入；评测时被 mock 为冻结日）
        today = None
        date_info = state.get("date_info") or {}
        date_str = date_info.get("date") if isinstance(date_info, dict) else None
        if date_str:
            try:
                today = date.fromisoformat(str(date_str))
            except Exception:
                today = None
        if today is None:
            today = date.today()

        plan = build_plan(
            question=state.get("query", ""),
            keywords=state.get("keywords", []),
            matched_dimension_values=state.get("matched_dimension_values", []),
            glossary_matches=state.get("glossary_matches", []),
            today=today,
        )
        import os as _os
        if _os.getenv("ABLATION_FORCE_LLM") == "1":
            # research ablation: disable rule-lane rendering so every
            # question goes through the LLM path
            plan.confidence = min(plan.confidence, 0.3)
            plan.notes.append("ablation: rule lane disabled")
        logger.info(
            f"SemanticPlan: source={plan.grounding_source} "
            f"confidence={plan.confidence} measures={len(plan.measures)} "
            f"dims={len(plan.dimensions)} joins={len(plan.joins)} "
            f"time={'Y' if plan.time else 'N'} today={today}"
        )
        from app.agent.events import emit
        emit("plan/built", {
            "confidence": plan.confidence,
            "grounding_source": plan.grounding_source,
            "measures": len(plan.measures), "dimensions": len(plan.dimensions),
        })
        return {"semantic_plan": plan.to_dict()}
    except Exception as e:
        logger.error(f"semantic_grounding error: {e}")
        # Don't block the pipeline — generate_sql will use LLM path.
        return {"semantic_plan": None}
