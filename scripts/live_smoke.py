#!/usr/bin/env python3
"""Development-only, read-only smoke test for the live Hero protocol."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

# Running this file directly makes Python use ``scripts/`` as ``sys.path[0]``.
# Add the repository root so the actual integration API classes can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from custom_components.hero_health.api.auth import HeroAuthClient
from custom_components.hero_health.api.client import HeroCloudClient
from custom_components.hero_health.api.exceptions import (
    HeroApiError,
    HeroAuthenticationError,
    HeroConnectionError,
    HeroError,
)


class LiveSmokeError(Exception):
    """A safe, user-actionable live smoke failure."""


def _accounts_from_response(payload: Any) -> list[Mapping[str, Any]]:
    """Normalize the account-list response without retaining account details."""
    accounts = (
        payload
        if isinstance(payload, list)
        else payload.get("results", [])
        if isinstance(payload, Mapping)
        else []
    )
    if not isinstance(accounts, list):
        raise LiveSmokeError("Hero returned an invalid account list.")
    return [account for account in accounts if isinstance(account, Mapping)]


def select_account_id(
    accounts: Sequence[Mapping[str, Any]], requested_account_id: str | None
) -> str:
    """Choose the sole account or validate the explicitly requested one."""
    account_ids = [
        str(account["account_id"]) for account in accounts if account.get("account_id")
    ]
    if not account_ids:
        raise LiveSmokeError("Hero returned no usable accounts.")
    if len(account_ids) == 1:
        return account_ids[0]
    if not requested_account_id:
        raise LiveSmokeError(
            "Multiple Hero accounts found; set HERO_ACCOUNT_ID in .env.local."
        )
    if requested_account_id not in account_ids:
        raise LiveSmokeError("HERO_ACCOUNT_ID did not match a discovered Hero account.")
    return requested_account_id


def _list_at(mapping: Any, key: str) -> list[Any]:
    return (
        mapping.get(key, [])
        if isinstance(mapping, Mapping) and isinstance(mapping.get(key), list)
        else []
    )


def structural_counts(config: Any, doses: Any) -> tuple[int, int, int]:
    """Return only non-sensitive response structure counts."""
    configured_slots = len(
        _list_at(
            config.get("config", {}) if isinstance(config, Mapping) else {}, "pills"
        )
    )
    slots_returned = 0
    dose_groups = 0
    for day in _list_at(doses, "dates"):
        for slot in _list_at(day, "times"):
            slots_returned += 1
            dose_groups += len(_list_at(slot, "doses"))
    return slots_returned, configured_slots, dose_groups


async def async_run() -> list[str]:
    """Authenticate and run only approved read-only Hero REST calls."""
    email = os.environ.get("HERO_EMAIL")
    password = os.environ.get("HERO_PASSWORD")
    if not email or not password:
        raise LiveSmokeError("HERO_EMAIL and HERO_PASSWORD are required.")

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
        tokens = await HeroAuthClient(http).login_with_password(email, password)
        client = HeroCloudClient(http, tokens.access_token)
        accounts = _accounts_from_response(await client.caregiver_patient_list())
        client.account_id = select_account_id(
            accounts, os.environ.get("HERO_ACCOUNT_ID")
        )

        await client.check_hero_offline()
        await client.user_status()
        config = await client.last_d2d_config()
        doses = await client.home_screen_doses()
        await client.get_home_screen_events()
        await client.stats(date.today().isoformat())

    slots, configured_slots, dose_groups = structural_counts(config, doses)
    return [
        "Authentication: OK",
        f"Accounts: {len(accounts)}",
        "Status: OK",
        f"Slots returned: {slots}",
        f"Configured slots: {configured_slots}",
        f"Dose groups: {dose_groups}",
        "Events: OK",
        "Stats: OK",
        "Read-only smoke test completed successfully.",
    ]


def main() -> int:
    """Run the smoke test without exposing remote responses or secrets."""
    try:
        for line in asyncio.run(async_run()):
            print(line)
    except HeroAuthenticationError:
        print("Live smoke test failed: Hero authentication failed.", file=sys.stderr)
        return 1
    except HeroConnectionError:
        print("Live smoke test failed: unable to connect to Hero.", file=sys.stderr)
        return 1
    except HeroApiError, LiveSmokeError:
        print(
            "Live smoke test failed: Hero returned an invalid response.",
            file=sys.stderr,
        )
        return 1
    except HeroError:
        print("Live smoke test failed: Hero request failed.", file=sys.stderr)
        return 1
    except Exception:
        print("Live smoke test failed unexpectedly.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
