"""Entity values and stable physical-slot identity."""

from types import SimpleNamespace

import pytest
from homeassistant.const import UnitOfRatio

from custom_components.hero_health.binary_sensor import (
    ConnectivitySensor,
    SlotLowSensor,
)
from custom_components.hero_health.sensor import (
    AdherenceSensor,
    LowMedicationsSensor,
    MetricSensor,
    NextDoseSensor,
    SlotSensor,
    async_setup_entry,
)


class FakeCoordinator:
    def __init__(self):
        self.entry = SimpleNamespace(entry_id="entry", unique_id="fake-account")
        self.device_info = {"identifiers": {("hero_health", "fake-account")}}
        self.data = {
            "offline": {"hero_offline": False},
            "medications": [
                {"slot": 1, "name": "Example A", "is_low": True, "exact_pill_count": 2},
                {"slot": 2, "name": "Example B", "is_low": True},
            ],
            "stats": {"stats": {"adherence": 87, "doses_taken": 10, "doses_missed": 2}},
            "doses": {
                "dates": [
                    {"times": [{"scheduled_datetime": "2026-01-01T10:00:00+00:00"}]}
                ]
            },
        }

    def async_add_listener(self, _listener):
        return lambda: None

    async def async_request_refresh(self):
        self.refreshed = True


def test_entity_values_and_slot_identity():
    coordinator = FakeCoordinator()
    low = LowMedicationsSensor(coordinator)
    assert low.native_value == "Example A, Example B"
    assert low.extra_state_attributes == {
        "medications": ["Example A", "Example B"],
        "slots": [1, 2],
        "count": 2,
    }
    assert AdherenceSensor(coordinator).native_value == 87
    assert (
        AdherenceSensor(coordinator).native_unit_of_measurement
        == UnitOfRatio.PERCENTAGE
    )
    assert MetricSensor(coordinator, "doses_taken", "Taken").native_value == 10
    slot = SlotSensor(coordinator, 1)
    unique_id = slot.unique_id
    assert slot.native_value == "Example A"
    assert slot.extra_state_attributes["exact_count"] == 2
    coordinator.data["medications"][0]["name"] = "Example Changed"
    assert slot.unique_id == unique_id
    assert slot.native_value == "Example Changed"
    assert SlotSensor(coordinator, 3).native_value == "Empty"
    assert ConnectivitySensor(coordinator).is_on
    coordinator.data["offline"] = {"hero_offline": True}
    assert not ConnectivitySensor(coordinator).is_on
    assert SlotLowSensor(coordinator, 1).is_on
    assert not SlotLowSensor(coordinator, 3).is_on
    assert NextDoseSensor(coordinator).native_value.tzinfo is not None


@pytest.mark.asyncio
async def test_platform_creates_expected_sensor_entities():
    coordinator = FakeCoordinator()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    added = []
    await async_setup_entry(None, entry, added.extend)
    assert len(added) == 15
    assert {entity.unique_id for entity in added if "slot_" in entity.unique_id} == {
        f"entry_slot_{slot}" for slot in range(1, 11)
    }
