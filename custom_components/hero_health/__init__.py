"""Hero Health integration setup and native service actions."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_CONFIG_ENTRY_ID as HA_ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api.exceptions import (
    HeroAuthenticationError,
    HeroConnectionError,
    HeroDispenseOutcomeUnknown,
)
from .const import (
    ATTR_SCHEDULED_DATETIME,
    DOMAIN,
    PLATFORMS,
    SERVICE_DISPENSE,
    SERVICE_REFRESH,
    HeroHealthRuntimeData,
)
from .coordinator import HeroCoordinator
from .dispense import evaluate_dispense_eligibility
from .session import HeroSession

type HeroHealthConfigEntry = ConfigEntry[HeroHealthRuntimeData]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register integration actions once, independently of loaded entries."""

    async def handle_refresh(call: ServiceCall) -> None:
        await _async_refresh(hass, call)

    async def handle_dispense(call: ServiceCall) -> None:
        await _async_dispense(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        handle_refresh,
        schema=vol.Schema({vol.Required(HA_ATTR_CONFIG_ENTRY_ID): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISPENSE,
        handle_dispense,
        schema=vol.Schema(
            {
                vol.Required(HA_ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Optional(ATTR_SCHEDULED_DATETIME): cv.string,
            }
        ),
    )
    return True


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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_refresh(hass: HomeAssistant, call: ServiceCall) -> None:
    await _coordinator_for_call(hass, call).async_request_refresh()


async def _async_dispense(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _coordinator_for_call(hass, call)
    async with coordinator.dispense_lock:
        await coordinator.async_request_refresh()
        requested = call.data.get(ATTR_SCHEDULED_DATETIME)
        evaluation = evaluate_dispense_eligibility(
            coordinator.data.get("doses"), dt_util.now(), requested
        )
        if not evaluation.eligible or not evaluation.scheduled_datetime:
            raise ServiceValidationError(
                "No eligible Hero scheduled dose is currently available",
                translation_domain=DOMAIN,
                translation_key="no_eligible_scheduled_dose",
                translation_placeholders={
                    "window_opens_at": (
                        evaluation.window_opens_at.isoformat()
                        if evaluation.window_opens_at
                        else ""
                    )
                },
            )
        selected = evaluation.scheduled_datetime
        last_id = await coordinator.session.async_last_dispense_id()
        unknown_check = getattr(
            coordinator.session, "async_dispense_outcome_unknown", None
        )
        unknown = await unknown_check(selected) if unknown_check else False
        if last_id == selected or unknown:
            raise ServiceValidationError(
                (
                    "The result of this scheduled dose is unknown; confirm the "
                    "dispenser state before trying again"
                    if unknown
                    else "This scheduled dose was already dispensed recently"
                ),
                translation_domain=DOMAIN,
                translation_key="duplicate_recent_dose",
            )
        try:
            await coordinator.session.async_execute(
                lambda client: client.dispense_scheduled_dose(
                    selected,
                    on_start_sent=(
                        lambda: coordinator.session.async_mark_dispense_start_sent(
                            selected
                        )
                    ),
                )
            )
        except HeroDispenseOutcomeUnknown as err:
            raise HomeAssistantError(
                "Hero may have started dispensing but did not confirm completion; "
                "do not retry this dose automatically"
            ) from err
        except HeroConnectionError as err:
            raise HomeAssistantError(
                "Unable to connect to Hero during the action",
                translation_domain=DOMAIN,
                translation_key="connection_failure",
            ) from err
        except HeroAuthenticationError as err:
            raise HomeAssistantError(
                "Hero authentication is required",
                translation_domain=DOMAIN,
                translation_key="authentication_required",
            ) from err
        await coordinator.session.async_save_dispense_id(selected)


def _coordinator_for_call(hass: HomeAssistant, call: ServiceCall) -> HeroCoordinator:
    entry_id = call.data.get(HA_ATTR_CONFIG_ENTRY_ID)
    if not entry_id:
        raise ServiceValidationError(
            "A Hero Health config entry is required",
            translation_domain=DOMAIN,
            translation_key="config_entry_not_found",
        )
    selected = hass.config_entries.async_get_entry(entry_id)
    if selected is None or getattr(selected, "domain", DOMAIN) != DOMAIN:
        raise ServiceValidationError(
            "The requested Hero Health config entry was not found",
            translation_domain=DOMAIN,
            translation_key="config_entry_not_found",
        )
    if selected.runtime_data is None:
        raise ServiceValidationError(
            "The requested Hero Health config entry is not loaded",
            translation_domain=DOMAIN,
            translation_key="config_entry_not_loaded",
        )
    return selected.runtime_data.coordinator


def _find_eligible_dose(home: dict, requested: str | None) -> str:
    """Backward-compatible wrapper around the shared eligibility evaluator."""
    evaluation = evaluate_dispense_eligibility(home, dt_util.now(), requested)
    if evaluation.eligible and evaluation.scheduled_datetime:
        return evaluation.scheduled_datetime
    raise ServiceValidationError(
        "No eligible Hero scheduled dose is currently available",
        translation_domain=DOMAIN,
        translation_key="no_eligible_scheduled_dose",
    )


async def async_unload_entry(hass: HomeAssistant, entry: HeroHealthConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.session.async_close()
    entry.runtime_data = None
    return ok
