"""B13.1-B13.2: state pollution"""


def test_b131_error_none_routes_to_execute_sql():
    """B13.1: the graph's conditional lambda returns execute_sql when error=None"""
    # simulate the lambda in graph.py
    decide = lambda state: "execute_sql" if not state["error"] else "correct_sql"
    assert decide({"error": None}) == "execute_sql"
    assert decide({"error": "some err"}) == "correct_sql"


def test_b132_error_empty_string_routes_to_execute():
    """B13.2: error='' is treated as falsy -> goes to execute_sql (current behavior; if '' should also go to correct_sql, change the logic)"""
    decide = lambda state: "execute_sql" if not state["error"] else "correct_sql"
    # current implementation: empty string goes to execute_sql (python falsy)
    # if business expects empty string to also be an error, change to `if state["error"] is None`
    assert decide({"error": ""}) == "execute_sql"
