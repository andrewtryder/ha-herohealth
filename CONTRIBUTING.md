# Contributing

Use the devcontainer (VS Code: **Dev Containers: Reopen in Container**) or a local
Python 3.14.2 or newer, Git, and uv. The validation script creates `.venv` and syncs the pinned `requirements_test.lock`. Regenerate it after changing requirements with `uv pip compile --python-version 3.14.2 --universal requirements_test.txt -o requirements_test.lock`.

## Local Validation Contract

The portfolio standard validation interface executes the entire local test, linting, and hygiene contract:

```bash
./scripts/validate-local.sh
```

Pre-commit git hooks can be installed via:

```bash
pre-commit install
```

Individual checks can also be run directly:

```bash
ruff check .
ruff format --check .
pytest
pre-commit run --all-files
```

Pytest runs in offline isolation with `-m "not live"` and enforces a strict `>=95%` component statement coverage gate (currently achieving >95.5%). Start local Home Assistant at `http://localhost:8123`
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
