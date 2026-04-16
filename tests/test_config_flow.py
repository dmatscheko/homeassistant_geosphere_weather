"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.geosphere_weather.const import (
    CONF_DATASET,
    DATASET_CHEM,
    DATASET_NWP,
    DOMAIN,
)

ZONE_ID = "zone.vienna"


def _register_zone(
    hass: HomeAssistant, latitude: float = 48.208, longitude: float = 16.373
) -> None:
    hass.states.async_set(
        ZONE_ID,
        "zoning",
        {"friendly_name": "Vienna", ATTR_LATITUDE: latitude, ATTR_LONGITUDE: longitude},
    )


async def test_user_flow_happy_path(
    hass: HomeAssistant, bypass_setup_fixture: None
) -> None:
    """Successful flow creates a config entry."""
    _register_zone(hass)
    with patch(
        "custom_components.geosphere_weather.config_flow._async_api_reachable",
        return_value=True,
    ):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert init["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            user_input={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP}
    assert result["title"].startswith("Vienna")


async def test_user_flow_out_of_range_shows_error(
    hass: HomeAssistant, bypass_setup_fixture: None
) -> None:
    """A location outside the dataset bbox is rejected with a form error."""
    _register_zone(hass, latitude=0.0, longitude=0.0)
    init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        init["flow_id"],
        user_input={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "out_of_range"}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, bypass_setup_fixture: None
) -> None:
    """If the API is unreachable, the flow surfaces a cannot_connect error."""
    _register_zone(hass)
    init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(
        "custom_components.geosphere_weather.config_flow._async_api_reachable",
        return_value=False,
    ):
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            user_input={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_CHEM},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_flow_updates_entry(
    hass: HomeAssistant, bypass_setup_fixture: None
) -> None:
    """Reconfigure flow lets the user switch dataset in place."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    _register_zone(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        unique_id=f"{ZONE_ID}_{DATASET_NWP}",
        title="Vienna (AROME)",
    )
    entry.add_to_hass(hass)

    init = await entry.start_reconfigure_flow(hass)
    assert init["type"] == FlowResultType.FORM
    assert init["step_id"] == "reconfigure"

    with patch(
        "custom_components.geosphere_weather.config_flow._async_api_reachable",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            user_input={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_CHEM},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DATASET] == DATASET_CHEM
    assert entry.unique_id == f"{ZONE_ID}_{DATASET_CHEM}"


async def test_user_flow_rejects_duplicate(
    hass: HomeAssistant, bypass_setup_fixture: None
) -> None:
    """A second flow for the same zone+dataset is aborted."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    _register_zone(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        unique_id=f"{ZONE_ID}_{DATASET_NWP}",
    )
    entry.add_to_hass(hass)

    init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        init["flow_id"],
        user_input={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
