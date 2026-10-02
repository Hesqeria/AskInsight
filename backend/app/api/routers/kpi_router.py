"""Subject-modeled KPI cards.

主体结构体 = 业务主体 x 时间语义 x 窗口 x 地理 x 指标, 全部数据驱动:
- GET /api/kpi/subjects  -> 完整结构体(前端选择器直接消费)
- GET /api/kpi/cards     -> 按主体计算卡片(每主体沿自身业务时间轴)

业务主体: 订单 / 退款 / 评价 / 购物车 / 收藏 / 曝光点击 / 页面浏览
每个主体: 自身事实表 + 时间语义集 + 自然指标集 + 地理可用性
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.auth import verify_token_factory
verify_token = verify_token_factory(lane="kpi", limit=60)
from app.clients.doris_client_manager import doris_client_manager
from app.core.log import logger

kpi_router = APIRouter()

_WINDOWS = {
    "yesterday": lambda today: (today, today),
    "7d": lambda today: (today - timedelta(days=6), today),
    "30d": lambda today: (today - timedelta(days=29), today),
    "month": lambda today: (today.replace(day=1), today),
}
_WIN_LABEL = {"yesterday": "昨天", "7d": "近7天", "30d": "近30天", "month": "本月"}

_GEO_JOIN = (" INNER JOIN dw.dim_base_province p ON t.province_id = p.id"
             " INNER JOIN dw.dim_base_region r ON p.region_id = r.id")
_PAID = "t.order_status = '1001'"

# --------------------------------------------------------------------------- #
# 主体注册表: 每个业务主体的结构体定义
# --------------------------------------------------------------------------- #
_SUBJECTS = [
    {
        "key": "order", "label": "订单",
        "source": "dw.dwd_order_info_inc", "geo": True, "default": True,
        "time_semantics": [
            {"key": "paid", "label": "付款时间", "column": "dt",
             "filter": _PAID, "caliber": "已支付订单", "default": True},
            {"key": "created", "label": "创建时间", "column": "create_time",
             "filter": "", "caliber": "全部订单(含未支付)"},
            {"key": "updated", "label": "更新时间", "column": "create_time",
             "filter": "", "caliber": "全部订单(演示库operate_time空,按创建时间近似)"},
            {"key": "done", "label": "订单完成时间", "column": "create_time",
             "filter": _PAID, "caliber": "已完成(演示库:已支付且按创建时间近似)"},
        ],
        "metrics": [
            {"key": "gmv", "label": "GMV", "unit": "元", "q": "GMV是多少",
             "expr": "SUM(t.total_amount)"},
            {"key": "order_count", "label": "订单数", "unit": "", "q": "订单数是多少",
             "expr": "COUNT(t.id)"},
            {"key": "payer_count", "label": "支付用户数", "unit": "", "q": "支付用户数有多少",
             "expr": "COUNT(DISTINCT t.user_id)"},
            {"key": "avg_order_amount", "label": "客单价", "unit": "元", "q": "客单价是多少",
             "expr": "AVG(t.total_amount)"},
        ],
        "fast_path": {  # paid + no geo -> pre-agg ads (dt axis)
            "time_semantic": "paid", "sql_builder": "_ads_paid_sql"},
    },
    {
        "key": "refund", "label": "退款",
        "source": "dw.dwd_order_refund_info_inc", "geo": False,
        "time_semantics": [
            {"key": "created", "label": "退款时间", "column": "create_time",
             "filter": "", "caliber": "退款单(无地理归属)", "default": True},
        ],
        "metrics": [
            {"key": "refund_amount", "label": "退款金额", "unit": "元", "q": "退款金额是多少",
             "expr": "SUM(r.refund_amount)"},
            {"key": "refund_count", "label": "退款单数", "unit": "", "q": "退款单数是多少",
             "expr": "COUNT(r.id)"},
        ],
    },
    {
        "key": "comment", "label": "评价",
        "source": "dw.dwd_comment_info_inc", "geo": False,
        "time_semantics": [
            {"key": "created", "label": "评价时间", "column": "create_time",
             "filter": "", "caliber": "全部评价", "default": True},
        ],
        "metrics": [
            {"key": "comment_count", "label": "评价数", "unit": "条", "q": "评价数是多少",
             "expr": "COUNT(t.id)"},
            {"key": "good_rate", "label": "好评率", "unit": "%", "q": "好评率是多少",
             "expr": "AVG(CASE WHEN t.appraise = '好评' THEN 100.0 ELSE 0 END)"},
        ],
    },
    {
        "key": "cart", "label": "购物车",
        "source": "dw.dwd_cart_info_inc", "geo": False,
        "time_semantics": [
            {"key": "created", "label": "加购时间", "column": "create_time",
             "filter": "", "caliber": "加购行为(演示库全量快照)", "default": True},
        ],
        "metrics": [
            {"key": "cart_user_count", "label": "加购用户数", "unit": "", "q": "加购用户数有多少",
             "expr": "COUNT(DISTINCT t.user_id)"},
            {"key": "cart_sku_count", "label": "加购件数", "unit": "件", "q": "加购件数是多少",
             "expr": "SUM(t.sku_num)"},
            {"key": "cart_amount", "label": "加购金额", "unit": "元", "q": "加购金额是多少",
             "expr": "SUM(t.cart_price * t.sku_num)"},
        ],
    },
    {
        "key": "favor", "label": "收藏",
        "source": "dw.dwd_favor_info_inc", "geo": False,
        "time_semantics": [
            {"key": "created", "label": "收藏时间", "column": "create_time",
             "filter": "", "caliber": "收藏行为", "default": True},
        ],
        "metrics": [
            {"key": "favor_user_count", "label": "收藏用户数", "unit": "", "q": "收藏用户数有多少",
             "expr": "COUNT(DISTINCT t.user_id)"},
            {"key": "favor_count", "label": "收藏次数", "unit": "次", "q": "收藏次数是多少",
             "expr": "COUNT(t.id)"},
        ],
    },
    {
        "key": "display", "label": "曝光点击",
        "source": "dw.dwd_display_log_inc", "geo": False,
        "time_semantics": [
            {"key": "event", "label": "事件时间", "column": "dt",
             "filter": "", "caliber": "曝光埋点(演示库暂无数据)", "default": True},
        ],
        "metrics": [
            {"key": "display_count", "label": "曝光次数", "unit": "次", "q": "曝光次数是多少",
             "expr": "COUNT(1)"},
            {"key": "display_user_count", "label": "曝光用户数", "unit": "", "q": "曝光用户数有多少",
             "expr": "COUNT(DISTINCT t.uid)"},
        ],
    },
    {
        "key": "page", "label": "页面浏览",
        "source": "dw.dwd_page_log_inc", "geo": False,
        "time_semantics": [
            {"key": "event", "label": "事件时间", "column": "dt",
             "filter": "", "caliber": "页面埋点(演示库暂无数据)", "default": True},
        ],
        "metrics": [
            {"key": "page_count", "label": "浏览次数", "unit": "次", "q": "浏览次数是多少",
             "expr": "COUNT(1)"},
            {"key": "avg_duration", "label": "人均停留", "unit": "毫秒", "q": "人均停留时长是多少",
             "expr": "AVG(t.during_time)"},
        ],
    },
]


def _delta_pct(cur, prev):
    try:
        if prev is None or float(prev) == 0:
            return None
        return round((float(cur) - float(prev)) / float(prev) * 100, 1)
    except (TypeError, ValueError):
        return None


def _find_subject(key: str) -> dict:
    return next((s for s in _SUBJECTS if s["key"] == key), _SUBJECTS[0])


def _find_ts(subject: dict, key: str) -> dict:
    ts_list = subject["time_semantics"]
    return next((t for t in ts_list if t["key"] == key),
                next((t for t in ts_list if t.get("default")), ts_list[0]))


def _ads_paid_sql(cs, ce, ps, pe) -> str:
    """Pre-agg fast path for 订单x付款时间x无地理."""
    return ("SELECT "
            f"SUM(CASE WHEN dt BETWEEN '{cs}' AND '{ce}' THEN gmv END) AS cur_gmv, "
            f"SUM(CASE WHEN dt BETWEEN '{ps}' AND '{pe}' THEN gmv END) AS prev_gmv, "
            f"SUM(CASE WHEN dt BETWEEN '{cs}' AND '{ce}' THEN order_count END) AS cur_order_count, "
            f"SUM(CASE WHEN dt BETWEEN '{ps}' AND '{pe}' THEN order_count END) AS prev_order_count, "
            f"SUM(CASE WHEN dt BETWEEN '{cs}' AND '{ce}' THEN payer_count END) AS cur_payer_count, "
            f"SUM(CASE WHEN dt BETWEEN '{ps}' AND '{pe}' THEN payer_count END) AS prev_payer_count, "
            f"AVG(CASE WHEN dt BETWEEN '{cs}' AND '{ce}' THEN avg_order_amount END) AS cur_avg_order_amount, "
            f"AVG(CASE WHEN dt BETWEEN '{ps}' AND '{pe}' THEN avg_order_amount END) AS prev_avg_order_amount "
            f"FROM dw.ads_gmv_total_day WHERE dt BETWEEN '{ps}' AND '{ce}'")


def _subject_sql(subject, ts, cs, ce, ps, pe, region, province) -> str:
    """Generic subject SQL: metric exprs x CASE windows on the subject's
    own business timeline. Alias t (r for refund)."""
    alias = "r" if subject["key"] == "refund" else "t"
    tc = (f"CAST({alias}.{ts['column']} AS DATE)"
          if ts["column"] != "dt" else f"{alias}.dt")
    conds = [f"({tc} BETWEEN '{ps}' AND '{ce}')"]
    if ts.get("filter"):
        conds.append(ts["filter"])
    geo = ""
    if subject.get("geo") and (region or province):
        geo = _GEO_JOIN
        if province:
            conds.append(f"p.name = '{province}'")
        elif region:
            conds.append(f"r.region_name = '{region}'")
    metric_selects = []
    for m in subject["metrics"]:
        expr = m["expr"].replace("t.", f"{alias}.")
        metric_selects.append(
            f"{_agg_case(expr, tc, cs, ce, 'cur_' + m['key'])}")
        metric_selects.append(
            f"{_agg_case(expr, tc, ps, pe, 'prev_' + m['key'])}")
    return ("SELECT " + ", ".join(metric_selects)
            + f" FROM {subject['source']} {alias}" + geo
            + " WHERE " + " AND ".join(conds))


def _agg_case(expr: str, tc: str, s: str, e: str, alias: str) -> str:
    """Wrap an aggregate expr over a CASE window:
    SUM(x)          -> SUM(CASE WHEN tc BETWEEN .. THEN x END)
    COUNT(DISTINCT x) -> COUNT(DISTINCT CASE WHEN .. THEN x END)"""
    import re
    ex = expr.strip()
    m = re.match(r"^COUNT\s*\(\s*DISTINCT\s+(.*)\)$", ex, re.S)
    if m:
        return (f"COUNT(DISTINCT CASE WHEN {tc} BETWEEN '{s}' AND '{e}' "
                f"THEN {m.group(1)} END) AS {alias}")
    m = re.match(r"^(COUNT|SUM|AVG|MAX|MIN)\s*\((.*)\)$", ex, re.S)
    if not m:
        return f"MAX(CASE WHEN {tc} BETWEEN '{s}' AND '{e}' THEN {ex} END) AS {alias}"
    fn, inner = m.group(1), m.group(2)
    return (f"{fn}(CASE WHEN {tc} BETWEEN '{s}' AND '{e}' "
            f"THEN {inner} END) AS {alias}")


@kpi_router.get("/api/kpi/subjects")
async def kpi_subjects(user: dict = Depends(verify_token)):
    """完整主体结构体: 业务主体 x 时间语义 x 窗口 x 地理 x 指标."""
    async with doris_client_manager.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT r.region_name, p.name FROM dw.dim_base_region r "
            "JOIN dw.dim_base_province p ON p.region_id = r.id "
            "WHERE r.region_name NOT LIKE :fake ORDER BY r.id, p.id"),
            {"fake": "模拟%"})).fetchall()
    regions, provinces = [], []
    for rn, pn in rows:
        if rn not in regions:
            regions.append(rn)
        provinces.append({"province": pn, "region": rn})
    return {
        "subjects": [
            {"key": s["key"], "label": s["label"], "geo": s.get("geo", False),
             "default": s.get("default", False),
             "time_semantics": [
                 {"key": t["key"], "label": t["label"],
                  "caliber": t["caliber"], "default": t.get("default", False)}
                 for t in s["time_semantics"]],
             "metrics": [{"key": m["key"], "label": m["label"],
                          "unit": m["unit"]} for m in s["metrics"]]}
            for s in _SUBJECTS],
        "windows": [{"key": k, "label": v} for k, v in _WIN_LABEL.items()],
        "regions": regions,
        "provinces": provinces,
    }


@kpi_router.get("/api/kpi/cards")
async def kpi_cards(subject: str = "order", time_type: str = "",
                    window: str = "7d", region: str = "", province: str = "",
                    user: dict = Depends(verify_token)):
    """按主体结构体计算卡片: 每主体沿自身业务时间轴取窗."""
    sub = _find_subject(subject)
    ts = _find_ts(sub, time_type)
    win_key = window if window in _WINDOWS else "7d"
    region = (region or "").strip() or None
    province = (province or "").strip() or None
    if not sub.get("geo"):
        region = province = None

    async with doris_client_manager.session_factory() as session:
        from app.core.data_today import get_data_today
        # per-timeline freeze: each subject aligns to ITS OWN data timeline
        if sub["key"] == "order" and ts["column"] == "dt":
            today = await get_data_today(session) or date.today()
        else:
            today = (await get_data_today(
                session, expr=ts["column"], table=sub["source"],
                key=f"{sub['key']}_{ts['column']}") or date.today())
        start, end = _WINDOWS[win_key](today)
        span = (end - start).days + 1
        p_end = start - timedelta(days=1)
        p_start = start - timedelta(days=span)
        cs, ce = start.isoformat(), end.isoformat()
        ps, pe = p_start.isoformat(), p_end.isoformat()
        row = None
        try:
            if (sub.get("fast_path") and ts["key"] == "paid"
                    and not region and not province):
                sql = _ads_paid_sql(cs, ce, ps, pe)
            else:
                sql = _subject_sql(sub, ts, cs, ce, ps, pe, region, province)
            row = (await session.execute(text(sql))).fetchone()
        except Exception as e:
            logger.warning(f"kpi cards query failed: {e}")

    scope = province or region or ""
    subject_parts = [_WIN_LABEL[win_key], ts["label"]]
    if scope:
        subject_parts.append(scope)
    subject_line = " · ".join(subject_parts)

    cards = []
    for m in sub["metrics"]:
        cur = getattr(row, f"cur_{m['key']}", None) if row is not None else None
        prev = getattr(row, f"prev_{m['key']}", None) if row is not None else None
        q = f"{_WIN_LABEL[win_key]}{scope}按{ts['label']}{m['q']}"
        cards.append({
            "metric": m["label"],
            "value": float(cur) if cur is not None else None,
            "unit": m["unit"],
            "delta_pct": _delta_pct(cur, prev),
            "window": cs if cs == ce else f"{cs} ~ {ce}",
            "window_key": win_key,
            "subject": sub["label"],
            "time_type": ts["key"],
            "subject_line": subject_line,
            "question": q,
        })
    return {
        "cards": cards,
        "subject": sub["key"],
        "time_type": ts["key"],
        "window": win_key,
        "region": region or "",
        "province": province or "",
        "caliber": ts["caliber"],
        "generated_at": str(today),
    }
