"""Integration setup, actions, and session lifecycle behavior."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import Context, is_callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
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
    HeroDispenseOutcomeUnknown,
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

    async def pills_by_schedules(self):
        self.calls.append(("schedules", self.access_token))
        return {"schedules": [], "pending_changes": False}


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
        self._journal = {}
        self.executed = AsyncMock()

    @property
    def dispense_journal(self):
        return self._journal

    async def async_initialize(self):
        return self.client

    async def async_close(self):
        self.closed = True

    async def async_last_dispense_id(self):
        return self.last

    async def async_save_dispense_id(self, value, outcome="completed"):
        self.last = value
        self._journal[value] = {
            "status": outcome,
            "recorded_at": dt_util.now().isoformat(),
        }

    async def async_mark_dispense_start_sent(self, value):
        self._journal[value] = {
            "status": "outcome_unknown",
            "recorded_at": dt_util.now().isoformat(),
        }

    async def async_dispense_outcome_unknown(self, value):
        return self._journal.get(value, {}).get("status") == "outcome_unknown"

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
        self.last_update_success = True
        self.device_tz = None

    async def async_config_entry_first_refresh(self):
        self.refreshed += 1

    async def async_request_refresh(self):
        self.refreshed += 1

    async def async_refresh(self):
        self.refreshed += 1


class RegistrySession:
    """Session fake for testing Home Assistant's real service dispatcher."""

    def __init__(self, *_args):
        self.last = None
        self._journal = {}
        self.async_execute = AsyncMock(side_effect=self._execute)

    @property
    def dispense_journal(self):
        return self._journal

    async def async_initialize(self):
        return SimpleNamespace()

    async def _execute(self, operation):
        return await operation(
            SimpleNamespace(dispense_scheduled_dose=AsyncMock(return_value={}))
        )

    async def async_last_dispense_id(self):
        return self.last

    async def async_save_dispense_id(self, identifier, outcome="completed"):
        self.last = identifier
        self._journal[identifier] = {
            "status": outcome,
            "recorded_at": dt_util.now().isoformat(),
        }

    async def async_mark_dispense_start_sent(self, value):
        self._journal[value] = {
            "status": "outcome_unknown",
            "recorded_at": dt_util.now().isoformat(),
        }

    async def async_dispense_outcome_unknown(self, value):
        return self._journal.get(value, {}).get("status") == "outcome_unknown"

    async def async_close(self):
        return None


class RegistryCoordinator:
    def __init__(self, _hass, entry, session):
        self.entry, self.session = entry, session
        self.dispense_lock = asyncio.Lock()
        self.data = {"doses": {"dates": []}}
        self.first_refresh = AsyncMock()
        self.async_request_refresh = AsyncMock()
        self.async_refresh = AsyncMock()
        self.last_update_success = True
        self.device_tz = None

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
    from homeassistant.config_entries import ConfigEntryState

    entry.mock_state(hass, ConfigEntryState.LOADED)
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

    from homeassistant.auth.const import GROUP_ID_ADMIN
    from homeassistant.core import Context

    admin = await hass.auth.async_create_user("Admin User", group_ids=[GROUP_ID_ADMIN])
    admin_context = Context(user_id=admin.id)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISPENSE,
        {"config_entry_id": entry.entry_id},
        blocking=True,
        context=admin_context,
    )

    coordinator.async_refresh.assert_awaited_once()
    session.async_execute.assert_awaited_once()
    assert await session.async_last_dispense_id() == dose
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_invalid_dispense_service_registry_surfaces_validation_error(
    hass, monkeypatch
):
    entry = await _setup_registry_entry(hass, monkeypatch)
    from homeassistant.auth.const import GROUP_ID_ADMIN
    from homeassistant.core import Context

    admin = await hass.auth.async_create_user("Admin User", group_ids=[GROUP_ID_ADMIN])
    admin_context = Context(user_id=admin.id)

    with pytest.raises(ServiceValidationError, match="No eligible Hero scheduled dose"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISPENSE,
            {"config_entry_id": entry.entry_id},
            blocking=True,
            context=admin_context,
        )

    assert entry.runtime_data.coordinator.async_refresh.await_count == 1
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
    admin_user = SimpleNamespace(id="admin", is_admin=True)
    hass = SimpleNamespace(
        config_entries=FakeEntries([entry]),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin_user)),
    )
    call = SimpleNamespace(
        data={"config_entry_id": "entry"}, context=Context(user_id="admin")
    )
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
    admin_user = SimpleNamespace(id="admin", is_admin=True)
    hass = SimpleNamespace(
        config_entries=FakeEntries([entry]),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin_user)),
    )
    call = SimpleNamespace(
        data={"config_entry_id": "entry"}, context=Context(user_id="admin")
    )
    await _async_dispense(hass, call)
    assert session.last == dose
    with pytest.raises(ServiceValidationError, match="already dispensed"):
        await _async_dispense(hass, call)


@pytest.mark.asyncio
async def test_dispense_aborts_when_immediate_refresh_fails():
    session = FakeSession()
    entry = SimpleNamespace(entry_id="entry", runtime_data=None)
    coordinator = FakeCoordinator(None, entry, session)
    coordinator.last_update_success = True
    coordinator.data = {
        "doses": {
            "dates": [
                {
                    "times": [
                        {
                            "scheduled_datetime": dt_util.now().isoformat(),
                            "doses": [{"state": "time_to_take"}],
                        }
                    ]
                }
            ]
        }
    }

    # Immediate refresh fails and flips last_update_success to False
    async def failing_refresh():
        coordinator.refreshed += 1
        coordinator.last_update_success = False

    coordinator.async_refresh = failing_refresh
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    admin_user = SimpleNamespace(id="admin", is_admin=True)
    hass = SimpleNamespace(
        config_entries=FakeEntries([entry]),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin_user)),
    )
    call = SimpleNamespace(
        data={"config_entry_id": "entry"}, context=Context(user_id="admin")
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await _async_dispense(hass, call)

    assert exc_info.value.translation_key == "dose_state_unavailable"
    assert session.executed.await_count == 0
    assert coordinator.refreshed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        HeroDispenseOutcomeUnknown("unknown"),
        HeroConnectionError("offline"),
        HeroAuthenticationError("expired"),
    ],
)
async def test_dispense_translates_safety_failures(error):
    class FailingSession(FakeSession):
        async def async_execute(self, _operation):
            raise error

    session = FailingSession()
    entry = SimpleNamespace(entry_id="entry", runtime_data=None)
    coordinator = FakeCoordinator(None, entry, session)
    coordinator.data = {
        "doses": {
            "dates": [
                {
                    "times": [
                        {
                            "scheduled_datetime": dt_util.now().isoformat(),
                            "doses": [{"state": "time_to_take"}],
                        }
                    ]
                }
            ]
        }
    }
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    admin_user = SimpleNamespace(id="admin", is_admin=True)
    hass = SimpleNamespace(
        config_entries=FakeEntries([entry]),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin_user)),
    )
    call = SimpleNamespace(
        data={"config_entry_id": "entry"}, context=Context(user_id="admin")
    )

    with pytest.raises(HomeAssistantError):
        await _async_dispense(hass, call)


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
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
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
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
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
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
    )

    async def execute_status(operation):
        return await operation(status_exc_client)

    coord_status_exc = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=execute_status)
    )
    with pytest.raises(UpdateFailed):
        await coord_status_exc._async_update_data()


@pytest.mark.asyncio
async def test_scheduled_eligibility_refresh_is_deduplicated(hass, caplog):
    import logging

    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    coordinator = HeroCoordinator(hass, entry, SimpleNamespace())
    assert is_callback(coordinator.async_schedule_eligibility_refresh)

    event_a = asyncio.Event()
    refresh_count = 0

    async def blocking_refresh():
        nonlocal refresh_count
        refresh_count += 1
        if refresh_count == 1:
            await event_a.wait()

    coordinator.async_refresh = AsyncMock(side_effect=blocking_refresh)
    boundary_a = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)
    boundary_b = datetime(2026, 9, 11, 13, 0, tzinfo=dt_util.UTC)

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.hero_health.coordinator"
    ):
        # 1. Start boundary A refresh and deliberately block it with an asyncio.Event
        coordinator.async_schedule_eligibility_refresh(boundary_a)
        assert coordinator._last_eligibility_refresh_boundary == boundary_a
        assert coordinator._pending_eligibility_refresh_boundary is None

        # Duplicate call for same boundary A is deduplicated
        coordinator.async_schedule_eligibility_refresh(boundary_a)
        assert (
            "Scheduled-dose eligibility refresh already handled for this boundary"
            in caplog.text
        )

        # 2. Call async_schedule_eligibility_refresh(boundary_B)
        # while A is still running
        coordinator.async_schedule_eligibility_refresh(boundary_b)

        # 3. Verify B is retained as pending
        assert coordinator._pending_eligibility_refresh_boundary == boundary_b
        assert (
            "Scheduled-dose eligibility refresh queued behind active refresh"
            in caplog.text
        )

        # Duplicate B request while pending does not queue extra refreshes
        coordinator.async_schedule_eligibility_refresh(boundary_b)
        assert coordinator._pending_eligibility_refresh_boundary == boundary_b

        # 4. Release A
        event_a.set()
        await hass.async_block_till_done()

        # 5. Verify a second authoritative refresh occurs for B
        # 6. Verify the total refresh count is exactly 2
        assert coordinator.async_refresh.await_count == 2
        assert coordinator._last_eligibility_refresh_boundary == boundary_b
        assert coordinator._pending_eligibility_refresh_boundary is None

        # 7. Verify duplicate B requests do not cause a third refresh
        coordinator.async_schedule_eligibility_refresh(boundary_b)
        await hass.async_block_till_done()
        assert coordinator.async_refresh.await_count == 2

        assert (
            "Scheduled-dose authoritative Hero refresh completed successfully"
            in caplog.text
        )

    # Verify no sensitive data or timestamp logged
    assert "hero-1" not in caplog.text
    assert "entry-1" not in caplog.text
    assert "2026-09-11" not in caplog.text


@pytest.mark.asyncio
async def test_scheduled_eligibility_refresh_coalesces_newest_pending_boundary(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    coordinator = HeroCoordinator(hass, entry, SimpleNamespace())

    event_a = asyncio.Event()
    refresh_count = 0

    async def blocking_refresh():
        nonlocal refresh_count
        refresh_count += 1
        if refresh_count == 1:
            await event_a.wait()

    coordinator.async_refresh = AsyncMock(side_effect=blocking_refresh)
    boundary_a = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)
    boundary_b = datetime(2026, 9, 11, 13, 0, tzinfo=dt_util.UTC)
    boundary_c = datetime(2026, 9, 11, 14, 0, tzinfo=dt_util.UTC)

    # Start A
    coordinator.async_schedule_eligibility_refresh(boundary_a)
    # Queue B then C while A is running
    coordinator.async_schedule_eligibility_refresh(boundary_b)
    coordinator.async_schedule_eligibility_refresh(boundary_c)
    assert coordinator._pending_eligibility_refresh_boundary == boundary_c

    # Complete A, which should immediately process newest pending C
    event_a.set()
    await hass.async_block_till_done()

    assert coordinator.async_refresh.await_count == 2
    assert coordinator._last_eligibility_refresh_boundary == boundary_c
    assert coordinator._pending_eligibility_refresh_boundary is None


@pytest.mark.asyncio
async def test_scheduled_eligibility_refresh_logs_update_failure(hass, caplog):
    import logging

    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    coordinator = HeroCoordinator(hass, entry, SimpleNamespace())
    coordinator.last_update_success = False
    coordinator.async_refresh = AsyncMock()
    boundary = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.hero_health.coordinator"
    ):
        coordinator.async_schedule_eligibility_refresh(boundary)
        await hass.async_block_till_done()
        assert (
            "Scheduled-dose authoritative Hero refresh completed with update failure"
            in caplog.text
        )


@pytest.mark.asyncio
async def test_scheduled_eligibility_refresh_logs_exception(hass, caplog):
    import logging

    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    coordinator = HeroCoordinator(hass, entry, SimpleNamespace())
    coordinator.async_refresh = AsyncMock(side_effect=RuntimeError("refresh explosion"))
    boundary = datetime(2026, 9, 11, 12, 0, tzinfo=dt_util.UTC)

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.hero_health.coordinator"
    ):
        coordinator.async_schedule_eligibility_refresh(boundary)
        await hass.async_block_till_done()
        assert "Scheduled-dose authoritative Hero refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_required_dose_failure_does_not_replace_prior_snapshot(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(side_effect=HeroConnectionError("temporary")),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
    )

    async def execute(operation):
        return await operation(client)

    coordinator = HeroCoordinator(hass, entry, SimpleNamespace(async_execute=execute))
    coordinator.data = {"doses": {"dates": [{"times": [{"scheduled_datetime": "x"}]}]}}
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.data["doses"]["dates"]


@pytest.mark.asyncio
async def test_required_dose_rate_limit_is_not_swallowed(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(side_effect=HeroRateLimitError(17)),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
    )

    async def execute(operation):
        return await operation(client)

    coordinator = HeroCoordinator(hass, entry, SimpleNamespace(async_execute=execute))
    with pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()
    assert err.value.retry_after == 17


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_doses", [None, [], {"dates": {}}])
async def test_malformed_required_doses_fail_snapshot(hass, bad_doses):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(return_value=bad_doses),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
    )

    async def execute(operation):
        return await operation(client)

    with pytest.raises(UpdateFailed):
        await HeroCoordinator(
            hass, entry, SimpleNamespace(async_execute=execute)
        )._async_update_data()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_offline", "bad_status", "bad_config"),
    [
        (None, {}, {"config": {"pills": []}}),
        ([], {}, {"config": {"pills": []}}),
        ({}, None, {"config": {"pills": []}}),
        ({}, "online", {"config": {"pills": []}}),
        ({}, {}, None),
        ({}, {}, []),
        ({}, {}, {"config": None}),
        ({}, {}, {"config": {"pills": "not-a-list"}}),
    ],
)
async def test_malformed_required_endpoints_fail_snapshot(
    hass, bad_offline, bad_status, bad_config
):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")
    client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value=bad_offline),
        user_status=AsyncMock(return_value=bad_status),
        last_d2d_config=AsyncMock(return_value=bad_config),
        home_screen_doses=AsyncMock(return_value={"dates": []}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(return_value={"schedules": []}),
    )

    async def execute(operation):
        return await operation(client)

    coordinator = HeroCoordinator(hass, entry, SimpleNamespace(async_execute=execute))
    coordinator.data = {"doses": {"dates": []}}
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    # Prior good data preserved
    assert coordinator.data["doses"]["dates"] == []


@pytest.mark.asyncio
async def test_pills_by_schedules_variations(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")

    # Success populates schedules
    good_client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(return_value={"dates": []}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(
            return_value={"schedules": [{"schedule_id": "1"}]}
        ),
    )
    coord = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=lambda op: op(good_client))
    )
    data = await coord._async_update_data()
    assert data["schedules"] == {"schedules": [{"schedule_id": "1"}]}

    # Connection failure on optional schedules endpoint does not fail snapshot
    conn_fail_client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(return_value={"dates": []}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(side_effect=HeroConnectionError("conn timeout")),
    )
    coord2 = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=lambda op: op(conn_fail_client))
    )
    data2 = await coord2._async_update_data()
    assert data2["schedules"] is None
    assert data2["doses"]["dates"] == []

    # Rate limit error on optional schedules endpoint does not fail snapshot
    rate_fail_client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(return_value={"dates": []}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(side_effect=HeroRateLimitError(60)),
    )
    coord3 = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=lambda op: op(rate_fail_client))
    )
    data3 = await coord3._async_update_data()
    assert data3["schedules"] is None

    # Auth error on optional schedules endpoint raises ConfigEntryAuthFailed
    auth_fail_client = SimpleNamespace(
        check_hero_offline=AsyncMock(return_value={"hero_offline": False}),
        user_status=AsyncMock(return_value={}),
        last_d2d_config=AsyncMock(return_value={"config": {"pills": []}}),
        home_screen_doses=AsyncMock(return_value={"dates": []}),
        get_home_screen_events=AsyncMock(return_value={}),
        stats=AsyncMock(return_value={}),
        pills_by_schedules=AsyncMock(
            side_effect=HeroAuthenticationError("auth failed")
        ),
    )
    coord4 = HeroCoordinator(
        hass, entry, SimpleNamespace(async_execute=lambda op: op(auth_fail_client))
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord4._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_rate_limit_retry_after_fallback(hass):
    entry = SimpleNamespace(entry_id="entry-1", unique_id="hero-1")

    async def execute_none(_operation):
        raise HeroRateLimitError(None)

    with pytest.raises(UpdateFailed) as raised:
        await HeroCoordinator(
            hass, entry, SimpleNamespace(async_execute=execute_none)
        )._async_update_data()
    assert raised.value.retry_after == 300

    async def execute_unexpected(_operation):
        raise TypeError("unexpected type error")

    with pytest.raises(UpdateFailed, match="Hero returned an unexpected snapshot"):
        await HeroCoordinator(
            hass, entry, SimpleNamespace(async_execute=execute_unexpected)
        )._async_update_data()


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


@pytest.mark.asyncio
async def test_setup_translates_auth_failure_to_config_entry_auth_failed(monkeypatch):
    class FailingAuthSession(FakeSession):
        async def async_initialize(self):
            raise HeroAuthenticationError("invalid credentials")

    entry = SimpleNamespace(
        entry_id="entry",
        data={"email": "test@example.invalid", "password": "fake"},
        runtime_data=None,
    )
    hass = SimpleNamespace(services=FakeServices(), config_entries=FakeEntries([entry]))
    monkeypatch.setattr("custom_components.hero_health.HeroSession", FailingAuthSession)
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_unload_entry_preserves_runtime_data_when_platforms_fail_unload():
    session = FakeSession()
    coordinator = FakeCoordinator(None, None, session)
    entry = SimpleNamespace(
        entry_id="entry",
        runtime_data=SimpleNamespace(session=session, coordinator=coordinator),
    )

    class FailingUnloadEntries(FakeEntries):
        async def async_unload_platforms(self, _entry, _platforms):
            return False

    hass = SimpleNamespace(config_entries=FailingUnloadEntries([entry]))
    result = await async_unload_entry(hass, entry)
    assert result is False
    assert entry.runtime_data is not None
    assert session.closed is False


@pytest.mark.asyncio
async def test_coordinator_for_entry_id_rejects_unloaded_entry():
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.hero_health import _coordinator_for_entry_id

    entry = SimpleNamespace(
        entry_id="entry-unloaded",
        domain=DOMAIN,
        state=ConfigEntryState.NOT_LOADED,
        runtime_data=None,
    )
    hass = SimpleNamespace(config_entries=FakeEntries([entry]))
    with pytest.raises(ServiceValidationError, match="not loaded"):
        _coordinator_for_entry_id(hass, "entry-unloaded")


@pytest.mark.asyncio
async def test_dispense_dose_ignores_refresh_failure_after_success():
    from custom_components.hero_health import async_dispense_dose

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

    async def failing_request_refresh():
        raise RuntimeError("refresh failed")

    coordinator.async_request_refresh = failing_request_refresh
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    admin_user = SimpleNamespace(id="admin", is_admin=True)
    hass = SimpleNamespace(
        config_entries=FakeEntries([entry]),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin_user)),
    )

    await async_dispense_dose(hass, "entry", context=Context(user_id="admin"))
    assert session.last == dose


@pytest.mark.asyncio
async def test_admin_services_registered_with_admin_permission(hass, monkeypatch):
    import homeassistant.exceptions
    from homeassistant.core import Context

    entry = await _setup_registry_entry(hass, monkeypatch)
    admin_service = hass.services.has_service(DOMAIN, SERVICE_DISPENSE)
    assert admin_service is True

    from homeassistant.auth.const import GROUP_ID_USER

    user = await hass.auth.async_create_user("Regular User", group_ids=[GROUP_ID_USER])
    user.is_owner = False
    assert user.is_admin is False
    non_admin_context = Context(user_id=user.id)

    with pytest.raises(homeassistant.exceptions.Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISPENSE,
            {"config_entry_id": entry.entry_id},
            context=non_admin_context,
            blocking=True,
        )
