"""Config flow: logger address and serial number, checked by reading the inverter identity."""

from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector as sel

from .const import CONF_INVERTER_SN, CONF_LOGGER_SN, CONF_NOTIFY_SERVICE, DEFAULT_NAME, DOMAIN
from .engine import DEFAULT_OPTIONS
from .forecast import MODELS
from .mx3000d import read_identity
from .solarmanv5 import SolarmanV5Client, SolarmanV5Error


async def _probe(host: str, sn: int) -> str:
    client = SolarmanV5Client(host, sn, timeout=8.0)
    try:
        await client.connect()
        return (await read_identity(client))["inverter_serial"]
    finally:
        await client.close()


def _validate(data: dict[str, Any]) -> dict[str, str]:
    errors = {}
    try:
        ipaddress.IPv4Address(str(data[CONF_HOST]).strip())
    except ValueError:
        errors[CONF_HOST] = "invalid_ip"
    sn = str(data[CONF_LOGGER_SN]).strip()
    if not sn.isdigit() or not 6 <= len(sn) <= 10:
        errors[CONF_LOGGER_SN] = "invalid_sn"
    return errors


class TsunConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check(self, data: dict[str, Any]) -> tuple[dict[str, str], str | None]:
        errors = _validate(data)
        if errors:
            return errors, None
        try:
            return {}, await _probe(str(data[CONF_HOST]).strip(), int(str(data[CONF_LOGGER_SN]).strip()))
        except SolarmanV5Error as err:
            if err.code == "ESERIAL":
                return {CONF_LOGGER_SN: "wrong_sn"}, None
            if err.code in ("EV5", "ETIMEDOUT") and err.phase != "connect":
                return {"base": "inverter_silent"}, None  # logger answers, inverter asleep (night)
            return {"base": "cannot_connect"}, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, inverter_sn = await self._check(user_input)
            if errors.get("base") in ("inverter_silent", "cannot_connect") and user_input.get("skip_check"):
                errors, inverter_sn = {}, None
            if not errors:
                sn = str(user_input[CONF_LOGGER_SN]).strip()
                await self.async_set_unique_id(sn)
                self._abort_if_unique_id_configured(updates={CONF_HOST: str(user_input[CONF_HOST]).strip()})
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                    data={
                        CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                        CONF_HOST: str(user_input[CONF_HOST]).strip(),
                        CONF_LOGGER_SN: sn,
                        CONF_INVERTER_SN: inverter_sn,
                    },
                    options=dict(DEFAULT_OPTIONS),
                )
        d = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Required(CONF_HOST, default=d.get(CONF_HOST, "")): str,
                vol.Required(CONF_LOGGER_SN, default=d.get(CONF_LOGGER_SN, "")): str,
                vol.Optional("skip_check", default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_HOST: str(user_input[CONF_HOST]).strip()}
            errors, inverter_sn = await self._check(data)
            if errors.get("base") in ("inverter_silent", "cannot_connect"):
                errors, inverter_sn = {}, entry.data.get(CONF_INVERTER_SN)
            if not errors:
                return self.async_update_reload_and_abort(entry, data={**data, CONF_INVERTER_SN: inverter_sn or entry.data.get(CONF_INVERTER_SN)})
        schema = vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str})
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TsunOptionsFlow()


def _num(lo: float, hi: float, step: float = 1, unit: str | None = None) -> sel.NumberSelector:
    cfg = sel.NumberSelectorConfig(min=lo, max=hi, step=step, mode=sel.NumberSelectorMode.BOX)
    if unit:
        cfg["unit_of_measurement"] = unit
    return sel.NumberSelector(cfg)


SECTIONS: dict[str, dict[str, Any]] = {
    "reading": {
        "poll_interval": _num(10, 600, 1, "s"),
        "temp_offset": _num(0, 100),
        "comm_alert_delay": _num(1, 180, 1, "min"),
    },
    "panels": {
        "underperf_mode": sel.SelectSelector(sel.SelectSelectorConfig(options=["adaptive", "simple"], translation_key="underperf_mode")),
        "underperf_threshold": _num(5, 60, 1, "%"),
        "underperf_delay": _num(1, 240, 1, "min"),
        "underperf_min_power": _num(10, 400, 1, "W"),
        "daily_threshold": _num(3, 50, 1, "%"),
    },
    "grid": {
        "voltage_max": _num(230, 270, 1, "V"),
        "voltage_min": _num(180, 230, 1, "V"),
        "temp_alert": _num(50, 110, 1, "°C"),
    },
    "forecast": {
        "forecast_enabled": sel.BooleanSelector(),
        "forecast_tilt": _num(0, 90, 1, "°"),
        "forecast_azimuth": _num(0, 360, 1, "°"),
        "forecast_peak": _num(0, 20000, 10, "Wc"),
        "forecast_model": sel.SelectSelector(sel.SelectSelectorConfig(options=MODELS, translation_key="forecast_model")),
    },
    "notifications": {
        "notify_comm": sel.BooleanSelector(),
        "notify_panel": sel.BooleanSelector(),
        "notify_alarm": sel.BooleanSelector(),
        "notify_grid": sel.BooleanSelector(),
        CONF_NOTIFY_SERVICE: sel.TextSelector(),
    },
}


class TsunOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            flat: dict[str, Any] = {}
            for name in SECTIONS:
                flat.update(user_input.get(name) or {})
            for k, v in flat.items():
                if isinstance(v, float) and v.is_integer():
                    flat[k] = int(v)
            if CONF_NOTIFY_SERVICE in flat:
                flat[CONF_NOTIFY_SERVICE] = str(flat[CONF_NOTIFY_SERVICE] or "").strip()
            return self.async_create_entry(data={**DEFAULT_OPTIONS, **self.config_entry.options, **flat})
        cur = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = {}
        for name, fields in SECTIONS.items():
            inner = {}
            for key, selector in fields.items():
                default = cur.get(key, "")
                inner[vol.Optional(key, default=default if default is not None else "")] = selector
            schema[vol.Required(name)] = section(vol.Schema(inner), {"collapsed": name not in ("panels",)})
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
