"""Config flow for Screen Switch Name Hook."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from . import DOMAIN


class ScreenSwitchNameHookConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single local Screen Switch Name Hook entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Show a confirmation-only setup form."""

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title="Screen Switch Name Hook",
                data={},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )
