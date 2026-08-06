"""Agent readiness smoke tests.

Verifies the entire NL2SQL pipeline is operational:
  1. Database connection (ping)
  2. Schema loading (list tables + describe)
  3. Query execution (basic SELECT)
  4. Embedding service (call embedding API)
  5. LLM connection (simple invoke)

All 5 must pass for the agent to be considered ready.
"""
import asyncio
import time
from dataclasses import dataclass
from app.core.log import logger


@dataclass
class SmokeResult:
    name: str
    passed: bool
    elapsed_ms: float
    detail: str = ""
    error: str = ""


class AgentSmokeTest:
    """Agent end-to-end smoke test."""

    def __init__(self, session_factory, embedding_client, llm_client, milvus_client):
        self.session_factory = session_factory
        self.embedding_client = embedding_client
        self.llm_client = llm_client
        self.milvus_client = milvus_client

    async def _check_db_connection(self) -> SmokeResult:
        """Test 1: Database connection."""
        start = time.time()
        try:
            async with self.session_factory() as session:
                from sqlalchemy import text
                result = await session.execute(text("SELECT 1 AS ping"))
                row = result.scalar()
                elapsed = (time.time() - start) * 1000
                return SmokeResult(
                    name="Database Connection",
                    passed=(row == 1),
                    elapsed_ms=round(elapsed, 1),
                    detail=f"Ping returned: {row}"
                )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Database Connection", passed=False, elapsed_ms=round(elapsed, 1), error=str(e)[:100])

    async def _check_schema_loading(self) -> SmokeResult:
        """Test 2: Schema loading — can list tables and describe one."""
        start = time.time()
        try:
            async with self.session_factory() as session:
                from sqlalchemy import text
                # List tables
                result = await session.execute(text("SHOW TABLES"))
                tables = [row[0] for row in result.fetchall()]
                if not tables:
                    raise RuntimeError("No tables found in database")

                # Describe first business table
                for t in tables:
                    if t.startswith(("dim_", "dwd_", "dws_", "ads_", "fact_")):
                        result = await session.execute(text(f"DESCRIBE `{t}`"))
                        cols = result.fetchall()
                        elapsed = (time.time() - start) * 1000
                        return SmokeResult(
                            name="Schema Loading",
                            passed=True,
                            elapsed_ms=round(elapsed, 1),
                            detail=f"{len(tables)} tables, sample: {t}({len(cols)} cols)"
                        )
                elapsed = (time.time() - start) * 1000
                return SmokeResult(name="Schema Loading", passed=True, elapsed_ms=round(elapsed, 1),
                                   detail=f"{len(tables)} tables found")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Schema Loading", passed=False, elapsed_ms=round(elapsed, 1), error=str(e)[:100])

    async def _check_query_execution(self) -> SmokeResult:
        """Test 3: Query execution — basic SELECT."""
        start = time.time()
        try:
            async with self.session_factory() as session:
                from sqlalchemy import text
                result = await session.execute(text("SELECT 1 + 2 AS calc"))
                row = result.scalar()
                elapsed = (time.time() - start) * 1000
                return SmokeResult(
                    name="Query Execution",
                    passed=(row == 3),
                    elapsed_ms=round(elapsed, 1),
                    detail=f"1+2 = {row}"
                )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Query Execution", passed=False, elapsed_ms=round(elapsed, 1), error=str(e)[:100])

    async def _check_embedding(self) -> SmokeResult:
        """Test 4: Embedding service — generate a test embedding."""
        start = time.time()
        try:
            if hasattr(self.embedding_client, 'client') and self.embedding_client.client:
                vec = self.embedding_client.client.embed_query("test connection")
                elapsed = (time.time() - start) * 1000
                return SmokeResult(
                    name="Embedding Service",
                    passed=(vec is not None and len(vec) > 0),
                    elapsed_ms=round(elapsed, 1),
                    detail=f"Vector dimension: {len(vec)}"
                )
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Embedding Service", passed=False, elapsed_ms=round(elapsed, 1),
                               error="Embedding client not initialized")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Embedding Service", passed=False, elapsed_ms=round(elapsed, 1), error=str(e)[:100])

    async def _check_llm(self) -> SmokeResult:
        """Test 5: LLM connection — simple invoke."""
        start = time.time()
        try:
            from langchain_core.messages import HumanMessage
            resp = await self.llm_client.ainvoke([HumanMessage(content="Say 'OK' if you can read this.")])
            content = resp.content.strip() if hasattr(resp, 'content') else str(resp)
            elapsed = (time.time() - start) * 1000
            passed = len(content) > 0 and "error" not in content.lower()
            return SmokeResult(
                name="LLM Connection",
                passed=passed,
                elapsed_ms=round(elapsed, 1),
                detail=f"Response: {content[:50]}"
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="LLM Connection", passed=False, elapsed_ms=round(elapsed, 1), error=str(e)[:100])

    async def _check_milvus(self) -> SmokeResult:
        """Test 6: Milvus connection — search test."""
        start = time.time()
        try:
            if hasattr(self.milvus_client, 'connected') and self.milvus_client.connected:
                elapsed = (time.time() - start) * 1000
                return SmokeResult(name="Milvus Connection", passed=True, elapsed_ms=round(elapsed, 1),
                                   detail="Connected")
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Milvus Connection", passed=False, elapsed_ms=round(elapsed, 1),
                               error="Milvus not connected")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SmokeResult(name="Milvus Connection", passed=False, elapsed_ms=round(elapsed, 1), error=str(e)[:100])

    async def run_all(self) -> list[SmokeResult]:
        """Run all smoke tests in parallel."""
        tasks = [
            self._check_db_connection(),
            self._check_schema_loading(),
            self._check_query_execution(),
            self._check_embedding(),
            self._check_llm(),
            self._check_milvus(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output = []
        for r in results:
            if isinstance(r, Exception):
                output.append(SmokeResult(name="Unknown", passed=False, elapsed_ms=0, error=str(r)[:100]))
            else:
                output.append(r)
        return output

    def compute_score(self, results: list[SmokeResult]) -> float:
        """Compute readiness score from smoke test results."""
        if not results:
            return 0.0
        passed = sum(1 for r in results if r.passed)
        return round(passed / len(results) * 100, 1)
