"""Sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN

from .engine import PANELS, TsunEngine
from .entity import TsunEntity

W, KWH, V, A = UnitOfPower.WATT, UnitOfEnergy.KILO_WATT_HOUR, UnitOfElectricPotential.VOLT, UnitOfElectricCurrent.AMPERE
M, TI = SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING
DIAG = EntityCategory.DIAGNOSTIC


@dataclass(frozen=True, kw_only=True)
class TsunSensorDescription(SensorEntityDescription):
    value: Callable[[TsunEngine], Any] | None = None
    attrs: Callable[[TsunEngine], dict[str, Any]] | None = None
    forecast: bool = False
    panel: int | None = None


def _d(key: str, unit=None, dc=None, sc=None, prec=None, **kw) -> TsunSensorDescription:
    return TsunSensorDescription(
        key=key, native_unit_of_measurement=unit, device_class=dc, state_class=sc, suggested_display_precision=prec, **kw
    )


def _panels(e: TsunEngine) -> dict[str, Any]:
    """Live per-panel data for the roof card (not recorded)."""
    v = e.values
    exp = v.get("panel_expected") or [None] * PANELS
    out = []
    for n in range(1, PANELS + 1):
        state = "off" if e.standby else "zero" if v.get(f"panel{n}_zero") else "under" if v.get(f"panel{n}_under") else "ok"
        out.append({
            "n": n,
            "power": v.get(f"panel{n}_power"),
            "energy": v.get(f"panel{n}_energy"),
            "voltage": None if e.standby else v.get(f"panel{n}_voltage"),
            "current": None if e.standby else v.get(f"panel{n}_current"),
            "expected": exp[n - 1] if not e.standby else None,
            "state": state,
        })
    last = e.comm["lastOk"]
    return {
        "panels": out,
        "standby": e.standby,
        "unreachable": e.unreachable,
        "rated_power": v.get("rated_power"),
        "last_read": e._time(last["t"]) if last else None,
    }


def _status_attrs(e: TsunEngine) -> dict[str, Any]:
    o = e.comm["outage"]
    return {
        "code": e.values.get("status_code"),
        "alarm_code": e.values.get("alarm_code"),
        "standby": e.standby,
        "outage_cause": e._t(f"diag.cause.{o['cause']}") if o else None,
        "last_error": (e.comm["lastError"] or {}).get("text"),
    }


BASE: list[TsunSensorDescription] = [
    _d("power", W, SensorDeviceClass.POWER, M, 0, attrs=_panels),
    _d("energy_today", KWH, SensorDeviceClass.ENERGY, TI, 2),
    _d("energy_total", KWH, SensorDeviceClass.ENERGY, TI, 1),
    _d("status", attrs=_status_attrs),
    _d("voltage", V, SensorDeviceClass.VOLTAGE, M, 1),
    _d("current", A, SensorDeviceClass.CURRENT, M, 2, entity_registry_enabled_default=False),
    _d("frequency", UnitOfFrequency.HERTZ, SensorDeviceClass.FREQUENCY, M, 2),
    _d("temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, M, 0),
    _d("dc_power", W, SensorDeviceClass.POWER, M, 0),
    _d("efficiency", PERCENTAGE, None, M, 1, entity_category=DIAG),
    _d("peak", W, SensorDeviceClass.POWER, None, 0,
       attrs=lambda e: {"at": e._time(e.day_stats["peakAt"])},
       value=lambda e: round(e.day_stats["peak"]) if e.day_stats["peak"] else 0),
    _d("latency", UnitOfTime.MILLISECONDS, SensorDeviceClass.DURATION, M, 0, entity_category=DIAG),
    _d("comm_rate", PERCENTAGE, None, M, 1, entity_category=DIAG),
    _d("forecast_today", KWH, SensorDeviceClass.ENERGY, None, 2, forecast=True),
    _d("forecast_remaining", KWH, SensorDeviceClass.ENERGY, None, 2, forecast=True),
    _d("forecast_tomorrow", KWH, SensorDeviceClass.ENERGY, None, 2, forecast=True),
    _d("forecast_power", W, SensorDeviceClass.POWER, None, 0, forecast=True),
    _d("forecast_mape", PERCENTAGE, None, None, 1, forecast=True, entity_category=DIAG,
       attrs=lambda e: {"days": e.forecast.accuracy["days"], "bias": None if e.forecast.accuracy["bias"] is None else round(e.forecast.accuracy["bias"], 1)}),
]


def _panel_descriptions() -> list[TsunSensorDescription]:
    out = []
    for n in range(1, PANELS + 1):
        out += [
            _d(f"panel{n}_power", W, SensorDeviceClass.POWER, M, 0, translation_key="panel_power", panel=n),
            _d(f"panel{n}_energy", KWH, SensorDeviceClass.ENERGY, TI, 2, translation_key="panel_energy", panel=n),
            _d(f"panel{n}_voltage", V, SensorDeviceClass.VOLTAGE, M, 1, translation_key="panel_voltage", panel=n,
               entity_registry_enabled_default=False),
            _d(f"panel{n}_current", A, SensorDeviceClass.CURRENT, M, 2, translation_key="panel_current", panel=n,
               entity_registry_enabled_default=False),
            _d(f"panel{n}_energy_total", KWH, SensorDeviceClass.ENERGY, TI, 1, translation_key="panel_energy_total", panel=n,
               entity_registry_enabled_default=False),
        ]
    return out


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [TsunSensor(coordinator, d) for d in BASE + _panel_descriptions()]
    entities.append(TsunDiagSensor(coordinator))
    async_add_entities(entities)


class TsunSensor(TsunEntity, SensorEntity):
    entity_description: TsunSensorDescription
    _unrecorded_attributes = frozenset({"panels", "at", "standby", "unreachable", "rated_power", "last_read", "last_error", "outage_cause"})

    def __init__(self, coordinator, description: TsunSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.translation_key:
            self._attr_translation_key = description.translation_key
        if description.panel:
            self._attr_translation_placeholders = {"n": str(description.panel)}

    @property
    def available(self) -> bool:
        e = self.coordinator.engine
        if self.entity_description.forecast:
            return bool(e and e.forecast_enabled())
        return e is not None

    @property
    def native_value(self) -> Any:
        e = self.coordinator.engine
        if e is None:
            return None
        if self.entity_description.value:
            return self.entity_description.value(e)
        return e.values.get(self.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        e = self.coordinator.engine
        if e is None or not self.entity_description.attrs:
            return None
        attrs = self.entity_description.attrs(e)
        if self.key == "power" and "panels" in attrs:
            ids = self._panel_entity_ids()
            for p in attrs["panels"]:
                p["entity_id"] = ids.get(p["n"])
        return attrs

    def _panel_entity_ids(self) -> dict[int, str | None]:
        ids = getattr(self, "_panel_ids", None)
        if (ids is None or None in ids.values()) and self.hass:
            reg = er.async_get(self.hass)
            uid = self.coordinator.entry.unique_id or self.coordinator.entry.entry_id
            ids = self._panel_ids = {n: reg.async_get_entity_id("sensor", DOMAIN, f"{uid}_panel{n}_power") for n in range(1, PANELS + 1)}
        return ids or {}


class TsunDiagSensor(TsunEntity, SensorEntity):
    """Last diagnostic message (info, warn, error) with the recent journal as attribute."""

    _attr_entity_category = DIAG
    _unrecorded_attributes = frozenset({"journal", "level", "category", "time"})

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last_diag")

    @property
    def native_value(self) -> str | None:
        log = self.coordinator.diag_log
        return log[-1]["message"][:255] if log else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        log = list(self.coordinator.diag_log)
        last = log[-1] if log else {}
        return {
            "level": last.get("level"),
            "category": last.get("category"),
            "time": last.get("t"),
            "journal": [f"{x['t'][11:16]} [{x['level']}] {x['message']}" for x in reversed(log[-30:])],
        }
