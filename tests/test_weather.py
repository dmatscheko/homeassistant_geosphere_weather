"""Tests for the weather platform."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.weather import WeatherEntityFeature
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.geosphere_weather.const import (
    CONF_DATASET,
    DATASET_ENSEMBLE,
    DATASET_NOWCAST,
    DATASET_NWP,
    DOMAIN,
)

from .fixtures import ensemble_payload, nowcast_payload, nwp_payload
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


async def _setup(hass: HomeAssistant, dataset: str, payload: dict) -> MockConfigEntry:
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
    return entry


@pytest.mark.parametrize(
    ("dataset", "payload", "expect_daily"),
    [
        (DATASET_NWP, nwp_payload(), True),
        (DATASET_ENSEMBLE, ensemble_payload(), True),
        (DATASET_NOWCAST, nowcast_payload(), False),
    ],
)
async def test_weather_state_and_attributes(
    hass: HomeAssistant, dataset: str, payload: dict, expect_daily: bool
) -> None:
    """Weather entity exposes native values and dataset-appropriate features."""
    entry = await _setup(hass, dataset, payload)

    weather_ids = hass.states.async_entity_ids("weather")
    assert len(weather_ids) == 1
    state = hass.states.get(weather_ids[0])
    assert state is not None
    assert state.state  # condition derived
    assert state.attributes["dataset"] == dataset
    # Lat/long exposed as extra state attributes for informational use.
    assert state.attributes["latitude"] == 48.208
    assert state.attributes["longitude"] == 16.373
    assert state.attributes["temperature"] is not None

    features = state.attributes["supported_features"]
    if expect_daily:
        assert features & WeatherEntityFeature.FORECAST_DAILY
    else:
        assert not (features & WeatherEntityFeature.FORECAST_DAILY)
    assert features & WeatherEntityFeature.FORECAST_HOURLY

    assert entry.runtime_data.dataset == dataset


async def test_hourly_forecast_filters_past_points(hass: HomeAssistant) -> None:
    """Forecast points older than ``utcnow()`` must not appear in the hourly list."""
    await _setup(hass, DATASET_NWP, nwp_payload())

    weather_id = hass.states.async_entity_ids("weather")[0]

    # Fixture timestamps are 2026-04-17T01..03:00Z. Freeze time to midday so
    # every point is in the past and the forecast list is empty.
    with patch(
        "homeassistant.util.dt.utcnow",
        return_value=dt_util.parse_datetime("2026-04-17T12:00:00+00:00"),
    ):
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_id, "type": "hourly"},
            blocking=True,
            return_response=True,
        )
    forecast = response[weather_id]["forecast"]
    assert forecast == []


async def test_hourly_forecast_includes_future_points(hass: HomeAssistant) -> None:
    """Future forecast points make it into the hourly response."""
    await _setup(hass, DATASET_NWP, nwp_payload())
    weather_id = hass.states.async_entity_ids("weather")[0]

    # Time just before the first fixture timestamp.
    before_first = dt_util.parse_datetime("2026-04-17T00:30:00+00:00")
    assert before_first is not None
    with patch("homeassistant.util.dt.utcnow", return_value=before_first):
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_id, "type": "hourly"},
            blocking=True,
            return_response=True,
        )
    forecast = response[weather_id]["forecast"]
    assert len(forecast) == 3
    for point in forecast:
        assert "condition" in point
        # The service converts native_* to display units, so the key is "temperature".
        assert "temperature" in point
    # All timestamps strictly after `before_first`.
    assert all(
        dt_util.parse_datetime(p["datetime"]) >= before_first  # type: ignore[operator]
        for p in forecast
    )


async def test_daily_forecast_none_for_nowcast(hass: HomeAssistant) -> None:
    """Nowcast does not declare FORECAST_DAILY, so a daily call must be rejected."""
    await _setup(hass, DATASET_NOWCAST, nowcast_payload())
    weather_id = hass.states.async_entity_ids("weather")[0]

    # Home Assistant raises ServiceValidationError when the feature is missing;
    # we accept either behaviour (error OR empty list) since the contract is
    # simply "no daily forecast".
    try:
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_id, "type": "daily"},
            blocking=True,
            return_response=True,
        )
    except Exception:
        return
    assert not response[weather_id]["forecast"]


async def test_nowcast_weather_entity_exposes_dew_point_and_gust(
    hass: HomeAssistant,
) -> None:
    """INCA is the only dataset that carries dew point and scalar gust — they must surface."""
    await _setup(hass, DATASET_NOWCAST, nowcast_payload())
    weather_id = hass.states.async_entity_ids("weather")[0]
    state = hass.states.get(weather_id)
    assert state is not None
    # Fixture first step: td=3.0 (°C) and fx=4.0 m/s. HA converts the native
    # wind speed to the display unit (km/h = m/s * 3.6 by default), so the
    # attribute reflects the converted value rather than the raw m/s.
    assert state.attributes["dew_point"] == pytest.approx(3.0)
    assert state.attributes["wind_gust_speed"] == pytest.approx(4.0 * 3.6)


async def test_ensemble_weather_entity_has_no_humidity(hass: HomeAssistant) -> None:
    """C-LAEF exposes no humidity percentile — the state must carry no humidity."""
    await _setup(hass, DATASET_ENSEMBLE, ensemble_payload())
    weather_id = hass.states.async_entity_ids("weather")[0]
    state = hass.states.get(weather_id)
    assert state is not None
    assert state.attributes.get("humidity") is None


async def test_daily_forecast_present_for_nwp(hass: HomeAssistant) -> None:
    """AROME produces a non-empty daily aggregation."""
    await _setup(hass, DATASET_NWP, nwp_payload())
    weather_id = hass.states.async_entity_ids("weather")[0]

    first = dt_util.parse_datetime("2026-04-16T23:00:00+00:00")
    assert first is not None
    with patch("homeassistant.util.dt.utcnow", return_value=first - timedelta(hours=1)):
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_id, "type": "daily"},
            blocking=True,
            return_response=True,
        )
    assert response[weather_id]["forecast"]
