"""Safety-gated dispense button behavior."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.hero_health.button import DispenseScheduledDoseButton
from custom_components.hero_health.const import DOMAIN, SERVICE_DISPENSE


class FakeCoordinator:
    def __init__(self):
        self.entry = SimpleNamespace(entry_id="entry-1", unique_id="account-1")
        self.device_info = {"identifiers": {(DOMAIN, "account-1")}}
        self.last_update_success = True
        self.data = {"doses": {"dates": []}}
        self.eligibility_refreshes = 0

    def async_add_listener(self, _listener, *_args):
        return lambda: None

    def async_schedule_eligibility_refresh(self, _boundary):
        self.eligibility_refreshes += 1


def _eligible_doses(scheduled: datetime):
    return {
        "dates": [
            {
                "times": [
                    {
                        "scheduled_datetime": scheduled.isoformat(),
                        "doses": [{"state": "time_to_take"}],
                    }
                ]
            }
        ]
    }


def test_dispense_button_available_only_inside_eligible_window(monkeypatch):
    coordinator = FakeCoordinator()
    button = DispenseScheduledDoseButton(coordinator)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)

    coordinator.data["doses"] = _eligible_doses(now + timedelta(minutes=15))
    assert button.available
    assert button.extra_state_attributes["scheduled_datetime"] is not None

    coordinator.data["doses"] = _eligible_doses(now + timedelta(hours=2))
    assert not button.available


@pytest.mark.asyncio
async def test_dispense_button_delegates_to_guarded_service(monkeypatch):
    coordinator = FakeCoordinator()
    button = DispenseScheduledDoseButton(coordinator)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)
    coordinator.data["doses"] = _eligible_doses(now)
    services = SimpleNamespace(async_call=AsyncMock())
    button.hass = SimpleNamespace(services=services)

    await button.async_press()

    services.async_call.assert_awaited_once_with(
        DOMAIN,
        SERVICE_DISPENSE,
        {"config_entry_id": "entry-1"},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_button_setup_entry():
    from custom_components.hero_health.button import async_setup_entry

    coordinator = FakeCoordinator()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    added = []
    await async_setup_entry(None, entry, added.extend)
    assert len(added) == 1
    assert isinstance(added[0], DispenseScheduledDoseButton)


@pytest.mark.asyncio
async def test_button_boundary_timers(monkeypatch):
    coordinator = FakeCoordinator()
    button = DispenseScheduledDoseButton(coordinator)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)
    coordinator.data["doses"] = _eligible_doses(now + timedelta(minutes=15))

    written = []
    button.async_write_ha_state = lambda: written.append(True)
    scheduled_timers = []

    def fake_track(hass, cb, target):
        scheduled_timers.append((cb, target))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.hero_health.button.async_track_point_in_time", fake_track
    )
    button.hass = SimpleNamespace()

    await button.async_added_to_hass()
    assert len(scheduled_timers) > 0

    button._async_boundary_fired(now)
    assert len(written) == 1

    button._handle_coordinator_update()
    await button.async_will_remove_from_hass()
    assert len(button._timer_unsubs) == 0


@pytest.mark.asyncio
async def test_button_scheduled_time_refreshes_authoritative_dose_state(monkeypatch):
    coordinator = FakeCoordinator()
    button = DispenseScheduledDoseButton(coordinator)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)
    scheduled = now + timedelta(minutes=15)
    coordinator.data["doses"] = {
        "dates": [
            {
                "times": [
                    {
                        "scheduled_datetime": scheduled.isoformat(),
                        "doses": [{"state": "not_time_to_take"}],
                    }
                ]
            }
        ]
    }
    assert not button.available

    timers = []
    monkeypatch.setattr(
        "custom_components.hero_health.button.async_track_point_in_time",
        lambda _hass, callback, target: (
            timers.append((callback, target)) or (lambda: None)
        ),
    )
    button.hass = SimpleNamespace()
    button.async_write_ha_state = lambda: None
    await button.async_added_to_hass()

    scheduled_callback = next(
        callback for callback, target in timers if target == scheduled
    )
    scheduled_callback(scheduled)
    assert coordinator.eligibility_refreshes == 1

    coordinator.data["doses"] = _eligible_doses(scheduled)
    assert button.available


@pytest.mark.asyncio
async def test_button_delegates_to_async_dispense_dose(monkeypatch):
    coordinator = FakeCoordinator()
    button = DispenseScheduledDoseButton(coordinator)
    button.hass = SimpleNamespace(config_entries=SimpleNamespace())
    dispensed = []

    async def fake_dispense(h, e, **kw):
        dispensed.append(e)

    monkeypatch.setattr(
        "custom_components.hero_health.async_dispense_dose", fake_dispense
    )
    await button.async_press()
    assert dispensed == ["entry-1"]
