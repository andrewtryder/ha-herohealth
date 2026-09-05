"""Session refresh and diagnostics privacy regression tests."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from custom_components.hero_health.api.models import HeroTokens
from custom_components.hero_health.diagnostics import async_get_config_entry_diagnostics
from custom_components.hero_health.session import HeroSession


class FakeStore:
    def __init__(self, data=None):
        self.data = data or {}

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


class FakeAuth:
    def __init__(self):
        self.refreshes = 0
        self.logins = 0

    async def refresh_access_token(self, _token):
        self.refreshes += 1
        await asyncio.sleep(0)
        return HeroTokens("new-access", "", 3600, time.time())

    async def login_with_password(self, _email, _password):
        self.logins += 1
        return HeroTokens("login-access", "login-refresh", 3600, 1)


@pytest.mark.asyncio
async def test_expired_token_refresh_is_serialized_and_preserves_refresh_token():
    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._tokens = HeroTokens("old", "refresh", 1, 0)
    session._auth = FakeAuth()
    session._email = "test@example.invalid"
    session._password = "secret"
    session._persist = True
    session._store = FakeStore()
    session.client = None
    await asyncio.gather(session._async_ensure_tokens(), session._async_ensure_tokens())
    assert session._auth.refreshes == 1
    assert session._tokens.refresh_token == "refresh"
    assert session._store.data["tokens"]["access_token"] == "new-access"


@pytest.mark.asyncio
async def test_dispense_ambiguity_persists_and_completion_clears_it():
    session = object.__new__(HeroSession)
    session._persist = True
    session._tokens = HeroTokens("access", "refresh", 3600, time.time())
    session._identity = {"email": "user@example.invalid", "account_id": "account"}
    session._store = FakeStore()
    session._state_lock = asyncio.Lock()

    await session.async_mark_dispense_start_sent("scheduled")
    assert await session.async_dispense_outcome_unknown("scheduled")
    assert session._store.data["dispense_attempt"] == {
        "state": "outcome_unknown",
        "scheduled_datetime": "scheduled",
    }

    await session.async_save_dispense_id("scheduled")
    assert not await session.async_dispense_outcome_unknown("scheduled")
    assert "dispense_attempt" not in session._store.data


@pytest.mark.asyncio
async def test_diagnostics_redacts_credentials_and_health_data():
    entry = SimpleNamespace(
        entry_id="entry",
        data={
            "email": "test@example.invalid",
            "password": "secret",
            "account_id": "account",
        },
        options={"scan_interval": 60, "future_secret": "private-option"},
        runtime_data=SimpleNamespace(coordinator=None),
    )
    coordinator = SimpleNamespace(
        data={
            "medications": [{"name": "Example medication", "exact_pill_count": 3}],
            "access_token": "token",
            "device_nickname": "Bedroom",
            "doses": {"private-dose-time": {"private-device-id": "private"}},
            "events": {"private-event": "private"},
            "stats": {"private-stat": "private"},
            "status": {"serial_number": "private-serial"},
        }
    )
    entry.runtime_data.coordinator = coordinator
    hass = SimpleNamespace()
    result = await async_get_config_entry_diagnostics(hass, entry)
    rendered = repr(result)
    for private_value in (
        "test@example.invalid",
        "secret",
        "account",
        "Example medication",
        "token",
        "Bedroom",
        "private-option",
        "private-dose-time",
        "private-device-id",
        "private-event",
        "private-stat",
        "private-serial",
    ):
        assert private_value not in rendered
    assert result["coordinator"]["medications"] == {"usable": True, "count": 1}
    assert result["coordinator"]["schedules"] == {"usable": False}
    assert result["entry"] == {"has_unique_id": False, "scan_interval": 60}


@pytest.mark.asyncio
async def test_diagnostics_privacy_with_sentinels():
    PRIVATE_SERIAL_SENTINEL = "PRIVATE_SERIAL_XYZ_999"
    PRIVATE_SCHEDULE_ID_SENTINEL = "PRIVATE_SCHEDULE_ID_ABC_123"
    PRIVATE_MEDICATION_SENTINEL = "PRIVATE_MEDICATION_LIPITOR_10MG"
    PRIVATE_TIMESTAMP_SENTINEL = "2026-09-05T12:34:56+00:00"
    PRIVATE_ACCOUNT_SENTINEL = "PRIVATE_ACCOUNT_ID_77777"
    PRIVATE_TIMEZONE_SENTINEL = "America/Secret_Zone"

    entry = SimpleNamespace(
        entry_id="entry_id_123",
        unique_id=PRIVATE_ACCOUNT_SENTINEL,
        data={
            "email": "test@example.invalid",
            "password": "secret_password",
            "account_id": PRIVATE_ACCOUNT_SENTINEL,
        },
        options={"scan_interval": 30},
        runtime_data=SimpleNamespace(coordinator=None),
    )
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={
            "offline": {"hero_offline": False},
            "status": {
                "serial": PRIVATE_SERIAL_SENTINEL,
                "device_timezone": PRIVATE_TIMEZONE_SENTINEL,
                "device_manifest": {"family": 1, "model": 1},
            },
            "medications": [
                {"name": PRIVATE_MEDICATION_SENTINEL, "exact_pill_count": 5}
            ],
            "doses": {
                "dates": [
                    {
                        "date": "2026-09-05",
                        "times": [
                            {
                                "scheduled_datetime": PRIVATE_TIMESTAMP_SENTINEL,
                                "doses": [{"name": PRIVATE_MEDICATION_SENTINEL}],
                            }
                        ],
                    }
                ]
            },
            "schedules": {
                "schedules": [
                    {
                        "schedule_id": PRIVATE_SCHEDULE_ID_SENTINEL,
                        "dow": "Mon",
                        "time": "08:00",
                    }
                ],
                "pending_changes": False,
            },
            "events": {"event_id": "event_123"},
            "stats": {"stats": {"adherence": 100}},
        },
    )
    entry.runtime_data.coordinator = coordinator
    result = await async_get_config_entry_diagnostics(SimpleNamespace(), entry)

    import json

    serialized = json.dumps(result)

    assert PRIVATE_SERIAL_SENTINEL not in serialized
    assert PRIVATE_SCHEDULE_ID_SENTINEL not in serialized
    assert PRIVATE_MEDICATION_SENTINEL not in serialized
    assert PRIVATE_TIMESTAMP_SENTINEL not in serialized
    assert PRIVATE_ACCOUNT_SENTINEL not in serialized
    assert PRIVATE_TIMEZONE_SENTINEL not in serialized
    assert result["coordinator"]["schedules"] == {"usable": True}
