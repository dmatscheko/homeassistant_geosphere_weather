"""Tests for integration setup/unload and entity wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.geosphere_weather.const import (
    CONF_DATASET,
    DATASET_CHEM,
    DATASET_NWP,
    DOMAIN,
)

from .fixtures import chem_payload, nwp_payload

ZONE_ID = "zone.home"


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict:
        return self._payload


def _mock_session_get(payload: dict):
    def _get(*_args, **_kwargs) -> _FakeResponse:
        return _FakeResponse(payload)

    return _get


def _register_home(hass: HomeAssistant) -> None:
    hass.states.async_set(
        ZONE_ID,
        "zoning",
        {
            "friendly_name": "Home",
            ATTR_LATITUDE: 48.208,
            ATTR_LONGITUDE: 16.373,
        },
    )


@pytest.mark.parametrize(
    ("dataset", "payload", "expected_platform"),
    [
        (DATASET_NWP, nwp_payload(), "weather"),
        (DATASET_CHEM, chem_payload(), "sensor"),
    ],
)
async def test_setup_and_unload(
    hass: HomeAssistant,
    dataset: str,
    payload: dict,
    expected_platform: str,
) -> None:
    """Integration sets up, creates expected platform entities, and unloads cleanly."""
    _register_home(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: dataset},
        unique_id=f"{ZONE_ID}_{dataset}",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.geosphere_weather.coordinator.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.get = _mock_session_get(payload)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    states = hass.states.async_entity_ids(expected_platform)
    assert states, f"expected at least one {expected_platform} entity"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_without_zone_is_not_ready(hass: HomeAssistant) -> None:
    """Missing zone puts the entry into setup-retry state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: "zone.missing", CONF_DATASET: DATASET_NWP},
        unique_id="zone.missing_nwp",
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
