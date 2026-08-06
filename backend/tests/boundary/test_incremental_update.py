"""Knowledge base incremental update boundary tests"""
import pytest
import inspect


# incremental update script exists
def test_incremental_script_exists():
    from app.scripts.incremental_update import incremental_update
    assert callable(incremental_update)


# supports the table_filter parameter
def test_incremental_supports_filter():
    sig = inspect.signature(incremental_update) if False else None
    from app.scripts.incremental_update import incremental_update as iu
    src = inspect.getsource(iu)
    assert 'table_filter' in src


# UPSERT logic (no full TRUNCATE)
def test_incremental_no_truncate():
    from app.scripts.incremental_update import incremental_update
    src = inspect.getsource(incremental_update)
    assert 'TRUNCATE' not in src
    assert 'UPSERT' in src or 'existing' in src.lower()


# incremental dimension value sync
def test_incremental_sync_new_values():
    from app.scripts.incremental_update import _sync_new_values
    assert callable(_sync_new_values)


# re-vectorization
def test_incremental_revectorize():
    from app.scripts.incremental_update import _revectorize_column
    assert callable(_revectorize_column)


# API endpoint
def test_incremental_api():
    from app.api.routers.admin_router import admin_router
    paths = [r.path for r in admin_router.routes]
    assert '/api/admin/knowledge/incremental' in paths


# CLI entry point
def test_incremental_cli():
    src = open('app/scripts/incremental_update.py', encoding='utf-8').read()
    assert '__main__' in src
    assert 'argparse' in src
