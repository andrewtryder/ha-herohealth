"""Shared normalized Hero state."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.exceptions import HeroAuthenticationError, HeroError, HeroRateLimitError
from .const import DEFAULT_SCAN_INTERVAL_MINUTES
from .entity import is_low_medication
from .session import HeroSession


class HeroCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, session: HeroSession
    ) -> None:
        super().__init__(
            hass,
            __import__("logging").getLogger(__name__),
            name="Hero Health",
            update_interval=timedelta(
                minutes=getattr(entry, "options", {}).get(
                    "scan_interval", DEFAULT_SCAN_INTERVAL_MINUTES
                )
            ),
        )
        self.entry, self.session, self.dispense_lock = entry, session, asyncio.Lock()

    @property
    def device_info(self) -> DeviceInfo:
        status = (self.data or {}).get("status", {})
        return DeviceInfo(
            identifiers={("hero_health", self.entry.unique_id or self.entry.entry_id)},
            manufacturer="Hero Health",
            model=status.get("device_model", "Hero dispenser"),
            name=status.get("device_nickname", "Hero Dispenser"),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            results = await self.session.async_execute(self._async_fetch_snapshot)
            offline, status, config, doses, events, stats = results
            # These values form the authoritative snapshot used by entities and
            # dispensing.  Never turn a transient failure into invented empty
            # data: DataUpdateCoordinator will retain the last good snapshot.
            for result in (offline, status, config, doses):
                if isinstance(result, Exception):
                    raise result
            pills = (
                config.get("config", {}).get("pills", [])
                if isinstance(config, dict)
                else []
            )
            medications = [
                {
                    **pill,
                    "is_low": is_low_medication(
                        pill.get("pill_level_enum"), pill.get("pill_level_calculated")
                    ),
                }
                for pill in pills
                if isinstance(pill, dict)
            ]
            return {
                "offline": offline if isinstance(offline, dict) else {},
                "status": status if isinstance(status, dict) else {},
                "medications": medications,
                "doses": doses if isinstance(doses, dict) else {},
                # Events and statistics are informational. Preserve their last
                # known values during a partial outage when possible.
                "events": (
                    events
                    if isinstance(events, dict)
                    else (self.data or {}).get("events", {})
                ),
                "stats": (
                    stats
                    if isinstance(stats, dict)
                    else (self.data or {}).get("stats", {})
                ),
            }
        except HeroAuthenticationError as err:
            raise ConfigEntryAuthFailed("Hero authentication required") from err
        except HeroRateLimitError as err:
            retry_after = err.retry_after
            if retry_after is None or not 0 < retry_after <= 86400:
                retry_after = 300
            raise UpdateFailed(
                "Hero temporarily rate limited requests", retry_after=retry_after
            ) from err
        except HeroError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed("Hero returned an unexpected snapshot") from err

    async def _async_fetch_snapshot(self, client: Any) -> list[Any]:
        """Fetch one coordinator snapshot under a single auth lifecycle."""
        results = await asyncio.gather(
            client.check_hero_offline(),
            client.user_status(),
            client.last_d2d_config(),
            client.home_screen_doses(),
            client.get_home_screen_events(),
            client.stats(dt_util.now().date().isoformat()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, HeroAuthenticationError):
                raise result
        return results
