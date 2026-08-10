"""P4-01: SQL generator & validator - read-only guard, 3-layer security check."""
import re

SELECT_ONLY_RE = re.compile(r"^\s*(SELECT|WITH|EXPLAIN)\b", re.IGNORECASE)
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|INTO|SET)\b",
    re.IGNORECASE,
)
TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)", re.IGNORECASE)

MAX_SCAN_ROWS = 10_000_000

ROLE_TABLE_PERMISSIONS = {
    "L1_business": ["ods_", "dwd_"],
    "L2_analyst": ["ods_", "dwd_", "dws_", "ads_"],
    "L3_engineer": ["ods_", "dwd_", "dws_", "ads_", "dim_"],
    "L4_admin": ["*"],
}


class ValidationPass:
    def __init__(self, syntax_ok, business_rule_ok, data_size_ok, errors=None, warnings=None):
        self.syntax_ok = syntax_ok
        self.business_rule_ok = business_rule_ok
        self.data_size_ok = data_size_ok
        self.errors = errors or []
        self.warnings = warnings or []

    @property
    def ok(self):
        return self.syntax_ok and self.business_rule_ok and self.data_size_ok


class GeneratedSQL:
    def __init__(self, sql, intent, model_used, tokens_in=0, tokens_out=0):
        self.sql = sql
        self.intent = intent
        self.model_used = model_used
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


class SQLToolkit:
    def __init__(self, llm=None, doris=None):
        self._llm = llm
        self._doris = doris

    async def generate_sql(self, intent, candidates, few_shot=None):
        from app.infra.llm_router import get_llm
        llm = self._llm or get_llm()
        few_shot = few_shot or []
        task_type = "sql_simple" if len(candidates) <= 3 else "sql_complex"
        prompt = self._build_prompt(intent, candidates, few_shot)
        resp = llm.complete(
            [{"role": "user", "content": prompt}],
            task_type=task_type, temperature=0.1, max_tokens=2048,
        )
        sql = self._extract_sql(resp["content"])
        return GeneratedSQL(sql, intent, resp.get("model_used", ""),
                            resp.get("tokens_in", 0), resp.get("tokens_out", 0))

    def _build_prompt(self, intent, candidates, few_shot):
        lines = []
        if getattr(intent, "metric", None):
            lines.append(f"目标指标: {intent.metric}")
        else:
            lines.append(f"意图: {getattr(intent, 'text', intent)}")
        lines.append("候选表:")
        for c in candidates[:8]:
            lines.append(f"- {c.get('id')}: {c.get('description', '')}")
        if few_shot:
            lines.append("示例:")
            for ex in few_shot[:3]:
                lines.append(f"Q: {ex.get('question')}\nSQL: {ex.get('sql')}")
        lines.append("请只输出Doris SQL，不要注释。")
        return "\n".join(lines)

    @staticmethod
    def _extract_sql(content):
        m = re.search(r"```(?:sql)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return content.strip()

    def validate_sql(self, sql, user_role, execute_explain=True) -> ValidationPass:
        errors = []
        warnings = []

        if not SELECT_ONLY_RE.match(sql) or FORBIDDEN_KEYWORDS.search(sql):
            errors.append("仅允许 SELECT 查询")
            return ValidationPass(False, False, False, errors=errors)

        if execute_explain and self._doris:
            try:
                self._doris.execute(f"EXPLAIN {sql}")
            except Exception as e:
                errors.append(f"语法错误: {e}")
                return ValidationPass(False, False, False, errors=errors)

        for table in self._extract_tables(sql):
            if not self._has_table_permission(user_role, table):
                errors.append(f"无权访问 {table}")

        scan_rows = self._estimate_scan_rows(sql)
        if scan_rows > MAX_SCAN_ROWS:
            errors.append(f"扫描行数过大: {scan_rows}")

        return ValidationPass(
            syntax_ok=not any("语法" in e for e in errors),
            business_rule_ok=not any("无权" in e for e in errors),
            data_size_ok=not any("扫描" in e for e in errors),
            errors=errors, warnings=warnings,
        )

    @staticmethod
    def _extract_tables(sql):
        return list(dict.fromkeys(TABLE_RE.findall(sql)))

    @staticmethod
    def _has_table_permission(user_role, table):
        allowed = ROLE_TABLE_PERMISSIONS.get(user_role, [])
        if "*" in allowed:
            return True
        base = table.split(".")[-1]
        return any(base.startswith(p) for p in allowed)

    @staticmethod
    def _estimate_scan_rows(sql):
        m = re.search(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
        if not m:
            return 0
        tname = m.group(1).lower()
        rough = {
            "ods_order": 20_000_000, "ods_payment": 12_000_000,
            "dwd_order_detail": 15_000_000, "dws_gmv_daily": 200_000,
            "ads_gmv_overview": 50_000,
        }
        return rough.get(tname, 500_000)

    def classify_complexity(self, sql) -> str:
        upper = sql.upper()
        join_cnt = upper.count(" JOIN ")
        if join_cnt == 0 and "SUBQUERY" not in upper and "UNION" not in upper:
            return "simple"
        if join_cnt <= 3:
            return "medium"
        return "complex"
