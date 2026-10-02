"""P5: gov task orchestration tests (pure scheduling logic)."""
from datetime import datetime, timedelta

from app.services.gov_task_service import is_due

NOW = datetime(2026, 9, 1, 12, 0, 0)


def test_due_when_never_run():
    assert is_due(None, 1440, NOW) is True


def test_due_when_interval_elapsed():
    assert is_due(NOW - timedelta(minutes=1441), 1440, NOW) is True
    assert is_due(NOW - timedelta(minutes=1440), 1440, NOW) is True


def test_not_due_within_interval():
    assert is_due(NOW - timedelta(minutes=1439), 1440, NOW) is False


def test_zero_interval_always_due():
    assert is_due(NOW, 0, NOW) is True


def test_string_last_run_parsed():
    past = (NOW - timedelta(hours=48)).isoformat()
    assert is_due(past, 1440, NOW) is True


def test_invalid_string_treated_due():
    assert is_due("garbage", 1440, NOW) is True
