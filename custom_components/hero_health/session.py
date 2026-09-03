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
        self._store = Store[dict[str, Any]](hass, 1, f"hero_health.{entry_id}")
        self._persist = persist
        # Hero's authentication cookies must not be shared with Home Assistant.
        self._http = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        self._auth = HeroAuthClient(self._http)
        self._tokens: HeroTokens | None = None
        self.client: HeroCloudClient | None = None
        self._lock = asyncio.Lock()

    async def async_initialize(self) -> HeroCloudClient:
        saved = (await self._store.async_load() or {}) if self._persist else {}
        try:
            self._tokens = HeroTokens.from_dict(saved["tokens"])
        except KeyError, TypeError, ValueError:
            pass
        await self._async_ensure_tokens()
        assert self._tokens
        self.client = HeroCloudClient(
            self._http, self._tokens.access_token, self.account_id
        )
        return self.client

    async def _async_ensure_tokens(self, force_login: bool = False) -> None:
        async with self._lock:
            if (
                not force_login
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
            if self._persist:
                await self._store.async_save({"tokens": tokens.as_dict()})
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
            await self._async_ensure_tokens(force_login=True)
            assert self.client
            return await operation(self.client)

    async def async_save_dispense_id(self, identifier: str) -> None:
        if not self._persist:
            return
        saved = await self._store.async_load() or {}
        saved["last_dispense_id"] = identifier
        await self._store.async_save(saved)

    async def async_last_dispense_id(self) -> str | None:
        if not self._persist:
            return None
        return (await self._store.async_load() or {}).get("last_dispense_id")

    async def async_close(self) -> None:
        """Release per-entry state; Home Assistant owns helper-created sessions."""
        # async_create_clientsession registers cleanup with Home Assistant.
        return None
