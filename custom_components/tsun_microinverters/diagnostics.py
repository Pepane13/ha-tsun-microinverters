"""Diagnostics download (logger and inverter serial numbers masked)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_INVERTER_SN, CONF_LOGGER_SN

REDACT = {CONF_LOGGER_SN, CONF_INVERTER_SN, "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    c = entry.runtime_data
    e = c.engine
    return {
        "entry": async_redact_data({"data": dict(entry.data), "options": dict(entry.options)}, REDACT),
        "status": e.diag_status() if e else None,
        "values": dict(e.values) if e else None,
        "baseline": e.baseline_summary() if e else None,
        "forecast": {**e.forecast_status(), "calibration": e.forecast.summary()} if e and e.forecast_enabled() else None,
        "journal": list(c.diag_log),
    }
