"""Binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine import PANELS, PRODUCING_W, TsunEngine
from .entity import TsunEntity

P = BinarySensorDeviceClass.PROBLEM


@dataclass(frozen=True, kw_only=True)
class TsunBinaryDescription(BinarySensorEntityDescription):
    is_on: Callable[[TsunEngine], bool]
    attrs: Callable[[TsunEngine], dict[str, Any]] | None = None
    panel: int | None = None


def _alarm_attrs(e: TsunEngine) -> dict[str, Any]:
    w = e.alarm_watch
    return {"code": e.values.get("alarm_code"), "confirmed": bool(w and w["notified"]), "status": w["status"] if w else None}


def _comm_attrs(e: TsunEngine) -> dict[str, Any]:
    o = e.comm["outage"]
    return {
        "since": e._time(o["start"]) if o else None,
        "cause": e._t(f"diag.cause.{o['cause']}") if o else None,
        "failures": e.failures,
    }


DESCRIPTIONS: list[TsunBinaryDescription] = [
    TsunBinaryDescription(key="producing", device_class=BinarySensorDeviceClass.RUNNING,
                          is_on=lambda e: not e.standby and (e.values.get("power") or 0) >= PRODUCING_W),
    TsunBinaryDescription(key="alarm", device_class=P, is_on=lambda e: bool(e.values.get("alarm")), attrs=_alarm_attrs),
    TsunBinaryDescription(key="communication", device_class=P, is_on=lambda e: e.unreachable, attrs=_comm_attrs),
    TsunBinaryDescription(key="derating", device_class=BinarySensorDeviceClass.HEAT, is_on=lambda e: e.derating is not None),
    TsunBinaryDescription(key="any_underperforming", device_class=P, is_on=lambda e: e.is_underperforming(),
                          attrs=lambda e: {"panels": [i + 1 for i in range(PANELS) if e.panel_watch[i]["flagged"] or e.zero_watch[i]["flagged"]]}),
]
for _n in range(1, PANELS + 1):
    DESCRIPTIONS += [
        TsunBinaryDescription(key=f"panel{_n}_underperforming", translation_key="panel_underperforming", device_class=P, panel=_n,
                              is_on=lambda e, i=_n - 1: e.panel_watch[i]["flagged"]),
        TsunBinaryDescription(key=f"panel{_n}_not_producing", translation_key="panel_not_producing", device_class=P, panel=_n,
                              is_on=lambda e, i=_n - 1: e.zero_watch[i]["flagged"]),
    ]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(TsunBinary(entry.runtime_data, d) for d in DESCRIPTIONS)


class TsunBinary(TsunEntity, BinarySensorEntity):
    entity_description: TsunBinaryDescription

    def __init__(self, coordinator, description: TsunBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.translation_key:
            self._attr_translation_key = description.translation_key
        if description.panel:
            self._attr_translation_placeholders = {"n": str(description.panel)}

    @property
    def is_on(self) -> bool | None:
        e = self.coordinator.engine
        return None if e is None else self.entity_description.is_on(e)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        e = self.coordinator.engine
        if e is None or not self.entity_description.attrs:
            return None
        return self.entity_description.attrs(e)
