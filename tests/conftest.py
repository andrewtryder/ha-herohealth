"""Shared Home Assistant custom-integration test configuration."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Load this repository's custom integration in real HA test instances."""
    yield
