from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ColumnConfig:
    name: str
    role: str
    description: str
    alias: list[str] = field(default_factory=list)
    sync: bool = False


@dataclass
class TableConfig:
    name: str
    role: str
    description: str
    columns: list[ColumnConfig] = field(default_factory=list)


@dataclass
class MetricConfig:
    name: str
    description: str
    relevant_columns: list[str] = field(default_factory=list)
    alias: list[str] = field(default_factory=list)


@dataclass
class MetaConfig:
    tables: Optional[list[TableConfig]] = None
    metrics: Optional[list[MetricConfig]] = None


# --------------------------------------------------------------------------- #
# Canonical YAML loader (single source of truth)
# --------------------------------------------------------------------------- #
# Four copies of "open conf/meta_config_dw.yaml + yaml.safe_load" existed
# (gov_service / readiness_router / admin_router / build scripts), each
# with its own path resolution. Use this everywhere.
from pathlib import Path as _Path  # noqa: E402

CONF_DIR = _Path(__file__).parents[2] / "conf"
# mtime-based cache: avoids re-parsing the YAML on every governance call,
# while still picking up edits (MetaConfig admin writes) automatically.
_yaml_cache: dict = {"path": None, "mtime": None, "tables": None}


def load_meta_tables(filename: str = "meta_config_dw.yaml") -> list[dict]:
    """Load the `tables` list from a conf YAML as raw dicts, with
    mtime caching and graceful fallback to meta_config.yaml."""
    global _yaml_cache
    path = CONF_DIR / filename
    if not path.exists():
        path = CONF_DIR / "meta_config.yaml"
    if not path.exists():
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if (_yaml_cache["path"] == str(path)
            and _yaml_cache["mtime"] == mtime
            and _yaml_cache["tables"] is not None):
        return _yaml_cache["tables"]
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tables = data.get("tables", []) or []
    _yaml_cache = {"path": str(path), "mtime": mtime, "tables": tables}
    return tables
