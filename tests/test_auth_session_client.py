"""Authentication, session ownership, and REST client behavior without network I/O."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hero_health.api.auth import (
    HeroAuthClient,
    parse_callback,
    parse_login_fields,
)
from custom_components.hero_health.api.client import HeroCloudClient
from custom_components.hero_health.api.exceptions import (
    HeroApiError,
    HeroAuthenticationError,
    HeroConnectionError,
    HeroRateLimitError,
)
from custom_components.hero_health.api.models import (
    HeroDose,
    HeroMedication,
    HeroTokens,
)
from custom_components.hero_health.session import HeroSession


class Response:
    def __init__(
        self,
        status=200,
        text="",
        json_data=None,
        headers=None,
        url="https://id.herohealth.com/login/",
    ):
        self.status, self._text, self._json = status, text, json_data
        self.headers, self.url = headers or {}, url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self):
        return self._text

    async def json(self, **_kwargs):
        return self._json


class Http:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self.responses.pop(0)

    def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_password_login_and_token_requests_use_sanitized_protocol():
    http = Http(
        [
            Response(
                text=(
                    '<input name="csrfmiddlewaretoken" value="csrf">'
                    '<input name="visitor_id" value="visitor">'
                )
            ),
            Response(status=302, headers={"Location": "heroapp://auth?code=fake-code"}),
            Response(
                json_data={
                    "access_token": "fake-access",
                    "refresh_token": "fake-refresh",
                    "expires_in": 3600,
                }
            ),
            Response(json_data={"access_token": "new-access", "expires_in": 3600}),
        ]
    )
    auth = HeroAuthClient(http)
    tokens = await auth.login_with_password("test@example.invalid", "fake-password")
    assert tokens.access_token == "fake-access"
    assert http.calls[1][2]["data"]["password"] == "fake-password"
    refreshed = await auth.refresh_access_token("fake-refresh")
    assert refreshed.access_token == "new-access"


@pytest.mark.asyncio
async def test_auth_errors_are_sanitized():
    auth = HeroAuthClient(Http([Response(status=401)]))
    with pytest.raises(HeroAuthenticationError):
        await auth.login_with_password("test@example.invalid", "fake-password")
    auth = HeroAuthClient(Http([Response(json_data={"not": "tokens"})]))
    with pytest.raises(HeroApiError):
        await auth.refresh_access_token("fake-refresh")
    with pytest.raises(HeroAuthenticationError):
        parse_login_fields("<html></html>")


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("heroapp://auth?code=example&state=expected", "example"),
        ("heroapp://auth?code=example", "example"),
    ],
)
def test_callback_validation_accepts_observed_optional_state(location, expected):
    assert parse_callback(location, "expected") == expected


@pytest.mark.parametrize(
    "location",
    [
        "https://auth?code=example&state=expected",
        "heroapp://other?code=example&state=expected",
        "heroapp://auth?code=example&state=wrong",
        "heroapp://auth?state=expected",
    ],
)
def test_callback_validation_rejects_unexpected_redirects(location):
    with pytest.raises(HeroAuthenticationError):
        parse_callback(location, "expected")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, HeroAuthenticationError),
        (429, HeroRateLimitError),
        (503, HeroConnectionError),
    ],
)
async def test_token_http_errors_distinguish_auth_and_transient_failures(status, error):
    auth = HeroAuthClient(Http([Response(status=status, headers={"Retry-After": "5"})]))
    with pytest.raises(error):
        await auth.refresh_access_token("fake-refresh")


@pytest.mark.asyncio
async def test_client_headers_requests_and_endpoint_wrappers():
    http = Http([Response(text='{"ok": true}'), Response(status=401)])
    client = HeroCloudClient(
        http, "fake-access", "fake-account", base_url="https://example.invalid"
    )
    assert client._headers()["x-hero-account"] == "fake-account"
    assert client._headers()["authorization"] == "Bearer fake-access"
    assert (await client._request("/test")) == {"ok": True}
    with pytest.raises(HeroAuthenticationError):
        await client._request("/test")
    client = HeroCloudClient(Http([Response(status=500)]), "fake-access")
    with pytest.raises(HeroApiError):
        await client._request("/test")
    client = HeroCloudClient(Http([Response(text="not-json")]), "fake-access")
    with pytest.raises(HeroApiError):
        await client._request("/test")
    client._request = AsyncMock(return_value={})
    await client.check_app_version()
    await client.caregiver_patient_list()
    await client.user_status("chosen")
    await client.check_hero_offline()
    await client.last_d2d_config()
    await client.home_screen_doses()
    await client.pills_by_schedules()
    await client.get_home_screen_events()
    await client.stats("2026-01-01")
    await client.insights_list("2026-01-01")
    await client.milestones_list()
    assert client._request.await_count == 11


@pytest.mark.asyncio
async def test_session_execute_storage_and_close_paths():
    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._tokens = HeroTokens("access", "refresh", 3600, 9999999999)
    session._persist = True
    session._store = SimpleNamespace(
        async_load=AsyncMock(return_value={}), async_save=AsyncMock()
    )
    session._http = Http([])
    session.client = SimpleNamespace(set_tokens=lambda _token: None)
    session._async_ensure_tokens = AsyncMock()
    assert await session.async_execute(AsyncMock(return_value="ok")) == "ok"
    operation = AsyncMock(side_effect=[HeroAuthenticationError("expired"), "retried"])
    assert await session.async_execute(operation) == "retried"
    assert session._async_ensure_tokens.await_count == 3
    await session.async_save_dispense_id("dose")
    assert session._store.async_save.await_count == 1
    session._store.async_load = AsyncMock(return_value={"last_dispense_id": "dose"})
    assert await session.async_last_dispense_id() == "dose"
    await session.async_close()
    assert not session._http.closed


def test_models_validate_and_normalize_values():
    assert (
        HeroTokens.from_dict(
            {
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 1,
                "created_at": 1,
            }
        ).as_dict()["access_token"]
        == "a"
    )
    assert HeroMedication.from_api({"slot": "2", "name": "Example"}).slot == 2
    assert HeroDose("2026-01-01T00:00:00+00:00", ["time_to_take"], []).has_time_to_take
    with pytest.raises(ValueError):
        HeroTokens.from_response({}, 0)
