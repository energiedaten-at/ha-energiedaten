"""Config flow for energiedaten.at."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AuthenticationError, EnergiedatenApiClient
from .const import CONF_METERS, CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Team is derived from the API key server-side; we don't know the team
# name without an extra call, so we use a fixed title. (See open question
# in the spec about adding /api/v1/user lookup for a friendlier label.)
_ENTRY_TITLE = "energiedaten.at"

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class EnergiedatenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for energiedaten.at integration."""

    VERSION = 4

    def __init__(self) -> None:
        """Initialize flow state."""
        self._token: str = ""
        self._meters: list[dict[str, Any]] = []

    def _create_client(self, token: str) -> EnergiedatenApiClient:
        """Create an API client using HA's shared aiohttp session."""
        session = async_get_clientsession(self.hass)
        return EnergiedatenApiClient(session, token)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Collect API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._token = user_input[CONF_TOKEN]
            client = self._create_client(self._token)

            try:
                await client.async_validate()
                self._meters = await client.async_get_meters()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during validation")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_meters()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Select which meters to import."""
        if user_input is not None:
            selected_uuids = user_input[CONF_METERS]
            # Live API returns `metering_point_number`; we store it as
            # `metering_point` so downstream code (coordinator, sensor) sees
            # a stable internal key.
            selected_meters = [
                {
                    "uuid": m["id"],
                    "metering_point": m["metering_point_number"],
                    "energy_direction": m["energy_direction"],
                    "label": m.get("label"),
                }
                for m in self._meters
                if m["id"] in selected_uuids
            ]
            if self.source == SOURCE_RECONFIGURE:
                # data_updates merges with entry.data, preserving sync cursors.
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data_updates={
                        CONF_TOKEN: self._token,
                        CONF_METERS: selected_meters,
                    },
                )
            return self.async_create_entry(
                title=_ENTRY_TITLE,
                data={
                    CONF_TOKEN: self._token,
                    CONF_METERS: selected_meters,
                },
            )

        connected = [m for m in self._meters if m["status"] == "connected"]
        options = [
            SelectOptionDict(
                value=m["id"],
                label=self._meter_display_name(m),
            )
            for m in connected
        ]
        default_uuids = self._default_meter_uuids(connected)

        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METERS, default=default_uuids): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    def _default_meter_uuids(
        self, connected: list[dict[str, Any]]
    ) -> list[str]:
        """Default the meter checkboxes to current selection (reconfigure) or all."""
        connected_ids = [m["id"] for m in connected]
        if self.source != SOURCE_RECONFIGURE:
            return connected_ids
        currently_imported = {
            m["uuid"] for m in self._get_reconfigure_entry().data.get(CONF_METERS, [])
        }
        # Intersect with connected so stale or disconnected meters drop out.
        return [uuid for uuid in connected_ids if uuid in currently_imported]

    @staticmethod
    def _meter_display_name(meter: dict[str, Any]) -> str:
        """Format meter display name for the selection list."""
        label = meter.get("label") or meter["metering_point_number"][-6:]
        direction = (
            "Consumption" if meter["energy_direction"] == "consumption" else "Feed-in"
        )
        return f"{label} ({direction})"

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run the user step + meter selection on an existing entry."""
        return await self.async_step_user(user_input)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication trigger."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step: Re-enter API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = self._create_client(user_input[CONF_TOKEN])
            try:
                await client.async_validate()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_TOKEN: user_input[CONF_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )
