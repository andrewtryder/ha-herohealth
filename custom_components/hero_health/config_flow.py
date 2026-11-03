"""UI configuration, reauth, and reconfigure flows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api.exceptions import HeroAuthenticationError, HeroConnectionError, HeroError
from .api.models import HeroTokens
from .const import (
    CONF_ACCOUNT_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .session import HeroSession, async_store_entry_tokens


class HeroHealthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HeroHealthOptionsFlow:
        return HeroHealthOptionsFlow()

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._accounts: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_schema())
        return await self._authenticate(user_input)

    async def _authenticate(self, user_input: dict[str, Any]) -> FlowResult:
        session = HeroSession(
            self.hass,
            "config-flow",
            user_input[CONF_EMAIL],
            user_input[CONF_PASSWORD],
            None,
            persist=False,
        )
        is_reconfigure = self.source == config_entries.SOURCE_RECONFIGURE
        step_id = "reconfigure" if is_reconfigure else "user"
        try:
            client = await session.async_initialize()
            accounts = await client.caregiver_patient_list()
            raw_accounts: list[Any] = []
            if isinstance(accounts, list):
                raw_accounts = accounts
            elif isinstance(accounts, dict) and isinstance(
                accounts.get("results"), list
            ):
                raw_accounts = accounts["results"]
            self._accounts = [
                a
                for a in raw_accounts
                if isinstance(a, dict)
                and a.get("account_id") is not None
                and str(a.get("account_id")).strip()
            ]
            if not self._accounts:
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=_schema(),
                    errors={"base": "invalid_response"},
                )
            self._data = user_input
            if len(self._accounts) == 1:
                return await self._finish(self._accounts[0])
            return self.async_show_form(
                step_id="account",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_ACCOUNT_ID): vol.In(
                            {
                                str(a["account_id"]): _account_label(a)
                                for a in self._accounts
                            }
                        )
                    }
                ),
            )
        except HeroAuthenticationError:
            return self.async_show_form(
                step_id=step_id, data_schema=_schema(), errors={"base": "invalid_auth"}
            )
        except HeroConnectionError:
            return self.async_show_form(
                step_id=step_id,
                data_schema=_schema(),
                errors={"base": "cannot_connect"},
            )
        except HeroError:
            return self.async_show_form(
                step_id=step_id,
                data_schema=_schema(),
                errors={"base": "invalid_response"},
            )
        finally:
            await session.async_close()

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="account")
        account = next(
            a
            for a in self._accounts
            if str(a.get("account_id")) == user_input[CONF_ACCOUNT_ID]
        )
        return await self._finish(account)

    async def _finish(self, account: dict[str, Any]) -> FlowResult:
        account_id = str(account["account_id"])
        data = {**self._data, CONF_ACCOUNT_ID: account_id}
        reauth_entry = (
            self._get_reauth_entry()
            if self.source == config_entries.SOURCE_REAUTH
            else None
        )
        reconfigure_entry = (
            self._get_reconfigure_entry()
            if self.source == config_entries.SOURCE_RECONFIGURE
            else None
        )
        if reauth_entry:
            step_id, schema = "reauth_confirm", _password_schema()
        elif reconfigure_entry:
            step_id, schema = "reconfigure", _schema()
        else:
            step_id, schema = "user", _schema()
        validation = HeroSession(
            self.hass,
            "config-flow-validation",
            data[CONF_EMAIL],
            data[CONF_PASSWORD],
            account_id,
            persist=False,
        )
        try:
            client = await validation.async_initialize()
            await client.user_status(account_id)
            tokens = validation.tokens
        except HeroAuthenticationError:
            return self._show_error(step_id, schema, "invalid_auth")
        except HeroConnectionError:
            return self._show_error(step_id, schema, "cannot_connect")
        except HeroError:
            return self._show_error(step_id, schema, "invalid_response")
        finally:
            await validation.async_close()

        if reauth_entry:
            await self.async_set_unique_id(account_id)
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            await self._async_hand_off_tokens(reauth_entry, tokens, data)
            return self.async_update_reload_and_abort(
                reauth_entry, data_updates=data, unique_id=account_id
            )

        if reconfigure_entry:
            if account_id != reconfigure_entry.unique_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
            await self._async_hand_off_tokens(reconfigure_entry, tokens, data)
            return self.async_update_reload_and_abort(
                reconfigure_entry, data_updates=data, unique_id=account_id
            )

        await self.async_set_unique_id(account_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Hero Health", data=data)

    def _show_error(self, step_id: str, schema: vol.Schema, error: str) -> FlowResult:
        placeholders = None
        if step_id == "reauth_confirm":
            placeholders = {"email": self._get_reauth_entry().data[CONF_EMAIL]}
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors={"base": error},
            description_placeholders=placeholders,
        )

    async def _async_hand_off_tokens(
        self, entry: ConfigEntry, tokens: HeroTokens | None, data: dict[str, str]
    ) -> None:
        """Replace the entry's stale persisted tokens with the validated ones.

        Without this, the reloaded entry would retry the rejected refresh token
        and never pick up the new credentials. Reusing the fresh tokens also
        avoids a second password login (Hero's IdP rate limits aggressively).
        """
        if tokens is None:
            return
        await async_store_entry_tokens(
            self.hass,
            entry.entry_id,
            tokens,
            data[CONF_EMAIL],
            data[CONF_ACCOUNT_ID],
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask only for the password; the email and account stay bound to the entry."""
        entry = self._get_reauth_entry()
        account_id = entry.data.get(CONF_ACCOUNT_ID) or entry.unique_id
        if not account_id:
            # Legacy entries without a bound account use the full flow.
            return await self.async_step_user()
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=_password_schema(),
                description_placeholders={"email": entry.data[CONF_EMAIL]},
            )
        self._data = {
            CONF_EMAIL: entry.data[CONF_EMAIL],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
        }
        return await self._finish({"account_id": account_id})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reconfigure", data_schema=_schema())
        return await self._authenticate(user_input)


def _password_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _schema():
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _account_label(account: dict[str, Any]) -> str:
    return str(account.get("device_nickname") or "Hero account")


class HeroHealthOptionsFlow(config_entries.OptionsFlowWithReload):
    """Configure the deliberately conservative background refresh cadence."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=MAX_SCAN_INTERVAL_MINUTES,
                            step=1,
                            unit_of_measurement="min",
                        )
                    )
                }
            ),
        )
