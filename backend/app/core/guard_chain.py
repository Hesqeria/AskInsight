"""SQL execution guard chain (PRD 05-DeepSeek-Harness研究 M6).

Borrowed from dsh tools/pre-execute waterfall + monotonic guard:
- Checks are declarative: a new check = register one guard, zero graph
  changes (FR1/FR5).
- Monotonic reject: the FIRST GuardReject terminates the chain; later
  guards can never flip a rejection back to pass (FR3, dsh "guard has
  no allow").
- GuardAsk models "needs human approval" (PII L>=3 today, extensible
  to dimension-value confirmation etc.) - the caller persists a ticket
  and suspends (FR4, hooks into the approval flow).
- Every pass/reject/ask decision is recorded as a guard/decision
  session event with latency (FR4, feeds M1's timeline).

Built-in guards, in registration order (FR2):
  forbidden_keywords -> rbac_tables -> pii_scan -> cost_estimate

The syntax check (validate_sql EXPLAIN) intentionally stays a separate
graph node because it drives routing (error -> correct_sql).
"""
from __future__ import annotations

import re
import time

from sqlalchemy import text
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.core.log import logger


@dataclass
class GuardReject:
    """Hard deny. Monotonic: nothing downstream can override."""
    reason: str
    guard: str = ""


@dataclass
class GuardAsk:
    """Suspend for human decision (approval ticket)."""
    reason: str
    kind: str = "approval"
    violations: list = field(default_factory=list)
    guard: str = ""


@dataclass
class GuardRewrite:
    """Transform the SQL and continue the chain (e.g. TABLESAMPLE
    downgrade for expensive scans)."""
    sql: str
    reason: str
    guard: str = ""


@dataclass
class GuardVerdict:
    """Chain-level outcome for one run."""
    outcome: str                 # pass | reject | ask
    guard: str = ""              # deciding guard ("" when pass)
    reason: str = ""
    violations: list = field(default_factory=list)
    rewrites: list = field(default_factory=list)  # [{guard, reason}]
    decisions: list = field(default_factory=list) # [{guard, outcome, ms}]

    @property
    def rejected(self) -> bool:
        return self.outcome == "reject"

    @property
    def asked(self) -> bool:
        return self.outcome == "ask"


# check_fn(state, sql, ctx) -> None | GuardReject | GuardAsk | GuardRewrite
CheckFn = Callable[[dict, str, dict], Any]

# Registry stages and their canonical order (FR2). Stages let thin-shell
# nodes at different graph positions run the right subset while sharing
# one global ordering; a full pre_execute run uses every stage in order.
STAGE_ORDER = {
    "pre_validate": 0,   # validate_sql_safety node: keywords + RBAC
    "pii": 1,            # correct_sql node: post-correction PII scan
    "cost": 2,           # execute_sql node: EXPLAIN cost estimate
}


class GuardChain:
    """Order-sensitive registry. Process-wide singleton `guard_chain`."""

    def __init__(self):
        self._guards: list[tuple[int, int, str, CheckFn, str]] = []
        self._names: set[str] = set()
        self._seq = 0

    def register(self, name: str, check_fn: CheckFn,
                 stage: str = "pre_validate") -> None:
        """Register a guard. Duplicate name replaces (idempotent init)."""
        if name in self._names:
            self._guards = [g for g in self._guards if g[2] != name]
        else:
            self._names.add(name)
        self._seq += 1
        order = STAGE_ORDER.get(stage, 99)
        self._guards.append((order, self._seq, name, check_fn, stage))
        # Keep (stage order, registration order) - stable and deterministic.
        self._guards.sort(key=lambda g: (g[0], g[1]))

    def unregister(self, name: str) -> None:
        self._names.discard(name)
        self._guards = [g for g in self._guards if g[2] != name]

    def names(self, stages: Optional[list[str]] = None) -> list[str]:
        return [g[2] for g in self._sorted(stages)]

    def _sorted(self, stages: Optional[list[str]] = None):
        if stages is None:
            return list(self._guards)
        keep = set(stages)
        return [g for g in self._guards if g[4] in keep]

    async def run(self, state: dict, sql: str, ctx: Optional[dict] = None,
                  stages: Optional[list[str]] = None) -> GuardVerdict:
        """Run guards in canonical order. First reject wins (monotonic);
        first ask wins; rewrites continue with the mutated SQL."""
        verdict = GuardVerdict(outcome="pass")
        current_sql = sql
        ctx = ctx or {}
        for _order, _seq, name, fn, _stage in self._sorted(stages):
            t0 = time.perf_counter()
            try:
                decision = await fn(state, current_sql, ctx)
            except Exception as e:
                # fail-closed on unexpected errors inside a guard? The
                # historical nodes treat per-check failures as "skip";
                # keep that behavior but record it.
                logger.warning(f"guard {name} raised (treated as skip): {e}")
                decision = None
            ms = round((time.perf_counter() - t0) * 1000, 2)
            outcome = "pass"
            if isinstance(decision, GuardReject):
                outcome = "reject"
                verdict.decisions.append(
                    {"guard": name, "outcome": outcome, "ms": ms,
                     "reason": decision.reason})
                _emit_decision(name, outcome, ms, decision.reason)
                verdict.outcome = "reject"
                verdict.guard = name
                verdict.reason = decision.reason
                return verdict
            if isinstance(decision, GuardAsk):
                outcome = "ask"
                verdict.decisions.append(
                    {"guard": name, "outcome": outcome, "ms": ms,
                     "reason": decision.reason})
                _emit_decision(name, outcome, ms, decision.reason)
                verdict.outcome = "ask"
                verdict.guard = name
                verdict.reason = decision.reason
                verdict.violations = decision.violations
                return verdict
            if isinstance(decision, GuardRewrite):
                outcome = "rewrite"
                current_sql = decision.sql
                verdict.rewrites.append(
                    {"guard": name, "reason": decision.reason})
            verdict.decisions.append(
                {"guard": name, "outcome": outcome, "ms": ms})
            _emit_decision(name, outcome, ms,
                           getattr(decision, "reason", ""))
        return verdict


def _emit_decision(name: str, outcome: str, ms: float, reason: str) -> None:
    """guard/decision -> M1 session event (never raises)."""
    try:
        from app.agent.events import emit
        emit("guard/decision", {"guard": name, "outcome": outcome,
                                "ms": ms, "reason": (reason or "")[:200]})
    except Exception:
        pass


# --------------------------------------------------------------------- #
# Built-in guards (FR2) - extracted as pure-ish functions from the
# validate_sql_safety / correct_sql / execute_sql nodes.
# --------------------------------------------------------------------- #
async def forbidden_keywords_guard(state: dict, sql: str,
                                   ctx: dict) -> Optional[GuardReject]:
    """DROP/DELETE/INSERT/... keyword deny (NFKC + homoglyph aware)."""
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    ok, msg = validate_sql_safety(sql)
    if not ok:
        return GuardReject(reason=msg, guard="forbidden_keywords")
    return None


async def rbac_tables_guard(state: dict, sql: str,
                            ctx: dict) -> Optional[GuardReject]:
    """Table-level RBAC by user role (OPT-M3, check_sql_permission)."""
    repo = ctx.get("meta_doris_repository")
    if repo is None:
        return None
    username = state.get("_username") or "anonymous"
    role = (state.get("_role")
            or ("admin" if username in ("admin",) else "user"))
    try:
        from app.services.access_control import check_sql_permission
        allowed, reason = await check_sql_permission(repo.session, sql, role)
        if not allowed:
            return GuardReject(reason=reason, guard="rbac_tables")
    except Exception as e:
        logger.warning(f"rbac_tables_guard skipped (non-fatal): {e}")
    return None


async def pii_scan_guard(state: dict, sql: str,
                         ctx: dict) -> Optional[GuardAsk]:
    """Ontology PII scan; L>=3 columns suspend for approval (OPT-M4 gate).
    Skipped when the turn was already approved (resume path) - the
    approval decision is sticky for the session."""
    if state.get("_pii_approved"):
        return None
    ontology_repo = ctx.get("ontology_repository")
    if ontology_repo is None:
        meta_repo = ctx.get("meta_doris_repository")
        session = getattr(meta_repo, "session", None)
        if session is not None:
            try:
                from app.repositories.doris.ontology.ontology_repository import (
                    OntologyRepository,
                )
                ontology_repo = OntologyRepository(session)
            except Exception:
                ontology_repo = None
    if ontology_repo is None:
        return None
    try:
        from app.agent.pii import (
            check_pii_violations, DEFAULT_PII_APPROVAL_THRESHOLD,
        )
        violations = await check_pii_violations(
            sql, ontology_repo, threshold=DEFAULT_PII_APPROVAL_THRESHOLD,
        )
        if not violations:
            return None
        bullets = [
            f"{v.column_ref} (class={v.class_name}, "
            f"pii_level={v.effective_pii_level})"
            for v in violations[:5]
        ]
        reason = (f"SQL references {len(violations)} PII L>= "
                  f"{DEFAULT_PII_APPROVAL_THRESHOLD} column(s): "
                  + "; ".join(bullets))
        return GuardAsk(
            reason=reason, kind="approval",
            violations=[{
                "column_ref": v.column_ref, "class_name": v.class_name,
                "pii_level": v.effective_pii_level,
            } for v in violations[:10]],
            guard="pii_scan",
        )
    except Exception as e:
        logger.warning(f"pii_scan_guard skipped (non-fatal): {e}")
        return None


async def cost_estimate_guard(state: dict, sql: str,
                              ctx: dict) -> Optional[GuardRewrite]:
    """EXPLAIN-based cost classify; expensive scans downgrade via
    TABLESAMPLE (OPT-M5) as a rewrite that keeps the chain running."""
    repo = ctx.get("dw_doris_repository")
    if repo is None:
        return None
    try:
        from app.services.access_control import (
            estimate_query_cost, classify_cost, maybe_inject_sample,
        )
        cost_info = await estimate_query_cost(repo, sql)
        level, reason = classify_cost(cost_info)
        if level == "downgrade":
            new_sql = maybe_inject_sample(sql)
            if new_sql != sql:
                return GuardRewrite(sql=new_sql, reason=reason,
                                    guard="cost_estimate")
    except Exception as e:
        logger.warning(f"cost_estimate_guard skipped (non-fatal): {e}")
    return None



# --------------------------------------------------------------------- #
# PRD v2.0 SS3.4 rules (G002/G004/G005/G006/G007/G008).
# sqlglot-based where available, regex fallback; confirm-mapped to
# GuardAsk (flows through the existing approval machinery).
# --------------------------------------------------------------------- #
def _ast_tree(sql):
    try:
        import sqlglot
        return sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return None


def _ast_count(tree, type_name: str) -> int:
    """Count nodes of a sqlglot expression type by class name."""
    import sqlglot.expressions as exp
    cls = getattr(exp, type_name, None)
    if tree is None or cls is None:
        return 0
    return sum(1 for _ in tree.find_all(cls))


async def limit_rewrite_guard(state: dict, sql: str,
                              ctx: Optional[dict] = None):
    """G005: missing LIMIT -> auto-append LIMIT 10000 (rewrite)."""
    tree = _ast_tree(sql)
    has_limit = False
    if tree is not None:
        has_limit = _ast_count(tree, "Limit") > 0
    else:
        has_limit = re.search(r"\bLIMIT\b", sql, re.I) is not None
    if has_limit:
        return None
    rewritten = sql.rstrip().rstrip(";") + " LIMIT 10000"
    return GuardRewrite(sql=rewritten, guard="limit_rewrite",
                        reason="missing LIMIT, auto-appended LIMIT 10000 (G005)")


async def in_list_guard(state: dict, sql: str,
                        ctx: Optional[dict] = None):
    """G007: IN-list with >1000 elements -> block."""
    tree = _ast_tree(sql)
    if tree is not None:
        for inexpr in tree.find_all(__import__("sqlglot.expressions", fromlist=["In"]).In):
            try:
                if len(list(inexpr.expressions)) > 1000:
                    return GuardReject(
                        reason="IN list exceeds 1000 elements (G007)",
                        guard="in_list")
            except Exception:
                pass
        return None
    for m in re.finditer(r"IN\s*\(", sql, re.I):
        depth = 1; i = m.end(); n = 0
        while i < len(sql) and depth:
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 1 and ch == ",":
                n += 1
            i += 1
        if n > 1000:
            return GuardReject(reason="IN list exceeds 1000 elements (G007)",
                               guard="in_list")
    return None


async def join_count_guard(state: dict, sql: str,
                           ctx: Optional[dict] = None):
    """G006: >=3 JOINs (>=4 tables) -> confirm(ask)."""
    tree = _ast_tree(sql)
    if tree is not None:
        n_joins = _ast_count(tree, "Join")
    else:
        n_joins = len(re.findall(r"\bJOIN\b", sql, re.I))
    if n_joins >= 3:
        return GuardAsk(reason=f"cross {n_joins}-table JOIN, confirm (G006)",
                        guard="join_count")
    return None


async def select_star_guard(state: dict, sql: str,
                            ctx: Optional[dict] = None):
    """G004: SELECT * on a wide table (>100 cols) -> confirm(ask)."""
    if not re.search(r"\bSELECT\s+\*", sql, re.I):
        return None
    try:
        from app.clients.doris_client_manager import doris_client_manager
        tables = re.findall(
            r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_.]+)", sql, re.I)
        for t in tables[:3]:
            t_short = t.split(".")[-1].strip("`")
            db = t.split(".")[0] if "." in t else "dw"
            async with doris_client_manager.session_factory() as s2:
                n = (await s2.execute(text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema=:d AND table_name=:t"),
                    {"d": db, "t": t_short})).scalar()
                if n and n > 100:
                    return GuardAsk(
                        reason=f"{t_short} has {n} cols, "
                               f"SELECT * needs confirm (G004)",
                        guard="select_star")
    except Exception as e:
        logger.debug(f"select_star_guard meta probe failed: {e}")
    return None


async def partition_scan_guard(state: dict, sql: str,
                               ctx: Optional[dict] = None):
    """G002/G008: big table without dt filter / span >366d -> ask."""
    if state.get("complexity") == "fallback":
        return None
    try:
        from app.clients.doris_client_manager import doris_client_manager
        tables = re.findall(
            r"\bFROM\s+([A-Za-z0-9_.]+)", sql, re.I)
        if not tables:
            return None
        t = tables[0]
        t_short = t.split(".")[-1].strip("`")
        db = t.split(".")[0] if "." in t else "dw"
        has_dt = bool(re.search(
            r"\bdt\b\s*(?:=|BETWEEN|>=|>|IN)", sql, re.I))
        m = re.search(
            r"BETWEEN\s+'(\d{4}-\d{2}-\d{2})'"
            r"\s+AND\s+'(\d{4}-\d{2}-\d{2})'", sql, re.I)
        span_days = 0
        if m:
            try:
                from datetime import date as _d
                span_days = (_d.fromisoformat(m.group(2))
                             - _d.fromisoformat(m.group(1))).days
            except Exception:
                pass
        if has_dt and span_days <= 366:
            return None
        async with doris_client_manager.session_factory() as s2:
            rows = (await s2.execute(text(
                "SELECT table_rows FROM information_schema.tables "
                "WHERE table_schema=:d AND table_name=:t"),
                {"d": db, "t": t_short})).scalar()
        big = (rows or 0) > 10_000_000
        if big and not has_dt:
            return GuardAsk(
                reason=f"{t_short} ~{rows} rows, no dt partition filter (G002)",
                guard="partition_scan")
        if span_days > 366:
            return GuardAsk(
                reason=f"time span {span_days}d without partition pruning (G008)",
                guard="partition_scan")
        return None
    except Exception as e:
        logger.debug(f"partition_scan_guard skipped: {e}")
        return None


# Process-wide singleton; nodes and future call-sites share it.
guard_chain = GuardChain()


def register_builtin_guards() -> GuardChain:
    """Idempotent registration in FR2 order (import-safe)."""
    guard_chain.register("forbidden_keywords", forbidden_keywords_guard,
                         stage="pre_validate")
    guard_chain.register("rbac_tables", rbac_tables_guard,
                         stage="pre_validate")
    guard_chain.register("pii_scan", pii_scan_guard, stage="pii")
    guard_chain.register("cost_estimate", cost_estimate_guard, stage="cost")
    # PRD v2.0 SS3.4 additions (pre_validate stage).
    guard_chain.register("limit_rewrite", limit_rewrite_guard,
                         stage="pre_validate")
    guard_chain.register("in_list", in_list_guard, stage="pre_validate")
    guard_chain.register("join_count", join_count_guard, stage="pre_validate")
    guard_chain.register("select_star", select_star_guard, stage="pre_validate")
    guard_chain.register("partition_scan", partition_scan_guard,
                         stage="pre_validate")
    return guard_chain


# Register at import so the very first query is already guarded.
register_builtin_guards()
