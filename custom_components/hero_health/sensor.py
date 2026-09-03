"""Hero Health sensor entities."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfRatio
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HeroCoordinator
from .entity import HeroEntity, parse_hero_datetime


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    c: HeroCoordinator = entry.runtime_data.coordinator
    add_entities(
        [
            LowMedicationsSensor(c),
            AdherenceSensor(c),
            MetricSensor(c, "doses_taken", "Doses taken"),
            MetricSensor(c, "doses_missed", "Doses missed"),
            NextDoseSensor(c),
            *[SlotSensor(c, slot) for slot in range(1, 11)],
        ]
    )


class LowMedicationsSensor(HeroEntity, SensorEntity):
    _attr_name = "Low medications"

    def __init__(self, coordinator: HeroCoordinator) -> None:
        super().__init__(coordinator, "low_medications")

    @property
    def native_value(self):
        names = [
            m.get("name")
            for m in self.coordinator.data["medications"]
            if m["is_low"] and m.get("name")
        ]
        return ", ".join(names) if names else "None"

    @property
    def extra_state_attributes(self):
        meds = [m for m in self.coordinator.data["medications"] if m["is_low"]]
        return {
            "medications": [m["name"] for m in meds if m.get("name")],
            "slots": [m.get("slot") for m in meds],
            "count": len(meds),
        }

    async def async_update(self):
        await self.coordinator.async_request_refresh()


class AdherenceSensor(HeroEntity, SensorEntity):
    _attr_name = "7-day adherence"
    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HeroCoordinator) -> None:
        super().__init__(coordinator, "adherence")

    @property
    def native_value(self):
        stats = self.coordinator.data["stats"].get(
            "stats", self.coordinator.data["stats"]
        )
        return next(
            (
                stats[key]
                for key in ("taken_percentage", "adherence_percentage", "adherence")
                if stats.get(key) is not None
            ),
            None,
        )


class MetricSensor(HeroEntity, SensorEntity):
    def __init__(self, c, key, name):
        super().__init__(c, key)
        self._attr_name = name

    @property
    def native_value(self):
        stats = self.coordinator.data["stats"].get(
            "stats", self.coordinator.data["stats"]
        )
        return stats.get(self._key)


class SlotSensor(HeroEntity, SensorEntity):
    def __init__(self, c, slot):
        super().__init__(c, f"slot_{slot}")
        self.slot = slot
        self._attr_name = f"Slot {slot}"

    @property
    def _med(self):
        return next(
            (
                m
                for m in self.coordinator.data["medications"]
                if m.get("slot") == self.slot
            ),
            {},
        )

    @property
    def native_value(self):
        return self._med.get("name") or "Empty"

    @property
    def extra_state_attributes(self):
        m = self._med
        return {
            "pill_type": m.get("pill_type"),
            "level_enum": m.get("pill_level_enum"),
            "level_calculated": m.get("pill_level_calculated"),
            "exact_count": m.get("exact_pill_count"),
            "low": m.get("is_low"),
            "updated_at": m.get("updated_at"),
        }


class NextDoseSensor(HeroEntity, SensorEntity):
    _attr_name = "Next scheduled dose"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: HeroCoordinator) -> None:
        super().__init__(coordinator, "next_scheduled_dose")

    @property
    def native_value(self):
        for day in self.coordinator.data["doses"].get("dates", []):
            for slot in day.get("times", []):
                if slot.get("scheduled_datetime"):
                    return parse_hero_datetime(slot["scheduled_datetime"])
        return None
