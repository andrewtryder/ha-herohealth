"""Pure recurring schedule parser and timezone resolution for Hero Health."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.util import dt as dt_util

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DOW_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def resolve_schedule_timezone(device_timezone: str | None) -> tzinfo:
    """Resolve the effective schedule timezone with Home Assistant local fallback.

    Prefers the confirmed `user-status.device_timezone` string when valid.
    Falls back to Home Assistant's configured default timezone if the string
    is missing, empty, or unparseable.
    """
    if isinstance(device_timezone, str) and device_timezone.strip():
        tz = dt_util.get_time_zone(device_timezone.strip())
        if tz is not None:
            return tz
    return dt_util.DEFAULT_TIME_ZONE


def next_recurring_schedule(
    payload: Any,
    now: datetime,
    target_tz: tzinfo | None = None,
) -> datetime | None:
    """Compute the next recurring scheduled dose as an informational fallback.

    This helper inspects the confirmed `pills-by-schedules` payload schema:
    {
        "schedules": [
            {
                "schedule_id": str,
                "dow": str,          # e.g. "Mon, Tue, Wed, Thu, Fri, Sat, Sun"
                "time": str,         # e.g. "08:00" (strict 24-hour HH:MM)
                "every_x_days": int | None,
                "pills": [...]
            }
        ],
        "pending_changes": bool
    }

    Note: `every_x_days` recurrence is deliberately not supported because the
    anchor/reference date is unconfirmed. If a schedule entry contains `dow`,
    it is computed using weekly `dow` recurrence.

    This fallback is strictly informational for display and must NEVER participate
    in dispense eligibility, availability, preflight, or selection.
    """
    if not isinstance(payload, dict):
        return None

    # If changes are pending sync, do not expose a stale or changing schedule.
    if payload.get("pending_changes") is True:
        return None

    schedules = payload.get("schedules")
    if not isinstance(schedules, list) or not schedules:
        return None

    tz = target_tz or dt_util.DEFAULT_TIME_ZONE
    now_tz = now if now.tzinfo is not None else now.replace(tzinfo=tz)
    now_tz = now_tz.astimezone(tz)

    today = now_tz.date()
    now_weekday = now_tz.weekday()

    candidates: set[datetime] = set()

    for item in schedules:
        if not isinstance(item, dict):
            continue

        time_str = item.get("time")
        if not isinstance(time_str, str):
            continue

        match = TIME_RE.match(time_str.strip())
        if not match:
            continue

        hour, minute = int(match.group(1)), int(match.group(2))

        dow_str = item.get("dow")
        if not isinstance(dow_str, str):
            continue

        tokens = [t.strip().lower() for t in dow_str.split(",") if t.strip()]
        valid_days = {DOW_MAP[t] for t in tokens if t in DOW_MAP}
        if not valid_days:
            continue

        for target_weekday in valid_days:
            days_ahead = (target_weekday - now_weekday) % 7
            candidate_date = today + timedelta(days=days_ahead)
            candidate_dt = datetime.combine(
                candidate_date, time(hour, minute), tzinfo=tz
            )

            # If the occurrence for today has already passed, advance by 7 days.
            if candidate_dt <= now_tz:
                if days_ahead == 0:
                    candidate_date = today + timedelta(days=7)
                    candidate_dt = datetime.combine(
                        candidate_date, time(hour, minute), tzinfo=tz
                    )

            if candidate_dt > now_tz:
                candidates.add(candidate_dt)

    return min(candidates) if candidates else None
