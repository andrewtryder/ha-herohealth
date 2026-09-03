"""Redacted diagnostics; no health or credential data is exported."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

TO_REDACT = {
    "email",
    "password",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "account_id",
    "user_id",
    "name",
    "device_nickname",
    "medications",
    "exact_pill_count",
    "pill_level_calculated",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data.coordinator.data
    return async_redact_data(
        {"entry": dict(entry.data), "coordinator": data}, TO_REDACT
    )
