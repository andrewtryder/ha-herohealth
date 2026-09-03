"""Shared normalized Hero state."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.exceptions import HeroAuthenticationError, HeroError
from .const import DEFAULT_SCAN_INTERVAL
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
            update_interval=DEFAULT_SCAN_INTERVAL,
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
            client = self.session.client or await self.session.async_initialize()
            results = await asyncio.gather(
                client.check_hero_offline(),
                client.user_status(),
                client.last_d2d_config(),
                client.home_screen_doses(),
                client.get_home_screen_events(),
                client.stats(date.today().isoformat()),
                return_exceptions=True,
            )
            offline, status, config, doses, events, stats = results
            if isinstance(offline, Exception) or isinstance(status, Exception):
                raise offline if isinstance(offline, Exception) else status
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
                "events": events if isinstance(events, dict) else {},
                "stats": stats if isinstance(stats, dict) else {},
            }
        except HeroAuthenticationError as err:
            raise ConfigEntryAuthFailed("Hero authentication required") from err
        except HeroError as err:
            raise UpdateFailed(str(err)) from err
