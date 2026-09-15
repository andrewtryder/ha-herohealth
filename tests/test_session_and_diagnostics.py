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


@pytest.mark.asyncio
async def test_session_dispense_journal_pruning_and_blocking():
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    session = object.__new__(HeroSession)
    session._persist = True
    session._tokens = HeroTokens("access", "refresh", 3600, time.time())
    session._identity = {"email": "user@example.invalid", "account_id": "account"}
    session._store = FakeStore()
    session._state_lock = asyncio.Lock()
    session._dispense_journal = {}

    now = dt_util.now()
    old_dose = (now - timedelta(hours=7)).isoformat()
    recent_dose = (now - timedelta(minutes=5)).isoformat()
    unknown_dose = (now - timedelta(minutes=2)).isoformat()

    await session.async_save_dispense_id(old_dose)
    await session.async_save_dispense_id(recent_dose)
    await session.async_mark_dispense_start_sent(unknown_dose)

    # Prune should remove old_dose because it's past DISPENSE_LATE_WINDOW (6h)
    session._prune_dispense_journal()
    assert old_dose not in session.dispense_journal
    assert recent_dose in session.dispense_journal
    assert unknown_dose in session.dispense_journal

    blocked, reason = session.is_dispense_blocked(recent_dose)
    assert blocked is True
    assert reason == "duplicate_recent_dose"

    blocked, reason = session.is_dispense_blocked(unknown_dose)
    assert blocked is True
    assert reason == "dispense_outcome_unknown"

    blocked, reason = session.is_dispense_blocked("not-in-journal")
    assert blocked is False
    assert reason is None


@pytest.mark.asyncio
async def test_session_non_persist_paths():
    from unittest.mock import Mock

    session = object.__new__(HeroSession)
    session._persist = False
    mock_http = SimpleNamespace(detach=Mock())
    session._http = mock_http
    session._dispense_journal = {}

    await session.async_save_dispense_id("dose-1")
    assert await session.async_last_dispense_id() is None
    await session.async_mark_dispense_start_sent("dose-2")
    assert await session.async_dispense_outcome_unknown("dose-2") is False

    await session.async_close()
    mock_http.detach.assert_called_once()


@pytest.mark.asyncio
async def test_session_legacy_state_migration():
    session = object.__new__(HeroSession)
    session._persist = True
    session._email = "e@x.com"
    session.account_id = "acc-1"
    session._lock = asyncio.Lock()
    session._state_lock = asyncio.Lock()
    session._identity = {"email": "e@x.com", "account_id": "acc-1"}
    now_ts = time.time()
    session._store = FakeStore(
        {
            "last_dispense_id": "legacy_dose_1",
            "dispense_attempt": {
                "state": "outcome_unknown",
                "scheduled_datetime": "legacy_dose_2",
            },
            "tokens": {
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 3600,
                "created_at": now_ts,
            },
            "identity": {"email": "e@x.com", "account_id": "acc-1"},
        }
    )
    session._http = SimpleNamespace()
    session._auth = SimpleNamespace()
    session._dispense_journal = {}
    await session.async_initialize()
    assert "legacy_dose_1" in session.dispense_journal
    assert session.dispense_journal["legacy_dose_1"]["status"] == "completed"
    assert "legacy_dose_2" in session.dispense_journal
    assert session.dispense_journal["legacy_dose_2"]["status"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_session_execute_reauth_fallback():
    from unittest.mock import Mock

    from custom_components.hero_health.api.exceptions import HeroAuthenticationError

    session = object.__new__(HeroSession)
    session._lock = asyncio.Lock()
    session._state_lock = asyncio.Lock()
    session._tokens = HeroTokens("bad", "bad-ref", 3600, 0)
    session._email = "user@example.invalid"
    session._password = "secret"
    session._persist = False
    session._store = FakeStore()
    session.client = SimpleNamespace(set_tokens=Mock())

    class FailingAuth:
        def __init__(self):
            self.refresh_calls = 0
            self.login_calls = 0

        async def refresh_access_token(self, _tok):
            self.refresh_calls += 1
            raise HeroAuthenticationError("refresh failed")

        async def login_with_password(self, _email, _pwd):
            self.login_calls += 1
            return HeroTokens("good-acc", "good-ref", 3600, time.time())

    session._auth = FailingAuth()

    first_attempt = True

    async def op(client):
        nonlocal first_attempt
        if first_attempt:
            first_attempt = False
            raise HeroAuthenticationError("expired")
        return "success"

    result = await session.async_execute(op)
    assert result == "success"
    assert session._auth.login_calls >= 1


@pytest.mark.asyncio
async def test_session_journal_pruning_timezone_aware(monkeypatch):
    """Timezone-aware timestamps expire exactly at scheduled instant + 6 hours."""
    from datetime import UTC, datetime

    session = object.__new__(HeroSession)
    session._persist = True
    session._store = FakeStore()
    session._state_lock = asyncio.Lock()
    session._dispense_journal = {}
    session.device_tz = None

    # Scheduled at 2026-09-14 12:00:00 UTC
    sched_dt = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
    now_ts = sched_dt.timestamp()
    monkeypatch.setattr(time, "time", lambda: now_ts)

    sched_str = sched_dt.isoformat()
    await session.async_save_dispense_id(sched_str)

    entry = session.dispense_journal[sched_str]
    assert "expires_at" in entry
    expected_expiry = sched_dt.timestamp() + 21600.0  # +6h
    assert entry["expires_at"] == pytest.approx(expected_expiry)

    # 5 hours 59 minutes after scheduled instant: must NOT be pruned
    monkeypatch.setattr(time, "time", lambda: expected_expiry - 60)
    session._prune_dispense_journal()
    assert sched_str in session.dispense_journal
    blocked, reason = session.is_dispense_blocked(sched_str)
    assert blocked is True
    assert reason == "duplicate_recent_dose"

    # 6 hours 1 second after scheduled instant: must be pruned
    monkeypatch.setattr(time, "time", lambda: expected_expiry + 1)
    session._prune_dispense_journal()
    assert sched_str not in session.dispense_journal
    blocked, reason = session.is_dispense_blocked(sched_str)
    assert blocked is False


@pytest.mark.asyncio
async def test_session_journal_pruning_naive_with_us_eastern_timezone(monkeypatch):
    """Naive Hero schedules use confirmed device_tz and never expire early."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    session = object.__new__(HeroSession)
    session._persist = True
    session._store = FakeStore()
    session._state_lock = asyncio.Lock()
    session._dispense_journal = {}
    session.device_tz = eastern

    # Naive Hero schedule: 19:15 local time (EDT, UTC-4)
    # 19:15 EDT == 23:15 UTC.
    sched_str = "2026-09-14T19:15:00"
    true_scheduled_instant = datetime(2026, 9, 14, 19, 15, 0, tzinfo=eastern)
    now_ts = true_scheduled_instant.timestamp()
    monkeypatch.setattr(time, "time", lambda: now_ts)

    await session.async_save_dispense_id(sched_str)

    entry = session.dispense_journal[sched_str]
    assert "expires_at" in entry

    # Expected expiration: 23:15 UTC + 6 hours = 05:15 UTC next day
    expected_expiry = true_scheduled_instant.timestamp() + 21600.0
    assert entry["expires_at"] == pytest.approx(expected_expiry)

    # If naive was mistakenly parsed as UTC, it would have expired at
    # 19:15 UTC + 6h = 01:15 UTC next day.
    # At 02:00 UTC (4h 45m after scheduled EDT time), it is STILL in the late window!
    utc_test_time_at_0200 = datetime(2026, 9, 15, 2, 0, 0, tzinfo=UTC).timestamp()
    monkeypatch.setattr(time, "time", lambda: utc_test_time_at_0200)
    session._prune_dispense_journal()
    assert sched_str in session.dispense_journal
    blocked, reason = session.is_dispense_blocked(sched_str)
    assert blocked is True
    assert reason == "duplicate_recent_dose"

    # At 05:14 UTC (5h 59m after scheduled time): still present
    monkeypatch.setattr(time, "time", lambda: expected_expiry - 60)
    session._prune_dispense_journal()
    assert sched_str in session.dispense_journal

    # At 05:16 UTC (6h 1m after scheduled time): pruned
    monkeypatch.setattr(time, "time", lambda: expected_expiry + 60)
    session._prune_dispense_journal()
    assert sched_str not in session.dispense_journal


@pytest.mark.asyncio
async def test_session_journal_dst_transition_preserves_safety(monkeypatch):
    """DST transitions in device timezone correctly anchor expiration instant."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    session = object.__new__(HeroSession)
    session._persist = True
    session._store = FakeStore()
    session._state_lock = asyncio.Lock()
    session._dispense_journal = {}
    session.device_tz = eastern

    # Spring forward date in US: 2026-03-08
    sched_dt = datetime(2026, 3, 8, 3, 30, 0, tzinfo=eastern)
    now_ts = sched_dt.timestamp()
    monkeypatch.setattr(time, "time", lambda: now_ts)

    sched_str = "2026-03-08T03:30:00"
    await session.async_mark_dispense_start_sent(sched_str)

    entry = session.dispense_journal[sched_str]
    assert entry["status"] == "outcome_unknown"
    assert "expires_at" in entry

    # Verify blocked status for outcome_unknown
    blocked, reason = session.is_dispense_blocked(sched_str)
    assert blocked is True
    assert reason == "dispense_outcome_unknown"

    # Within 6 hours of expiry
    monkeypatch.setattr(time, "time", lambda: entry["expires_at"] - 10)
    session._prune_dispense_journal()
    assert sched_str in session.dispense_journal

    # Past 6 hours of expiry
    monkeypatch.setattr(time, "time", lambda: entry["expires_at"] + 10)
    session._prune_dispense_journal()
    assert sched_str not in session.dispense_journal


@pytest.mark.asyncio
async def test_legacy_journal_records_load_safely_and_backfill_expiry(monkeypatch):
    """Legacy journal entries without expires_at load safely and compute expiry."""
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    now_ts = 1789427700.0  # Approx 2026-09-14 23:15:00 UTC
    monkeypatch.setattr(time, "time", lambda: now_ts)

    session = object.__new__(HeroSession)
    session._persist = True
    session._email = "user@example.invalid"
    session.account_id = "acc"
    session._lock = asyncio.Lock()
    session._state_lock = asyncio.Lock()
    session._identity = {"email": "user@example.invalid", "account_id": "acc"}
    session.device_tz = None
    session._store = FakeStore(
        {
            "identity": {"email": "user@example.invalid", "account_id": "acc"},
            "tokens": {
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 3600,
                "created_at": now_ts,
            },
            "dispense_journal": {
                "2026-09-14T19:15:00": {
                    "status": "completed",
                    "recorded_at": now_ts,
                },
                "2026-09-14T20:00:00": {
                    "status": "outcome_unknown",
                    "recorded_at": now_ts,
                    "expires_at": now_ts + 21600.0,
                },
            },
        }
    )
    session._http = SimpleNamespace()
    session._auth = SimpleNamespace()
    session._dispense_journal = {}

    await session.async_initialize()

    # Legacy record without expires_at loads without error
    assert "2026-09-14T19:15:00" in session.dispense_journal
    # Existing record with expires_at preserves its value
    expected_val = now_ts + 21600.0
    assert session.dispense_journal["2026-09-14T20:00:00"]["expires_at"] == expected_val

    # Setting device_tz backfills missing expires_at using eastern timezone
    session.device_tz = eastern
    assert "expires_at" in session.dispense_journal["2026-09-14T19:15:00"]
    assert session.dispense_journal["2026-09-14T19:15:00"]["expires_at"] > now_ts


@pytest.mark.asyncio
async def test_malformed_timestamp_journal_fails_conservative(monkeypatch):
    """Malformed timestamps fail conservative: never pruned early, kept for 48h."""
    now_ts = 1000000.0
    monkeypatch.setattr(time, "time", lambda: now_ts)

    session = object.__new__(HeroSession)
    session._persist = True
    session._store = FakeStore()
    session._state_lock = asyncio.Lock()
    session._dispense_journal = {}
    session.device_tz = None

    bad_key = "invalid-schedule-string"
    await session.async_save_dispense_id(bad_key)

    # 1 hour later: must NOT be pruned
    monkeypatch.setattr(time, "time", lambda: now_ts + 3600)
    session._prune_dispense_journal()
    assert bad_key in session.dispense_journal
    blocked, reason = session.is_dispense_blocked(bad_key)
    assert blocked is True
    assert reason == "duplicate_recent_dose"

    # 24 hours later: must NOT be pruned (conservative 48h retention)
    monkeypatch.setattr(time, "time", lambda: now_ts + 86400)
    session._prune_dispense_journal()
    assert bad_key in session.dispense_journal

    # 49 hours later: pruned safely
    monkeypatch.setattr(time, "time", lambda: now_ts + 172801)
    session._prune_dispense_journal()
    assert bad_key not in session.dispense_journal
