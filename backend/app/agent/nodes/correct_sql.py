"""SQL correction node.

Post-correction PII gate (Phase 4 PRD §6.2):
  After the LLM rewrites the SQL, we scan the corrected SQL for any
  column that maps to a high-PII ontology class. When found, we set
  `state.needs_approval = True` (with `approval_reason`) so downstream
  nodes / the UI can require human review before execution. We do NOT
  block execution here - that decision belongs to a graph-level
  conditional edge (the PRD leaves that to dev-prd/P1-04 信任分级).
"""
import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.agent.nodes.generate_sql import _clean_sql
from app.agent.pii import check_pii_violations, DEFAULT_PII_APPROVAL_THRESHOLD
from app.core.log import logger
from app.core.metrics import SQL_CORRECTED, PII_GATE_DECISIONS
from app.prompt.prompt_loader import load_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Correct SQL"})
    sql = state.get("sql", "")
    if not sql or not sql.strip():
        logger.info("Empty SQL, skipping correction")
        return {"sql": sql, "error": None,
                "needs_approval": False, "approval_reason": ""}

    try:
        prompt = PromptTemplate(
            template=load_prompt("correct_sql"),
            input_variables=["query", "table_infos", "metric_infos",
                             "date_info", "db_info", "error", "sql"],
        )
        chain = prompt | llm | StrOutputParser()
        sql = await chain.ainvoke({
            "query": state["query"],
            "table_infos": yaml.dump(state.get("table_infos", []), allow_unicode=True, sort_keys=False),
            "metric_infos": yaml.dump(state.get("metric_infos", []), allow_unicode=True, sort_keys=False),
            "date_info": yaml.dump(state.get("date_info", {}), allow_unicode=True, sort_keys=False),
            "db_info": yaml.dump(state.get("db_info", {}), allow_unicode=True, sort_keys=False),
            "error": state.get("error", ""),
            "sql": state.get("sql", ""),
        })
        sql = _clean_sql(sql)
        logger.info("SQL correction done")
        SQL_CORRECTED.inc()

        # dash-style learnings: persist pitfall->fix so generation can
        # avoid the same mistake next time (non-fatal).
        try:
            from app.services.sql_learning_store import save_learning
            meta_repo = runtime.context.get("meta_doris_repository")
            if meta_repo is not None and sql:
                await save_learning(
                    meta_repo.session,
                    question=state.get("query", ""),
                    error_msg=state.get("error", "") or "validation failed",
                    wrong_sql=state.get("sql", ""),
                    fixed_sql=sql)
        except Exception as le:
            logger.debug(f"learning save skipped: {le}")

        # ============================================================ #
        # Post-correction PII gate (PRD §6.2)
        # ============================================================ #
        # We always return needs_approval; default False unless a
        # violation is found. Repo lookup failures degrade to "no
        # violation" so the pipeline still runs without the ontology.
        # M6 guard chain (thin shell): pii_scan guard, stage="pii".
        # GuardAsk == the old _scan_pii "blocked" outcome.
        from app.core.guard_chain import guard_chain
        ctx = dict(runtime.context)
        ontology_repo = _coerce_ontology_repo(
            runtime.context.get("meta_doris_repository"))
        if ontology_repo is not None:
            ctx["ontology_repository"] = ontology_repo
        verdict = await guard_chain.run(state, sql, ctx, stages=["pii"])
        needs_approval = verdict.asked
        reason = verdict.reason if verdict.asked else ""

        if needs_approval:
            PII_GATE_DECISIONS.labels(outcome="blocked").inc()
            logger.warning(f"PII gate triggered: {reason}")
            writer({"pii_gate": {"status": "blocked", "reason": reason}})
        else:
            PII_GATE_DECISIONS.labels(outcome="passed").inc()

        return {"sql": sql, "error": None,
                "needs_approval": needs_approval,
                "approval_reason": reason}
    except Exception as e:
        logger.error(f"SQL correction error: {e}")
        raise


async def _scan_pii(sql, ontology_repo) -> tuple[bool, str]:
    """Run the PII check; return (needs_approval, reason). On any error
    or missing repo, return (False, "") so the pipeline keeps flowing."""
    if ontology_repo is None:
        PII_GATE_DECISIONS.labels(outcome="skipped").inc()
        return False, ""
    try:
        violations = await check_pii_violations(
            sql, ontology_repo, threshold=DEFAULT_PII_APPROVAL_THRESHOLD,
        )
    except Exception as e:
        logger.warning(f"PII scan failed (non-fatal): {e}")
        PII_GATE_DECISIONS.labels(outcome="skipped").inc()
        return False, ""
    if not violations:
        return False, ""
    # Build a concise human-readable reason.
    bullets = [
        f"{v.column_ref} (class={v.class_name}, pii_level={v.effective_pii_level})"
        for v in violations[:5]
    ]
    reason = (
        f"Corrected SQL references {len(violations)} PII L>= "
        f"{DEFAULT_PII_APPROVAL_THRESHOLD} column(s): " + "; ".join(bullets)
    )
    return True, reason


def _coerce_ontology_repo(meta_repo):
    """The meta_doris_repository slot is the MetaDorisRepository today.
    PII scans need OntologyRepository. Both share the same session, so
    we adapt on the fly. When the ontology isn't installed (DDL missing),
    calls into it simply return empty and the gate is a no-op."""
    if meta_repo is None:
        return None
    # If it's already an OntologyRepository, use directly.
    try:
        from app.repositories.doris.ontology.ontology_repository import OntologyRepository
        if isinstance(meta_repo, OntologyRepository):
            return meta_repo
    except Exception:
        pass
    # Otherwise adapt by reusing the existing session.
    session = getattr(meta_repo, "session", None)
    if session is None:
        return None
    try:
        from app.repositories.doris.ontology.ontology_repository import OntologyRepository
        return OntologyRepository(session)
    except Exception:
        return None
