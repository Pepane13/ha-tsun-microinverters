"""Config flow, setup, entities, services and diagnostics in a real Home Assistant core."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tsun_microinverters.const import DOMAIN
from custom_components.tsun_microinverters.solarmanv5 import SolarmanV5Client
from fake_logger import FakeLogger, registers

SN = 2712345678


@pytest.fixture
async def fake(monkeypatch, socket_enabled):
    f = FakeLogger(SN, registers())
    await f.start()
    orig = SolarmanV5Client.__init__

    def init(self, host, serial, port=8899, slave_id=1, timeout=8.0):
        orig(self, "127.0.0.1", serial, port=f.port, slave_id=slave_id, timeout=0.5)

    monkeypatch.setattr(SolarmanV5Client, "__init__", init)
    yield f
    await f.stop()


async def test_config_flow(hass: HomeAssistant, fake) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "TSUN", "host": "192.0.2.10", "logger_sn": "12"})
    assert r["errors"] == {"logger_sn": "invalid_sn"}
    fake.mode = "wrong_serial"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "TSUN", "host": "192.0.2.10", "logger_sn": str(SN)})
    assert r["errors"] == {"logger_sn": "wrong_sn"}
    fake.mode = "ok"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "TSUN", "host": "192.0.2.10", "logger_sn": str(SN)})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"]["inverter_serial"] == "TSN1234567890XYZ" and r["result"].unique_id == str(SN)
    await hass.async_block_till_done()


async def test_setup_entities_services(hass: HomeAssistant, fake) -> None:
    hass.config.language = "fr"
    entry = MockConfigEntry(domain=DOMAIN, unique_id=str(SN), title="TSUN",
                            data={"name": "TSUN", "host": "192.0.2.10", "logger_sn": str(SN), "inverter_serial": "X"},
                            options={"forecast_enabled": False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    reg = er.async_get(hass)
    power = reg.async_get_entity_id("sensor", DOMAIN, f"{SN}_power")
    st = hass.states.get(power)
    assert st and float(st.state) == 1500
    assert st.attributes["panels"][0]["entity_id"] == reg.async_get_entity_id("sensor", DOMAIN, f"{SN}_panel1_power")
    assert hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, f"{SN}_energy_total")).state == "1234.56"
    assert hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, f"{SN}_status")).state == "Normal"
    assert hass.states.get(reg.async_get_entity_id("binary_sensor", DOMAIN, f"{SN}_producing")).state == "on"
    fc = reg.async_get_entity_id("sensor", DOMAIN, f"{SN}_forecast_today")
    assert hass.states.get(fc).state == "unavailable"
    print(sorted(s.entity_id for s in hass.states.async_all() if "tsun" in s.entity_id)[:60])

    from homeassistant.exceptions import ServiceValidationError
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "forecast_window", {"start": "10:00", "end": "12:00"}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "reset_baseline", {}, blocking=True)

    # options flow with sections
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "reading": {"poll_interval": 20, "temp_offset": 40, "comm_alert_delay": 10},
        "panels": {"underperf_mode": "simple", "underperf_threshold": 15, "underperf_delay": 30, "underperf_min_power": 80, "daily_threshold": 10},
        "grid": {"voltage_max": 253, "voltage_min": 207, "temp_alert": 90},
        "forecast": {"forecast_enabled": False, "forecast_tilt": 30, "forecast_azimuth": 180, "forecast_peak": 0, "forecast_model": "best_match"},
        "notifications": {"notify_comm": True, "notify_panel": True, "notify_alarm": True, "notify_grid": False, "notify_service": ""},
    })
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.engine.options["underperf_mode"] == "simple"
    assert entry.runtime_data.engine.options["poll_interval"] == 20

    from custom_components.tsun_microinverters.diagnostics import async_get_config_entry_diagnostics
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["logger_sn"] == "**REDACTED**" and diag["status"]["reads_today"]["ok"] >= 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
