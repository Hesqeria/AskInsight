"""SQL safety validation with NFKC normalization, comment stripping, CTE scan."""

import re
import unicodedata


ALLOWED_TABLES = set()  # Populated from meta_config at startup


FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
    "TRUNCATE", "EXEC", "EXECUTE", "GRANT", "REVOKE", "MERGE",
]


_HOMOGLYPH_MAP = {
    # Cyrillic -> Latin
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x",
    # Greek -> Latin
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P",
    "Τ": "T", "Υ": "Y", "Χ": "X",
}


def _normalize(text: str) -> str:
    """NFKC + homoglyph mapping: converts Cyrillic/Greek/fullwidth to ASCII."""
    nfkc = unicodedata.normalize("NFKC", text)
    return "".join(_HOMOGLYPH_MAP.get(c, c) for c in nfkc)


def _strip_string_literals(sql: str) -> str:
    result = re.sub(r"'[^']*'", "''", sql)
    result = re.sub(r'"[^"]*"', '""', result)
    return result


def _strip_comments(sql: str) -> str:
    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'#[^\n]*', '', sql)
    return sql


def validate_sql_safety(sql: str) -> tuple:
    """Check SQL for dangerous operations.

    NFKC-normalizes input to defeat homoglyph attacks,
    strips comments and string literals before keyword matching,
    and scans CTE/WITH bodies for forbidden keywords.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL"

    normalized = _normalize(sql.strip())
    clean = _strip_comments(normalized)
    clean = _strip_string_literals(clean)
    upper = clean.upper()

    for kw in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, upper):
            return False, f"Forbidden keyword: {kw}"

    if re.search(r';\s*[\S\n\r]', clean):
        return False, "Multiple SQL statements not allowed"

    return True, "OK"


def populate_allowed_tables(table_names):
    """Populate ALLOWED_TABLES from meta_config at startup."""
    global ALLOWED_TABLES
    ALLOWED_TABLES = set(t.lower() for t in table_names)


# =====================================================
# LangGraph node wrapper
# =====================================================
# validate_sql_safety(sql) 本身是工具函数（参数为 sql 字符串）。
# LangGraph 节点要求签名 (state, runtime)，所以需要这层包装，
# 否则 graph.py add_node 时会把 state(dict) 当作 sql 传入，导致
# "'dict' object has no attribute 'strip'" 异常（BUG-07）。

from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def validate_sql_safety_node(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """LangGraph 节点：从 state 取 SQL，调用 validate_sql_safety 工具函数。

    OPT-M3: 同时做 RBAC 表级权限检查（按 state._username 查 role_table_perm）。
    Admin 角色全表放行；其他角色按 role_table_perm 配置逐表校验。
    OPT-M6: 安全/权限拦截事件发审计日志（traceId = request_id_ctx_var）。
    """
    writer = runtime.stream_writer
    writer({"stage": "Validate SQL Safety"})
    try:
        sql = state.get("sql", "")
        if not isinstance(sql, str):
            raise ValueError(
                f"state['sql'] 非字符串: type={type(sql).__name__}, "
                f"value={str(sql)[:200]}"
            )
        username = state.get("_username") or "anonymous"
        role = "admin" if username in ("admin",) else "user"

        # Helper for OPT-M6 audit (non-fatal on failure).
        async def _audit(stage, status, **kw):
            try:
                from app.core.audit import send_audit_log
                from app.core.context import request_id_ctx_var
                await send_audit_log(
                    request_id=request_id_ctx_var.get(),
                    username=username, query=state.get("query", ""),
                    sql=sql, status=status, stage=stage, **kw
                )
            except Exception as e:
                logger.debug(f"audit log failed (non-fatal): {e}")

        # M6 guard chain (thin shell): forbidden_keywords -> rbac_tables.
        # Monotonic - first GuardReject wins; decisions land in the M1
        # event log as guard/decision (see core/guard_chain.py).
        from app.core.guard_chain import guard_chain
        verdict = await guard_chain.run(
            state, sql, dict(runtime.context), stages=["pre_validate"],
        )
        if verdict.rejected:
            logger.error(
                f"guard {verdict.guard} rejected: {verdict.reason} "
                f"(sql={sql[:80]})"
            )
            if verdict.guard == "rbac_tables":
                await _audit("rbac", "perm_blocked",
                             perm_blocked=verdict.reason)
                return {"error": f"权限不足: {verdict.reason}"}
            await _audit("safety", "safety_violation",
                         safety_violation=verdict.reason)
            return {"error": verdict.reason}

        logger.info(f"SQL safety check passed: {sql[:80]} (role={role})")
        return {"error": None}
    except Exception as e:
        logger.error(f"validate_sql_safety_node 异常: {e}")
        return {"error": str(e)}
