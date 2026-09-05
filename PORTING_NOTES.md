# Porting notes

`cloudflare-hero` is a behavioral reference only; it was not altered.

| Worker concept | Home Assistant replacement |
|---|---|
| `HeroSession` | Per-config-entry `HeroSession` with isolated aiohttp cookies |
| KV tokens/account | HA encrypted/private config entry and per-entry `Store` |
| `/api/status` | connectivity entity and device registry |
| `/api/medications` | low-medications and stable physical-slot entities |
| `/api/stats` | adherence, taken, and missed sensors |
| `/api/events` | coordinator activity snapshot |
| `/api/dispense` | `hero_health.dispense_scheduled_dose` service |
| Hono/dashboard/middleware | config entry, coordinator, native entities |
| Wrangler/Access/KV | removed |

The deliberately unusual Hero endpoints, headers, login form action, and WebSocket
messages are isolated under `custom_components/hero_health/api/`. They were derived
from observed mobile-app traffic and can change without notice. Unlike the Worker,
the integration uses a normal `wss://` aiohttp WebSocket transport.

Protocol discovery must use the read-only `HERO_SMOKE_SCHEMA=1 scripts/live-smoke`
tool. It reports response shape only and must never be extended to invoke dispense
or any mutation endpoint. The observed Android protocol header values are retained
in `api/client.py` intentionally until new read-only evidence shows they changed.

The Worker explicit-account query-string bug is corrected: account selection is sent
only as `x-hero-account`. Naive Hero timestamps are interpreted in Home Assistant's
configured local timezone; offset-bearing timestamps retain their offsets.

## Physical Device Metadata and Schedule Fallback

- **Device Registry Metadata**: Serial number, model code, and hardware family are
  read from confirmed `user-status` payload fields (`serial`, `device_manifest.model`,
  `device_manifest.family`). Home Assistant device registry identity remains
  account/config-entry based `(DOMAIN, entry_id/unique_id)` for compatibility.
- **Device Timezone**: Informational recurring schedule calculations resolve timezone
  from `user-status.device_timezone` if valid, falling back to Home Assistant's
  configured local timezone.
- **Permanent Schedule Fallback**: `pills-by-schedules` provides recurring weekly
  timetable rules. It is consumed strictly as an informational fallback for
  `Next scheduled dose` when no future dose exists in `home-screen-doses`.
- **Dispense Safety Isolation**: Recurring schedule data never feeds into
  dispense eligibility, availability, preflight, or dose selection. `home-screen-doses`
  plus Hero's WebSocket preflight remain authoritative for dispensing.
- **Pending Changes**: If Hero reports `pending_changes=true`, recurring schedule
  fallback is suppressed.
- **Recurrence Support**: Weekly `dow` rules are parsed; `every_x_days` recurrence is
  deliberately unsupported until a recurrence reference anchor date is confirmed.
- **Android Client Headers**: Mobile handshake headers (`x-hero-client: HeroApp;android-33;3.8.6`,
  `User-Agent: okhttp/4.9.2`) remain unchanged because read-only observation showed
  zero protocol drift.
