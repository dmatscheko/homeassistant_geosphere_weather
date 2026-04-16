"""Diagnostics support for the GeoSphere Austria integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import GeoSphereConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GeoSphereConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "unique_id": entry.unique_id,
        },
        "coordinator": {
            "dataset": coordinator.dataset,
            "latitude": coordinator.latitude,
            "longitude": coordinator.longitude,
            "last_update_success": coordinator.last_update_success,
        },
        "data": _dump(data),
    }


def _dump(value: Any) -> Any:
    """Recursively convert dataclasses/datetimes to primitives."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _dump(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
