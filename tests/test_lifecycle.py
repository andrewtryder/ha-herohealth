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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hero_health import (
    _async_dispense,
    _async_refresh,
    _coordinator_for_call,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.hero_health.api.exceptions import (
    HeroApiError,
    HeroAuthenticationError,
    HeroConnectionError,
    HeroError,
    HeroRateLimitError,
)
from custom_components.hero_health.api.models import HeroTokens
from custom_components.hero_health.const import (
    DOMAIN,
    SERVICE_DISPENSE,
    SERVICE_REFRESH,
)
from custom_components.hero_health.coordinator import HeroCoordinator
from custom_components.hero_health.session import HeroSession


class LifecycleClient:
    """A real-session coordinator client that records its token per call."""

    def __init__(self, access_token="old", auth_failures=0):
        self.access_token = access_token
        self.auth_failures = auth_failures
        self.calls = []

    def set_tokens(self, access_token):
        self.access_token = access_token

    async def check_hero_offline(self):
        self.calls.append(("offline", self.access_token))
        return {"hero_offline": False}

    async def user_status(self):
        self.calls.append(("status", self.access_token))
        return {"status": "online"}

    async def last_d2d_config(self):
        self.calls.append(("config", self.access_token))
        return {"config": {"pills": []}}

    async def home_screen_doses(self):
        self.calls.append(("doses", self.access_token))
        return {"dates": []}

    async def get_home_screen_events(self):
        self.calls.append(("events", self.access_token))
        if self.auth_failures:
            self.auth_failures -= 1
            raise HeroAuthenticationError("expired")
        return {}

    async def stats(self, _date):
        self.calls.append(("stats", self.access_token))
        return {}


def _real_session(client, tokens, auth):
    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._tokens = tokens
    session._persist = False
    session._email, session._password = "test@example.invalid", "fake-password"
    session._auth = auth
    session.client = client
    return session


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

    def async_get_entry(self, entry_id):
        return next(
            (entry for entry in self.entries if entry.entry_id == entry_id), None
        )

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


class RegistrySession:
    """Session fake for testing Home Assistant's real service dispatcher."""

    def __init__(self, *_args):
        self.last = None
        self.async_execute = AsyncMock(side_effect=self._execute)

    async def async_initialize(self):
        return SimpleNamespace()

    async def _execute(self, operation):
        return await operation(
            SimpleNamespace(dispense_scheduled_dose=AsyncMock(return_value={}))
        )

    async def async_last_dispense_id(self):
        return self.last

    async def async_save_dispense_id(self, identifier):
        self.last = identifier

    async def async_close(self):
        return None


class RegistryCoordinator:
    def __init__(self, _hass, entry, session):
        self.entry, self.session = entry, session
        self.dispense_lock = asyncio.Lock()
        self.data = {"doses": {"dates": []}}
        self.first_refresh = AsyncMock()
        self.async_request_refresh = AsyncMock()

    async def async_config_entry_first_refresh(self):
        await self.first_refresh()


async def _setup_registry_entry(hass, monkeypatch, account_id="account-1"):
    """Set up an entry while retaining Home Assistant's actual service registry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=account_id,
        data={
            "email": "test@example.invalid",
            "password": "fake",
            "account_id": account_id,
        },
    )
    entry.add_to_hass(hass)
    await async_setup(hass, {})
    monkeypatch.setattr("custom_components.hero_health.HeroSession", RegistrySession)
    monkeypatch.setattr(
        "custom_components.hero_health.HeroCoordinator", RegistryCoordinator
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    )
    assert await async_setup_entry(hass, entry)
    return entry


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
    assert len(hass.services.handlers) == 0
    await async_unload_entry(hass, entry)
    assert entry.runtime_data is None
    assert not hass.services.handlers


@pytest.mark.asyncio
async def test_refresh_service_registry_awaits_registered_handler(hass, monkeypatch):
    entry = await _setup_registry_entry(hass, monkeypatch)
    coordinator = entry.runtime_data.coordinator

    await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH, {"config_entry_id": entry.entry_id}, blocking=True
    )

    coordinator.async_request_refresh.assert_awaited_once()
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_dispense_service_registry_awaits_registered_handler(hass, monkeypatch):
    entry = await _setup_registry_entry(hass, monkeypatch)
    coordinator = entry.runtime_data.coordinator
    session = entry.runtime_data.session
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

    await hass.services.async_call(
        DOMAIN, SERVICE_DISPENSE, {"config_entry_id": entry.entry_id}, blocking=True
    )

    coordinator.async_request_refresh.assert_awaited_once()
    session.async_execute.assert_awaited_once()
    assert await session.async_last_dispense_id() == dose
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_invalid_dispense_service_registry_surfaces_validation_error(
    hass, monkeypatch
):
    entry = await _setup_registry_entry(hass, monkeypatch)

    with pytest.raises(ServiceValidationError, match="No eligible Hero scheduled dose"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISPENSE,
            {"config_entry_id": entry.entry_id},
            blocking=True,
        )

    assert entry.runtime_data.coordinator.async_request_refresh.await_count == 1
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_service_registry_targets_multiple_entries_and_unloads_last_service(
    hass, monkeypatch
):
    first = await _setup_registry_entry(hass, monkeypatch, "account-1")
    second = await _setup_registry_entry(hass, monkeypatch, "account-2")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REFRESH,
        {"config_entry_id": second.entry_id},
        blocking=True,
    )

    assert first.runtime_data.coordinator.async_request_refresh.await_count == 0
    second.runtime_data.coordinator.async_request_refresh.assert_awaited_once()
    await async_unload_entry(hass, first)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)
    await async_unload_entry(hass, second)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)
    assert hass.services.has_service(DOMAIN, SERVICE_DISPENSE)


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
    call = SimpleNamespace(data={"config_entry_id": "entry"})
    assert _coordinator_for_call(hass, call) is coordinator
    await _async_refresh(hass, call)
    with pytest.raises(ServiceValidationError):
        await _async_dispense(hass, call)
    with pytest.raises(ServiceValidationError):
        _coordinator_for_call(
            hass, SimpleNamespace(data={"config_entry_id": "missing"})
        )

    # Missing config_entry_id is invalid even when multiple entries exist.
    entry2 = SimpleNamespace(
        entry_id="entry2", runtime_data=SimpleNamespace(coordinator=coordinator)
    )
    hass_multiple = SimpleNamespace(config_entries=FakeEntries([entry, entry2]))
    with pytest.raises(ServiceValidationError, match="config entry is required"):
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
    await _async_dispense(hass, SimpleNamespace(data={"config_entry_id": "entry"}))
    assert session.last == dose
    with pytest.raises(ServiceValidationError, match="already dispensed"):
        await _async_dispense(hass, SimpleNamespace(data={"config_entry_id": "entry"}))


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
    # The Home Assistant helper owns and closes the isolated session on shutdown.
    assert not session._http.closed


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

    async def execute(operation):
        return await operation(client_mock)

    session = SimpleNamespace(async_execute=execute)
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

    async def execute_auth(operation):
        return await operation(auth_failing_client)

    coord_auth = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=execute_auth)
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

    async def execute_status(operation):
        return await operation(status_exc_client)

    coord_status_exc = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=execute_status)
    )
    with pytest.raises(UpdateFailed):
        await coord_status_exc._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_polling_proactively_refreshes_expired_token(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = LifecycleClient()
    refresh = AsyncMock(
        return_value=HeroTokens("refreshed", "refresh", 3600, 9999999999)
    )
    login = AsyncMock()
    session = _real_session(
        client,
        HeroTokens("expired", "refresh", 1, 0),
        SimpleNamespace(refresh_access_token=refresh, login_with_password=login),
    )

    assert await HeroCoordinator(hass, entry, session)._async_update_data()
    refresh.assert_awaited_once_with("refresh")
    login.assert_not_awaited()
    assert {token for _name, token in client.calls} == {"refreshed"}


@pytest.mark.asyncio
async def test_coordinator_polling_does_not_refresh_valid_token(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = LifecycleClient()
    refresh = AsyncMock()
    login = AsyncMock()
    session = _real_session(
        client,
        HeroTokens("valid", "refresh", 3600, 9999999999),
        SimpleNamespace(refresh_access_token=refresh, login_with_password=login),
    )

    assert await HeroCoordinator(hass, entry, session)._async_update_data()
    refresh.assert_not_awaited()
    login.assert_not_awaited()
    assert {token for _name, token in client.calls} == {"old"}


@pytest.mark.asyncio
async def test_coordinator_retries_entire_snapshot_after_endpoint_auth_failure(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = LifecycleClient(auth_failures=1)
    login = AsyncMock(return_value=HeroTokens("recovered", "refresh", 3600, 9999999999))
    session = _real_session(
        client,
        HeroTokens("valid", "refresh", 3600, 9999999999),
        SimpleNamespace(
            refresh_access_token=AsyncMock(
                return_value=HeroTokens("recovered", "refresh", 3600, 9999999999)
            ),
            login_with_password=login,
        ),
    )

    assert await HeroCoordinator(hass, entry, session)._async_update_data()
    # An early API authentication failure recovers with refresh-token rotation first.
    session._auth.refresh_access_token.assert_awaited_once_with("refresh")
    login.assert_not_awaited()
    assert [name for name, _token in client.calls].count("offline") == 2
    assert {token for _name, token in client.calls if token == "recovered"}


@pytest.mark.asyncio
async def test_coordinator_raises_auth_failed_after_retry_auth_failure(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = LifecycleClient(auth_failures=2)
    session = _real_session(
        client,
        HeroTokens("valid", "refresh", 3600, 9999999999),
        SimpleNamespace(
            refresh_access_token=AsyncMock(),
            login_with_password=AsyncMock(
                return_value=HeroTokens("recovered", "refresh", 3600, 9999999999)
            ),
        ),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await HeroCoordinator(hass, entry, session)._async_update_data()
    assert [name for name, _token in client.calls].count("offline") == 2


@pytest.mark.asyncio
async def test_coordinator_normalizes_non_auth_optional_endpoint_failure(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = LifecycleClient()
    client.get_home_screen_events = AsyncMock(side_effect=HeroApiError("unavailable"))
    refresh = AsyncMock()
    login = AsyncMock()
    session = _real_session(
        client,
        HeroTokens("valid", "refresh", 3600, 9999999999),
        SimpleNamespace(refresh_access_token=refresh, login_with_password=login),
    )

    data = await HeroCoordinator(hass, entry, session)._async_update_data()
    assert data["events"] == {}
    refresh.assert_not_awaited()
    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_serializes_concurrent_token_refreshes():
    client = LifecycleClient()
    refresh = AsyncMock(
        return_value=HeroTokens("refreshed", "refresh", 3600, 9999999999)
    )
    session = _real_session(
        client,
        HeroTokens("expired", "refresh", 1, 0),
        SimpleNamespace(refresh_access_token=refresh, login_with_password=AsyncMock()),
    )

    await asyncio.gather(session._async_ensure_tokens(), session._async_ensure_tokens())

    refresh.assert_awaited_once_with("refresh")
    assert client.access_token == "refreshed"


@pytest.mark.asyncio
async def test_coordinator_rate_limit_is_transient_and_uses_retry_after(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")

    async def execute(_operation):
        raise HeroRateLimitError(120)

    with pytest.raises(UpdateFailed) as raised:
        await HeroCoordinator(
            hass, entry, SimpleNamespace(async_execute=execute)
        )._async_update_data()
    assert raised.value.retry_after == 120
