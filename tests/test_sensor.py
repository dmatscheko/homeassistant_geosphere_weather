"""Tests for the sensor platform."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.geosphere_weather.const import (
    CONF_DATASET,
    DATASET_CHEM,
    DATASET_NOWCAST,
    DATASET_NWP,
    DOMAIN,
)

from .fixtures import chem_payload, nowcast_payload, nwp_payload
from .test_init import _mock_session_get

ZONE_ID = "zone.home"


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


async def _setup(hass: HomeAssistant, dataset: str, payload: dict) -> None:
    _register_home(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: dataset},
        unique_id=f"{ZONE_ID}_{dataset}",
        title=f"Home ({dataset})",
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.geosphere_weather.coordinator.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.get = _mock_session_get(payload)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_air_quality_sensors_expose_each_pollutant(
    hass: HomeAssistant,
) -> None:
    """WRF-Chem exposes one sensor per pollutant with the first payload sample."""
    await _setup(hass, DATASET_CHEM, chem_payload())

    reg = er.async_get(hass)
    # Build a map of description.key -> state via entity registry unique_ids
    # (unique_id is ``<entry_id>_<key>``).
    state_map: dict[str, object] = {}
    for entry in reg.entities.values():
        if entry.platform != DOMAIN or entry.domain != "sensor":
            continue
        key = entry.unique_id.rsplit("_", 1)[-1]
        state_map[key] = hass.states.get(entry.entity_id)

    # Fixture populates all four pollutants with value-index 0.
    assert state_map["no2"].state == "15.0"
    assert state_map["o3"].state == "60.0"
    assert state_map["pm10"].state == "12.0"
    assert state_map["pm25"].state == "6.0"

    for state in state_map.values():
        # Metadata attributes
        assert state.attributes["dataset"] == DATASET_CHEM
        assert state.attributes["latitude"] == 48.208
        assert state.attributes["longitude"] == 16.373
        assert "reference_time" in state.attributes


async def test_weather_symbol_sensor_uses_stable_slug(hass: HomeAssistant) -> None:
    """AROME exposes a weather_symbol enum sensor with an English slug."""
    await _setup(hass, DATASET_NWP, nwp_payload())

    ids = [s for s in hass.states.async_entity_ids("sensor") if s.endswith("weather_symbol")]
    assert len(ids) == 1
    state = hass.states.get(ids[0])
    # Fixture sets sy=1 for the first step => "cloudless".
    assert state.state == "cloudless"


async def test_precipitation_type_sensor_for_nowcast(hass: HomeAssistant) -> None:
    """INCA exposes a precipitation_type enum sensor using the pt slug map."""
    await _setup(hass, DATASET_NOWCAST, nowcast_payload())

    ids = [
        s
        for s in hass.states.async_entity_ids("sensor")
        if s.endswith("precipitation_type")
    ]
    assert len(ids) == 1
    state = hass.states.get(ids[0])
    # Fixture sets pt=255 for the first step => "none".
    assert state.state == "none"


@pytest.mark.parametrize(
    "pt_value, expected",
    [(1, "rain"), (7, "snow"), (255, "none")],
)
async def test_precipitation_type_pt_slug_mapping(
    hass: HomeAssistant, pt_value: int, expected: str
) -> None:
    """Each mapped INCA pt code translates to its slug."""
    payload = nowcast_payload()
    payload["features"][0]["properties"]["parameters"]["pt"]["data"][0] = pt_value
    await _setup(hass, DATASET_NOWCAST, payload)
    ids = [
        s
        for s in hass.states.async_entity_ids("sensor")
        if s.endswith("precipitation_type")
    ]
    assert hass.states.get(ids[0]).state == expected


async def test_precipitation_type_unknown_pt_code_is_unknown(
    hass: HomeAssistant,
) -> None:
    """An unmapped pt code must surface as ``unknown`` rather than a number."""
    payload = nowcast_payload()
    payload["features"][0]["properties"]["parameters"]["pt"]["data"][0] = 99
    await _setup(hass, DATASET_NOWCAST, payload)
    ids = [
        s
        for s in hass.states.async_entity_ids("sensor")
        if s.endswith("precipitation_type")
    ]
    # Unmapped -> None -> "unknown" in the HA state machine.
    assert hass.states.get(ids[0]).state == "unknown"
