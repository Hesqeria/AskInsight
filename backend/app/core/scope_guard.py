"""Role-based table access control.

Enterprise scenario: Finance should not see HR tables.

scope is set in meta_config (table level) and JWT (user level).
Tables without explicit scope default to all roles.
"""
from app.core.log import logger

# Default scope: all users can access
DEFAULT_SCOPE = "*"


def filter_tables_by_scope(table_infos: list[dict], user_scopes: list[str]) -> list[dict]:
    """Filter tables based on user's scope.

    Args:
        table_infos: list of table dicts with optional 'scope' field
        user_scopes: list of scope strings from JWT, e.g. ['sales', 'finance']

    Returns:
        filtered table_infos
    """
    if not user_scopes or "*" in user_scopes:
        return table_infos

    user_scope_set = set(user_scopes)
    filtered = []
    for t in table_infos:
        table_scopes = t.get("scope", [])
        if not table_scopes:
            # No scope = visible to all
            filtered.append(t)
        elif any(s in table_scopes for s in user_scope_set):
            filtered.append(t)
        else:
            logger.debug(f"Scope filtered: {t['name']} not in {user_scopes}")

    if len(filtered) < len(table_infos):
        logger.info(f"Scope filter: {len(table_infos)} -> {len(filtered)} tables for scope {user_scopes}")
    return filtered


def validate_table_scope(table_name: str, table_scopes: list, user_scopes: list) -> bool:
    """Check if a specific table is allowed for the user's scope.

    Used by validate_sql_safety as a second layer of defense.
    """
    if not user_scopes or "*" in user_scopes:
        return True
    if not table_scopes:
        return True
    return any(s in table_scopes for s in user_scopes)
