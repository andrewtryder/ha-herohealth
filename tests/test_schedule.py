"""Tests for pure Hero recurring schedule parser and timezone resolution."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

from custom_components.hero_health.schedule import (
    next_recurring_schedule,
    resolve_schedule_timezone,
)


def test_resolve_schedule_timezone_valid():
    tz = resolve_schedule_timezone("America/New_York")
    assert str(tz) == "America/New_York"


def test_resolve_schedule_timezone_invalid():
    tz = resolve_schedule_timezone("Invalid/Timezone_Name")
    assert tz == dt_util.DEFAULT_TIME_ZONE


def test_resolve_schedule_timezone_none_or_empty():
    assert resolve_schedule_timezone(None) == dt_util.DEFAULT_TIME_ZONE
    assert resolve_schedule_timezone("") == dt_util.DEFAULT_TIME_ZONE
    assert resolve_schedule_timezone("   ") == dt_util.DEFAULT_TIME_ZONE


def test_schedule_same_day_future():
    # 2026-09-07 Mon 07:00, schedule is Mon 08:00 -> today 2026-09-07 08:00
    now = datetime(2026, 9, 7, 7, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_today_time_passed_advances_to_next_week():
    # 2026-09-07 Mon 09:00, schedule is Mon 08:00 -> next Mon 2026-09-14 08:00
    now = datetime(2026, 9, 7, 9, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 14, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_tomorrow_occurrence():
    # Monday 09:00 -> Tuesday 08:00
    now = datetime(2026, 9, 7, 9, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Tue", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 8, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_sunday_to_monday_boundary():
    # 2026-09-13 is Sunday 22:00 -> Monday 2026-09-14 08:00
    now = datetime(2026, 9, 13, 22, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 14, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_midnight_boundary():
    # Sunday 23:59:59 -> Monday 00:00
    now = datetime(2026, 9, 13, 23, 59, 59, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "00:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 14, 0, 0, tzinfo=dt_util.UTC)


def test_schedule_multiple_weekdays():
    # Monday 10:00 -> Wed 08:00 (since Mon 08:00 passed)
    now = datetime(2026, 9, 7, 10, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon, Wed, Fri", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 9, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_duplicate_weekdays():
    # Duplicate day tokens should not produce duplicated or erroneous occurrences
    now = datetime(2026, 9, 7, 10, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {"schedule_id": "s1", "dow": "Mon, mon, Wed, WED", "time": "08:00"}
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 9, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_multiple_schedules_earliest_selected():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {"schedule_id": "s1", "dow": "Mon", "time": "12:00"},
            {"schedule_id": "s2", "dow": "Mon", "time": "08:00"},
            {"schedule_id": "s3", "dow": "Tue", "time": "07:00"},
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_duplicate_schedules_deduplicated():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {"schedule_id": "s1", "dow": "Mon", "time": "08:00"},
            {"schedule_id": "s2", "dow": "Mon", "time": "08:00"},
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_malformed_time_ignored():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {"schedule_id": "s1", "dow": "Mon", "time": "25:00"},  # impossible hour
            {"schedule_id": "s2", "dow": "Mon", "time": "08:61"},  # impossible minute
            {"schedule_id": "s3", "dow": "Mon", "time": "invalid"},
            {"schedule_id": "s4", "dow": "Mon", "time": "10:00"},  # valid
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 10, 0, tzinfo=dt_util.UTC)


def test_schedule_invalid_weekday_ignored():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {"schedule_id": "s1", "dow": "Funday, Unknown", "time": "08:00"},
            {"schedule_id": "s2", "dow": "Tue", "time": "08:00"},
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 8, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_missing_dow_or_time():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {"schedule_id": "s1", "time": "08:00"},
            {"schedule_id": "s2", "dow": "Mon"},
            {"schedule_id": "s3", "dow": "Mon", "time": "09:00"},
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 9, 0, tzinfo=dt_util.UTC)


def test_schedule_root_missing_or_wrong_type():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    assert next_recurring_schedule(None, now) is None
    assert next_recurring_schedule([], now) is None
    assert next_recurring_schedule("string", now) is None
    assert next_recurring_schedule({}, now) is None
    assert next_recurring_schedule({"schedules": "not a list"}, now) is None
    assert next_recurring_schedule({"schedules": []}, now) is None


def test_schedule_item_wrong_type():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            None,
            "not a dict",
            {"schedule_id": "s1", "dow": "Mon", "time": "08:00"},
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_pending_changes_true_suppresses_fallback():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "08:00"}],
        "pending_changes": True,
    }
    assert next_recurring_schedule(payload, now, dt_util.UTC) is None


def test_schedule_every_x_days_only_is_ignored():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [{"schedule_id": "s1", "every_x_days": 3, "time": "08:00"}],
        "pending_changes": False,
    }
    assert next_recurring_schedule(payload, now, dt_util.UTC) is None


def test_schedule_dow_with_every_x_days_uses_dow_semantics():
    now = datetime(2026, 9, 7, 6, 0, tzinfo=dt_util.UTC)
    payload = {
        "schedules": [
            {
                "schedule_id": "s1",
                "dow": "Mon",
                "every_x_days": 3,
                "time": "08:00",
            }
        ],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, dt_util.UTC)
    assert result == datetime(2026, 9, 7, 8, 0, tzinfo=dt_util.UTC)


def test_schedule_naive_now_localized_to_target_tz():
    now = datetime(2026, 9, 7, 6, 0)
    tz = ZoneInfo("America/New_York")
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, tz)
    assert result == datetime(2026, 9, 7, 8, 0, tzinfo=tz)


def test_schedule_dst_boundary_real_timezone():
    # In America/New_York, DST ends in November (e.g. 2026-11-01)
    tz = ZoneInfo("America/New_York")
    # Saturday before DST fall-back
    now = datetime(2026, 10, 31, 20, 0, tzinfo=tz)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Sun", "time": "08:00"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, tz)
    assert result == datetime(2026, 11, 1, 8, 0, tzinfo=tz)


def test_schedule_dst_spring_forward_gap():
    # America/New_York spring-forward occurs 2026-03-08, jumping 02:00 -> 03:00.
    # Schedule at 02:30 is a gap; zoneinfo preserves wall time with fold=0.
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 7, 20, 0, tzinfo=tz)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Sun", "time": "02:30"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, tz)
    assert result == datetime(2026, 3, 8, 2, 30, tzinfo=tz)


def test_schedule_dst_fall_back_ambiguity():
    # America/New_York fall-back occurs 2026-11-01, repeating 01:00 -> 02:00.
    # Schedule at 01:30 is ambiguous; zoneinfo resolves fold=0 (first occurrence).
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 10, 31, 20, 0, tzinfo=tz)
    payload = {
        "schedules": [{"schedule_id": "s1", "dow": "Sun", "time": "01:30"}],
        "pending_changes": False,
    }
    result = next_recurring_schedule(payload, now, tz)
    assert result == datetime(2026, 11, 1, 1, 30, tzinfo=tz)
    assert result.fold == 0
