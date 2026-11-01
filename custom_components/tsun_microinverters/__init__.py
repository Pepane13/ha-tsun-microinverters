"""TSUN micro-inverters (MX3000D) read locally through the Solarman V5 logger. Read-only."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import TsunCoordinator
from .engine import EngineError

VERSION = "1.0.2"

type TsunConfigEntry = ConfigEntry[TsunCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ENTRY = vol.Optional("config_entry_id")
SCHEMA_WINDOW = vol.Schema(
    {
        ENTRY: cv.string,
        vol.Optional("day", default="today"): vol.In(["today", "tomorrow"]),
        vol.Required("start"): cv.string,
        vol.Required("end"): cv.string,
    }
)
SCHEMA_ENTRY = vol.Schema({ENTRY: cv.string})


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> TsunCoordinator:
    entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
    entry_id = call.data.get("config_entry_id")
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError("No loaded TSUN inverter")
    return entries[0].runtime_data


CARD_URL = f"/{DOMAIN}/tsun-roof-card.js"


async def _register_card(hass: HomeAssistant) -> None:
    """Serves the roof card and loads it in every dashboard (no manual resource needed)."""
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    path = Path(__file__).parent / "frontend" / "tsun-roof-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if "frontend" in hass.config.components and getattr(hass, "http", None) is not None:
        await _register_card(hass)

    async def forecast_window(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        assert c.engine is not None
        try:
            kwh = c.engine.forecast_window(call.data["day"], str(call.data["start"])[:5], str(call.data["end"])[:5])
        except EngineError as err:
            raise ServiceValidationError(str(err)) from err
        return {"energy": kwh, "unit": "kWh", "day": call.data["day"], "start": call.data["start"], "end": call.data["end"]}

    async def refresh_forecast(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        assert c.engine is not None
        if not c.engine.forecast_enabled():
            raise ServiceValidationError(c.engine._t("errors.forecast_disabled"))
        if not await c.async_fetch_forecast():
            err = c.engine.forecast_error
            raise ServiceValidationError(err["message"] if err else c.engine._t("errors.forecast_unavailable"))

    async def reset_baseline(call: ServiceCall) -> None:
        c = _coordinator(hass, call)
        assert c.engine is not None
        c.engine.reset_baseline()
        c.refresh_values()

    hass.services.async_register(DOMAIN, "forecast_window", forecast_window, SCHEMA_WINDOW, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "refresh_forecast", refresh_forecast, SCHEMA_ENTRY)
    async def import_homey(call: ServiceCall) -> ServiceResponse:
        c = _coordinator(hass, call)
        assert c.engine is not None
        report = call.data["report"]
        devices = report.get("devices") if isinstance(report, dict) and "devices" in report else [report]
        if not devices or not isinstance(devices[0], dict):
            raise ServiceValidationError("No device in the Homey report")
        res = c.engine.import_homey(devices[0])
        c.refresh_values()
        return res

    hass.services.async_register(DOMAIN, "reset_baseline", reset_baseline, SCHEMA_ENTRY)
    hass.services.async_register(
        DOMAIN, "import_homey", import_homey, vol.Schema({ENTRY: cv.string, vol.Required("report"): dict}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TsunConfigEntry) -> bool:
    coordinator = TsunCoordinator(hass, entry)
    await coordinator.async_setup()
    # No first refresh blocking the start-up: the inverter is off at night
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} first read")
    return True


async def _options_updated(hass: HomeAssistant, entry: TsunConfigEntry) -> None:
    await entry.runtime_data.async_options_updated()


async def async_unload_entry(hass: HomeAssistant, entry: TsunConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown_engine()
    return ok
