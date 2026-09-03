"""Per-entry authenticated Hero session and private HA storage."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api.auth import HeroAuthClient
from .api.client import HeroCloudClient
from .api.exceptions import HeroAuthenticationError
from .api.models import HeroTokens


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
    ) -> None:
        self._hass, self._email, self._password, self.account_id = (
            hass,
            email,
            password,
            account_id,
        )
        self._identity = {
            "email": email.strip().lower(),
            "account_id": account_id or "",
        }
        self._store = Store[dict[str, Any]](hass, 1, f"hero_health.{entry_id}")
        self._persist = persist
        # Hero's authentication cookies must not be shared with Home Assistant.
        self._http = async_create_clientsession(
            hass,
            cookie_jar=aiohttp.CookieJar(),
            timeout=aiohttp.ClientTimeout(total=25),
        )
        self._auth = HeroAuthClient(self._http)
        self._tokens: HeroTokens | None = None
        self._last_dispense_id: str | None = None
        self.client: HeroCloudClient | None = None
        self._lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

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
        last_dispense_id = (
            saved.get("last_dispense_id") if isinstance(saved, dict) else None
        )
        self._last_dispense_id = (
            last_dispense_id
            if identity_matches and isinstance(last_dispense_id, str)
            else None
        )
        await self._async_ensure_tokens()
        assert self._tokens
        self.client = HeroCloudClient(
            self._http, self._tokens.access_token, self.account_id
        )
        return self.client

    async def _async_save_state(self) -> None:
        """Atomically persist all session state without clobbering sibling fields."""
        if not self._persist:
            return
        if not hasattr(self, "_state_lock"):
            self._state_lock = asyncio.Lock()
        async with self._state_lock:
            state: dict[str, Any] = {}
            if self._tokens:
                state["tokens"] = self._tokens.as_dict()
                state["identity"] = self._current_identity()
            if last_dispense_id := getattr(self, "_last_dispense_id", None):
                state["last_dispense_id"] = last_dispense_id
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

    async def async_save_dispense_id(self, identifier: str) -> None:
        if not self._persist:
            return
        self._last_dispense_id = identifier
        await self._async_save_state()

    async def async_last_dispense_id(self) -> str | None:
        if not self._persist:
            return None
        return getattr(self, "_last_dispense_id", None)

    async def async_close(self) -> None:
        """Release per-entry state; Home Assistant owns helper-created sessions."""
        # async_create_clientsession registers cleanup with Home Assistant.
        return None
