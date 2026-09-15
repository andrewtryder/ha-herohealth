"""Security regression tests for remote dispense authorization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.util import dt as dt_util

from custom_components.hero_health import (
    HeroAdminRequired,
    _async_dispense,
    async_dispense_dose,
)
from custom_components.hero_health.button import DispenseScheduledDoseButton
from custom_components.hero_health.const import DOMAIN


class SecurityMockSession:
    def __init__(self):
        self._dispense_journal = {}
        self.device_tz = None
        self.execute_called = 0
        self.last_dispensed = None

    @property
    def dispense_journal(self):
        return dict(self._dispense_journal)

    async def async_execute(self, operation):
        self.execute_called += 1
        fake_client = SimpleNamespace(
            dispense_scheduled_dose=AsyncMock(return_value={"status": "dispensed"})
        )
        return await operation(fake_client)

    async def async_mark_dispense_start_sent(self, scheduled_datetime):
        self._dispense_journal[scheduled_datetime] = {
            "status": "outcome_unknown",
            "recorded_at": 1000.0,
        }

    async def async_save_dispense_id(self, scheduled_datetime):
        self.last_dispensed = scheduled_datetime
        self._dispense_journal[scheduled_datetime] = {
            "status": "completed",
            "recorded_at": 1000.0,
        }


class SecurityMockCoordinator:
    def __init__(self, session):
        self.session = session
        self.dispense_lock = AsyncMock()
        self.dispense_lock.__aenter__ = AsyncMock()
        self.dispense_lock.__aexit__ = AsyncMock()
        self.last_update_success = True
        self.refreshes = 0
        self.device_tz = None
        self.data = {}
        self.entry = SimpleNamespace(entry_id="entry-sec", unique_id="hero-sec")

    async def async_refresh(self):
        self.refreshes += 1

    async def async_request_refresh(self):
        self.refreshes += 1


def _eligible_doses(scheduled_dt: str) -> dict:
    return {
        "dates": [
            {
                "times": [
                    {
                        "scheduled_datetime": scheduled_dt,
                        "doses": [{"state": "time_to_take"}],
                    }
                ]
            }
        ]
    }


@pytest.fixture
def security_setup():
    session = SecurityMockSession()
    coordinator = SecurityMockCoordinator(session)
    dose_str = dt_util.now().isoformat()
    coordinator.data = {"doses": _eligible_doses(dose_str)}

    entry = SimpleNamespace(
        entry_id="entry-sec",
        domain=DOMAIN,
        runtime_data=SimpleNamespace(coordinator=coordinator, session=session),
    )

    admin_user = SimpleNamespace(id="admin-1", is_admin=True)
    regular_user = SimpleNamespace(id="regular-1", is_admin=False)
    users = {admin_user.id: admin_user, regular_user.id: regular_user}

    async def async_get_user(user_id):
        return users.get(user_id)

    auth = SimpleNamespace(async_get_user=async_get_user)
    entries_map = {entry.entry_id: entry}
    config_entries = SimpleNamespace(
        async_get_entry=lambda eid: entries_map.get(eid),
    )

    hass = SimpleNamespace(
        auth=auth,
        config_entries=config_entries,
    )
    return SimpleNamespace(
        hass=hass,
        coordinator=coordinator,
        session=session,
        entry=entry,
        dose_str=dose_str,
        admin_context=Context(user_id="admin-1"),
        regular_context=Context(user_id="regular-1"),
    )


@pytest.mark.asyncio
async def test_admin_dispense_through_shared_function_succeeds(security_setup):
    """Admin user invoking async_dispense_dose succeeds and records journal entry."""
    s = security_setup
    await async_dispense_dose(
        s.hass,
        s.entry.entry_id,
        s.dose_str,
        context=s.admin_context,
    )
    assert s.session.execute_called == 1
    assert s.session.last_dispensed == s.dose_str
    assert s.dose_str in s.session.dispense_journal
    assert s.session.dispense_journal[s.dose_str]["status"] == "completed"


@pytest.mark.asyncio
async def test_admin_dispense_through_button_succeeds(security_setup):
    """Admin user pressing button entity succeeds and calls physical dispense."""
    s = security_setup
    button = DispenseScheduledDoseButton(s.coordinator)
    button.hass = s.hass
    button._context = s.admin_context

    await button.async_press()

    assert s.session.execute_called == 1
    assert s.session.last_dispensed == s.dose_str
    assert s.dose_str in s.session.dispense_journal


@pytest.mark.asyncio
async def test_non_admin_dispense_through_shared_function_rejected(security_setup):
    """Non-admin user calling async_dispense_dose is blocked before any API call."""
    s = security_setup
    initial_journal = dict(s.session.dispense_journal)

    with pytest.raises(Unauthorized) as exc_info:
        await async_dispense_dose(
            s.hass,
            s.entry.entry_id,
            s.dose_str,
            context=s.regular_context,
        )

    assert isinstance(exc_info.value, HeroAdminRequired)
    assert exc_info.value.translation_key == "admin_required"
    assert exc_info.value.translation_domain == DOMAIN
    # Assert fail-closed: NO coordinator refresh, NO execute, NO journal changes
    assert s.coordinator.refreshes == 0
    assert s.session.execute_called == 0
    assert s.session.dispense_journal == initial_journal


@pytest.mark.asyncio
async def test_non_admin_dispense_through_button_rejected(security_setup):
    """Non-admin user pressing the button is blocked before any API call or refresh."""
    s = security_setup
    button = DispenseScheduledDoseButton(s.coordinator)
    button.hass = s.hass
    button._context = s.regular_context
    initial_journal = dict(s.session.dispense_journal)

    with pytest.raises(HeroAdminRequired):
        await button.async_press()

    assert s.coordinator.refreshes == 0
    assert s.session.execute_called == 0
    assert s.session.dispense_journal == initial_journal


@pytest.mark.asyncio
async def test_missing_context_fails_closed(security_setup):
    """Dispense call with missing context fails closed."""
    s = security_setup
    initial_journal = dict(s.session.dispense_journal)

    with pytest.raises(HeroAdminRequired):
        await async_dispense_dose(s.hass, s.entry.entry_id, s.dose_str, context=None)

    assert s.coordinator.refreshes == 0
    assert s.session.execute_called == 0
    assert s.session.dispense_journal == initial_journal


@pytest.mark.asyncio
async def test_empty_user_id_fails_closed(security_setup):
    """Dispense call with empty user_id in context fails closed."""
    s = security_setup
    empty_context = Context(user_id=None)

    with pytest.raises(HeroAdminRequired):
        await async_dispense_dose(
            s.hass, s.entry.entry_id, s.dose_str, context=empty_context
        )

    assert s.coordinator.refreshes == 0
    assert s.session.execute_called == 0


@pytest.mark.asyncio
async def test_unknown_user_id_fails_closed(security_setup):
    """Dispense call with unknown user_id fails closed."""
    s = security_setup
    unknown_context = Context(user_id="nonexistent-user")

    with pytest.raises(HeroAdminRequired):
        await async_dispense_dose(
            s.hass, s.entry.entry_id, s.dose_str, context=unknown_context
        )

    assert s.coordinator.refreshes == 0
    assert s.session.execute_called == 0


@pytest.mark.asyncio
async def test_missing_auth_manager_fails_closed(security_setup):
    """If hass.auth is unavailable, dispense fails closed."""
    s = security_setup
    s.hass.auth = None

    with pytest.raises(HeroAdminRequired):
        await async_dispense_dose(
            s.hass, s.entry.entry_id, s.dose_str, context=s.admin_context
        )

    assert s.coordinator.refreshes == 0
    assert s.session.execute_called == 0


@pytest.mark.asyncio
async def test_admin_domain_service_call_succeeds(security_setup):
    """Admin service call handler (_async_dispense) executes when context is admin."""
    s = security_setup
    call = SimpleNamespace(
        data={"config_entry_id": s.entry.entry_id},
        context=s.admin_context,
    )
    await _async_dispense(s.hass, call)
    assert s.session.execute_called == 1
    assert s.session.last_dispensed == s.dose_str


@pytest.mark.asyncio
async def test_unauthorized_attempts_do_not_mutate_journal(security_setup):
    """Repeated unauthorized attempts must never add or mutate journal entries."""
    s = security_setup
    s.session._dispense_journal["existing-dose"] = {
        "status": "completed",
        "recorded_at": 500.0,
    }
    before = dict(s.session._dispense_journal)

    for bad_ctx in [
        None,
        Context(user_id=None),
        s.regular_context,
        Context(user_id="fake"),
    ]:
        with pytest.raises(HeroAdminRequired):
            await async_dispense_dose(
                s.hass, s.entry.entry_id, s.dose_str, context=bad_ctx
            )

    assert s.session.dispense_journal == before
    assert s.session.execute_called == 0
