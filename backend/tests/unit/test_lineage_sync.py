"""Lineage sync script tests.

Tests the pure-Python parts of `app.scripts.lineage_sync` (filesystem
scan + summarize + classification) without needing Doris or LLM. The
Airflow DAG wrapper is just a thin shell over these primitives, so
covering them covers the DAG behavior too.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scripts.lineage_sync import (
    SyncConfig, FileResult, discover_etl_files, _classify,
    sync_file, summarize,
)


# --------------------------------------------------------------------------- #
# Filesystem discovery
# --------------------------------------------------------------------------- #
def test_discover_finds_supported_extensions(tmp_path: Path):
    # Mix of supported + unsupported files.
    (tmp_path / "etl1.sql").write_text("SELECT 1")
    (tmp_path / "load.py").write_text("print('hi')")
    (tmp_path / "transform.sh").write_text("echo hi")
    (tmp_path / "ignore.md").write_text("# docs")
    (tmp_path / "data.csv").write_text("a,b")

    cfg = SyncConfig(etl_root=tmp_path)
    files = discover_etl_files(cfg)
    names = sorted(p.name for p in files)
    assert names == ["etl1.sql", "load.py", "transform.sh"]


def test_discover_respects_size_cap(tmp_path: Path):
    big = "x" * 5000
    (tmp_path / "big.sql").write_text(big)
    (tmp_path / "small.sql").write_text("SELECT 1")

    cfg = SyncConfig(etl_root=tmp_path, max_file_bytes=100)
    files = discover_etl_files(cfg)
    # big.sql skipped, small.sql kept.
    assert [p.name for p in files] == ["small.sql"]


def test_discover_excludes_tests_and_cache(tmp_path: Path):
    (tmp_path / "real.sql").write_text("SELECT 1")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_etl.py").write_text("def test_x(): pass")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "etl.cpython-310.pyc").write_text("binary")

    cfg = SyncConfig(etl_root=tmp_path)
    files = discover_etl_files(cfg)
    assert [p.name for p in files] == ["real.sql"]


def test_discover_caps_files_per_run(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}.sql").write_text("SELECT 1")
    cfg = SyncConfig(etl_root=tmp_path, max_files_per_run=2)
    files = discover_etl_files(cfg)
    assert len(files) == 2


def test_discover_missing_root_returns_empty(tmp_path: Path):
    cfg = SyncConfig(etl_root=tmp_path / "does_not_exist")
    assert discover_etl_files(cfg) == []


def test_discover_results_are_sorted(tmp_path: Path):
    # Create out of order so sort is observable.
    for name in ["c.sql", "a.sql", "b.sql"]:
        (tmp_path / name).write_text("SELECT 1")
    cfg = SyncConfig(etl_root=tmp_path)
    files = discover_etl_files(cfg)
    assert [p.name for p in files] == ["a.sql", "b.sql", "c.sql"]


# --------------------------------------------------------------------------- #
# File classification
# --------------------------------------------------------------------------- #
def test_classify_by_extension():
    assert _classify(Path("foo.sql")) == "sql"
    assert _classify(Path("foo.py")) == "python"
    assert _classify(Path("foo.sh")) == "shell"
    # Unknown extension defaults to sql (the most common ETL type).
    assert _classify(Path("foo.unknown")) == "sql"
    # Case-insensitive.
    assert _classify(Path("FOO.SQL")) == "sql"


# --------------------------------------------------------------------------- #
# sync_file
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sync_file_parses_and_persists(tmp_path: Path):
    """A normal file: parser returns N edges, repo persists them all."""
    f = tmp_path / "etl.sql"
    f.write_text("SELECT u.id FROM dim_user u")

    fake_parser = MagicMock()
    fake_parser.parse = AsyncMock(return_value=[
        MagicMock(lineage_id="L1"), MagicMock(lineage_id="L2"),
    ])
    fake_repo = MagicMock()
    fake_repo.upsert_technical_lineage_batch = AsyncMock(return_value=2)

    result = await sync_file(f, fake_parser, fake_repo, source_label="LLM")

    assert result.ok
    assert result.code_type == "sql"
    assert result.parsed_edges == 2
    assert result.persisted_edges == 2
    fake_repo.upsert_technical_lineage_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_file_handles_read_error(tmp_path: Path):
    """If the file can't be read, the result reports the error without
    crashing the whole sync run."""
    f = tmp_path / "gone.sql"  # never created
    fake_parser = MagicMock()
    fake_repo = MagicMock()
    result = await sync_file(f, fake_parser, fake_repo, source_label="LLM")
    assert not result.ok
    assert result.error is not None
    assert result.parsed_edges == 0


@pytest.mark.asyncio
async def test_sync_file_handles_parser_error(tmp_path: Path):
    f = tmp_path / "etl.sql"
    f.write_text("SELECT 1")
    fake_parser = MagicMock()
    fake_parser.parse = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    fake_repo = MagicMock()
    result = await sync_file(f, fake_parser, fake_repo, source_label="LLM")
    assert not result.ok
    assert "LLM timeout" in (result.error or "")


@pytest.mark.asyncio
async def test_sync_file_handles_persist_error(tmp_path: Path):
    """Persist failure is non-fatal for the per-file result; we still
    report parsed_edges so the operator knows what would have been written."""
    f = tmp_path / "etl.sql"
    f.write_text("SELECT 1")
    fake_parser = MagicMock()
    fake_parser.parse = AsyncMock(return_value=[MagicMock(lineage_id="L1")])
    fake_repo = MagicMock()
    fake_repo.upsert_technical_lineage_batch = AsyncMock(
        side_effect=RuntimeError("DB down"),
    )
    result = await sync_file(f, fake_parser, fake_repo, source_label="LLM")
    # Sync itself succeeded; persistence silently dropped to 0.
    assert result.ok
    assert result.parsed_edges == 1
    assert result.persisted_edges == 0


@pytest.mark.asyncio
async def test_sync_file_no_edges_no_persist_call(tmp_path: Path):
    """When the parser returns no edges, we skip the persist call entirely."""
    f = tmp_path / "etl.sql"
    f.write_text("-- just a comment")
    fake_parser = MagicMock()
    fake_parser.parse = AsyncMock(return_value=[])
    fake_repo = MagicMock()
    fake_repo.upsert_technical_lineage_batch = AsyncMock(return_value=0)
    result = await sync_file(f, fake_parser, fake_repo, source_label="LLM")
    assert result.parsed_edges == 0
    fake_repo.upsert_technical_lineage_batch.assert_not_awaited()


# --------------------------------------------------------------------------- #
# summarize
# --------------------------------------------------------------------------- #
def test_summarize_aggregates_counts():
    results = [
        FileResult(Path("a.sql"), "sql", 5, 5),
        FileResult(Path("b.py"), "python", 3, 3),
        FileResult(Path("c.sql"), "sql", 0, 0, error="read fail"),
    ]
    s = summarize(results)
    assert s == {
        "files_total": 3,
        "files_ok": 2,
        "files_failed": 1,
        "edges_parsed": 8,
        "edges_persisted": 8,
    }


def test_summarize_empty_results():
    s = summarize([])
    assert s == {
        "files_total": 0, "files_ok": 0, "files_failed": 0,
        "edges_parsed": 0, "edges_persisted": 0,
    }


# --------------------------------------------------------------------------- #
# SyncConfig defaults
# --------------------------------------------------------------------------- #
def test_sync_config_defaults():
    cfg = SyncConfig(etl_root=Path("/tmp"))
    assert ".sql" in cfg.extensions
    assert ".py" in cfg.extensions
    assert ".sh" in cfg.extensions
    assert cfg.use_llm is True
    assert cfg.max_files_per_run == 0  # unlimited


def test_file_result_ok_property():
    ok = FileResult(Path("x"), "sql", 1, 1)
    assert ok.ok is True
    bad = FileResult(Path("x"), "sql", 0, 0, error="fail")
    assert bad.ok is False
