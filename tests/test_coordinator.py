"""Tests for the coordinator payload parsers and helpers."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from homeassistant.components.weather import (
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
)

from custom_components.geosphere_weather.coordinator import (
    AirQualityBundle,
    WeatherBundle,
    _condition_from_pt,
    _derive_condition,
    _dominant_condition,
    _parse_chem,
    _parse_datetime,
    _parse_ensemble,
    _parse_nowcast,
    _parse_nwp,
    _precip_delta,
    _precip_probability,
    _wind_from_components,
)

from .fixtures import chem_payload, ensemble_payload, nowcast_payload, nwp_payload


def test_parse_datetime_accepts_z_suffix() -> None:
    """ISO strings ending in Z must be treated as UTC."""
    assert _parse_datetime("2026-04-17T00:00:00Z") == datetime(
        2026, 4, 17, 0, 0, tzinfo=UTC
    )


def test_wind_from_components_speed_and_bearing() -> None:
    """Eastward-only wind (u>0, v=0) blows *from* the west → 270°."""
    speed, bearing = _wind_from_components(3.0, 4.0)
    assert speed == pytest.approx(5.0)
    assert 0.0 <= bearing <= 360.0

    speed_e, bearing_e = _wind_from_components(1.0, 0.0)
    assert math.isclose(bearing_e, 270.0)
    assert speed_e == pytest.approx(1.0)

    assert _wind_from_components(None, 1.0) == (None, None)
    assert _wind_from_components(1.0, None) == (None, None)


def test_precip_delta_first_step_averages_from_reference() -> None:
    """Within the first forecast step, accumulated precipitation is spread over the gap."""
    ts = [datetime(2026, 4, 17, 2, 0, tzinfo=UTC)]
    ref = datetime(2026, 4, 17, 0, 0, tzinfo=UTC)
    assert _precip_delta([4.0], 0, ts, ref) == pytest.approx(2.0)


def test_precip_delta_subsequent_steps_are_diffs() -> None:
    """Subsequent steps diff against the previous accumulated value, clamped at 0."""
    ts = [
        datetime(2026, 4, 17, 1, 0, tzinfo=UTC),
        datetime(2026, 4, 17, 2, 0, tzinfo=UTC),
    ]
    ref = datetime(2026, 4, 17, 0, 0, tzinfo=UTC)
    assert _precip_delta([1.0, 2.5], 1, ts, ref) == pytest.approx(1.5)
    # Going "backwards" should clamp to 0 — accumulated series should never
    # decrease, but if the API misbehaves we do not emit negative precipitation.
    assert _precip_delta([2.0, 1.0], 1, ts, ref) == 0.0
    # None propagates through as None.
    assert _precip_delta([None, 1.0], 0, ts, ref) is None


def test_precip_probability_buckets() -> None:
    """p10 above threshold → very likely; all below → very unlikely."""
    assert _precip_probability(0.2, 0.5, 1.0) == 95.0
    assert _precip_probability(0.0, 0.5, 1.0) == 75.0
    assert _precip_probability(0.0, 0.0, 0.5) == 25.0
    assert _precip_probability(0.0, 0.0, 0.0) == 5.0
    assert _precip_probability(None, None, None) is None


def test_derive_condition_snow_rain_thresholds() -> None:
    """Temperature and precipitation jointly select snow/sleet/rain."""
    assert (
        _derive_condition(cloud_pct=90, precipitation=1.0, temperature=-1.0)
        == ATTR_CONDITION_SNOWY
    )
    assert (
        _derive_condition(cloud_pct=90, precipitation=1.0, temperature=1.5)
        == ATTR_CONDITION_SNOWY_RAINY
    )
    assert (
        _derive_condition(cloud_pct=90, precipitation=3.0, temperature=10.0)
        == ATTR_CONDITION_POURING
    )
    assert (
        _derive_condition(cloud_pct=90, precipitation=0.5, temperature=10.0)
        == ATTR_CONDITION_RAINY
    )
    assert _derive_condition(cloud_pct=10, precipitation=0.0, temperature=10) == (
        ATTR_CONDITION_SUNNY
    )
    assert _derive_condition(cloud_pct=50, precipitation=0.0, temperature=10) == (
        ATTR_CONDITION_PARTLYCLOUDY
    )
    assert _derive_condition(cloud_pct=90, precipitation=0.0, temperature=10) == (
        ATTR_CONDITION_CLOUDY
    )
    assert _derive_condition(cloud_pct=None, precipitation=0.0, temperature=10) is None


def test_condition_from_pt_codes() -> None:
    """INCA precipitation-type codes map to sensible conditions."""
    assert _condition_from_pt(1, 0.5) == ATTR_CONDITION_RAINY
    assert _condition_from_pt(1, 3.0) == ATTR_CONDITION_POURING
    assert _condition_from_pt(7, 0.0) == ATTR_CONDITION_SNOWY
    assert _condition_from_pt(255, 0.0) is None
    assert _condition_from_pt(None, 0.0) is None


def test_dominant_condition_priority() -> None:
    """Dominant condition picks the most severe present."""
    assert _dominant_condition(
        [ATTR_CONDITION_SUNNY, ATTR_CONDITION_CLOUDY, ATTR_CONDITION_LIGHTNING_RAINY]
    ) == ATTR_CONDITION_LIGHTNING_RAINY
    assert _dominant_condition([None, None]) is None


def test_parse_nwp_produces_bundle_with_hourly_and_daily() -> None:
    """AROME payload produces a WeatherBundle with current, hourly and daily."""
    bundle = _parse_nwp(nwp_payload())
    assert isinstance(bundle, WeatherBundle)
    assert len(bundle.hourly) == 3
    # sy=1 maps to sunny; current condition is derived from first hourly step.
    assert bundle.current.condition == ATTR_CONDITION_SUNNY
    # Pressure converted from Pa to hPa.
    assert bundle.current.pressure == pytest.approx(1013.0)
    # Daily aggregation produces at least one day and reasonable min/max.
    assert bundle.daily
    day = bundle.daily[0]
    assert day.temperature_max == pytest.approx(13.0)
    assert day.temperature_min == pytest.approx(9.0)


def test_parse_nwp_precipitation_is_differenced() -> None:
    """Accumulated precipitation must be turned into per-step deltas."""
    bundle = _parse_nwp(nwp_payload())
    # rr_acc = [0.0, 0.5, 2.5] over 1h steps → deltas 0.0, 0.5, 2.0
    assert bundle.hourly[0].precipitation == pytest.approx(0.0)
    assert bundle.hourly[1].precipitation == pytest.approx(0.5)
    assert bundle.hourly[2].precipitation == pytest.approx(2.0)


def test_parse_ensemble_emits_probability() -> None:
    """Ensemble payload yields probability derived from p10/p50/p90."""
    bundle = _parse_ensemble(ensemble_payload())
    assert isinstance(bundle, WeatherBundle)
    probs = [p.precipitation_probability for p in bundle.hourly]
    # Last step has rr_p90=1.2 > 0.1 but rr_p50=0.4 also > 0.1 → 75%.
    assert probs[-1] == 75.0


def test_parse_nowcast_uses_scalar_wind_and_no_daily() -> None:
    """Nowcast uses scalar ff/dd/fx; no daily forecast is produced."""
    bundle = _parse_nowcast(nowcast_payload())
    assert isinstance(bundle, WeatherBundle)
    assert bundle.daily == []
    assert bundle.hourly[0].wind_speed == pytest.approx(2.0)
    assert bundle.hourly[0].wind_bearing == pytest.approx(90.0)
    # pt=7 → snow
    assert bundle.hourly[2].condition == ATTR_CONDITION_SNOWY


def test_parse_chem_produces_air_quality_bundle() -> None:
    """Chem payload maps to an AirQualityBundle with all four pollutants."""
    bundle = _parse_chem(chem_payload())
    assert isinstance(bundle, AirQualityBundle)
    assert bundle.current.no2 == 15.0
    assert bundle.current.o3 == 60.0
    assert bundle.current.pm10 == 12.0
    assert bundle.current.pm25 == 6.0
    assert len(bundle.hourly) == 2
