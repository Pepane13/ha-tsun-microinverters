"""Buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TsunEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([RefreshForecastButton(c), ResetBaselineButton(c)])


class RefreshForecastButton(TsunEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh_forecast")

    @property
    def available(self) -> bool:
        e = self.coordinator.engine
        return bool(e and e.forecast_enabled())

    async def async_press(self) -> None:
        if not await self.coordinator.async_fetch_forecast():
            err = self.coordinator.engine.forecast_error
            raise HomeAssistantError(err["message"] if err else "Forecast unavailable")


class ResetBaselineButton(TsunEntity, ButtonEntity):
    """Forgets the learned panel profile (relearned in a few sunny days). Disabled by default."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "reset_baseline")

    async def async_press(self) -> None:
        self.coordinator.engine.reset_baseline()
        self.coordinator.refresh_values()
