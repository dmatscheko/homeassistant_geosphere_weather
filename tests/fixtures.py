"""Synthetic GeoSphere API payloads used in tests."""

from __future__ import annotations

from typing import Any


def _payload(
    reference_time: str,
    timestamps: list[str],
    parameters: dict[str, list[float | None]],
) -> dict[str, Any]:
    return {
        "reference_time": reference_time,
        "timestamps": timestamps,
        "features": [
            {
                "properties": {
                    "parameters": {
                        name: {"data": values}
                        for name, values in parameters.items()
                    }
                }
            }
        ],
    }


def nwp_payload() -> dict[str, Any]:
    """Minimal 3-step AROME NWP payload."""
    return _payload(
        reference_time="2026-04-17T00:00:00Z",
        timestamps=[
            "2026-04-17T01:00:00Z",
            "2026-04-17T02:00:00Z",
            "2026-04-17T03:00:00Z",
        ],
        parameters={
            "t2m": [10.0, 12.0, 11.0],
            "rh2m": [70.0, 65.0, 60.0],
            "tcc": [0.1, 0.5, 0.9],
            "sp": [101300.0, 101280.0, 101250.0],
            "rr_acc": [0.0, 0.5, 2.5],
            "u10m": [1.0, 2.0, -1.0],
            "v10m": [1.0, -2.0, -1.0],
            "ugust": [2.0, 3.0, -2.0],
            "vgust": [2.0, -3.0, -2.0],
            "sy": [1, 3, 9],
            "mnt2m": [9.0, 11.0, 10.0],
            "mxt2m": [11.0, 13.0, 12.0],
        },
    )


def ensemble_payload() -> dict[str, Any]:
    """Minimal 3-step C-LAEF ensemble payload."""
    return _payload(
        reference_time="2026-04-17T00:00:00Z",
        timestamps=[
            "2026-04-17T01:00:00Z",
            "2026-04-17T02:00:00Z",
            "2026-04-17T03:00:00Z",
        ],
        parameters={
            "t2m_p10": [8.0, 9.0, 10.0],
            "t2m_p50": [10.0, 11.0, 12.0],
            "t2m_p90": [12.0, 13.0, 14.0],
            "tcc_p50": [0.1, 0.4, 0.8],
            "rr_p10": [0.0, 0.0, 0.0],
            "rr_p50": [0.0, 0.1, 0.4],
            "rr_p90": [0.0, 0.3, 1.2],
            "u10m_p50": [1.0, 2.0, 3.0],
            "v10m_p50": [1.0, 0.0, -1.0],
            "mnt2m_p50": [9.0, 10.0, 11.0],
            "mxt2m_p50": [11.0, 12.0, 13.0],
        },
    )


def nowcast_payload() -> dict[str, Any]:
    """Minimal 3-step INCA nowcast payload."""
    return _payload(
        reference_time="2026-04-17T00:00:00Z",
        timestamps=[
            "2026-04-17T00:15:00Z",
            "2026-04-17T00:30:00Z",
            "2026-04-17T00:45:00Z",
        ],
        parameters={
            "t2m": [5.0, 5.5, 6.0],
            "td": [3.0, 3.2, 3.5],
            "rh2m": [80.0, 78.0, 76.0],
            "rr": [0.0, 0.5, 3.0],
            "pt": [255, 1, 7],
            "ff": [2.0, 2.5, 3.0],
            "dd": [90.0, 100.0, 110.0],
            "fx": [4.0, 5.0, 6.0],
        },
    )


def chem_payload() -> dict[str, Any]:
    """Minimal 2-step WRF-Chem air-quality payload."""
    return _payload(
        reference_time="2026-04-17T00:00:00Z",
        timestamps=[
            "2026-04-17T01:00:00Z",
            "2026-04-17T02:00:00Z",
        ],
        parameters={
            "no2surf": [15.0, 20.0],
            "o3surf": [60.0, 55.0],
            "pm10surf": [12.0, 14.0],
            "pm25surf": [6.0, 7.0],
        },
    )
