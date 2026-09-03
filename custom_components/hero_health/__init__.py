"""Hero Health integration setup and native service actions."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError

from .api.exceptions import HeroConnectionError
from .const import (
    ATTR_SCHEDULED_DATETIME,
    DOMAIN,
    PLATFORMS,
    SERVICE_DISPENSE,
    SERVICE_REFRESH,
    HeroHealthRuntimeData,
)
from .coordinator import HeroCoordinator
from .entity import parse_hero_datetime
from .session import HeroSession

type HeroHealthConfigEntry = ConfigEntry[HeroHealthRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: HeroHealthConfigEntry) -> bool:
    session = HeroSession(
        hass,
        entry.entry_id,
        entry.data["email"],
        entry.data["password"],
        entry.data.get("account_id"),
    )
    try:
        await session.async_initialize()
        coordinator = HeroCoordinator(hass, entry, session)
        await coordinator.async_config_entry_first_refresh()
    except HeroConnectionError as err:
        await session.async_close()
        raise ConfigEntryNotReady("Unable to connect to Hero") from err
    except Exception:
        await session.async_close()
        raise
    entry.runtime_data = HeroHealthRuntimeData(session=session, coordinator=coordinator)

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):

        async def handle_refresh(call: ServiceCall) -> None:
            await _async_refresh(hass, call)

        hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
    if not hass.services.has_service(DOMAIN, SERVICE_DISPENSE):

        async def handle_dispense(call: ServiceCall) -> None:
            await _async_dispense(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_DISPENSE,
            handle_dispense,
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_SCHEDULED_DATETIME): str,
                    vol.Optional("entry_id"): str,
                }
            ),
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_refresh(hass: HomeAssistant, call: ServiceCall) -> None:
    await _coordinator_for_call(hass, call).async_request_refresh()


async def _async_dispense(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _coordinator_for_call(hass, call)
    async with coordinator.dispense_lock:
        await coordinator.async_request_refresh()
        requested = call.data.get(ATTR_SCHEDULED_DATETIME)
        selected = _find_eligible_dose(coordinator.data["doses"], requested)
        last_id = await coordinator.session.async_last_dispense_id()
        if last_id == selected:
            raise ServiceValidationError(
                "This scheduled dose was already dispensed recently"
            )
        await coordinator.session.async_execute(
            lambda client: client.dispense_scheduled_dose(selected)
        )
        await coordinator.session.async_save_dispense_id(selected)


def _coordinator_for_call(hass: HomeAssistant, call: ServiceCall) -> HeroCoordinator:
    entry_id = call.data.get("entry_id")
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.runtime_data is not None
    ]
    if entry_id:
        selected = next(
            (entry for entry in entries if entry.entry_id == entry_id), None
        )
        if selected is None:
            raise ServiceValidationError(
                "The requested Hero Health entry is not loaded"
            )
        return selected.runtime_data.coordinator
    if len(entries) != 1:
        raise ServiceValidationError(
            "Specify entry_id when multiple Hero Health entries exist"
        )
    return entries[0].runtime_data.coordinator


def _find_eligible_dose(home: dict, requested: str | None) -> str:
    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    eligible: list[tuple[object, str]] = []
    for day in home.get("dates", []):
        for slot in day.get("times", []):
            value = slot.get("scheduled_datetime")
            if not value or (requested and value != requested):
                continue
            if not any(d.get("state") == "time_to_take" for d in slot.get("doses", [])):
                continue
            try:
                scheduled = parse_hero_datetime(value)
            except TypeError, ValueError:
                continue
            delta = (scheduled - now).total_seconds() / 60
            if -360 <= delta <= 30:
                eligible.append((scheduled, value))
    if eligible:
        return min(eligible, key=lambda candidate: candidate[0])[1]
    raise ServiceValidationError(
        "No eligible Hero scheduled dose is currently available"
    )


async def async_unload_entry(hass: HomeAssistant, entry: HeroHealthConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.session.async_close()
    entry.runtime_data = None
    if not any(
        config_entry.runtime_data is not None
        for config_entry in hass.config_entries.async_entries(DOMAIN)
    ):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
        hass.services.async_remove(DOMAIN, SERVICE_DISPENSE)
    return ok
