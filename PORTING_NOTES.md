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

The Worker explicit-account query-string bug is corrected: account selection is sent
only as `x-hero-account`. Naive Hero timestamps are interpreted in Home Assistant's
configured local timezone; offset-bearing timestamps retain their offsets.
