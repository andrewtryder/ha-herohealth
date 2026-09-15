"""Per-entry authenticated Hero session and private HA storage."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, tzinfo
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api.auth import HeroAuthClient
from .api.client import HeroCloudClient
from .api.exceptions import HeroAuthenticationError
from .api.models import HeroTokens
from .const import DISPENSE_LATE_WINDOW


class HeroSession:
    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        email: str,
        password: str,
        account_id: str | None,
        *,
        persist: bool = True,
        device_tz: tzinfo | None = None,
    ) -> None:
        self._hass, self._email, self._password, self.account_id = (
            hass,
            email,
            password,
            account_id,
        )
        self._device_tz: tzinfo | None = device_tz
        self._identity = {
            "email": email.strip().lower(),
            "account_id": account_id or "",
        }
        self._store = Store[dict[str, Any]](hass, 1, f"hero_health.{entry_id}")
        self._persist = persist
        # Hero's authentication cookies must not be shared with Home Assistant.
        # When persist is False (e.g. config-flow validation), auto_cleanup is False
        # so that async_close() can explicitly detach() without accumulating listeners.
        self._http = async_create_clientsession(
            hass,
            cookie_jar=aiohttp.CookieJar(),
            timeout=aiohttp.ClientTimeout(total=25),
            auto_cleanup=persist,
        )
        self._auth = HeroAuthClient(self._http)
        self._tokens: HeroTokens | None = None
        self._last_dispense_id: str | None = None
        self._dispense_attempt: dict[str, str] | None = None
        self._dispense_journal: dict[str, dict[str, Any]] = {}
        self.client: HeroCloudClient | None = None
        self._lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

    @property
    def device_tz(self) -> tzinfo | None:
        return getattr(self, "_device_tz", None)

    @device_tz.setter
    def device_tz(self, value: tzinfo | None) -> None:
        self._device_tz = value
        if value is not None:
            journal = self._get_journal()
            for sched_str, entry in journal.items():
                if isinstance(entry, dict) and "expires_at" not in entry:
                    exp = self._compute_expiry(sched_str, device_tz=value)
                    if exp is not None:
                        entry["expires_at"] = exp

    def _compute_expiry(
        self, sched_str: str, device_tz: tzinfo | None = None
    ) -> float | None:
        """Compute the absolute expiration timestamp for a scheduled dose identifier."""
        try:
            parsed = datetime.fromisoformat(
                sched_str.replace("Z", "+00:00").replace(" ", "T")
            )
            if parsed.tzinfo is not None:
                return parsed.timestamp() + DISPENSE_LATE_WINDOW.total_seconds()
            tz = device_tz or self.device_tz
            if tz is not None:
                aware = parsed.replace(tzinfo=tz)
                return aware.timestamp() + DISPENSE_LATE_WINDOW.total_seconds()
        except Exception:
            pass
        return None

    async def async_initialize(self) -> HeroCloudClient:
        self._tokens = None
        saved = (await self._store.async_load() or {}) if self._persist else {}
        saved_identity = saved.get("identity") if isinstance(saved, dict) else None
        identity_matches = saved_identity == self._current_identity()
        if identity_matches:
            try:
                self._tokens = HeroTokens.from_dict(saved["tokens"])
            except KeyError, TypeError, ValueError:
                pass
        journal = saved.get("dispense_journal") if isinstance(saved, dict) else None
        if identity_matches and isinstance(journal, dict):
            self._dispense_journal = {}
            for k, v in journal.items():
                if isinstance(k, str) and isinstance(v, dict) and "status" in v:
                    record: dict[str, Any] = {
                        "status": v["status"],
                        "recorded_at": float(v.get("recorded_at", 0)),
                    }
                    if "expires_at" in v and isinstance(v["expires_at"], (int, float)):
                        record["expires_at"] = float(v["expires_at"])
                    else:
                        exp = self._compute_expiry(k)
                        if exp is not None:
                            record["expires_at"] = exp
                    self._dispense_journal[k] = record
        else:
            self._dispense_journal = {}

        # Backward compatibility migration for legacy keys:
        if identity_matches:
            last_dispense_id = (
                saved.get("last_dispense_id") if isinstance(saved, dict) else None
            )
            if (
                isinstance(last_dispense_id, str)
                and last_dispense_id not in self._dispense_journal
            ):
                rec: dict[str, Any] = {
                    "status": "completed",
                    "recorded_at": time.time(),
                }
                exp = self._compute_expiry(last_dispense_id)
                if exp is not None:
                    rec["expires_at"] = exp
                self._dispense_journal[last_dispense_id] = rec
            attempt = saved.get("dispense_attempt") if isinstance(saved, dict) else None
            if (
                isinstance(attempt, dict)
                and attempt.get("state") == "outcome_unknown"
                and isinstance(attempt.get("scheduled_datetime"), str)
            ):
                sched = attempt["scheduled_datetime"]
                if sched not in self._dispense_journal:
                    rec = {
                        "status": "outcome_unknown",
                        "recorded_at": time.time(),
                    }
                    exp = self._compute_expiry(sched)
                    if exp is not None:
                        rec["expires_at"] = exp
                    self._dispense_journal[sched] = rec

        self._prune_dispense_journal()
        self._sync_legacy_fields()
        await self._async_ensure_tokens()
        assert self._tokens
        self.client = HeroCloudClient(
            self._http, self._tokens.access_token, self.account_id
        )
        return self.client

    def _get_journal(self) -> dict[str, dict[str, Any]]:
        """Return internal journal dict, initializing if bypassed by mocks."""
        if not hasattr(self, "_dispense_journal") or self._dispense_journal is None:
            self._dispense_journal = {}
        return self._dispense_journal

    @property
    def dispense_journal(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the current in-memory dispense journal."""
        self._prune_dispense_journal()
        return dict(self._get_journal())

    def _sync_legacy_fields(self) -> None:
        """Synchronize legacy single-marker attributes for backward compatibility."""
        journal = self._get_journal()
        completed = [
            (v.get("recorded_at", 0), k)
            for k, v in journal.items()
            if v.get("status") == "completed"
        ]
        self._last_dispense_id = max(completed)[1] if completed else None

        unknowns = [
            (v.get("recorded_at", 0), k)
            for k, v in journal.items()
            if v.get("status") == "outcome_unknown"
        ]
        if unknowns:
            latest_unknown_dose = max(unknowns)[1]
            self._dispense_attempt = {
                "state": "outcome_unknown",
                "scheduled_datetime": latest_unknown_dose,
            }
        else:
            self._dispense_attempt = None

    def _prune_dispense_journal(self) -> None:
        """Prune records older than scheduled_datetime + DISPENSE_LATE_WINDOW."""
        now_ts = time.time()
        to_delete: list[str] = []
        journal = self._get_journal()
        for sched_str, entry in journal.items():
            if not isinstance(entry, dict):
                continue
            expire_ts = entry.get("expires_at")
            if expire_ts is None:
                expire_ts = self._compute_expiry(sched_str)
                if expire_ts is not None:
                    entry["expires_at"] = expire_ts

            if expire_ts is not None:
                if now_ts > expire_ts:
                    to_delete.append(sched_str)
            else:
                recorded_at = entry.get("recorded_at", 0)
                if isinstance(recorded_at, (int, float)) and recorded_at > 0:
                    if now_ts > recorded_at + 172800:
                        to_delete.append(sched_str)
        for k in to_delete:
            journal.pop(k, None)

    async def _async_save_state(self) -> None:
        """Atomically persist all session state without clobbering sibling fields."""
        if not self._persist:
            return
        if not hasattr(self, "_state_lock"):
            self._state_lock = asyncio.Lock()
        async with self._state_lock:
            state: dict[str, Any] = {}
            if getattr(self, "_tokens", None):
                state["tokens"] = self._tokens.as_dict()
            if getattr(self, "_identity", None):
                state["identity"] = self._identity
            self._prune_dispense_journal()
            self._sync_legacy_fields()
            if self._dispense_journal:
                state["dispense_journal"] = self._dispense_journal
            if self._last_dispense_id:
                state["last_dispense_id"] = self._last_dispense_id
            if self._dispense_attempt:
                state["dispense_attempt"] = self._dispense_attempt
            await self._store.async_save(state)

    def _current_identity(self) -> dict[str, str]:
        """Return normalized non-secret identity metadata, including test shims."""
        return getattr(
            self,
            "_identity",
            {
                "email": getattr(self, "_email", "").strip().lower(),
                "account_id": getattr(self, "account_id", None) or "",
            },
        )

    async def _async_ensure_tokens(
        self, *, force_refresh: bool = False, force_login: bool = False
    ) -> None:
        async with self._lock:
            if (
                not force_refresh
                and not force_login
                and self._tokens
                and time.time()
                < self._tokens.created_at + self._tokens.expires_in - 300
            ):
                return
            try:
                if not force_login and self._tokens and self._tokens.refresh_token:
                    tokens = await self._auth.refresh_access_token(
                        self._tokens.refresh_token
                    )
                    if not tokens.refresh_token:
                        tokens.refresh_token = self._tokens.refresh_token
                else:
                    tokens = await self._auth.login_with_password(
                        self._email, self._password
                    )
            except HeroAuthenticationError:
                if not force_login and self._email and self._password:
                    tokens = await self._auth.login_with_password(
                        self._email, self._password
                    )
                else:
                    raise
            self._tokens = tokens
            await self._async_save_state()
            if self.client:
                self.client.set_tokens(tokens.access_token)

    async def async_execute(
        self, operation: Callable[[HeroCloudClient], Awaitable[Any]]
    ) -> Any:
        await self._async_ensure_tokens()
        assert self.client
        try:
            return await operation(self.client)
        except HeroAuthenticationError:
            try:
                await self._async_ensure_tokens(force_refresh=True)
            except HeroAuthenticationError:
                await self._async_ensure_tokens(force_login=True)
            assert self.client
            return await operation(self.client)

    async def async_save_dispense_id(
        self,
        identifier: str,
        *,
        device_tz: tzinfo | None = None,
        expires_at: float | None = None,
    ) -> None:
        if not self._persist:
            return
        entry: dict[str, Any] = {
            "status": "completed",
            "recorded_at": time.time(),
        }
        exp = expires_at or self._compute_expiry(identifier, device_tz=device_tz)
        if exp is not None:
            entry["expires_at"] = exp
        self._get_journal()[identifier] = entry
        await self._async_save_state()

    async def async_last_dispense_id(self) -> str | None:
        if not self._persist:
            return None
        self._sync_legacy_fields()
        return self._last_dispense_id

    async def async_mark_dispense_start_sent(
        self,
        scheduled_datetime: str,
        *,
        device_tz: tzinfo | None = None,
        expires_at: float | None = None,
    ) -> None:
        """Persist ambiguity before waiting for the device completion event."""
        if not self._persist:
            return
        entry: dict[str, Any] = {
            "status": "outcome_unknown",
            "recorded_at": time.time(),
        }
        exp = expires_at or self._compute_expiry(
            scheduled_datetime, device_tz=device_tz
        )
        if exp is not None:
            entry["expires_at"] = exp
        self._get_journal()[scheduled_datetime] = entry
        await self._async_save_state()

    async def async_dispense_outcome_unknown(self, scheduled_datetime: str) -> bool:
        if not self._persist:
            return False
        entry = self._get_journal().get(scheduled_datetime)
        return bool(entry and entry.get("status") == "outcome_unknown")

    def is_dispense_blocked(self, scheduled_datetime: str) -> tuple[bool, str | None]:
        """Check whether a dose is blocked by recent completed or unknown state."""
        self._prune_dispense_journal()
        entry = self._get_journal().get(scheduled_datetime)
        if not entry:
            return False, None
        status = entry.get("status")
        if status == "completed":
            return True, "duplicate_recent_dose"
        if status == "outcome_unknown":
            return True, "dispense_outcome_unknown"
        return False, None

    async def async_close(self) -> None:
        """Release per-entry state; detach session if not auto-cleaned."""
        if not self._persist and self._http is not None:
            self._http.detach()
        return None
