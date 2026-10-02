"""Table-level access control for the semantic layer.

Adopted from the cross-project consensus (Cube #11574 row-level
filters, OpenMetadata #33833/33834 domain-scoped RBAC, SQLBot #1385
table/library-level control): a role must not reach tables outside its
grant, no matter how natural the question was.

ACL source (env TABLE_ACL_JSON, wildcard suffix supported):
    {"admin": ["*"], "viewer": ["ads_*", "dim_*"]}
Unknown roles default to the most restrictive known set; a missing role
in state (internal/legacy paths) is allowed through unchanged.
"""
import fnmatch
import json
import os

from app.core.log import logger

_DEFAULT_ACL = {
    "admin": ["*"],
    "viewer": ["ads_*", "dim_*"],
}


def _load_acl() -> dict:
    raw = os.getenv("TABLE_ACL_JSON", "")
    if raw:
        try:
            acl = json.loads(raw)
            if isinstance(acl, dict):
                return acl
        except Exception as e:
            logger.warning(f"TABLE_ACL_JSON invalid, using default: {e}")
    return _DEFAULT_ACL


def extract_tables(sql: str) -> "list[str]":
    """Top-level table names (bare, db-stripped, lowercased)."""
    import sqlglot
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
        names = set()
        for t in tree.find_all(lambda n: n.key in ("table", "dot")):
            try:
                if t.key == "table":
                    names.add(t.name.lower())
                else:  # db.table as dot expression
                    names.add(t.this.name.lower() if hasattr(t.this, "name") else "")
            except Exception:
                continue
        names.discard("")
        return sorted(names)
    except Exception as e:
        logger.debug(f"table extract parse failed: {e}")
        return []


def check_tables(role: str, tables: "list[str]") -> "list[str]":
    """Return the tables this role may NOT access (empty = allowed)."""
    if not role:
        return []
    acl = _load_acl()
    if role not in acl:
        # unknown role: most restrictive known policy
        return list(tables)
    patterns = acl[role]
    denied = []
    for t in tables:
        t = t.split(".")[-1].lower()
        if not any(fnmatch.fnmatch(t, pat.lower()) or pat == "*"
                   for pat in patterns):
            denied.append(t)
    return denied
