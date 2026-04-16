"""Config flow for the GeoSphere Austria weather integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.zone import DOMAIN as ZONE_DOMAIN
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DATASET,
    DATASET_BBOX,
    DATASET_DOC_URL,
    DATASET_SHORT_NAMES,
    DEFAULT_DATASET,
    DOMAIN,
    SUPPORTED_DATASETS,
)


def _doc_links_markdown() -> str:
    """Render a markdown bullet list of dataset documentation URLs."""
    return "\n".join(
        f"- **{DATASET_SHORT_NAMES[ds]}** (`{ds}`): {DATASET_DOC_URL[ds]}"
        for ds in SUPPORTED_DATASETS
    )


class GeoSphereFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GeoSphere Austria."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zone_id = user_input[CONF_ZONE]
            dataset = user_input[CONF_DATASET]
            await self.async_set_unique_id(f"{zone_id}_{dataset}")
            self._abort_if_unique_id_configured()

            zone_state = self.hass.states.get(zone_id)
            if zone_state is None:
                errors["base"] = "zone_not_found"
            else:
                latitude = zone_state.attributes.get(ATTR_LATITUDE)
                longitude = zone_state.attributes.get(ATTR_LONGITUDE)
                if latitude is None or longitude is None:
                    errors["base"] = "zone_missing_coordinates"
                elif not _within_bbox(latitude, longitude, dataset):
                    errors["base"] = "out_of_range"
                else:
                    label = DATASET_SHORT_NAMES.get(dataset, dataset)
                    return self.async_create_entry(
                        title=f"{zone_state.name} ({label})",
                        data={CONF_ZONE: zone_id, CONF_DATASET: dataset},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE): EntitySelector(
                        EntitySelectorConfig(domain=ZONE_DOMAIN)
                    ),
                    vol.Required(CONF_DATASET, default=DEFAULT_DATASET): SelectSelector(
                        SelectSelectorConfig(
                            options=list(SUPPORTED_DATASETS),
                            translation_key="dataset",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"doc_links": _doc_links_markdown()},
        )


def _within_bbox(latitude: float, longitude: float, dataset: str) -> bool:
    """Check whether coordinates fall inside the dataset's coverage domain."""
    bbox = DATASET_BBOX.get(dataset)
    if bbox is None:
        return True
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon
