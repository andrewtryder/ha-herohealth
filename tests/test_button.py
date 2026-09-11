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

    def async_add_listener(self, _listener):
        return lambda: None


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
