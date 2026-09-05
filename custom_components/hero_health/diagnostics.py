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

    doses = data.get("doses")
    dates = doses.get("dates", []) if isinstance(doses, dict) else []
    slot_count = sum(
        len(day.get("times", []))
        for day in dates
        if isinstance(day, dict) and isinstance(day.get("times"), list)
    )
    medications = data.get("medications")
    options = getattr(entry, "options", {})
    scan_interval = options.get("scan_interval") if isinstance(options, dict) else None
    return {
        "entry": {
            "has_unique_id": bool(getattr(entry, "unique_id", None)),
            "scan_interval": (
                scan_interval
                if isinstance(scan_interval, int)
                and not isinstance(scan_interval, bool)
                else None
            ),
        },
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "offline": {"usable": isinstance(data.get("offline"), dict)},
            "status": {"usable": isinstance(data.get("status"), dict)},
            "doses": {"usable": isinstance(doses, dict), "slot_count": slot_count},
            "medications": {
                "usable": isinstance(medications, list),
                "count": len(medications) if isinstance(medications, list) else 0,
            },
            "events": {"usable": isinstance(data.get("events"), dict)},
            "stats": {"usable": isinstance(data.get("stats"), dict)},
            "schedules": {"usable": isinstance(data.get("schedules"), dict)},
        },
    }
