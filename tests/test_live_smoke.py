"""Offline unit tests for development-only live Hero smoke tooling."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "live_smoke.py"
_SPEC = importlib.util.spec_from_file_location("live_smoke", _SCRIPT)
assert _SPEC and _SPEC.loader
live_smoke = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(live_smoke)


def test_select_account_id_uses_only_available_account():
    assert live_smoke.select_account_id([{"account_id": "one"}], None) == "one"


def test_select_account_id_requires_valid_choice_for_multiple_accounts():
    accounts = [{"account_id": "one"}, {"account_id": "two"}]
    with pytest.raises(live_smoke.LiveSmokeError, match="HERO_ACCOUNT_ID"):
        live_smoke.select_account_id(accounts, None)
    with pytest.raises(live_smoke.LiveSmokeError, match="did not match"):
        live_smoke.select_account_id(accounts, "three")
    assert live_smoke.select_account_id(accounts, "two") == "two"


def test_structural_counts_do_not_include_medication_contents():
    assert live_smoke.structural_counts(
        {"config": {"pills": [{"name": "private"}, {"name": "private"}]}},
        {
            "dates": [
                {"times": [{"doses": [{}, {}]}, {"doses": [{}]}]},
                {"times": [{"doses": []}]},
            ]
        },
    ) == (3, 2, 3)


@pytest.mark.asyncio
async def test_async_run_uses_only_approved_read_only_calls(monkeypatch):
    class Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, _http, _access_token):
            self.account_id = None
            self.caregiver_patient_list = AsyncMock(
                return_value=[{"account_id": "account"}]
            )
            self.check_hero_offline = AsyncMock(return_value={})
            self.user_status = AsyncMock(return_value={})
            self.last_d2d_config = AsyncMock(return_value={"config": {"pills": []}})
            self.home_screen_doses = AsyncMock(return_value={"dates": []})
            self.pills_by_schedules = AsyncMock(return_value={})
            self.get_home_screen_events = AsyncMock(return_value={})
            self.stats = AsyncMock(return_value={})

    client: Client | None = None

    def make_client(*args):
        nonlocal client
        client = Client(*args)
        return client

    monkeypatch.setenv("HERO_EMAIL", "user@example.invalid")
    monkeypatch.setenv("HERO_PASSWORD", "private-password")
    monkeypatch.delenv("HERO_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(live_smoke.aiohttp, "ClientSession", lambda **_kwargs: Http())
    monkeypatch.setattr(
        live_smoke,
        "HeroAuthClient",
        lambda _http: SimpleNamespace(
            login_with_password=AsyncMock(
                return_value=SimpleNamespace(access_token="private-token")
            )
        ),
    )
    monkeypatch.setattr(live_smoke, "HeroCloudClient", make_client)

    report = await live_smoke.async_run()

    assert client is not None
    assert client.account_id == "account"
    assert client.check_hero_offline.await_count == 1
    assert client.user_status.await_count == 1
    assert client.last_d2d_config.await_count == 1
    assert client.home_screen_doses.await_count == 1
    assert client.pills_by_schedules.await_count == (
        1 if os.environ.get("HERO_SMOKE_SCHEMA") == "1" else 0
    )
    assert client.get_home_screen_events.await_count == 1
    assert client.stats.await_count == 1
    assert "Read-only smoke test completed successfully." in report
    assert "private-token" not in "\n".join(report)


def test_schema_lines_never_render_private_values_or_keys():
    report = "\n".join(
        live_smoke.schema_lines(
            {
                "device": {"serial": "private-serial"},
                "private-key": {"name": "private-med"},
            }
        )
    )
    assert "private-serial" not in report
    assert "private-med" not in report
    assert "private-key" not in report
    assert "device.serial: str" in report


def test_schema_lines_inspect_all_list_entries():
    report = "\n".join(
        live_smoke.schema_lines([{"config": {}}, {"device": {"model": "x"}}])
    )
    assert "[].config: dict" in report
    assert "[].device.model: str" in report


@pytest.mark.asyncio
async def test_schema_mode_calls_schedule_endpoint(monkeypatch):
    monkeypatch.setenv("HERO_SMOKE_SCHEMA", "1")
    await test_async_run_uses_only_approved_read_only_calls(monkeypatch)
