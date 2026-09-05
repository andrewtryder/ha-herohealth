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


def test_next_dose_sensor_recurring_fallback(monkeypatch):
    coordinator = FakeCoordinator()
    sensor = NextDoseSensor(coordinator)

    # Reference time: Monday 2026-09-07 10:00 UTC
    now = datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)

    # Case 1: Live future dose exists -> takes precedence over recurring fallback
    coordinator.data["doses"] = {
        "dates": [{"times": [{"scheduled_datetime": "2026-09-07T11:00:00+00:00"}]}]
    }
    coordinator.data["schedules"] = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "10:30"}],
        "pending_changes": False,
    }
    # Even if recurring fallback is earlier (10:30), live dose takes precedence!
    assert sensor.native_value == datetime(2026, 9, 7, 11, 0, 0, tzinfo=dt_util.UTC)

    # Case 2: No live future dose -> recurring fallback is used
    coordinator.data["doses"] = {
        "dates": [
            {
                "times": [
                    {"scheduled_datetime": "2026-09-07T08:00:00+00:00"}  # past
                ]
            }
        ]
    }
    # Recurring fallback is at 12:00
    coordinator.data["schedules"] = {
        "schedules": [{"schedule_id": "s1", "dow": "Mon", "time": "12:00"}],
        "pending_changes": False,
    }
    from zoneinfo import ZoneInfo

    assert sensor.native_value == datetime(
        2026, 9, 7, 12, 0, 0, tzinfo=ZoneInfo("US/Pacific")
    )

    # Case 3: Recurring fallback unavailable or pending_changes=True -> None
    coordinator.data["schedules"]["pending_changes"] = True
    assert sensor.native_value is None

    coordinator.data["schedules"] = None
    assert sensor.native_value is None


def test_coordinator_device_info_metadata():
    from custom_components.hero_health.coordinator import HeroCoordinator

    class DummyCoordinator(HeroCoordinator):
        def __init__(self, entry, status):
            self.entry = entry
            self.data = {"status": status}

    entry = SimpleNamespace(entry_id="entry_abc", unique_id="account_123")

    # Case 1: Complete valid metadata
    status_full = {
        "serial": "HERO-SN-12345",
        "device_nickname": "Kitchen Dispenser",
        "device_model": "Hero dispenser",
        "device_manifest": {"model": 1, "family": 2},
    }
    coord = DummyCoordinator(entry, status_full)
    info = coord.device_info
    assert info["identifiers"] == {("hero_health", "account_123")}
    assert info["manufacturer"] == "Hero Health"
    assert info["name"] == "Kitchen Dispenser"
    assert info["serial_number"] == "HERO-SN-12345"
    assert info["model"] == "Model 1"
    assert info["hw_version"] == "Family 2"

    # Case 2: Meaningful custom device_model string overrides "Model X"
    status_custom_model = {
        "serial": "HERO-SN-12345",
        "device_model": "Hero Smart Dispenser Pro",
        "device_manifest": {"model": 1, "family": 2},
    }
    coord2 = DummyCoordinator(entry, status_custom_model)
    assert coord2.device_info["model"] == "Hero Smart Dispenser Pro"

    # Case 3: Missing/malformed/empty metadata
    status_empty = {
        "serial": "   ",  # whitespace only
        "device_manifest": "malformed string",
    }
    coord3 = DummyCoordinator(entry, status_empty)
    info3 = coord3.device_info
    assert info3["identifiers"] == {("hero_health", "account_123")}
    assert "serial_number" not in info3
    assert info3["model"] == "Hero dispenser"
    assert "hw_version" not in info3

    # Case 4: None status
    coord4 = DummyCoordinator(entry, None)
    info4 = coord4.device_info
    assert info4["identifiers"] == {("hero_health", "account_123")}
    assert "serial_number" not in info4
    assert info4["model"] == "Hero dispenser"
    assert "hw_version" not in info4

    # Case 5: Serial with surrounding whitespace is stripped
    status_padded_serial = {
        "serial": "   HERO-SN-STRIPPED   ",
    }
    coord5 = DummyCoordinator(entry, status_padded_serial)
    assert coord5.device_info["serial_number"] == "HERO-SN-STRIPPED"

    # Case 6: Booleans in manifest (subclass of int) are rejected
    status_bool_manifest = {
        "device_manifest": {"model": True, "family": False},
    }
    coord6 = DummyCoordinator(entry, status_bool_manifest)
    assert coord6.device_info["model"] == "Hero dispenser"
    assert "hw_version" not in coord6.device_info
