"""Constants for Hero Health."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import CONF_EMAIL as HA_CONF_EMAIL
from homeassistant.const import CONF_PASSWORD as HA_CONF_PASSWORD

DOMAIN = "hero_health"
PLATFORMS = ["sensor", "binary_sensor"]
DEFAULT_SCAN_INTERVAL = timedelta(minutes=15)
CONF_ACCOUNT_ID = "account_id"
CONF_EMAIL = HA_CONF_EMAIL
CONF_PASSWORD = HA_CONF_PASSWORD
SERVICE_DISPENSE = "dispense_scheduled_dose"
SERVICE_REFRESH = "refresh"
ATTR_SCHEDULED_DATETIME = "scheduled_datetime"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
DISPENSE_EARLY_WINDOW = timedelta(minutes=30)
DISPENSE_LATE_WINDOW = timedelta(hours=6)
EVENT_REAUTH = "hero_health_reauth_required"

if TYPE_CHECKING:
    from .coordinator import HeroCoordinator
    from .session import HeroSession


@dataclass(slots=True)
class HeroHealthRuntimeData:
    """Resources owned by one Hero Health config entry."""

    session: HeroSession
    coordinator: HeroCoordinator
