# Contributing

Use the devcontainer (VS Code: **Dev Containers: Reopen in Container**) for the
supported Python 3.14 environment and the deterministic Home Assistant 2026.8.3
baseline. It installs `/home/vscode/.venv` from `requirements_test.txt`.

Run `pytest`, `ruff check .`, `ruff format --check .`, and
`pre-commit run --all-files`. Start local Home Assistant at `http://localhost:8123`
with `scripts/run-ha-dev`; it pins Home Assistant 2026.8.3. Configure Hero through
the UI and test the real config flow there manually. The `.ha-dev/` directory is
sensitive: Home Assistant can persist real integration credentials in it. Docker
Hassfest can be run with `docker run --rm -v "$PWD:/github/workspace"
ghcr.io/home-assistant/hassfest`.

## Development-only live Hero smoke test

The optional smoke test contacts the real Hero service using read-only endpoints.
It is development tooling only and does not replace manual config-flow testing in
the Home Assistant UI.

```sh
cp .env.example .env.local
# fill credentials
scripts/live-smoke
```

Keep `.env.local` private. It requires `HERO_EMAIL` and `HERO_PASSWORD`; if account
discovery returns more than one account, set `HERO_ACCOUNT_ID` to one of the
discovered account IDs and run the command again. `.env.local`, `.ha-dev/`,
`.storage/`, databases, and logs are ignored and must never be committed.

Pull-request titles use lower-case Conventional Commits. Release Please prepares
releases and updates the integration manifest; do not manually publish releases.
