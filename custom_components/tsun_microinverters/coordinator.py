"""Coordinator: runs the engine on an adaptive schedule, persists its state, relays its outputs."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_LOGGER_SN, CONF_NOTIFY_SERVICE, DEFAULT_NAME, DIAG_BUFFER, DOMAIN, EVENT, STORAGE_VERSION
from .engine import DEFAULT_OPTIONS, TsunEngine

_LOGGER = logging.getLogger(__package__)
LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class TsunCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(seconds=5))
        self.entry = entry
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.diag_log: deque[dict[str, Any]] = deque(maxlen=DIAG_BUFFER)
        self.engine: TsunEngine | None = None
        self._forecast_unsub = None
        self.device_id: str | None = None

    @property
    def device_name(self) -> str:
        return self.entry.data.get(CONF_NAME) or DEFAULT_NAME

    async def async_setup(self) -> None:
        data = await self.store.async_load() or {}
        lang = (self.hass.config.language or "en").split("-")[0]
        self.engine = TsunEngine(
            name=self.device_name,
            host=self.entry.data[CONF_HOST],
            logger_sn=int(self.entry.data[CONF_LOGGER_SN]),
            options={**DEFAULT_OPTIONS, **self.entry.options},
            store=data,
            tz=str(self.hass.config.time_zone),
            lang="fr" if lang == "fr" else "en",
            location=(self.hass.config.latitude, self.hass.config.longitude),
            diag=self._diag,
            notify=self._notify,
            fire=self._fire,
            save=self._schedule_save,
            now=lambda: dt_util.utcnow().timestamp(),
        )
        if self.engine.forecast_enabled():
            self._schedule_forecast(3)

    # ---------- Engine callbacks ----------

    @callback
    def _diag(self, level: str, category: str, message: str) -> None:
        _LOGGER.log(LOG_LEVELS.get(level, logging.INFO), "[%s] %s", category, message)
        if level == "debug":
            return
        entry = {"t": dt_util.now().isoformat(timespec="seconds"), "level": level, "category": category, "message": message}
        self.diag_log.append(entry)
        if level in ("warn", "error") and "logbook" in self.hass.config.components:
            from homeassistant.components.logbook import async_log_entry  # noqa: PLC0415

            async_log_entry(self.hass, self.device_name, message, DOMAIN)

    async def _notify(self, kind: str, message: str) -> None:
        persistent_notification.async_create(self.hass, message, title=self.device_name, notification_id=f"{DOMAIN}_{kind}")
        target = (self.entry.options.get(CONF_NOTIFY_SERVICE) or "").strip()
        if not target:
            return
        domain, _, service = target.partition(".")
        if not service:
            domain, service = "notify", target
        try:
            await self.hass.services.async_call(domain, service, {"title": self.device_name, "message": message}, blocking=False)
        except Exception as err:  # noqa: BLE001 - a wrong notify service must not stop the polling
            _LOGGER.warning("Notification via %s failed: %s", target, err)

    @callback
    def _fire(self, event: str, data: dict[str, Any]) -> None:
        self.hass.bus.async_fire(EVENT, {"type": event, "device_id": self.device_id, "entry_id": self.entry.entry_id, **data})

    @callback
    def _schedule_save(self) -> None:
        assert self.engine is not None
        self.store.async_delay_save(lambda: self.engine.store, 5)

    async def async_shutdown_engine(self) -> None:
        if self._forecast_unsub:
            self._forecast_unsub()
            self._forecast_unsub = None
        if self.engine:
            await self.store.async_save(self.engine.snapshot_store())

    # ---------- Polling ----------

    async def _async_update_data(self) -> dict[str, Any]:
        assert self.engine is not None
        nxt = await self.engine.poll()
        self.update_interval = timedelta(seconds=nxt)
        return dict(self.engine.values)

    @callback
    def refresh_values(self) -> None:
        assert self.engine is not None
        self.async_set_updated_data(dict(self.engine.values))

    # ---------- Forecast ----------

    @callback
    def _schedule_forecast(self, seconds: float) -> None:
        if self._forecast_unsub:
            self._forecast_unsub()

        async def _run(_now: datetime) -> None:
            self._forecast_unsub = None
            await self.async_fetch_forecast()

        self._forecast_unsub = async_call_later(self.hass, seconds, _run)

    async def _get_json(self, url: str, params: dict[str, str]) -> dict:
        session = async_get_clientsession(self.hass)
        async with session.get(url, params=params, timeout=20, headers={"User-Agent": "Home Assistant tsun_microinverters"}) as resp:
            try:
                js = await resp.json(content_type=None)
            except ValueError:
                js = None
            if resp.status != 200:
                reason = f": {js.get('reason')}" if isinstance(js, dict) and js.get("reason") else ""
                raise RuntimeError(f"HTTP {resp.status}{reason}")
            if not isinstance(js, dict):
                raise RuntimeError("Invalid JSON answer")
            return js

    async def async_fetch_forecast(self) -> bool:
        assert self.engine is not None
        if not self.engine.forecast_enabled():
            return False
        delay = await self.engine.fetch_forecast(self._get_json)
        self.refresh_values()
        if self.engine.forecast_enabled():
            self._schedule_forecast(delay)
        return self.engine.forecast_error is None

    async def async_options_updated(self) -> None:
        assert self.engine is not None
        was = self.engine.forecast_enabled()
        changed = self.engine.update_options({**DEFAULT_OPTIONS, **self.entry.options})
        if not changed:
            return
        if self.engine.forecast_enabled() and any(k.startswith("forecast_") for k in changed):
            self._schedule_forecast(1)
        elif was and not self.engine.forecast_enabled() and self._forecast_unsub:
            self._forecast_unsub()
            self._forecast_unsub = None
        self._schedule_save()
        self.update_interval = timedelta(seconds=2)
        self.refresh_values()
