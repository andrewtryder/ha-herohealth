"""Integration setup, actions, and session lifecycle behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.hero_health import (
    _async_dispense,
    _async_refresh,
    _coordinator_for_call,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.hero_health.api.exceptions import (
    HeroAuthenticationError,
    HeroConnectionError,
    HeroError,
)
from custom_components.hero_health.api.models import HeroTokens
from custom_components.hero_health.coordinator import HeroCoordinator
from custom_components.hero_health.session import HeroSession


class FakeServices:
    def __init__(self):
        self.handlers = {}

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, **_kwargs):
        self.handlers[domain, service] = handler

    def async_remove(self, domain, service):
        self.handlers.pop((domain, service), None)


class FakeEntries:
    def __init__(self, entries):
        self.entries = entries
        self.forwarded = []

    def async_entries(self, _domain):
        return self.entries

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(self, _entry, _platforms):
        return True


class FakeSession:
    def __init__(self, *_args):
        self.client = SimpleNamespace()
        self.closed = False
        self.last = None
        self.executed = AsyncMock()

    async def async_initialize(self):
        return self.client

    async def async_close(self):
        self.closed = True

    async def async_last_dispense_id(self):
        return self.last

    async def async_save_dispense_id(self, value):
        self.last = value

    async def async_execute(self, operation):
        self.executed = await operation(
            SimpleNamespace(dispense_scheduled_dose=AsyncMock())
        )


class FakeCoordinator:
    def __init__(self, _hass, entry, session):
        self.entry, self.session = entry, session
        self.dispense_lock = asyncio.Lock()
        self.data = {"doses": {"dates": []}}
        self.refreshed = 0

    async def async_config_entry_first_refresh(self):
        self.refreshed += 1

    async def async_request_refresh(self):
        self.refreshed += 1


@pytest.mark.asyncio
async def test_setup_runtime_data_actions_and_unload(monkeypatch):
    entry = SimpleNamespace(
        entry_id="entry",
        data={
            "email": "test@example.invalid",
            "password": "fake",
            "account_id": "fake",
        },
        runtime_data=None,
    )
    hass = SimpleNamespace(services=FakeServices(), config_entries=FakeEntries([entry]))
    monkeypatch.setattr("custom_components.hero_health.HeroSession", FakeSession)
    monkeypatch.setattr(
        "custom_components.hero_health.HeroCoordinator", FakeCoordinator
    )
    assert await async_setup_entry(hass, entry)
    assert entry.runtime_data.coordinator.refreshed == 1
    assert len(hass.services.handlers) == 2
    await async_unload_entry(hass, entry)
    assert entry.runtime_data is None
    assert not hass.services.handlers


@pytest.mark.asyncio
async def test_setup_translates_temporary_connection_failure(monkeypatch):
    class FailingSession(FakeSession):
        async def async_initialize(self):
            raise HeroConnectionError("offline")

    entry = SimpleNamespace(
        entry_id="entry",
        data={"email": "test@example.invalid", "password": "fake"},
        runtime_data=None,
    )
    hass = SimpleNamespace(services=FakeServices(), config_entries=FakeEntries([entry]))
    monkeypatch.setattr("custom_components.hero_health.HeroSession", FailingSession)
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_setup_handles_generic_exception_and_closes_session(monkeypatch):
    class CrashingSession(FakeSession):
        async def async_initialize(self):
            raise RuntimeError("unexpected crash")

    entry = SimpleNamespace(
        entry_id="entry",
        data={"email": "test@example.invalid", "password": "fake"},
        runtime_data=None,
    )
    hass = SimpleNamespace(services=FakeServices(), config_entries=FakeEntries([entry]))
    monkeypatch.setattr("custom_components.hero_health.HeroSession", CrashingSession)
    with pytest.raises(RuntimeError):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_action_targeting_and_dispense_safety():
    session = FakeSession()
    entry = SimpleNamespace(entry_id="entry", runtime_data=None)
    coordinator = FakeCoordinator(None, entry, session)
    coordinator.data = {
        "doses": {
            "dates": [
                {
                    "times": [
                        {
                            "scheduled_datetime": "2099-01-01T00:00:00+00:00",
                            "doses": [{"state": "time_to_take"}],
                        }
                    ]
                }
            ]
        }
    }
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    hass = SimpleNamespace(config_entries=FakeEntries([entry]))
    call = SimpleNamespace(data={})
    assert _coordinator_for_call(hass, call) is coordinator
    await _async_refresh(hass, call)
    with pytest.raises(ServiceValidationError):
        await _async_dispense(hass, call)
    with pytest.raises(ServiceValidationError):
        _coordinator_for_call(hass, SimpleNamespace(data={"entry_id": "missing"}))

    # Multiple entries without specifying entry_id raises ServiceValidationError
    entry2 = SimpleNamespace(
        entry_id="entry2", runtime_data=SimpleNamespace(coordinator=coordinator)
    )
    hass_multiple = SimpleNamespace(config_entries=FakeEntries([entry, entry2]))
    with pytest.raises(ServiceValidationError, match="multiple Hero Health entries"):
        _coordinator_for_call(hass_multiple, SimpleNamespace(data={}))


@pytest.mark.asyncio
async def test_dispense_action_executes_one_eligible_dose_and_deduplicates():
    session = FakeSession()
    entry = SimpleNamespace(entry_id="entry", runtime_data=None)
    coordinator = FakeCoordinator(None, entry, session)
    dose = dt_util.now().isoformat()
    coordinator.data = {
        "doses": {
            "dates": [
                {
                    "times": [
                        {
                            "scheduled_datetime": dose,
                            "doses": [{"state": "time_to_take"}],
                        }
                    ]
                }
            ]
        }
    }
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    hass = SimpleNamespace(config_entries=FakeEntries([entry]))
    await _async_dispense(hass, SimpleNamespace(data={}))
    assert session.last == dose
    with pytest.raises(ServiceValidationError, match="already dispensed"):
        await _async_dispense(hass, SimpleNamespace(data={}))


@pytest.mark.asyncio
async def test_session_initialization_and_token_login_paths(monkeypatch):
    session = object.__new__(HeroSession)
    session._persist = False
    session._store = SimpleNamespace(
        async_load=AsyncMock(return_value={}), async_save=AsyncMock()
    )
    session._tokens = None
    session._lock = asyncio.Lock()
    session._email, session._password, session.account_id = (
        "test@example.invalid",
        "fake",
        "fake-account",
    )
    session._auth = SimpleNamespace(
        login_with_password=AsyncMock(
            return_value=HeroTokens("access", "refresh", 3600, 9999999999)
        )
    )
    session._http = SimpleNamespace()
    session.client = None
    monkeypatch.setattr(
        "custom_components.hero_health.session.HeroCloudClient",
        lambda *_args: SimpleNamespace(set_tokens=lambda _token: None),
    )
    client = await session.async_initialize()
    assert client is session.client
    assert session._auth.login_with_password.await_count == 1


@pytest.mark.asyncio
async def test_session_refresh_fallback_and_nonpersistent_state():
    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._tokens = HeroTokens("expired", "refresh", 1, 0)
    session._email, session._password = "test@example.invalid", "fake-password"
    session._persist = False
    session._store = SimpleNamespace(async_load=AsyncMock(), async_save=AsyncMock())
    session.client = None
    session._auth = SimpleNamespace(
        refresh_access_token=AsyncMock(side_effect=HeroAuthenticationError("expired")),
        login_with_password=AsyncMock(
            return_value=HeroTokens("new", "refresh", 3600, 9999999999)
        ),
    )
    await session._async_ensure_tokens()
    assert session._tokens.access_token == "new"
    assert session._auth.login_with_password.await_count == 1
    await session.async_save_dispense_id("dose")
    assert await session.async_last_dispense_id() is None


@pytest.mark.asyncio
async def test_session_persists_refreshed_token_and_updates_client():
    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._tokens = HeroTokens("expired", "refresh", 1, 0)
    session._email, session._password = "test@example.invalid", "fake-password"
    session._persist = True
    session._store = SimpleNamespace(async_save=AsyncMock())
    session.client = SimpleNamespace(set_tokens=Mock())
    session._auth = SimpleNamespace(
        refresh_access_token=AsyncMock(
            return_value=HeroTokens("new", "new-refresh", 3600, 9999999999)
        )
    )
    await session._async_ensure_tokens()
    session._store.async_save.assert_awaited_once()
    session.client.set_tokens.assert_called_once_with("new")


@pytest.mark.asyncio
async def test_forced_login_auth_failure_is_preserved():
    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._tokens = None
    session._email, session._password = "test@example.invalid", "fake-password"
    session._persist = False
    session._store = SimpleNamespace()
    session.client = None
    session._auth = SimpleNamespace(
        login_with_password=AsyncMock(side_effect=HeroAuthenticationError("invalid"))
    )
    with pytest.raises(HeroAuthenticationError):
        await session._async_ensure_tokens(force_login=True)


@pytest.mark.asyncio
async def test_real_session_owns_and_closes_its_aiohttp_session(hass):
    session = HeroSession(
        hass, "entry", "test@example.invalid", "fake-password", "fake-account"
    )
    assert not session._http.closed
    await session.async_close()
    assert session._http.closed


@pytest.mark.asyncio
async def test_coordinator_stats_date_uses_ha_local_timezone(hass, monkeypatch):
    from datetime import datetime, timedelta, timezone

    # UTC is 2026-06-11 02:00:00, but HA local is UTC-5 -> date 2026-06-10
    local_tz = timezone(timedelta(hours=-5))
    local_now = datetime(2026, 6, 10, 21, 0, 0, tzinfo=local_tz)
    monkeypatch.setattr(dt_util, "now", lambda: local_now)

    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    stats_mock = AsyncMock(return_value={"stats": {}})
    client_mock = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(return_value={"status": "online"}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(return_value={"dates": []}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=stats_mock,
    )
    session = SimpleNamespace(
        client=client_mock, async_initialize=AsyncMock(return_value=client_mock)
    )
    coordinator = HeroCoordinator(hass, entry, session)
    data = await coordinator._async_update_data()
    assert data is not None
    stats_mock.assert_awaited_once_with("2026-06-10")


@pytest.mark.asyncio
async def test_coordinator_update_data_error_handling(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")

    # HeroAuthenticationError -> ConfigEntryAuthFailed
    auth_failing_client = SimpleNamespace(
        check_hero_offline=AsyncMock(side_effect=HeroAuthenticationError("auth error")),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={}),
        home_screen_doses=AsyncMock(return_value={}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
    )
    coord_auth = HeroCoordinator(
        hass, entry, SimpleNamespace(client=auth_failing_client)
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord_auth._async_update_data()

    # HeroError on status check -> UpdateFailed
    status_exc_client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(side_effect=HeroError("offline error")),
        last_d2d_config=AsyncMock(return_value={}),
        home_screen_doses=AsyncMock(return_value={}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
    )
    coord_status_exc = HeroCoordinator(
        hass, entry, SimpleNamespace(client=status_exc_client)
    )
    with pytest.raises(UpdateFailed):
        await coord_status_exc._async_update_data()
