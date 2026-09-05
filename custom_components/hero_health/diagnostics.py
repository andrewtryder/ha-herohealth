"""Redacted diagnostics; no health or credential data is exported."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return operational metadata only; never a redacted raw API snapshot."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data or {}

    def mapping_summary(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"usable": False, "type": type(value).__name__}
        return {"usable": True, "keys": sorted(str(key) for key in value)[:20]}

    doses = data.get("doses")
    dates = doses.get("dates", []) if isinstance(doses, dict) else []
    slot_count = sum(
        len(day.get("times", []))
        for day in dates
        if isinstance(day, dict) and isinstance(day.get("times"), list)
    )
    medications = data.get("medications")
    return {
        "entry": {
            "has_unique_id": bool(getattr(entry, "unique_id", None)),
            "options": dict(getattr(entry, "options", {})),
        },
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "offline": mapping_summary(data.get("offline")),
            "status": mapping_summary(data.get("status")),
            "doses": {"usable": isinstance(doses, dict), "slot_count": slot_count},
            "medications": {
                "usable": isinstance(medications, list),
                "count": len(medications) if isinstance(medications, list) else 0,
            },
            "events": mapping_summary(data.get("events")),
            "stats": mapping_summary(data.get("stats")),
        },
    }
