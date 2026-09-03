"""Shared Home Assistant custom-integration test configuration."""

import os

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Load this repository's custom integration in real HA test instances."""
    yield


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip explicitly selected live tests unless their deliberate gate is open."""
    if (
        os.environ.get("HERO_LIVE_TESTS") == "1"
        and os.environ.get("HERO_EMAIL")
        and os.environ.get("HERO_PASSWORD")
    ):
        return

    skip_live = pytest.mark.skip(
        reason=(
            "live Hero tests require HERO_LIVE_TESTS=1, HERO_EMAIL, and HERO_PASSWORD"
        )
    )
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip_live)
