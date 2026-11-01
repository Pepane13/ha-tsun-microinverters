"""Base entity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INVERTER_SN, DOMAIN
from .coordinator import TsunCoordinator


class TsunEntity(CoordinatorEntity[TsunCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TsunCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self.key = key
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id or entry.entry_id))},
            name=coordinator.device_name,
            manufacturer="TSUN",
            model="MX3000D",
            serial_number=entry.data.get(CONF_INVERTER_SN) or None,
            configuration_url=None,
        )

    @property
    def values(self) -> dict[str, Any]:
        e = self.coordinator.engine
        return e.values if e else {}

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.device_id is None and self.registry_entry:
            self.coordinator.device_id = self.registry_entry.device_id
