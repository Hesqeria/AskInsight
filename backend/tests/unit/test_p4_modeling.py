"""P4: modeling service tests (pure functions, no DB)."""
import pytest

from app.services.modeling_service import draft_ddl, review_ddl

SPEC = {
    "db": "dw", "table": "ads_test_demo_day", "table_type": "ads",
    "columns": [
        {"name": "dt", "type": "DATE", "comment": "date", "is_key": True},
        {"name": "region_name", "type": "VARCHAR(64)", "comment": "region", "is_key": True},
        {"name": "gmv", "type": "DECIMAL(18,2)", "comment": "metric"},
    ],
    "key_columns": ["dt", "region_name"],
}


def test_draft_compliant():
    ddl = draft_ddl(SPEC)
    assert "UNIQUE KEY(dt, region_name)" in ddl
    assert "DISTRIBUTED BY HASH(dt)" in ddl
    assert "dynamic_partition.enable" in ddl
    assert review_ddl(ddl) == []


def test_unique_key_cols_not_null():
    ddl = draft_ddl(SPEC)
    assert "dt DATE NOT NULL" in ddl
    assert "region_name VARCHAR(64) NOT NULL" in ddl
    assert "gmv DECIMAL(18,2) COMMENT" in ddl  # non-key nullable


def test_review_detects_stripped_not_null():
    ddl = draft_ddl(SPEC).replace("NOT NULL", "")
    v = review_ddl(ddl)
    assert any("DT" in x and "NOT NULL" in x for x in v)


def test_review_detects_missing_props():
    ddl = draft_ddl(SPEC).replace("dynamic_partition.enable", "x").replace("replication_num", "x_num")
    v = review_ddl(ddl)
    assert any("replication_num" in x for x in v)
    assert any("dynamic partition" in x for x in v)


def test_draft_rejects_bad_type():
    spec = dict(SPEC, columns=[{"name": "a", "type": "JSONB", "is_key": True}])
    with pytest.raises(ValueError, match="not allowed"):
        draft_ddl(spec)


def test_draft_rejects_bad_table_type():
    with pytest.raises(ValueError, match="table_type"):
        draft_ddl(dict(SPEC, table_type="ods2"))


def test_ods_uses_duplicate_key_no_partition():
    ddl = draft_ddl(dict(SPEC, table_type="ods", table="ods_test_src"))
    assert "DUPLICATE KEY" in ddl
    assert "PARTITION BY" not in ddl
