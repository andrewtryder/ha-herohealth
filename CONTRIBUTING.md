# Contributing

Use the devcontainer (VS Code: **Dev Containers: Reopen in Container**) for the
supported Python 3.14 environment and the deterministic Home Assistant 2026.8.3
baseline. It installs `/home/vscode/.venv` from `requirements_test.txt`.

Run `pytest`, `ruff check .`, `ruff format --check .`, and
`pre-commit run --all-files`. Start local Home Assistant at `http://localhost:8123`
with `scripts/run-ha-dev`; configure Hero only through its UI. Docker Hassfest can
be run with `docker run --rm -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest`.

Pull-request titles use lower-case Conventional Commits. Release Please prepares
releases and updates the integration manifest; do not manually publish releases.
