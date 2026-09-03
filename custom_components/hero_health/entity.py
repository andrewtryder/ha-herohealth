"""Shared entity helpers and pure Hero data rules."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .coordinator import HeroCoordinator


def is_low_medication(level_enum: str | None, calculated: str | float | None) -> bool:
    """Preserve Worker semantics: intentionally do not classify `midlow` as low."""
    known_low = {"low", "alert", "empty"}
    if (level_enum or "").lower() in known_low or str(
        calculated or ""
    ).lower() in known_low:
        return True
    try:
        return float(calculated) < 0.25
    except TypeError, ValueError:
        return False


def parse_hero_datetime(value: str) -> datetime:
    """Parse offsets as supplied; naive Hero times are HA-local by assumption."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    return parsed if parsed.tzinfo else dt_util.as_local(parsed)


class HeroEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HeroCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def device_info(self):
        return self.coordinator.device_info
