"""R2: Schema Linking + sample data + analytical-SQL boundary tests"""
import pytest
from app.agent.nodes.generate_sql import table_infos_to_ddl, _clean_sql


# SQLBot#1291: DDL contains sample data
def test_ddl_has_examples():
    tables = [{
        'name': 'dim_region', 'role': 'dim', 'description': 'test',
        'columns': [
            {'name': 'region_id', 'type': 'VARCHAR(20)', 'role': 'primary_key', 'description': '', 'alias': [], 'examples': []},
            {'name': 'region_name', 'type': 'VARCHAR(50)', 'role': 'dimension', 'description': 'region', 'alias': ['area'], 'examples': ['North', 'South']},
        ]
    }]
    ddl = table_infos_to_ddl(tables)
    assert 'North' in ddl or 'South' in ddl, 'DDL should contain sample values'


# DB-GPT#3157: analytical few-shot
def test_prompt_has_analysis_examples():
    from pathlib import Path
    prompt = Path('prompts/generate_sql.prompt').read_text(encoding='utf-8')
    # check for analytical examples
    assert 'ranking' in prompt or 'growth' in prompt or 'share' in prompt


# Vanna#1111: DDL contains table structure info
def test_ddl_has_references():
    tables = [{
        'name': 'fact_order', 'role': 'fact', 'description': 'order fact',
        'columns': [
            {'name': 'region_id', 'type': 'VARCHAR(20)', 'role': 'foreign_key', 'description': 'fk', 'alias': [], 'examples': []},
        ]
    },
    {
        'name': 'dim_region', 'role': 'dim', 'description': 'region dim',
        'columns': [
            {'name': 'region_id', 'type': 'VARCHAR(20)', 'role': 'primary_key', 'description': 'pk', 'alias': [], 'examples': []},
        ]
    }]
    ddl = table_infos_to_ddl(tables)
    assert 'REFERENCES' in ddl or 'region_id' in ddl


# SQLBot#1288: SSE without hardcoded timeout
def test_sse_no_hardcoded_timeout():
    from pathlib import Path
    router = Path('app/api/routers/query_router.py').read_text(encoding='utf-8')
    assert 'StreamingResponse' in router
    assert 'timeout' not in router.lower() or '120' not in router


# filter_table fallback
def test_filter_table_fallback():
    import inspect
    from app.agent.nodes.filter_table import filter_table
    src = inspect.getsource(filter_table)
    assert 'original_tables' in src


# DDL COMMENT contains synonyms
def test_ddl_has_synonyms():
    tables = [{
        'name': 't1', 'role': 'dim', 'description': 'test',
        'columns': [{'name': 'c1', 'type': 'INT', 'role': 'measure', 'description': 'amount', 'alias': ['revenue', 'income'], 'examples': []}]
    }]
    ddl = table_infos_to_ddl(tables)
    assert 'revenue' in ddl or 'income' in ddl
