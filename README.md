# Hero Health for Home Assistant

Unofficial HACS custom integration for a Hero smart pill dispenser. It connects
Home Assistant directly to Hero Cloud—there is no Cloudflare Worker dependency.

> This project is not affiliated with, endorsed by, or supported by Hero Health.
> It implements a private/mobile API reverse engineered from observed app traffic;
> Hero can change it without notice.

## Features

- UI setup with PKCE login, refresh tokens, reauthentication, and account selection
- Dispenser connectivity; seven-day adherence, taken, and missed metrics
- A compatibility-friendly `sensor.hero_health_low_medications` state (`None` or
  comma-separated low medication names), plus structured attributes
- Ten stable physical slot sensors and low-level binary sensors
- A `binary_sensor.hero_health_dispense_available` indicator that mirrors the backend's remote-dispense eligibility checks
- Next scheduled-dose timestamp and native device registry association
- Explicit refresh and safety-gated remote scheduled-dose service actions

## Install

In HACS, add `https://github.com/andrewtryder/ha-herohealth` as a **Custom
repository** of type **Integration**, download it, then restart Home Assistant.
Alternatively copy `custom_components/hero_health` into your Home Assistant
configuration directory and restart. Add **Hero Health** under Settings → Devices
& services; credentials are configured only through the UI.

## Entities

The integration creates `sensor.hero_health_low_medications`, 7-day adherence,
doses taken/missed, next scheduled dose, and slot 1–10 sensors. It also creates a
dispenser connectivity binary sensor and a low binary sensor per physical slot.
Slot entity identity remains stable when medication assignments change.

Use a Lovelace entities card with the above entities; no custom card is required.

## Actions

Hero Health refreshes in the background every three hours by default to reduce
traffic to Hero's undocumented cloud API. Change this interval in the integration's
Configure dialog (15 minutes to 24 hours), or use the refresh action when immediate
data is needed.

`hero_health.refresh` immediately refreshes shared data. Both Hero actions require
`config_entry_id`; Home Assistant presents this as a Hero Health connection selector.
Manual entity updates of the low-medications sensor also refresh its coordinator.

`hero_health.dispense_scheduled_dose` is safety-sensitive. It refreshes Hero data,
requires Hero itself to report `time_to_take`, and permits a dose only from 30
minutes before through six hours after its scheduled time. The six-hour late window
is an observed implementation limitation pending additional live validation. It
matches an optional `scheduled_datetime` against Hero's live data, performs WebSocket
authorization and preflight, and succeeds only after Hero reports completion. Put it
behind a Lovelace confirmation or a deliberate automation; it is not a button entity.

The dispense-available binary sensor is observability only. Lovelace users may use
it to conditionally show their own confirmed action control, but it never bypasses
the action's Hero preflight or safety boundary.

## Privacy and troubleshooting

Credentials and session tokens are never entity attributes or diagnostics. Hero's
password is retained in Home Assistant's configuration storage only so the integration
can recover when refresh credentials fail; treat Home Assistant configuration storage
and backups as sensitive. Avoid sharing debug logs: they may still reveal operational timing. For temporary debug
logging, add `custom_components.hero_health: debug`, reproduce the issue, then
remove it. Do not post credentials, tokens, cookies, account IDs, or medication
details in an issue.

If setup fails, check Home Assistant network access and credentials, then reconfigure
or reauthenticate the integration. See [MIGRATION.md](MIGRATION.md) to move the
existing low-medication announcement without retiring the Worker prematurely.

## HACS publishing

This repository has HACS metadata and Hassfest/HACS validation workflows. HACS may
require original brand assets for default-directory inclusion; none are copied here.

## Development

The minimum tested Home Assistant version is **2026.8.3** on **Python 3.14**.
The recommended environment is the included devcontainer: in VS Code, choose
**Dev Containers: Reopen in Container**. It creates `/home/vscode/.venv` using
the deterministic baseline in `requirements_test.txt`.

Run `pytest`, `ruff check .`, `ruff format --check .`, and
`pre-commit run --all-files`. Start the matching local Home Assistant runtime
with `scripts/run-ha-dev`, then open <http://localhost:8123>. The source tree is
symlinked into `.ha-dev/custom_components`, so edits are immediately available;
never put credentials in `.ha-dev` or tracked files.

The scheduled `future-compat` workflow tests the newest compatible
`pytest-homeassistant-custom-component` release and logs its resolved Home
Assistant version. It is an early-warning lane only: it does not imply support
for unreleased Home Assistant versions or replace the 2026.8.3 baseline.
