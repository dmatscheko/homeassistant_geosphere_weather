"""Config flow for the GeoSphere Austria weather integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from aiohttp import ClientError, ClientTimeout
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    API_BASE_URL,
    CONF_DATASET,
    DATASET_BBOX,
    DATASET_DOC_URL,
    DATASET_SHORT_NAMES,
    DEFAULT_DATASET,
    DOMAIN,
    LOGGER,
    SUPPORTED_DATASETS,
)


def _doc_links_markdown() -> str:
    """Render a markdown bullet list of dataset documentation URLs."""
    return "\n".join(
        f"- **{DATASET_SHORT_NAMES[ds]}** (`{ds}`): {DATASET_DOC_URL[ds]}"
        for ds in SUPPORTED_DATASETS
    )


def _schema(default_zone: str | None, default_dataset: str) -> vol.Schema:
    """Return the data schema used by the user and reconfigure steps."""
    zone_field: Any = (
        vol.Required(CONF_ZONE, default=default_zone)
        if default_zone is not None
        else vol.Required(CONF_ZONE)
    )
    return vol.Schema(
        {
            zone_field: EntitySelector(EntitySelectorConfig(domain="zone")),
            vol.Required(CONF_DATASET, default=default_dataset): SelectSelector(
                SelectSelectorConfig(
                    options=list(SUPPORTED_DATASETS),
                    translation_key="dataset",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


async def _validate_input(
    hass: HomeAssistant, zone_id: str, dataset: str
) -> tuple[dict[str, str], str | None]:
    """Validate the combination of zone and dataset.

    Returns ``(errors, zone_name)``. ``zone_name`` is set only when validation
    fully succeeds and is used to label the created/updated entry.
    """
    zone_state = hass.states.get(zone_id)
    if zone_state is None:
        return {"base": "zone_not_found"}, None
    latitude = zone_state.attributes.get(ATTR_LATITUDE)
    longitude = zone_state.attributes.get(ATTR_LONGITUDE)
    if latitude is None or longitude is None:
        return {"base": "zone_missing_coordinates"}, None
    if not _within_bbox(latitude, longitude, dataset):
        return {"base": "out_of_range"}, None
    if not await _async_api_reachable(hass):
        return {"base": "cannot_connect"}, None
    return {}, zone_state.name


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

            errors, zone_name = await _validate_input(self.hass, zone_id, dataset)
            if not errors and zone_name is not None:
                label = DATASET_SHORT_NAMES.get(dataset, dataset)
                return self.async_create_entry(
                    title=f"{zone_name} ({label})",
                    data={CONF_ZONE: zone_id, CONF_DATASET: dataset},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(None, DEFAULT_DATASET),
            errors=errors,
            description_placeholders={"doc_links": _doc_links_markdown()},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the zone and/or dataset of an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            zone_id = user_input[CONF_ZONE]
            dataset = user_input[CONF_DATASET]
            new_unique_id = f"{zone_id}_{dataset}"

            # If the user picked a combination that belongs to a *different*
            # config entry, abort instead of silently clobbering it.
            for other in self._async_current_entries():
                if other.entry_id != entry.entry_id and other.unique_id == (
                    new_unique_id
                ):
                    return self.async_abort(reason="already_configured")

            errors, zone_name = await _validate_input(self.hass, zone_id, dataset)
            if not errors and zone_name is not None:
                label = DATASET_SHORT_NAMES.get(dataset, dataset)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_unique_id,
                    title=f"{zone_name} ({label})",
                    data={CONF_ZONE: zone_id, CONF_DATASET: dataset},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(
                entry.data.get(CONF_ZONE),
                entry.data.get(CONF_DATASET, DEFAULT_DATASET),
            ),
            errors=errors,
            description_placeholders={"doc_links": _doc_links_markdown()},
        )


async def _async_api_reachable(hass: HomeAssistant) -> bool:
    """Check whether the GeoSphere Data Hub API responds to a cheap request."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{API_BASE_URL}/datasets", timeout=ClientTimeout(total=10)
        ) as response:
            return response.status < 500
    except (ClientError, TimeoutError) as err:
        LOGGER.debug("GeoSphere reachability check failed: %s", err)
        return False


def _within_bbox(latitude: float, longitude: float, dataset: str) -> bool:
    """Check whether coordinates fall inside the dataset's coverage domain."""
    bbox = DATASET_BBOX.get(dataset)
    if bbox is None:
        return True
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon
