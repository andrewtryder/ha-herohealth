"""Hero Health button entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ATTR_CONFIG_ENTRY_ID, DOMAIN, SERVICE_DISPENSE
from .dispense import evaluate_dispense_eligibility
from .entity import HeroEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    """Set up the safety-gated dispense button."""
    coordinator = entry.runtime_data.coordinator
    add_entities([DispenseScheduledDoseButton(coordinator)])


class DispenseScheduledDoseButton(HeroEntity, ButtonEntity):
    """Dispense the currently eligible scheduled dose through the guarded service."""

    _attr_name = "Dispense scheduled dose"
    _attr_icon = "mdi:pill"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "dispense_scheduled_dose")

    @property
    def _evaluation(self):
        return evaluate_dispense_eligibility(
            (self.coordinator.data or {}).get("doses"), dt_util.now()
        )

    @property
    def available(self) -> bool:
        """Expose the button only while the same safety rule reports eligibility."""
        return super().available and self._evaluation.eligible

    @property
    def extra_state_attributes(self):
        evaluation = self._evaluation
        return {
            "scheduled_datetime": evaluation.scheduled_datetime,
            "window_opens_at": evaluation.window_opens_at,
            "window_closes_at": evaluation.window_closes_at,
        }

    async def async_press(self) -> None:
        """Delegate dispensing to the service, which refreshes and revalidates state."""
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_DISPENSE,
            {ATTR_CONFIG_ENTRY_ID: self.coordinator.entry.entry_id},
            blocking=True,
        )
