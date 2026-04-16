"""Shared fixtures for the GeoSphere Austria integration tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable loading of custom integrations in every test."""
    return None


@pytest.fixture
def bypass_setup_fixture() -> Generator[None]:
    """Prevent actual setup of the integration during config flow tests."""
    with patch(
        "custom_components.geosphere_weather.async_setup_entry",
        return_value=True,
    ):
        yield
