"""Tests for integration setup/unload and entity wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.geosphere_weather.const import (
    CONF_DATASET,
    DATASET_CHEM,
    DATASET_ENSEMBLE,
    DATASET_NWP,
    DOMAIN,
)

from .fixtures import chem_payload, ensemble_payload, nwp_payload

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


def _mock_session_dispatch(payloads: dict[str, dict]):
    """Return a session.get mock that picks a payload by dataset id in the URL."""

    def _get(url: str, *_args, **_kwargs) -> _FakeResponse:
        for key, payload in payloads.items():
            if key in url:
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected request url: {url}")

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


async def test_reconfigure_switches_weather_to_air_quality(
    hass: HomeAssistant,
) -> None:
    """Switching AROME -> WRF-Chem removes the weather entity and creates sensors."""
    _register_home(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        unique_id=f"{ZONE_ID}_{DATASET_NWP}",
        title="Home (AROME)",
    )
    entry.add_to_hass(hass)

    payloads = {"nwp-v1": nwp_payload(), "chem-v2": chem_payload()}
    with patch(
        "custom_components.geosphere_weather.coordinator.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.get = _mock_session_dispatch(payloads)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        weather_entities = hass.states.async_entity_ids("weather")
        assert weather_entities, "weather entity should exist for AROME"
        assert not hass.states.async_entity_ids("sensor")

        hass.config_entries.async_update_entry(
            entry,
            data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_CHEM},
            unique_id=f"{ZONE_ID}_{DATASET_CHEM}",
            title="Home (WRF-Chem)",
        )
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not hass.states.async_entity_ids("weather"), (
        "weather entity must be gone after switching to WRF-Chem"
    )
    sensor_entities = hass.states.async_entity_ids("sensor")
    assert len(sensor_entities) == 4, sensor_entities

    ent_reg = er.async_get(hass)
    remaining = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert all(e.domain == "sensor" for e in remaining)


async def test_reconfigure_switches_between_weather_datasets(
    hass: HomeAssistant,
) -> None:
    """Switching AROME -> C-LAEF keeps the weather entity but rebuilds coordinator."""
    _register_home(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        unique_id=f"{ZONE_ID}_{DATASET_NWP}",
        title="Home (AROME)",
    )
    entry.add_to_hass(hass)

    payloads = {"nwp-v1": nwp_payload(), "ensemble-v1": ensemble_payload()}
    with patch(
        "custom_components.geosphere_weather.coordinator.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.get = _mock_session_dispatch(payloads)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.async_entity_ids("weather")

        hass.config_entries.async_update_entry(
            entry,
            data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_ENSEMBLE},
            unique_id=f"{ZONE_ID}_{DATASET_ENSEMBLE}",
            title="Home (C-LAEF)",
        )
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.async_entity_ids("weather")
    assert not hass.states.async_entity_ids("sensor")
    assert entry.runtime_data.dataset == DATASET_ENSEMBLE


async def test_reconfigure_switches_zone(hass: HomeAssistant) -> None:
    """Pointing the entry at a different zone rebuilds the coordinator's location."""
    hass.states.async_set(
        "zone.a",
        "zoning",
        {"friendly_name": "A", ATTR_LATITUDE: 48.208, ATTR_LONGITUDE: 16.373},
    )
    hass.states.async_set(
        "zone.b",
        "zoning",
        {"friendly_name": "B", ATTR_LATITUDE: 47.070, ATTR_LONGITUDE: 15.439},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: "zone.a", CONF_DATASET: DATASET_NWP},
        unique_id=f"zone.a_{DATASET_NWP}",
        title="A (AROME)",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.geosphere_weather.coordinator.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.get = _mock_session_get(nwp_payload())
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.latitude == 48.208
        assert entry.runtime_data.longitude == 16.373

        hass.config_entries.async_update_entry(
            entry,
            data={CONF_ZONE: "zone.b", CONF_DATASET: DATASET_NWP},
            unique_id=f"zone.b_{DATASET_NWP}",
            title="B (AROME)",
        )
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.latitude == 47.070
    assert entry.runtime_data.longitude == 15.439
    assert hass.states.async_entity_ids("weather")


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
