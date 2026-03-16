"""Config flow for energiedaten.at."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
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

from .api import AuthenticationError, EnergiedatenApiClient, TeamNotFoundError
from .const import CONF_METERS, CONF_TEAM_SLUG, CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_TEAM_SLUG): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
    }
)


class EnergiedatenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for energiedaten.at integration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._token: str = ""
        self._team_slug: str = ""
        self._meters: list[dict[str, Any]] = []

    def _create_client(self, token: str, team_slug: str) -> EnergiedatenApiClient:
        """Create an API client using HA's shared aiohttp session."""
        session = async_get_clientsession(self.hass)
        return EnergiedatenApiClient(session, token, team_slug)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Collect API token and team slug."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._token = user_input[CONF_TOKEN]
            self._team_slug = user_input[CONF_TEAM_SLUG]
            client = self._create_client(self._token, self._team_slug)

            try:
                await client.async_validate()
                self._meters = await client.async_get_meters()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except TeamNotFoundError:
                errors["base"] = "team_not_found"
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
            selected_meters = [
                {
                    "uuid": m["id"],
                    "metering_point": m["metering_point"],
                    "energy_direction": m["energy_direction"],
                    "label": m.get("label"),
                }
                for m in self._meters
                if m["id"] in selected_uuids
            ]
            return self.async_create_entry(
                title=self._team_slug,
                data={
                    CONF_TOKEN: self._token,
                    CONF_TEAM_SLUG: self._team_slug,
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
        all_uuids = [m["id"] for m in connected]

        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METERS, default=all_uuids): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    @staticmethod
    def _meter_display_name(meter: dict[str, Any]) -> str:
        """Format meter display name for the selection list."""
        label = meter.get("label") or meter["metering_point"][-6:]
        direction = (
            "Consumption" if meter["energy_direction"] == "consumption" else "Feed-in"
        )
        return f"{label} ({direction})"

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication trigger."""
        self._team_slug = entry_data[CONF_TEAM_SLUG]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step: Re-enter API token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = self._create_client(user_input[CONF_TOKEN], self._team_slug)
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
