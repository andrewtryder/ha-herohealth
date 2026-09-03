"""Hero Health binary sensors."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .dispense import evaluate_dispense_eligibility
from .entity import HeroEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data.coordinator
    add_entities(
        [
            ConnectivitySensor(c),
            DispenseAvailableSensor(c),
            *[SlotLowSensor(c, n) for n in range(1, 11)],
        ]
    )


class ConnectivitySensor(HeroEntity, BinarySensorEntity):
    _attr_name = "Dispenser connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connectivity")

    @property
    def is_on(self):
        return not bool(self.coordinator.data["offline"].get("hero_offline", True))


class DispenseAvailableSensor(HeroEntity, BinarySensorEntity):
    """Expose the service's exact safety eligibility as read-only observability."""

    _attr_translation_key = "dispense_available"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "dispense_available")

    @property
    def _evaluation(self):
        return evaluate_dispense_eligibility(
            self.coordinator.data.get("doses"), dt_util.now()
        )

    @property
    def is_on(self):
        return self._evaluation.eligible

    @property
    def extra_state_attributes(self):
        evaluation = self._evaluation
        return {
            "scheduled_datetime": evaluation.scheduled_datetime,
            "window_opens_at": evaluation.window_opens_at,
            "window_closes_at": evaluation.window_closes_at,
        }


class SlotLowSensor(HeroEntity, BinarySensorEntity):
    def __init__(self, c, slot):
        super().__init__(c, f"slot_{slot}_low")
        self.slot = slot
        self._attr_name = f"Slot {slot} low"

    @property
    def is_on(self):
        return bool(
            next(
                (
                    m.get("is_low")
                    for m in self.coordinator.data["medications"]
                    if m.get("slot") == self.slot
                ),
                False,
            )
        )
