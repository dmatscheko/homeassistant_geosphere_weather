"""Tests for the coordinator payload parsers and helpers."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from aiohttp import ClientError, ClientResponseError, RequestInfo
from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from yarl import URL

from custom_components.geosphere_weather.const import DATASET_NWP, DOMAIN
from custom_components.geosphere_weather.coordinator import (
    AirQualityBundle,
    GeoSphereDataUpdateCoordinator,
    HourlyPoint,
    WeatherBundle,
    _aggregate_daily,
    _apply_night_condition,
    _condition_from_pt,
    _derive_condition,
    _dominant_condition,
    _is_night,
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


@pytest.mark.parametrize(
    ("u", "v", "expected_bearing"),
    [
        # v>0 means wind blows toward north → comes *from* the south (180°).
        (0.0, 1.0, 180.0),
        # v<0 means wind blows toward south → comes *from* the north (0°).
        (0.0, -1.0, 0.0),
        # u<0 means wind blows toward west → comes *from* the east (90°).
        (-1.0, 0.0, 90.0),
        # u>0 means wind blows toward east → comes *from* the west (270°).
        (1.0, 0.0, 270.0),
    ],
)
def test_wind_from_components_bearing_quadrants(
    u: float, v: float, expected_bearing: float
) -> None:
    """Bearing is the meteorological direction the wind comes *from*."""
    speed, bearing = _wind_from_components(u, v)
    assert speed == pytest.approx(1.0)
    assert bearing is not None
    # 0°/360° are equivalent — compare modulo 360 with a small tolerance.
    assert math.isclose(bearing % 360.0, expected_bearing % 360.0, abs_tol=1e-6)


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


def test_precip_delta_zero_hours_returns_value_as_is() -> None:
    """If the first timestamp is at or before reference time, return the raw value.

    This covers the ``hours <= 0`` guard that would otherwise divide by zero.
    """
    ts = [datetime(2026, 4, 17, 0, 0, tzinfo=UTC)]
    ref = datetime(2026, 4, 17, 0, 0, tzinfo=UTC)
    # hours == 0 branch: no division, just clamp to >= 0.
    assert _precip_delta([3.0], 0, ts, ref) == pytest.approx(3.0)
    # Negative accumulation is clamped to 0.
    assert _precip_delta([-1.0], 0, ts, ref) == 0.0
    # If the timestamp is *before* reference time, still take the value as-is.
    ts_before = [datetime(2026, 4, 16, 23, 0, tzinfo=UTC)]
    assert _precip_delta([2.0], 0, ts_before, ref) == pytest.approx(2.0)


def test_precip_delta_previous_none_falls_back_to_current() -> None:
    """When the previous accumulator is None, return the current value clamped to 0."""
    ts = [
        datetime(2026, 4, 17, 1, 0, tzinfo=UTC),
        datetime(2026, 4, 17, 2, 0, tzinfo=UTC),
    ]
    ref = datetime(2026, 4, 17, 0, 0, tzinfo=UTC)
    assert _precip_delta([None, 0.7], 1, ts, ref) == pytest.approx(0.7)
    assert _precip_delta([None, -0.5], 1, ts, ref) == 0.0


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


def test_parse_rejects_empty_timestamps() -> None:
    """A payload with no timestamps must raise a ``ValueError`` the
    coordinator maps to UpdateFailed.
    """
    payload = nwp_payload()
    payload["timestamps"] = []
    with pytest.raises(ValueError):
        _parse_nwp(payload)


def test_parse_rejects_missing_features() -> None:
    """A payload with no GeoJSON features must raise ``ValueError``."""
    payload = nwp_payload()
    payload["features"] = []
    with pytest.raises(ValueError):
        _parse_nwp(payload)


class _RaiseResponse:
    """Async context manager whose ``raise_for_status`` raises a given error."""

    def __init__(self, err: Exception) -> None:
        self._err = err
        self.status = 500

    async def __aenter__(self) -> _RaiseResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        raise self._err

    async def json(self) -> dict:  # pragma: no cover - never reached
        return {}


class _PayloadResponse:
    """Stub that returns a fixed JSON payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    async def __aenter__(self) -> _PayloadResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict:
        return self._payload


def _build_coordinator(hass: HomeAssistant) -> GeoSphereDataUpdateCoordinator:
    """Create a coordinator with a MockConfigEntry for error-path tests."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return GeoSphereDataUpdateCoordinator(hass, entry, 48.2, 16.3, DATASET_NWP)


async def test_coordinator_http_error_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """HTTP 5xx responses surface as UpdateFailed with the translated key."""
    coordinator = _build_coordinator(hass)
    request_info = RequestInfo(
        url=URL("https://example.invalid/"),
        method="GET",
        headers={},  # type: ignore[arg-type]
        real_url=URL("https://example.invalid/"),
    )
    err = ClientResponseError(
        request_info=request_info,
        history=(),
        status=503,
        message="Service Unavailable",
    )

    def _get(*_args, **_kwargs) -> _RaiseResponse:
        return _RaiseResponse(err)

    with patch.object(coordinator, "_session") as session:
        session.get = _get
        with pytest.raises(UpdateFailed) as exc:
            await coordinator._async_update_data()
    assert exc.value.translation_key == "api_http_error"
    assert exc.value.translation_placeholders == {
        "status": "503",
        "reason": "Service Unavailable",
    }


async def test_coordinator_client_error_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """Transport errors surface as UpdateFailed with the communication key."""
    coordinator = _build_coordinator(hass)

    def _get(*_args, **_kwargs):
        raise ClientError("boom")

    with patch.object(coordinator, "_session") as session:
        session.get = _get
        with pytest.raises(UpdateFailed) as exc:
            await coordinator._async_update_data()
    assert exc.value.translation_key == "api_communication_error"


async def test_coordinator_timeout_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """A request timeout surfaces as UpdateFailed with the communication key.

    aiohttp's total timeout raises ``TimeoutError``, which is *not* a
    ``ClientError`` — it must be mapped explicitly.
    """
    coordinator = _build_coordinator(hass)

    def _get(*_args, **_kwargs):
        raise TimeoutError

    with patch.object(coordinator, "_session") as session:
        session.get = _get
        with pytest.raises(UpdateFailed) as exc:
            await coordinator._async_update_data()
    assert exc.value.translation_key == "api_communication_error"


async def test_coordinator_invalid_json_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """A 200 response with a malformed JSON body surfaces as unexpected-response."""
    import json

    coordinator = _build_coordinator(hass)

    class _BadJsonResponse(_PayloadResponse):
        async def json(self) -> dict:
            raise json.JSONDecodeError("Expecting value", "<html>", 0)

    def _get(*_args, **_kwargs) -> _PayloadResponse:
        return _BadJsonResponse({})

    with patch.object(coordinator, "_session") as session:
        session.get = _get
        with pytest.raises(UpdateFailed) as exc:
            await coordinator._async_update_data()
    assert exc.value.translation_key == "api_unexpected_response"


async def test_coordinator_malformed_payload_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """Structurally broken payloads surface as UpdateFailed with unexpected-response."""
    coordinator = _build_coordinator(hass)
    broken = nwp_payload()
    broken["timestamps"] = []

    def _get(*_args, **_kwargs) -> _PayloadResponse:
        return _PayloadResponse(broken)

    with patch.object(coordinator, "_session") as session:
        session.get = _get
        with pytest.raises(UpdateFailed) as exc:
            await coordinator._async_update_data()
    assert exc.value.translation_key == "api_unexpected_response"


def test_aggregate_daily_falls_back_to_hourly_temps_when_min_max_missing() -> None:
    """If mnt2m/mxt2m are all None, min/max come from the hourly temperature series."""
    hourly = [
        HourlyPoint(
            time=datetime(2026, 4, 17, h, 0, tzinfo=UTC),
            temperature=t,
            wind_speed=ws,
            wind_bearing=wb,
            condition=cond,
        )
        for h, t, ws, wb, cond in [
            (1, 5.0, 1.0, 90.0, ATTR_CONDITION_SUNNY),
            (2, 10.0, 3.0, 100.0, ATTR_CONDITION_RAINY),
            (3, 7.0, 2.0, 110.0, ATTR_CONDITION_CLOUDY),
        ]
    ]
    mnt2m: list[float | None] = [None, None, None]
    mxt2m: list[float | None] = [None, None, None]
    daily = _aggregate_daily(hourly, mnt2m, mxt2m)
    assert len(daily) == 1
    day = daily[0]
    assert day.temperature_max == pytest.approx(10.0)
    assert day.temperature_min == pytest.approx(5.0)
    # Wind bearing is the bearing at the step with the maximum wind speed.
    assert day.wind_speed_max == pytest.approx(3.0)
    assert day.wind_bearing == pytest.approx(100.0)
    # Dominant condition picks the most significant one present.
    assert day.condition == ATTR_CONDITION_RAINY


def test_aggregate_daily_buckets_across_utc_midnight() -> None:
    """Hourly points spanning UTC midnight produce one bucket per UTC date."""
    hourly = [
        HourlyPoint(
            time=datetime(2026, 4, 17, 23, 0, tzinfo=UTC),
            temperature=8.0,
            precipitation=0.5,
        ),
        HourlyPoint(
            time=datetime(2026, 4, 18, 0, 0, tzinfo=UTC),
            temperature=6.0,
            precipitation=0.2,
        ),
        HourlyPoint(
            time=datetime(2026, 4, 18, 1, 0, tzinfo=UTC),
            temperature=5.0,
            precipitation=0.0,
        ),
    ]
    daily = _aggregate_daily(hourly, [None, None, None], [None, None, None])
    assert [d.day.isoformat() for d in daily] == ["2026-04-17", "2026-04-18"]
    # Precipitation is summed per UTC day.
    assert daily[0].precipitation == pytest.approx(0.5)
    assert daily[1].precipitation == pytest.approx(0.2)


def test_aggregate_daily_handles_all_none_values() -> None:
    """With no usable data in a bucket, all aggregates are None / 0."""
    hourly = [
        HourlyPoint(time=datetime(2026, 4, 17, 1, 0, tzinfo=UTC)),
        HourlyPoint(time=datetime(2026, 4, 17, 2, 0, tzinfo=UTC)),
    ]
    daily = _aggregate_daily(hourly, [None, None], [None, None])
    assert len(daily) == 1
    day = daily[0]
    assert day.temperature_max is None
    assert day.temperature_min is None
    # No precipitation values present → falls back to 0.0 (not None).
    assert day.precipitation == 0.0
    assert day.precipitation_probability is None
    assert day.wind_speed_max is None
    assert day.wind_bearing is None
    assert day.condition is None


# Vienna — used for the night/day fixup tests.
_VIENNA_LAT = 48.208
_VIENNA_LON = 16.373


def test_is_night_matches_solar_elevation_for_vienna() -> None:
    """Midnight UTC in April in Vienna is clearly night; midday is clearly day."""
    assert _is_night(datetime(2026, 4, 17, 0, 0, tzinfo=UTC), _VIENNA_LAT, _VIENNA_LON)
    assert not _is_night(
        datetime(2026, 4, 17, 12, 0, tzinfo=UTC), _VIENNA_LAT, _VIENNA_LON
    )


def test_apply_night_condition_rewrites_sunny_to_clear_night() -> None:
    """AROME ``sy=1`` at night must be converted to ``clear-night`` after parse."""
    # Seed the payload so every step is at night over Vienna.
    payload = nwp_payload()
    payload["reference_time"] = "2026-04-16T22:00:00Z"
    payload["timestamps"] = [
        "2026-04-16T23:00:00Z",
        "2026-04-17T00:00:00Z",
        "2026-04-17T01:00:00Z",
    ]
    bundle = _parse_nwp(payload)
    # Before the fixup the first step is sunny (sy=1) — pre-condition.
    assert bundle.hourly[0].condition == ATTR_CONDITION_SUNNY

    _apply_night_condition(bundle, _VIENNA_LAT, _VIENNA_LON)

    assert bundle.hourly[0].condition == ATTR_CONDITION_CLEAR_NIGHT
    # ``current`` is re-synced from the first hourly step.
    assert bundle.current.condition == ATTR_CONDITION_CLEAR_NIGHT
    # The symbol slug keeps the raw model label — that is *not* a condition,
    # it's the AROME ``sy`` code and is used for a separately translated
    # enum sensor.
    assert bundle.hourly[0].symbol_slug == "cloudless"


def test_apply_night_condition_leaves_daytime_points_untouched() -> None:
    """A daytime sunny step stays sunny."""
    payload = nwp_payload()  # timestamps at 01..03 UTC, but fixture is April
    payload["reference_time"] = "2026-04-17T10:00:00Z"
    payload["timestamps"] = [
        "2026-04-17T11:00:00Z",
        "2026-04-17T12:00:00Z",
        "2026-04-17T13:00:00Z",
    ]
    bundle = _parse_nwp(payload)
    _apply_night_condition(bundle, _VIENNA_LAT, _VIENNA_LON)
    assert bundle.hourly[0].condition == ATTR_CONDITION_SUNNY
    assert bundle.current.condition == ATTR_CONDITION_SUNNY


def test_parse_chem_produces_air_quality_bundle() -> None:
    """Chem payload maps to an AirQualityBundle with all four pollutants."""
    bundle = _parse_chem(chem_payload())
    assert isinstance(bundle, AirQualityBundle)
    assert bundle.current.no2 == 15.0
    assert bundle.current.o3 == 60.0
    assert bundle.current.pm10 == 12.0
    assert bundle.current.pm25 == 6.0
    assert len(bundle.hourly) == 2
