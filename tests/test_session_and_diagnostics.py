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
async def test_diagnostics_redacts_credentials_and_health_data():
    entry = SimpleNamespace(
        entry_id="entry",
        data={
            "email": "test@example.invalid",
            "password": "secret",
            "account_id": "account",
        },
        runtime_data=SimpleNamespace(coordinator=None),
    )
    coordinator = SimpleNamespace(
        data={
            "medications": [{"name": "Example medication", "exact_pill_count": 3}],
            "access_token": "token",
            "device_nickname": "Bedroom",
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
    ):
        assert private_value not in rendered
    assert result["coordinator"]["medications"] == {"usable": True, "count": 1}
