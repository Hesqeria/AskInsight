"""ETL lineage synchronization script.

Scans a directory of ETL scripts (.sql / .py / .sh), parses each with
the LineageParser (LLM-driven with rule-based fallback), and upserts
the resulting edges into `lineage_technical`.

Designed to be invoked from:
  - An Airflow DAG (airflow/dags/lineage_sync_dag.py)
  - A k8s CronJob
  - A manual `python -m app.scripts.lineage_sync` run

Idempotent: re-running on unchanged scripts produces no new rows
(lineage_id is a stable hash of src+dst+transform). Re-running on
changed scripts overwrites the previous edges for that file.

Exit codes (for Airflow/K8s observability):
  0 - success (or nothing to do)
  1 - partial failure (some files failed but the run completed)
  2 - fatal misconfiguration (couldn't even start)
"""
import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.core.log import logger


# ------------------------------------------------------------------ #
# Configuration
# ------------------------------------------------------------------ #
@dataclass
class SyncConfig:
    etl_root: Path
    extensions: tuple[str, ...] = (".sql", ".py", ".sh")
    # Skip files larger than this (bytes) - LLM context + cost guard.
    max_file_bytes: int = 100_000
    # Skip files matching any of these substrings (test fixtures etc).
    # Paths are normalized to forward slashes before matching so the
    # rules work cross-platform (Windows uses backslashes by default).
    # Note: filename-style patterns (test_* / *_test.py) are matched on
    # the basename only - see discover_etl_files - so we don't include
    # them here to avoid double-matching on pytest tmp dirs.
    exclude_substrings: tuple[str, ...] = (
        "__pycache__",
        "/tests/",            # tests directory
        ".git",
    )
    # Set False to skip the LLM (rule baseline only) - useful for CI.
    use_llm: bool = True
    # Cap per-run cost. 0 = unlimited.
    max_files_per_run: int = 0


@dataclass
class FileResult:
    path: Path
    code_type: str
    parsed_edges: int
    persisted_edges: int
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ------------------------------------------------------------------ #
# Filesystem scan
# ------------------------------------------------------------------ #
def discover_etl_files(cfg: SyncConfig) -> list[Path]:
    """Walk `etl_root` and return the list of files to parse.

    Honors extensions + size + exclusion rules. Sorted for deterministic
    execution order (helps reproducibility in CI)."""
    if not cfg.etl_root.exists():
        logger.warning(f"ETL root does not exist: {cfg.etl_root}")
        return []

    out: list[Path] = []
    for path in cfg.etl_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in cfg.extensions:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > cfg.max_file_bytes:
            logger.info(f"skip (size {size} > {cfg.max_file_bytes}): {path}")
            continue
        # Exclusion rules. We match the file NAME for suffix-style rules
        # and the normalized PATH for directory-style rules. Matching the
        # full path with `/test_` would false-positive on pytest tmp dirs
        # like `/pytest-of-user/pytest-N/test_discover_X_0/`.
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            continue
        normalized = str(path).replace("\\", "/")
        if any(ex in normalized for ex in cfg.exclude_substrings):
            continue
        out.append(path)
    out.sort()
    if cfg.max_files_per_run > 0:
        out = out[: cfg.max_files_per_run]
    return out


def _classify(path: Path) -> str:
    """Map file extension to LineageParser code_type."""
    return {
        ".sql": "sql",
        ".py":  "python",
        ".sh":  "shell",
    }.get(path.suffix.lower(), "sql")


# ------------------------------------------------------------------ #
# Main sync coroutine
# ------------------------------------------------------------------ #
async def sync_file(
    path: Path, parser, ontology_repo, source_label: str,
) -> FileResult:
    """Parse one file and persist its edges."""
    code_type = _classify(path)
    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return FileResult(path=path, code_type=code_type,
                          parsed_edges=0, persisted_edges=0, error=str(e))

    try:
        edges = await parser.parse(code, code_type=code_type)
    except Exception as e:
        # parse() already catches internal errors, but be defensive.
        return FileResult(path=path, code_type=code_type,
                          parsed_edges=0, persisted_edges=0, error=str(e))

    persisted = 0
    if edges and ontology_repo is not None:
        try:
            persisted = await ontology_repo.upsert_technical_lineage_batch(
                edges, source=source_label,
            )
        except Exception as e:
            logger.warning(f"persist failed for {path}: {e}")

    return FileResult(
        path=path, code_type=code_type,
        parsed_edges=len(edges), persisted_edges=persisted,
    )


async def run_sync(cfg: SyncConfig) -> list[FileResult]:
    """Top-level entry: discover files, parse, persist, return per-file
    results.Caller (CLI / DAG / CronJob) decides logging policy."""
    # Lazy imports so this module is importable without Doris / LLM
    # configured (helps tests + Airflow DAG import-time validation).
    from app.agents.lineage_agent import LineageParser
    from app.infra.llm_router import get_llm
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.clients.doris_client_manager import doris_client_manager
    from app.repositories.doris.ontology.ontology_repository import OntologyRepository

    files = discover_etl_files(cfg)
    if not files:
        logger.info("lineage_sync: no ETL files found, nothing to do")
        return []

    logger.info(f"lineage_sync: parsing {len(files)} files (LLM={cfg.use_llm})")

    try:
        llm = get_llm() if cfg.use_llm else None
    except Exception:
        llm = None
    parser = LineageParser(llm=llm)

    results: list[FileResult] = []
    async with doris_client_manager.session_factory() as session:
        repo = OntologyRepository(session)
        source_label = "LLM" if (cfg.use_llm and llm is not None) else "RULE"
        for path in files:
            res = await sync_file(path, parser, repo, source_label)
            results.append(res)
            status = "OK" if res.ok else "FAIL"
            logger.info(
                f"  [{status}] {path} ({res.code_type}) "
                f"parsed={res.parsed_edges} persisted={res.persisted_edges}"
                + (f" err={res.error}" if res.error else "")
            )
    return results


# ------------------------------------------------------------------ #
# Summary helper
# ------------------------------------------------------------------ #
def summarize(results: Iterable[FileResult]) -> dict:
    """Aggregate per-file results into a run summary for logs/metrics."""
    results = list(results)
    return {
        "files_total": len(results),
        "files_ok": sum(1 for r in results if r.ok),
        "files_failed": sum(1 for r in results if not r.ok),
        "edges_parsed": sum(r.parsed_edges for r in results),
        "edges_persisted": sum(r.persisted_edges for r in results),
    }


# ------------------------------------------------------------------ #
# CLI entrypoint
# ------------------------------------------------------------------ #
def _parse_args(argv: list[str] | None = None) -> SyncConfig:
    p = argparse.ArgumentParser(description="Sync ETL lineage to lineage_technical")
    p.add_argument("--etl-root", required=True,
                   help="Root directory of ETL scripts to scan")
    p.add_argument("--no-llm", action="store_true",
                   help="Use rule-based parser only (no LLM calls)")
    p.add_argument("--max-files", type=int, default=0,
                   help="Cap number of files per run (0 = unlimited)")
    p.add_argument("--max-file-bytes", type=int, default=100_000,
                   help="Skip files larger than this (bytes)")
    args = p.parse_args(argv)
    return SyncConfig(
        etl_root=Path(args.etl_root),
        use_llm=not args.no_llm,
        max_files_per_run=args.max_files,
        max_file_bytes=args.max_file_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    cfg = _parse_args(argv)
    results = asyncio.run(run_sync(cfg))
    summary = summarize(results)
    logger.info(f"lineage_sync summary: {summary}")
    # Exit 0 if everything succeeded OR there was nothing to do.
    # Exit 1 if any file failed (DAG should still mark the run as
    # succeeded to avoid blocking on a single broken script; we surface
    # the partial failure via logs + the summary metric).
    return 0 if summary["files_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
