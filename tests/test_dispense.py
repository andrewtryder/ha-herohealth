"""Shared remote-dispense eligibility boundaries."""

from datetime import datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.hero_health.dispense import evaluate_dispense_eligibility


def _home(scheduled: datetime, state: str = "time_to_take") -> dict:
    return {
        "dates": [
            {
                "times": [
                    {
                        "scheduled_datetime": scheduled.isoformat(),
                        "doses": [{"state": state}],
                    }
                ]
            }
        ]
    }


@pytest.mark.parametrize(
    ("offset", "state", "eligible"),
    [
        (timedelta(minutes=-31), "time_to_take", False),
        (timedelta(minutes=-30), "time_to_take", True),
        (timedelta(), "time_to_take", True),
        (timedelta(hours=6), "time_to_take", True),
        (timedelta(hours=6, seconds=1), "time_to_take", False),
        (timedelta(), "not_ready", False),
    ],
)
def test_dispense_eligibility_boundaries(offset, state, eligible):
    now = datetime(2026, 9, 3, 12, tzinfo=dt_util.UTC)
    assert (
        evaluate_dispense_eligibility(_home(now - offset, state), now).eligible
        is eligible
    )


def test_dispense_eligibility_ignores_malformed_or_missing_dose():
    now = datetime(2026, 9, 3, 12, tzinfo=dt_util.UTC)
    assert not evaluate_dispense_eligibility({"dates": [{"times": [{}]}]}, now).eligible
    assert not evaluate_dispense_eligibility(
        {"dates": [{"times": [{"scheduled_datetime": "bad", "doses": []}]}]}, now
    ).eligible
    # Malformed day (not a dict) or slot (not a dict) are gracefully skipped
    assert not evaluate_dispense_eligibility(
        {"dates": ["not-a-dict", {"times": ["not-a-slot", {}]}]}, now
    ).eligible


def test_recurring_schedule_never_becomes_dispense_candidate():
    """Recurring fallback schedules must never participate in dispense eligibility."""
    now = datetime(2026, 9, 3, 12, tzinfo=dt_util.UTC)
    # A schedule payload passed instead of doses payload must evaluate to not eligible
    schedules_payload = {
        "schedules": [
            {"schedule_id": "s1", "dow": "Mon, Tue, Wed, Thu, Fri", "time": "12:00"}
        ],
        "pending_changes": False,
    }
    result = evaluate_dispense_eligibility(schedules_payload, now)
    assert result.eligible is False
    assert result.scheduled_datetime is None
    assert result.window_opens_at is None
