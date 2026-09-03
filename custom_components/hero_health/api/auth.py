"""Observed Hero mobile authentication protocol.

The Hero-specific details in this module were derived from observed Hero mobile-app
traffic. They are intentionally isolated here and may change without notice.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from .exceptions import (
    HeroApiError,
    HeroAuthenticationError,
    HeroConnectionError,
    HeroRateLimitError,
)
from .models import HeroTokens

DEFAULT_CLIENT_ID = "sGNw0O6padHYWwSWIon21jt1QqEYAkmZLYUps60L"
AUTH_BASE_URL = "https://id.herohealth.com"
ANDROID_USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 15; sdk_gphone64_arm64 Build/AE3A.240806.036)"
)


class _LoginFieldsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "input":
            values = {key.lower(): value or "" for key, value in attrs}
            if values.get("name") in {"csrfmiddlewaretoken", "visitor_id"}:
                self.fields[values["name"]] = values.get("value", "")


def generate_pkce_verifier() -> str:
    """Return a RFC 7636-compatible cryptographically secure verifier."""
    return secrets.token_urlsafe(64).rstrip("=")


def pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def parse_login_fields(html: str) -> tuple[str, str]:
    parser = _LoginFieldsParser()
    parser.feed(html)
    csrf = parser.fields.get("csrfmiddlewaretoken")
    if not csrf:
        raise HeroAuthenticationError(
            "Hero login page did not contain the required form"
        )
    return csrf, parser.fields.get("visitor_id", "")


def parse_callback(location: str | None, expected_state: str) -> str:
    """Validate Hero's custom callback without logging its sensitive values."""
    parsed = urlparse(location or "")
    if parsed.scheme != "heroapp" or parsed.netloc != "auth":
        raise HeroAuthenticationError("Hero login returned an unexpected callback")
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    if not code:
        raise HeroAuthenticationError("Hero login did not return an authorization code")
    returned_state = params.get("state", [None])[0]
    # Existing observed callbacks may omit state; validate it whenever supplied.
    if returned_state is not None and returned_state != expected_state:
        raise HeroAuthenticationError("Hero login callback state did not match")
    return code


def _raise_for_status(status: int, headers: Any) -> None:
    if status in (401, 403):
        raise HeroAuthenticationError("Hero authentication was rejected")
    if status == 429:
        try:
            retry_after = int(headers.get("Retry-After", ""))
        except TypeError, ValueError:
            retry_after = None
        raise HeroRateLimitError(retry_after)
    if status >= 500:
        raise HeroConnectionError(
            "Hero authentication service is temporarily unavailable"
        )
    if status >= 400:
        raise HeroApiError("Hero authentication request was rejected")


class HeroAuthClient:
    """Async PKCE/password and refresh authentication client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str = DEFAULT_CLIENT_ID,
        base_url: str = AUTH_BASE_URL,
    ) -> None:
        self._session, self._client_id, self._base_url = (
            session,
            client_id,
            base_url.rstrip("/"),
        )

    async def login_with_password(self, email: str, password: str) -> HeroTokens:
        verifier = generate_pkce_verifier()
        params = {
            "redirect_uri": "heroapp://auth",
            "client_id": self._client_id,
            "response_type": "code",
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        try:
            async with self._session.get(
                f"{self._base_url}/login/?{urlencode(params)}",
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                    ),
                    "User-Agent": ANDROID_USER_AGENT,
                },
            ) as response:
                _raise_for_status(response.status, response.headers)
                if response.status != 200:
                    raise HeroApiError(
                        "Hero login page returned an unexpected response"
                    )
                post_url, csrf, visitor = (
                    str(response.url),
                    *parse_login_fields(await response.text()),
                )
            form = {
                "csrfmiddlewaretoken": csrf,
                "visitor_id": visitor,
                "email": email,
                "password": password,
                "action": "app-login-with-otp",
            }
            async with self._session.post(
                post_url,
                data=form,
                allow_redirects=False,
                headers={
                    "Origin": self._base_url,
                    "Referer": post_url,
                    "User-Agent": ANDROID_USER_AGENT,
                },
            ) as response:
                if response.status not in (302, 303):
                    _raise_for_status(response.status, response.headers)
                    raise HeroAuthenticationError(
                        "Hero rejected the supplied credentials"
                    )
                location = response.headers.get("Location")
            code = parse_callback(location, params["state"])
            return await self.exchange_code(code, verifier)
        except aiohttp.ClientError as err:
            raise HeroConnectionError(
                "Unable to connect to Hero authentication"
            ) from err
        except TimeoutError as err:
            raise HeroConnectionError("Hero authentication timed out") from err

    async def exchange_code(self, code: str, verifier: str) -> HeroTokens:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "heroapp://auth",
                "code_verifier": verifier,
                "client_id": self._client_id,
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> HeroTokens:
        return await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            }
        )

    async def _token_request(self, data: dict[str, str]) -> HeroTokens:
        try:
            async with self._session.post(
                f"{self._base_url}/o/token/",
                data=data,
                headers={
                    "Accept": "application/json",
                    "User-Agent": ANDROID_USER_AGENT,
                },
            ) as response:
                _raise_for_status(response.status, response.headers)
                payload: dict[str, Any] = await response.json(content_type=None)
            return HeroTokens.from_response(payload, time.time())
        except aiohttp.ClientError as err:
            raise HeroConnectionError(
                "Unable to connect to Hero authentication"
            ) from err
        except ValueError as err:
            raise HeroApiError("Hero token response was invalid") from err
        except TimeoutError as err:
            raise HeroConnectionError("Hero authentication timed out") from err
