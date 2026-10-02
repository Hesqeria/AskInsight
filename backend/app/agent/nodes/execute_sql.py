from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

MAX_RESULT_ROWS = 1000


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Execute SQL"})
    try:
        sql = state["sql"]
        repo = runtime.context["dw_doris_repository"]
        from app.clients.doris_client_manager import (
            doris_client_manager as _dcm,
        )
        _sf = _dcm.session_factory
        from sqlalchemy import text as _text

        # M6 guard chain (thin shell): pii + cost guards run right
        # BEFORE execution on EVERY path (simple queries used to bypass
        # the PII gate because it only lived inside correct_sql).
        # GuardRewrite (TABLESAMPLE downgrade) mutates the SQL in place;
        # GuardAsk (PII L>=3) flips needs_approval so the conditional
        # edge routes to wait_approval and suspends the turn.
        try:
            from app.core.guard_chain import guard_chain
            verdict = await guard_chain.run(
                state, sql, dict(runtime.context), stages=["pii", "cost"],
            )
            if verdict.asked:
                from app.agent.events import emit
                emit("guard/decision", {
                    "guard": verdict.guard, "outcome": "ask",
                    "reason": verdict.reason[:200],
                })
                return {
                    "needs_approval": True,
                    "approval_reason": verdict.reason,
                    "error": None,
                }
            for rw in verdict.rewrites:
                logger.warning(
                    f"guard {rw['guard']} rewrote SQL: {rw['reason']}"
                )
            if verdict.rewrites:
                new_sql = sql
                # The chain ran on `sql`; apply the (single) rewrite.
                from app.services.access_control import maybe_inject_sample
                new_sql = maybe_inject_sample(sql)
                if new_sql != sql:
                    sql = new_sql
                    state["sql"] = sql
        except Exception as e:
            logger.warning(f"Cost estimate skipped (non-fatal): {e}")

        import time as _time
        _t0 = _time.time()
        result = await repo.execute_sql(sql)
        total = len(result)
        _cost_ms = int((_time.time() - _t0) * 1000)

        # P0: access log (PRD meta_access_log) - powers hotness_30d and
        # GOV-4 cost analysis. Best-effort, never blocks the turn.
        try:
            import hashlib as _hashlib
            from app.agent.nodes.extract_lineage import (
                extract_lineage_from_sql as _tables_of,
            )
            import re as _re
            _tbls = sorted({
                m.group(1).strip("`")
                for m in _re.finditer(
                    r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_.]+)", sql, _re.I)
            }) or []
            if not _tbls:
                _tbls = ["unknown"]
            _seen = set()
            async with _sf() as _s:
                for _t in _tbls:
                    if _t in _seen:
                        continue
                    _seen.add(_t)
                    await _s.execute(_text(
                        "INSERT INTO data_agent.meta_access_log "
                        "(user_name, db_name, table_name, query_md5, "
                        "returned_rows, cost_ms, queried_at) VALUES "
                        "(:u, :db, :t, :md5, :rows, :ms, NOW())"), {
                        "u": state.get("_username") or "anonymous",
                        "db": _t.split(".")[0] if "." in _t else "dw",
                        "t": _t.split(".")[-1],
                        "md5": _hashlib.md5(sql.encode()).hexdigest(),
                        "rows": total, "ms": _cost_ms,
                    })
                await _s.commit()
        except Exception as _e:
            logger.debug(f"access log skipped: {_e}")

        # OPT-M4: PII field masking BEFORE any spill/view split - spilled
        # rows are masked too (security invariant, M10 note).
        masked_count = 0
        meta_repo = runtime.context.get("meta_doris_repository")
        try:
            from app.services.access_control import mask_result_pii
            if meta_repo is not None and result:
                result, masked_count = await mask_result_pii(
                    meta_repo.session, result, sql
                )
        except Exception as e:
            logger.warning(f"PII masking skipped (non-fatal): {e}")

        # Drop junk rows where every non-numeric cell is NULL/empty
        # (e.g. dirty ETL group keys like region_name=NULL). Applied after
        # masking so the filter sees the same values the user would.
        if result and len(result) > 1:
            def _junk(row) -> bool:
                for v in row.values():
                    if v is None or (isinstance(v, str) and not v.strip()):
                        continue
                    if isinstance(v, (int, float)):
                        continue  # numeric columns don't rescue a junk row
                    return False  # any real text value -> keep
                return True
            before = len(result)
            result = [r for r in result if not _junk(r)]
            if len(result) != before:
                total -= before - len(result)
                logger.info(f"dropped {before - len(result)} null-key junk rows")

        # M10: spill the FULL masked result; give the model/frontend a
        # head/tail preview + spill_id instead of a blind 1000-row cut.
        spill_id = None
        try:
            from app.services.spill_store import (
                should_spill, save_spill, head_tail,
            )
            from app.core.context import request_id_ctx_var
            if should_spill(result) and meta_repo is not None:
                spill_id = await save_spill(
                    meta_repo.session, request_id_ctx_var.get() or "",
                    sql, result,
                )
        except Exception as e:
            logger.warning(f"spill skipped (non-fatal): {e}")

        if spill_id:
            view_result = head_tail(result)
        else:
            view_result = result[:MAX_RESULT_ROWS]
        truncated = total > MAX_RESULT_ROWS
        if truncated:
            logger.warning(
                f"SQL result truncated: {total} -> {len(view_result)} rows"
                f"{' (spilled ' + spill_id + ')' if spill_id else ''}"
            )
        result = view_result

        logger.info(f"SQL execution result: {total} rows (returned {len(result)}, pii_masked={masked_count})")

        # OPT-M6: emit an execution-stage audit log (non-fatal).
        try:
            from app.core.audit import send_audit_log
            from app.core.context import request_id_ctx_var
            await send_audit_log(
                request_id=request_id_ctx_var.get(),
                username=state.get("_username") or "anonymous",
                query=state.get("query", ""), sql=sql,
                status="exec_ok", stage="exec",
                result_rows=total, pii_masked_cells=masked_count,
            )
        except Exception as e:
            logger.debug(f"exec audit failed (non-fatal): {e}")

        if not result:
            try:
                from app.core.data_today import coverage_text
                cov = coverage_text()
            except Exception:
                cov = ""
            hint = ("Query result is empty. Possible reasons: "
                    "1) data does not cover this condition "
                    "2) no match in the date range"
                    + (f" (note: {cov})" if cov else ""))
            writer({"result": [{"hint": hint}], "sql": sql[:2000]})
        else:
            # Single-value answers deserve a sentence, not a bare number
            # over a raw "SUM(t.gmv)" header. Build a natural-language
            # summary from the plan (metric name + window + value).
            summary = _result_summary(state, result)
            # Auto period-over-period (SuperSonic-style): append
            # "较上一周期 ±X%" by re-running the same SQL on the shifted
            # window. Rule-rendered plans only (dates are literal).
            if summary:
                try:
                    delta = await _period_delta(repo, state, sql, result)
                    if delta:
                        summary["delta"] = delta
                        summary["text"] = summary["text"] + f"，{delta}"
                except Exception as _de:
                    logger.debug(f"period delta skipped: {_de}")
            payload = {
                "result": result, "truncated": truncated,
                "total_rows": total, "pii_masked_cells": masked_count,
                "spill_id": spill_id,
                "spill_url": (f"/api/v1/spills/{spill_id}" if spill_id else None),
                "sql": sql[:2000],
            }
            if summary:
                payload["summary"] = summary
            writer(payload)
        from app.agent.events import emit
        emit("sql/executed", {
            "rows": total, "truncated": truncated,
            "masked_cells": masked_count, "spill_id": spill_id,
        })
        return {
            "_last_result": result, "_result_truncated": truncated,
            "_total_rows": total, "_pii_masked_cells": masked_count,
            "_spill_id": spill_id,
        }
    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        raise


def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _result_summary(state, result):
    """Natural-language summary for 1-row x 1-numeric-col results.

    Turns {SUM(t.gmv): 544692.38} into a readable sentence using the
    semantic plan (metric name + window + formatted value).
    Returns None when the shape doesn't qualify.
    """
    try:
        if not result:
            return None
        if (len(result) == 1 and isinstance(result[0], dict)
                and len(result[0]) > 1
                and all(v is None or _is_num(v)
                        for v in result[0].values())):
            # multi-metric single row: "近7天GMV和订单数" -> enumerate
            # metric=value pairs instead of max/min prose
            metric, time_desc, _ = _plan_metric_and_window(state)
            pairs = []
            for k, v in result[0].items():
                fv = float(v) if _is_num(v) else None
                pairs.append("无数据" if fv is None else
                             (f"{fv:,.2f}" if fv % 1 else f"{int(fv):,}"))
            text = "；".join(f"{k} {v}"
                            for k, v in zip(result[0].keys(), pairs))
            return {"metric": metric, "value": None, "unit": "",
                    "text": f"共{len(result)}行；{metric}{time_desc}：{text}"}
        if len(result) == 1 and isinstance(result[0], dict)                 and len(result[0]) == 1:
            row = result[0]
            raw_val = next(iter(row.values()))
            if raw_val is None:
                metric, time_desc, _ = _plan_metric_and_window(state)
                return {"metric": metric, "value": None, "unit": "",
                        "text": f"{metric}{time_desc}无数据"}
            if not isinstance(raw_val, (int, float)):
                try:
                    raw_val = float(raw_val)
                except (TypeError, ValueError):
                    return None
            return _plan_summary_payload(state, raw_val, None)
        if len(result) <= 100:
            return _multirow_summary_payload(state, result)
        return None
        return _plan_summary_payload(state, raw_val, None)
    except Exception:
        return None


_METRIC_CN = {
    "cashier_anomaly_count": "收银异常数",
    "cart_item_count": "加购件数", "cart_user_count": "加购人数",
    "favor_count": "收藏数", "good_rate": "好评率",
    "order_count": "订单数", "payer_count": "支付用户数",
    "avg_order_amount": "客单价", "GMV": "GMV",
    "DAU": "日活", "repurchase_rate": "复购率", "retention_rate": "留存率",
}


def _plan_metric_and_window(state):
    """(metric_term, time_desc, unit_kind) from the semantic plan."""
    plan = state.get("semantic_plan") or {}
    if isinstance(plan, dict):
        measures = plan.get("measures") or []
        metric = (measures[0].get("business_term") if measures else "") or "指标"
        tr = plan.get("time") or {}
        dims = [g.get("class_id") for g in (plan.get("group_by") or [])]
    else:
        measures = getattr(plan, "measures", []) or []
        metric = (measures[0].business_term if measures else "") or "指标"
        tr_obj = getattr(plan, "time", None)
        tr = {"start": tr_obj.start, "end": tr_obj.end} if tr_obj else {}
        dims = [g.class_id for g in (getattr(plan, "group_by", []) or [])]
    start, end = tr.get("start"), tr.get("end")
    if start and end:
        window = str(start) if str(start) == str(end) else f"{start} ~ {end}"
        time_desc = f"（{window}）"
    else:
        time_desc = ""
    return _METRIC_CN.get(str(metric), str(metric)), time_desc, dims


def _plan_summary_payload(state, raw_val, extra_text):
    metric, time_desc, _ = _plan_metric_and_window(state)
    term = metric
    if "率" in term:
        text = f"{term}{time_desc}为 {raw_val:.2%}"
        unit = ""
    elif any(w in term for w in ("GMV", "金额", "销售额", "客单价")):
        text = f"{term}{time_desc}为 {raw_val:,.2f} 元"
        unit = "元"
    else:
        text = f"{term}{time_desc}为 {raw_val:,.0f}"
        unit = ""
    return {"metric": term, "value": raw_val, "unit": unit, "text": text}


_DIM_CN = {"C050": "区域", "C021": "品类", "C011": "用户"}


def _multirow_summary_payload(state, result):
    """SuperSonic-style textInfo: one facts line + follow-up suggestions."""
    try:
        metric, time_desc, dims = _plan_metric_and_window(state)
        keys = list(result[0].keys())
        num_key = next((k for k in keys
                        if isinstance(result[0].get(k), (int, float))
                        or str(result[0].get(k)).replace('.', '', 1)
                        .replace('-', '', 1).isdigit()), None)
        cat_key = next((k for k in keys if k != num_key), None)
        if not num_key:
            return None
        vals = []
        for r in result:
            try:
                vals.append(float(r.get(num_key)))
            except (TypeError, ValueError):
                vals.append(None)
        real = [v for v in vals if v is not None]
        if not real:
            return {"metric": metric, "value": None, "unit": "",
                    "text": f"{metric}{time_desc}各周期均无数据"}

        def _label(i):
            return str(result[i].get(cat_key)) if cat_key else f"第{i+1}行"
        is_amount = any(w in metric for w in ("GMV", "金额", "销售额", "客单价"))
        fmt = (lambda v: f"{v:,.2f}元") if is_amount else (lambda v: f"{v:,.0f}")
        top_i = max((i for i, v in enumerate(vals) if v is not None),
                    key=lambda i: vals[i])
        parts = [f"最高：{_label(top_i)}（{fmt(vals[top_i])}）"]
        others = [(i, v) for i, v in enumerate(vals)
                  if v is not None and i != top_i]
        if others:
            low_i = min(others, key=lambda kv: kv[1])[0]
            parts.append(f"最低：{_label(low_i)}（{fmt(vals[low_i])}）")
        nulls = [_label(i) for i, v in enumerate(vals) if v is None]
        if nulls:
            parts.append(f"{','.join(nulls)}无数据")
        text = f"共{len(result)}行；{metric}{time_desc}" + "，".join(parts)
        # Follow-up suggestions (SuperSonic/WrenAI recommended questions).
        present = [d for d in dims if d in _DIM_CN]
        absent = [d for d in _DIM_CN if d not in dims]
        sugg = []
        if present:
            sugg.append(f"{metric}最高的3个{_DIM_CN[present[0]]}")
        if absent:
            sugg.append(f"按{_DIM_CN[absent[0]]}看{metric}")
        sugg.append(f"上个月的{metric}")
        return {"metric": metric, "value": vals[top_i], "unit": "元" if is_amount else "",
                "text": text, "suggestions": sugg[:3]}
    except Exception:
        return None


def _shift_date(d_str: str, days: int) -> str:
    from datetime import date, timedelta as _td
    y, m, d = (int(x) for x in str(d_str).split("-"))
    return (date(y, m, d) + _td(days=days)).isoformat()


async def _period_delta(repo, state, sql: str, result) -> "str | None":
    """Re-run the rendered SQL on the previous same-length window and
    return a human delta sentence. Best-effort; None when not applicable.
    """
    plan = state.get("semantic_plan") or {}
    if isinstance(plan, dict):
        if plan.get("grounding_source") != "rule":
            return None
        if plan.get("pop_windows"):
            return None  # the query itself is the period comparison
        tr = plan.get("time") or {}
    else:
        if getattr(plan, "grounding_source", "") != "rule":
            return None
        tr_obj = getattr(plan, "time", None)
        tr = {"start": tr_obj.start, "end": tr_obj.end} if tr_obj else {}
    start, end = tr.get("start"), tr.get("end")
    if not start or not end:
        return None
    from datetime import date as _d
    sy, sm, sd = (int(x) for x in str(start).split("-"))
    ey, em, ed = (int(x) for x in str(end).split("-"))
    span_days = (_d(ey, em, ed) - _d(sy, sm, sd)).days + 1
    prev_start = _shift_date(start, -span_days)
    prev_end = _shift_date(end, -span_days)
    prev_sql = sql.replace(f"'{start}'", f"'{prev_start}'")                   .replace(f"'{end}'", f"'{prev_end}'")
    if prev_sql == sql:
        return None
    from sqlalchemy import text as _text

    import decimal as _decimal

    def _is_num(v):
        if isinstance(v, (int, float, _decimal.Decimal)):
            return True
        return (isinstance(v, str) and v.replace('.', '', 1)
                .replace('-', '', 1).replace(',', '').isdigit())

    def _row_total(r):
        for v in (r.values() if isinstance(r, dict) else r):
            if _is_num(v):
                return float(str(v).replace(',', ''))
        return 0.0


    prev_rows = (await repo.session.execute(_text(prev_sql))).fetchall()
    if not prev_rows:
        return "上一周期无数据"
    # measure is the first numeric column of each row (alias order)
    cur_total = sum(_row_total(r) for r in result)
    prev_total = sum(_row_total(r) for r in prev_rows)
    if prev_total == 0:
        return None
    pct = (cur_total - prev_total) / prev_total * 100
    arrow = "上升" if pct >= 0 else "下降"
    return f"较上一周期{arrow} {abs(pct):.1f}%"
