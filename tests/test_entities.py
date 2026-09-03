"""Entity values, stable physical-slot identity, and schedule ordering."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from homeassistant.const import UnitOfRatio
from homeassistant.util import dt as dt_util

from custom_components.hero_health.binary_sensor import (
    ConnectivitySensor,
    SlotLowSensor,
)
from custom_components.hero_health.entity import HeroEntity
from custom_components.hero_health.sensor import (
    AdherenceSensor,
    LowMedicationsSensor,
    MetricSensor,
    NextDoseSensor,
    SlotSensor,
    async_setup_entry,
)


class FakeCoordinator:
    def __init__(self, entry_id="entry-1", unique_id="fake-account"):
        self.entry = SimpleNamespace(entry_id=entry_id, unique_id=unique_id)
        self.device_info = {"identifiers": {("hero_health", unique_id or entry_id)}}
        self.data = {
            "offline": {"hero_offline": False},
            "medications": [
                {"slot": 1, "name": "Example A", "is_low": True, "exact_pill_count": 2},
                {"slot": 2, "name": "Example B", "is_low": True},
            ],
            "stats": {"stats": {"adherence": 87, "doses_taken": 10, "doses_missed": 2}},
            "doses": {
                "dates": [
                    {"times": [{"scheduled_datetime": "2099-01-01T10:00:00+00:00"}]}
                ]
            },
        }

    def async_add_listener(self, _listener):
        return lambda: None

    async def async_request_refresh(self):
        self.refreshed = True


@pytest.mark.asyncio
async def test_entity_values_and_slot_identity():
    coordinator = FakeCoordinator()
    low = LowMedicationsSensor(coordinator)
    await low.async_update()
    assert coordinator.refreshed
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
    assert unique_id == "fake-account_slot_1"
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


def test_unique_id_stability_across_recreated_config_entry():
    coord1 = FakeCoordinator(entry_id="entry_id_aaa", unique_id="hero_acc_123")
    coord2 = FakeCoordinator(entry_id="entry_id_bbb", unique_id="hero_acc_123")

    entity1 = SlotSensor(coord1, 1)
    entity2 = SlotSensor(coord2, 1)
    assert entity1.unique_id == "hero_acc_123_slot_1"
    assert entity2.unique_id == "hero_acc_123_slot_1"
    assert entity1.unique_id == entity2.unique_id

    # Defensive fallback when unique_id is None
    coord_fallback = FakeCoordinator(entry_id="fallback_entry", unique_id=None)
    fallback_entity = HeroEntity(coord_fallback, "test_key")
    assert fallback_entity.unique_id == "fallback_entry_test_key"


@pytest.mark.asyncio
async def test_platform_creates_expected_sensor_entities():
    coordinator = FakeCoordinator()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    added = []
    await async_setup_entry(None, entry, added.extend)
    assert len(added) == 15
    assert {entity.unique_id for entity in added if "slot_" in entity.unique_id} == {
        f"fake-account_slot_{slot}" for slot in range(1, 11)
    }


def test_next_dose_sensor_selection_and_ordering(monkeypatch):
    coordinator = FakeCoordinator()
    sensor = NextDoseSensor(coordinator)

    # Empty schedules
    coordinator.data["doses"] = {}
    assert sensor.native_value is None

    coordinator.data["doses"] = {"dates": []}
    assert sensor.native_value is None

    coordinator.data["doses"] = {"dates": [{"times": []}]}
    assert sensor.native_value is None

    # Fixed reference time
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)

    # Unsorted dates + past & future + malformed timestamps
    coordinator.data["doses"] = {
        "dates": [
            {
                "times": [
                    {
                        "scheduled_datetime": "2026-06-10T14:00:00+00:00"
                    },  # Future (2h later)
                    {"scheduled_datetime": "not-a-date"},  # Malformed
                    {"scheduled_datetime": None},  # None
                    {"scheduled_datetime": 12345},  # Non-str
                ]
            },
            {
                "times": [
                    {
                        "scheduled_datetime": "2026-06-10T08:00:00+00:00"
                    },  # Past (4h ago)
                    {
                        "scheduled_datetime": "2026-06-10T13:00:00+00:00"
                    },  # Future (1h later) -> EARLIEST FUTURE
                    {
                        "scheduled_datetime": "2026-06-10T18:00:00+00:00"
                    },  # Future (6h later)
                ]
            },
        ]
    }
    result = sensor.native_value
    assert result == datetime(2026, 6, 10, 13, 0, 0, tzinfo=dt_util.UTC)

    # Only past entries -> returns None (does not report past dose as next dose)
    coordinator.data["doses"] = {
        "dates": [
            {
                "times": [
                    {"scheduled_datetime": "2026-06-10T06:00:00+00:00"},
                    {"scheduled_datetime": "2026-06-10T07:00:00+00:00"},
                ]
            }
        ]
    }
    assert sensor.native_value is None
