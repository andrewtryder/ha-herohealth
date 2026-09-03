"""Observed Hero Cloud REST and WebSocket protocol, separated from HA code."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from .exceptions import (
    HeroApiError,
    HeroAuthenticationError,
    HeroConnectionError,
    HeroDispenseError,
    HeroRateLimitError,
)

CLOUD_BASE_URL = "https://cloud.herohealth.com"
HERO_CLIENT = "HeroApp;android-33;3.8.6"
OKHTTP_USER_AGENT = "okhttp/4.9.2"


class HeroCloudClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        account_id: str | None = None,
        base_url: str = CLOUD_BASE_URL,
    ) -> None:
        self._session, self.access_token, self.account_id, self._base_url = (
            session,
            access_token,
            account_id,
            base_url.rstrip("/"),
        )
        self._dispense_lock = asyncio.Lock()

    def set_tokens(self, access_token: str) -> None:
        self.access_token = access_token

    def _headers(
        self, *, auth: bool = True, account_id: str | None = None
    ) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "User-Agent": OKHTTP_USER_AGENT,
            "x-hero-client": HERO_CLIENT,
        }
        if auth:
            headers["authorization"] = f"Bearer {self.access_token}"
        selected = self.account_id if account_id is None else account_id
        if selected:
            headers["x-hero-account"] = selected
        return headers

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        auth: bool = True,
        account_id: str | None = None,
    ) -> Any:
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                params=params,
                headers=self._headers(auth=auth, account_id=account_id),
            ) as response:
                if response.status in (401, 403):
                    raise HeroAuthenticationError(
                        f"Hero API returned HTTP {response.status}"
                    )
                if response.status >= 400:
                    if response.status == 429:
                        try:
                            retry_after = int(response.headers.get("Retry-After", ""))
                        except TypeError, ValueError:
                            retry_after = None
                        raise HeroRateLimitError(retry_after)
                    raise HeroApiError(
                        f"Hero API request failed with HTTP {response.status}"
                    )
                text = await response.text()
                return json.loads(text) if text else None
        except aiohttp.ClientError as err:
            raise HeroConnectionError("Unable to connect to Hero Cloud") from err
        except TimeoutError as err:
            raise HeroConnectionError("Hero Cloud request timed out") from err
        except json.JSONDecodeError as err:
            raise HeroApiError("Hero API returned invalid JSON") from err

    async def check_app_version(self) -> Any:
        return await self._request(
            "/frontend/check-app-version/", auth=False, account_id=""
        )

    async def caregiver_patient_list(self) -> Any:
        return await self._request("/frontend/caregiver-patient-list/")

    async def user_status(self, explicit_account_id: str | None = None) -> Any:
        return await self._request(
            "/frontend/user-status/", account_id=explicit_account_id
        )

    async def check_hero_offline(self) -> Any:
        return await self._request("/frontend/check-hero-offline/")

    async def last_d2d_config(self) -> Any:
        return await self._request("/frontend/last-d2d-config/")

    async def home_screen_doses(self) -> Any:
        return await self._request("/frontend/home-screen-doses/")

    async def pills_by_schedules(self) -> Any:
        return await self._request("/frontend/pills-by-schedules/")

    async def get_home_screen_events(self) -> Any:
        return await self._request("/frontend/get-home-screen-events/")

    async def stats(
        self, date: str, view: str = "overview", mode: str = "weekly"
    ) -> Any:
        return await self._request(
            "/frontend/stats/", params={"date": date, "view": view, "mode": mode}
        )

    async def insights_list(self, date: str, period: str = "weekly") -> Any:
        return await self._request(
            "/frontend/insights/insights-list/", params={"date": date, "period": period}
        )

    async def milestones_list(self) -> Any:
        return await self._request("/frontend/insights/milestones-list/")

    async def dispense_scheduled_dose(
        self, scheduled_datetime: str, timeout_seconds: float = 30
    ) -> dict[str, Any]:
        """Complete only after Hero's completion event, never the started event."""
        async with self._dispense_lock:
            return await self._async_dispense_scheduled_dose(
                scheduled_datetime, timeout_seconds
            )

    async def _async_dispense_scheduled_dose(
        self, scheduled_datetime: str, timeout_seconds: float
    ) -> dict[str, Any]:
        headers = self._headers()
        try:
            async with self._session.ws_connect(
                f"{self._base_url.replace('https://', 'wss://')}/ws/frontend/",
                headers=headers,
                timeout=timeout_seconds,
            ) as ws:
                await ws.send_json(
                    {
                        "type": "request_authorization",
                        "payload": {"authorization_token": self.access_token},
                    }
                )
                messages: list[str] = []
                async with asyncio.timeout(timeout_seconds):
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            raise HeroDispenseError(
                                "Hero WebSocket closed before dispense completed"
                            )
                        try:
                            payload = json.loads(message.data)
                        except (TypeError, json.JSONDecodeError) as err:
                            raise HeroDispenseError(
                                "Hero WebSocket returned malformed data"
                            ) from err
                        if not isinstance(payload, dict):
                            raise HeroDispenseError(
                                "Hero WebSocket returned malformed data"
                            )
                        kind = payload.get("type")
                        body = payload.get("payload", {})
                        if not isinstance(body, dict):
                            raise HeroDispenseError(
                                "Hero WebSocket returned malformed data"
                            )
                        if kind == "response_authorization":
                            if body.get("status") != "success":
                                raise HeroDispenseError(
                                    "Hero WebSocket authorization failed"
                                )
                            await ws.send_json(
                                {
                                    "type": "dispense_frontend_preflight_check",
                                    "payload": {
                                        "type": "scheduled_dose",
                                        "account_id": self.account_id,
                                        "scheduled_datetime": scheduled_datetime,
                                    },
                                }
                            )
                        elif kind == "dispense_frontend_preflight_status":
                            if (
                                body.get("status") is not True
                                and body.get("can_dispense") is not True
                            ):
                                raise HeroDispenseError(
                                    "Hero preflight check rejected dispensing"
                                )
                            await ws.send_json(
                                {
                                    "type": "dispense_frontend_start",
                                    "payload": {
                                        "dispense_type": "scheduled_dose",
                                        "account_id": self.account_id,
                                        "scheduled_datetime": scheduled_datetime,
                                    },
                                }
                            )
                        elif kind == "dispense_frontend_message" and body.get(
                            "message"
                        ):
                            messages.append(str(body["message"]))
                        elif kind == "request_ping":
                            await ws.send_json({"type": "response_ping"})
                        elif kind == "dispense_frontend_completed":
                            if body.get("status", True) is not True:
                                raise HeroDispenseError(
                                    "Hero reported dispense failure"
                                )
                            return {
                                "success": True,
                                "status": "completed",
                                "messages": messages,
                            }
                raise HeroDispenseError(
                    "Hero WebSocket closed before dispense completed"
                )
        except TimeoutError as err:
            raise HeroDispenseError("Dispense action timed out") from err
        except aiohttp.ClientError as err:
            raise HeroConnectionError("Unable to connect to Hero dispenser") from err
