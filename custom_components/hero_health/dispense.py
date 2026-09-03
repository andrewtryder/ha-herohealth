"""Pure, shared safety evaluation for scheduled-dose dispensing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .const import DISPENSE_EARLY_WINDOW, DISPENSE_LATE_WINDOW
from .entity import parse_hero_datetime


@dataclass(frozen=True, slots=True)
class DispenseEligibility:
    """The non-sensitive result of evaluating one Hero scheduled dose."""

    eligible: bool
    scheduled_datetime: str | None = None
    window_opens_at: datetime | None = None
    window_closes_at: datetime | None = None
    reason: str | None = None


def evaluate_dispense_eligibility(
    home: dict[str, Any] | None, now: datetime, requested: str | None = None
) -> DispenseEligibility:
    """Find the earliest Hero-approved dose currently inside the safety window."""
    candidates: list[tuple[datetime, str]] = []
    next_scheduled: tuple[datetime, str] | None = None
    for day in (home or {}).get("dates", []):
        if not isinstance(day, dict):
            continue
        for slot in day.get("times", []):
            if not isinstance(slot, dict):
                continue
            value = slot.get("scheduled_datetime")
            if not isinstance(value, str) or (requested and value != requested):
                continue
            try:
                scheduled = parse_hero_datetime(value)
            except TypeError, ValueError, AttributeError:
                continue
            if scheduled >= now and (
                next_scheduled is None or scheduled < next_scheduled[0]
            ):
                next_scheduled = (scheduled, value)
            if not any(
                isinstance(dose, dict) and dose.get("state") == "time_to_take"
                for dose in slot.get("doses", [])
            ):
                continue
            opens_at = scheduled - DISPENSE_EARLY_WINDOW
            closes_at = scheduled + DISPENSE_LATE_WINDOW
            if opens_at <= now <= closes_at:
                candidates.append((scheduled, value))
    if candidates:
        scheduled, value = min(candidates)
        return DispenseEligibility(
            True,
            value,
            scheduled - DISPENSE_EARLY_WINDOW,
            scheduled + DISPENSE_LATE_WINDOW,
        )
    if next_scheduled:
        scheduled, _ = next_scheduled
        return DispenseEligibility(
            False,
            window_opens_at=scheduled - DISPENSE_EARLY_WINDOW,
            window_closes_at=scheduled + DISPENSE_LATE_WINDOW,
            reason="not_eligible",
        )
    return DispenseEligibility(False, reason="not_eligible")
