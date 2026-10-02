"""Human-readable explanation layer for SemanticPlan.

Converts ontology symbols (C030 / total_amount / SUM / joins) into
business-user-friendly Chinese. Two output formats:

  1. `to_business_summary(plan)` — one-sentence plain-language summary
     "查询【订单】表【总成交金额(GMV)】的【总和】,筛选【地域】=华北,时间范围 2026-07-01 至 2026-07-31"

  2. `to_explain_blocks(plan)` — structured blocks for UI display
     - 指标: ...
     - 筛选条件: ...
     - 时间范围: ...
     - 数据来源: ...
     - 关联路径: 订单 → 省份 → 地域

Both functions are pure (no I/O). The class-name lookup uses a
registry that mirrors ont_class.class_name_zh. For production use,
inject the live mapping via the `class_names` parameter so the
explainer stays in sync as ontology data grows.
"""
from __future__ import annotations

from typing import Iterable, Optional

from app.ontology.plan import (
    SemanticPlan, Measure, DimensionFilter, DimensionGroupBy,
    TimeRange, JoinSpec,
)


# --------------------------------------------------------------------------- #
# Chinese-name registries
# --------------------------------------------------------------------------- #
# Mirror of ont_class.class_name_zh. Kept here so pure functions don't
# need DB access. Refresh by calling `refresh_class_name_cache(session)`.
CLASS_NAME_ZH: dict[str, str] = {
    "C000": "业务实体",
    "C010": "客户",
    "C011": "注册用户",
    "C012": "会员等级",
    "C020": "商品",
    "C021": "品类",
    "C022": "SKU单品",
    "C030": "订单",
    "C031": "订单明细",
    "C040": "支付",
    "C050": "地域",
    "C060": "活动",
    "C070": "评价",
    "C080": "时间",
    "C090": "物流",
    "C100": "优惠券",
}

# Business term human-friendly names + descriptions.
# business_term → (短名, 完整描述)
BUSINESS_TERM_ZH: dict[str, tuple[str, str]] = {
    "GMV":          ("总成交金额", "已支付订单的金额总和(不含退款)"),
    "DAU":          ("日活跃用户", "当日有访问/操作的去重用户数"),
    "order_count":  ("订单数",     "订单总数量"),
}

# Aggregation operators → Chinese
AGGREGATION_ZH: dict[str, str] = {
    "SUM":            "总和",
    "COUNT":          "数量",
    "COUNT_DISTINCT": "去重数量",
    "AVG":            "平均值",
    "MAX":            "最大值",
    "MIN":            "最小值",
}

# Filter operators → Chinese (for natural-language description)
OPERATOR_ZH: dict[str, str] = {
    "=":          "等于",
    "!=":         "不等于",
    ">":          "大于",
    "<":          "小于",
    ">=":         "大于等于",
    "<=":         "小于等于",
    "IN":         "属于",
    "NOT IN":     "不属于",
    "LIKE":       "包含",
    "IS NULL":    "为空",
    "IS NOT NULL": "不为空",
}

# Pre-filter clauses (raw SQL fragments) → Chinese description.
# Each entry maps a SQL fragment to a human phrase.
PRE_FILTER_ZH: dict[str, str] = {
    "order_status = 'PAID'":      "已支付(不含退款/未支付)",
    "order_status = 'REFUND'":    "已退款",
    "order_status != 'CANCEL'":   "未取消",
    "payment_status = 'SUCCESS'": "支付成功",
}

# Common pre-filter substrings for fuzzy matching when the exact
# fragment isn't in PRE_FILTER_ZH (handles minor formatting drift).
_PRE_FILTER_FUZZY: list[tuple[str, str]] = [
    ("order_status", "PAID",     "已支付"),
    ("order_status", "CANCEL",   "已取消"),
    ("order_status", "REFUND",   "已退款"),
    ("payment_status", "SUCCESS", "支付成功"),
    ("refund_amount", "0",        "无退款"),
]

# Table name → Chinese (for "数据来源" explanation)
TABLE_NAME_ZH: dict[str, str] = {
    "dwd_order_info_inc":       "订单事实表",
    "dwd_order_detail_inc":     "订单明细事实表",
    "dwd_order_refund_info_inc": "订单退款事实表",
    "dwd_payment_info_inc":     "支付事实表",
    "dwd_user_info_inc":        "用户事实表",
    "dws_user_active_day":      "用户日活跃汇总表",
    "dws_region_region_order_day": "地域订单日汇总表",
    "dws_sales_wide":           "销售宽表",
    "dim_base_region":          "地域维度表",
    "dim_base_province":        "省份维度表",
    "dim_user_info":            "用户维度表",
    "dim_sku_info":             "商品维度表",
    "dim_base_category1":       "一级品类维度表",
    "dim_base_category2":       "二级品类维度表",
    "dim_base_category3":       "三级品类维度表",
    "dim_date":                 "日期维度表",
}


# --------------------------------------------------------------------------- #
# Cache refresh (call once at startup, or per-request if ontology changes)
# --------------------------------------------------------------------------- #
async def refresh_class_name_cache(session) -> None:
    """Load class_id → class_name_zh from ont_class into the registry.

    Call this at app startup (or in graph lifespan) so the explainer
    picks up new ontology entries without code changes.
    """
    global CLASS_NAME_ZH
    try:
        from sqlalchemy import text
        rows = await session.execute(text(
            "SELECT class_id, class_name_zh FROM data_agent.ont_class"
        ))
        for r in rows.fetchall():
            CLASS_NAME_ZH[r[0]] = r[1]
    except Exception as e:
        # Don't fail startup if ont_class isn't ready yet.
        import logging
        logging.getLogger(__name__).warning(f"class name cache refresh failed: {e}")


# --------------------------------------------------------------------------- #
# Lookup helpers
# --------------------------------------------------------------------------- #
def class_zh(class_id: str) -> str:
    """Return the Chinese name for a class_id, with graceful fallback."""
    return CLASS_NAME_ZH.get(class_id, class_id or "?")


def table_zh(table_short: str) -> str:
    """Return the Chinese description for a table short name.

    Strips 'dw.' prefix and any backticks for lookup.
    """
    if not table_short:
        return "?"
    short = table_short.split(".")[-1].strip("`")
    return TABLE_NAME_ZH.get(short, short)


def business_term_zh(term: str) -> tuple[str, str]:
    """Return (short_name, description) for a business term."""
    return BUSINESS_TERM_ZH.get(term, (term, term))


def aggregation_zh(agg: str) -> str:
    return AGGREGATION_ZH.get(agg.upper(), agg or "?")


def operator_zh(op: str) -> str:
    return OPERATOR_ZH.get(op.upper(), op or "=")


def pre_filter_zh(fragment: str) -> str:
    """Translate a raw SQL pre-filter into Chinese."""
    if not fragment:
        return ""
    # Exact match first.
    if fragment in PRE_FILTER_ZH:
        return PRE_FILTER_ZH[fragment]
    # Fuzzy fallback: check substring patterns.
    f_lower = fragment.lower().replace(" ", "")
    for col, val, zh in _PRE_FILTER_FUZZY:
        if col.lower() in f_lower and val.lower() in f_lower:
            return zh
    # Unknown: return the raw SQL (still readable enough for power users).
    return f"原始条件: {fragment}"


def column_zh(column: str, fact_table_short: str = "") -> str:
    """Translate a column reference like 'dw.dwd_order_info_inc.total_amount'
    or 'r.region_name' into a friendly Chinese form.

    Heuristic: take the column part (last segment) and translate common
    patterns. Returns the raw name if no translation is known.
    """
    if not column:
        return "?"
    # Get the column part (after last '.')
    col_part = column.split(".")[-1].strip("`")
    # Common column translations.
    _COLUMN_ZH = {
        "total_amount":     "订单金额",
        "final_amount":     "成交金额",
        "original_total_amount": "原价金额",
        "activity_reduce_amount": "活动优惠",
        "coupon_reduce_amount":   "优惠券抵扣",
        "feight_fee":       "运费",
        "id":               "ID",
        "user_id":          "用户ID",
        "order_id":         "订单号",
        "sku_id":           "SKU编号",
        "order_status":     "订单状态",
        "payment_status":   "支付状态",
        "region_name":      "地域名称",
        "region_id":        "地域ID",
        "province_id":      "省份ID",
        "name":             "名称",
        "gender":           "性别",
        "age":              "年龄",
        "category3_name":   "三级品类",
        "category2_name":   "二级品类",
        "category1_name":   "一级品类",
        "dt":               "业务日期",
        "create_time":      "创建时间",
    }
    return _COLUMN_ZH.get(col_part, col_part)


# --------------------------------------------------------------------------- #
# Time range → Chinese phrase
# --------------------------------------------------------------------------- #
def time_range_zh(time_range: Optional[TimeRange]) -> str:
    """Render a TimeRange as a Chinese phrase."""
    if not time_range:
        return "未指定时间范围"
    start = time_range.start
    end = time_range.end
    if start == end:
        return f"日期 {start}"
    return f"{start} 至 {end}"


# --------------------------------------------------------------------------- #
# Join path → Chinese chain
# --------------------------------------------------------------------------- #
def join_chain_zh(joins: list[JoinSpec], fact_table: str = "") -> str:
    """Render joins as a Chinese chain like '订单 → 省份 → 地域'."""
    if not joins:
        return ""
    parts: list[str] = []
    if fact_table:
        parts.append(table_zh(fact_table))
    seen_aliases = {j.right_table: False for j in joins}
    for j in joins:
        zh = table_zh(j.right_table_real)
        # Use just the core name (drop "表" suffix to keep chain compact).
        if zh.endswith("表"):
            zh = zh[:-1]
        parts.append(zh)
    return " → ".join(parts)


# --------------------------------------------------------------------------- #
# Public: business summary (one-sentence)
# --------------------------------------------------------------------------- #
def to_business_summary(plan: SemanticPlan) -> str:
    """One-sentence plain-language summary.

    Example:
      "查询【订单】的【总成交金额(GMV) 总和】(已支付),筛选【地域名称】等于华北,时间范围 2026-07-01 至 2026-07-31"
    """
    if not plan.measures:
        return f"无法理解问题「{plan.question}」,请补充更明确的指标或字段"

    # Measure clause
    m = plan.measures[0]
    short, desc = business_term_zh(m.business_term)
    agg = aggregation_zh(m.aggregation)
    measure_phrase = f"【{class_zh(m.class_id)}】的【{short}({m.business_term}) {agg}】"
    if m.pre_filters:
        filter_phrases = [pre_filter_zh(f) for f in m.pre_filters]
        measure_phrase += "(" + "、".join(filter_phrases) + ")"

    # Dimension clause
    dim_phrase = ""
    if plan.dimensions:
        parts = []
        for d in plan.dimensions:
            col = column_zh(d.column)
            op = operator_zh(d.operator)
            val = _format_value(d.value)
            parts.append(f"【{class_zh(d.class_id)}.{col}】{op}{val}")
        dim_phrase = ",筛选" + "、".join(parts)

    # Group-by clause
    gb_phrase = ""
    if plan.group_by:
        parts = [f"【{class_zh(g.class_id)}.{column_zh(g.column)}】"
                 for g in plan.group_by]
        gb_phrase = ",按" + "、".join(parts) + "分组"

    # Time clause
    time_phrase = ""
    if plan.time:
        time_phrase = f",时间范围 {time_range_zh(plan.time)}"

    return f"查询{measure_phrase}{dim_phrase}{gb_phrase}{time_phrase}"


def _format_value(value) -> str:
    """Format a Python value as a Chinese-readable literal."""
    if value is None:
        return "空"
    if isinstance(value, (list, tuple)):
        return "(" + "、".join(_format_value(v) for v in value) + ")"
    return str(value)


# --------------------------------------------------------------------------- #
# Public: structured explain blocks (for UI panels)
# --------------------------------------------------------------------------- #
def to_explain_blocks(plan: SemanticPlan) -> dict:
    """Structured explanation for UI display.

    Returns a dict with these keys:
      - question: original NL question
      - confidence: grounding confidence (0-1)
      - source: "rule" | "llm" | "hybrid"
      - 指标: list of measure descriptions
      - 筛选条件: list of filter descriptions
      - 分组: list of group-by descriptions
      - 时间范围: human-readable phrase
      - 数据来源: fact table Chinese name
      - 关联路径: join chain
      - notes: any grounding warnings
      - sql_preview: rendered SQL (optional, populated by caller)
    """
    blocks: dict = {
        "question": plan.question,
        "confidence": plan.confidence,
        "source": plan.grounding_source,
        "指标": [],
        "筛选条件": [],
        "分组": [],
        "时间范围": "",
        "数据来源": "",
        "关联路径": "",
        "notes": list(plan.notes),
    }

    # 指标
    for m in plan.measures:
        short, desc = business_term_zh(m.business_term)
        agg = aggregation_zh(m.aggregation)
        col_zh_name = column_zh(m.column)
        entry = {
            "业务术语": m.business_term,
            "中文名": short,
            "说明": desc,
            "聚合方式": agg,
            "字段": m.column,
            "字段中文名": col_zh_name,
            "来源本体类": class_zh(m.class_id),
        }
        if m.pre_filters:
            entry["业务过滤"] = [pre_filter_zh(f) for f in m.pre_filters]
        blocks["指标"].append(entry)

    # 筛选条件
    for d in plan.dimensions:
        blocks["筛选条件"].append({
            "本体类": class_zh(d.class_id),
            "字段": d.column,
            "字段中文名": column_zh(d.column),
            "运算符": operator_zh(d.operator),
            "值": _format_value(d.value),
        })

    # 分组
    for g in plan.group_by:
        blocks["分组"].append({
            "本体类": class_zh(g.class_id),
            "字段": g.column,
            "字段中文名": column_zh(g.column),
        })

    # 时间范围
    if plan.time:
        blocks["时间范围"] = time_range_zh(plan.time)
        blocks["时间字段"] = plan.time.column

    # 数据来源 + 关联路径
    if plan.measures:
        fact_table = plan.fact_table
        blocks["数据来源"] = table_zh(fact_table)
        blocks["关联路径"] = join_chain_zh(plan.joins, fact_table)

    return blocks


# --------------------------------------------------------------------------- #
# Public: Markdown render (for chat/Slack-style output)
# --------------------------------------------------------------------------- #
def to_markdown(plan: SemanticPlan) -> str:
    """Render plan as Markdown for chat-style display."""
    blocks = to_explain_blocks(plan)
    lines: list[str] = []
    lines.append(f"### 🔍 问题理解: {plan.question}")
    lines.append("")
    lines.append(f"> 置信度: **{plan.confidence:.0%}** · 来源: `{plan.grounding_source}`")
    lines.append("")

    if blocks["指标"]:
        lines.append("**📌 指标**")
        for m in blocks["指标"]:
            line = f"- **{m['中文名']}** ({m['业务术语']}) — {m['说明']}"
            line += f"\n  - 聚合: `{m['聚合方式']}`"
            line += f"\n  - 字段: `{m['字段']}` ({m['字段中文名']})"
            if m.get("业务过滤"):
                line += f"\n  - 业务过滤: {'、'.join(m['业务过滤'])}"
            lines.append(line)
        lines.append("")

    if blocks["筛选条件"]:
        lines.append("**🎯 筛选条件**")
        for f in blocks["筛选条件"]:
            lines.append(f"- {f['本体类']}.{f['字段中文名']} {f['运算符']} `{f['值']}`")
        lines.append("")

    if blocks["分组"]:
        lines.append("**📊 分组**")
        for g in blocks["分组"]:
            lines.append(f"- {g['本体类']}.{g['字段中文名']}")
        lines.append("")

    if blocks["时间范围"]:
        lines.append("**📅 时间范围**")
        lines.append(f"- {blocks['时间范围']} (字段 `{blocks.get('时间字段', 'dt')}`)")
        lines.append("")

    if blocks["数据来源"]:
        lines.append("**🗃️ 数据来源**")
        lines.append(f"- 主表: {blocks['数据来源']}")
        if blocks["关联路径"]:
            lines.append(f"- 关联路径: {blocks['关联路径']}")
        lines.append("")

    if blocks["notes"]:
        lines.append("**⚠️ 说明**")
        for n in blocks["notes"]:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)
