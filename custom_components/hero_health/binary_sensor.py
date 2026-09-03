"""Hero Health binary sensors."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import HeroEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data.coordinator
    add_entities([ConnectivitySensor(c), *[SlotLowSensor(c, n) for n in range(1, 11)]])


class ConnectivitySensor(HeroEntity, BinarySensorEntity):
    _attr_name = "Dispenser connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connectivity")

    @property
    def is_on(self):
        return not bool(self.coordinator.data["offline"].get("hero_offline", True))


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
