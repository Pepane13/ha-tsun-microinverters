"""Monitoring engine of one TSUN MX3000D (independent of Home Assistant). Port of the Homey device.

The engine keeps the published values in `self.values`, its persistent state in `self.store`
(saved by the coordinator through `save()`), logs through `diag(level, category, message)`, sends
user notifications through `notify(kind, message)` (kind: comm, panel, alarm, grid) and fires
events through `fire(event, data)`. Time comes from `now()` (POSIX seconds).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .forecast import HOUR_S, SolarForecast, build_url, local_parts, next_day, parse_time
from .messages import t as translate
from .mx3000d import RETRY_DELAY_S, read_all
from .panelbaseline import ACCEPT_DAYS, BEST_MIN_DAYS, DAILY_MIN_DAYS, PanelBaseline
from .solarmanv5 import SolarmanV5Client, SolarmanV5Error

PANELS = 6
STANDBY_AFTER_FAILURES = 3
STANDBY_POLL_S = 300
PRODUCING_W = 30
SLOW_RESPONSE_MS = 3000
SLOW_CONNECT_MS = 1000
CONFIRM_FAILURES = 2
RETRY_RATE_WARN = 20
COMM_WINDOW_S = 3600
FREQ_MIN = 49.8
FREQ_MAX = 50.2
EFFICIENCY_MIN = 0.85
EFFICIENCY_MIN_DC_W = 300
RATE_LIMIT_MIN = 30
BASELINE_SAVE_S = 15 * 60
DAILY_MIN_KWH = 1
DAY_STATS_SAVE_S = 5 * 60
UNREACHABLE_POLL_S = 60
LAST_OK_MAX_AGE_S = 30 * 60
STARTUP_GRACE_S = 15 * 60
ALARM_CONFIRM_S = 5 * 60
DERATE_WINDOW_S = 5 * 60
DERATE_POWER_DROP = 0.10
DERATE_VOLTAGE_RISE = 0.08
DERATE_MIN_PANELS = 4
DERATE_MIN_DC_W = 600
DERATE_TEMP_MARGIN = 5
DERATE_PLATEAU_S = 5 * 60
DERATE_RECOVERED = 0.95
DERATE_PLATEAU_SPREAD = 0.10
FORECAST_REFRESH_MIN = 60
FORECAST_RETRY_MIN = 15
FORECAST_SAVE_S = 15 * 60
DEFAULT_RATED_W = 3000
FORECAST_RECALIBRATE_KEYS = ("forecast_tilt", "forecast_azimuth", "forecast_peak")

DEFAULT_OPTIONS: dict[str, Any] = {
    "poll_interval": 30,
    "temp_offset": 40,
    "underperf_mode": "adaptive",
    "underperf_threshold": 15,
    "underperf_delay": 30,
    "underperf_min_power": 80,
    "daily_threshold": 10,
    "comm_alert_delay": 10,
    "voltage_max": 253,
    "voltage_min": 207,
    "temp_alert": 90,
    "forecast_enabled": False,
    "forecast_tilt": 30,
    "forecast_azimuth": 180,
    "forecast_peak": 0,
    "forecast_model": "best_match",
    "notify_comm": True,
    "notify_panel": True,
    "notify_alarm": True,
    "notify_grid": False,
}


def rnd(v: float, d: int = 1) -> float:
    return round(v, d)


def status_label(d: dict[str, Any], tr: Callable[[str], str]) -> str:
    if d["faults"]:
        return f"{tr('status.fault')} 0x{d['faults']:04x}"
    if d["alarms"]:
        return f"{tr('status.alarm')} 0x{d['alarms']:04x}"
    if d["status"] == 1:
        return tr("status.normal")
    return f"{tr('status.code')} {d['status']}"


def classify_error(err: Exception) -> str:
    code = getattr(err, "code", None)
    if code == "ESERIAL":
        return "serial"
    if code == "EV5":
        return "inverter_silent"
    if code in ("ECHECKSUM", "ECRC", "EFRAMETYPE"):
        return "corrupt"
    if code == "ECONNREFUSED":
        return "port_closed"
    if getattr(err, "phase", None) == "connect" and code in ("ETIMEDOUT", "EHOSTUNREACH", "ENETUNREACH", "EHOSTDOWN", "ECONNRESET"):
        return "unreachable"
    if code in ("ECLOSED", "ECONNRESET"):
        return "closed"
    if code == "ETIMEDOUT":
        return "no_response"
    return "other"


def new_day_stats() -> dict[str, Any]:
    return {
        "ok": 0, "fail": 0, "okProducing": 0, "failProducing": 0, "latSum": 0, "latMax": 0,
        "retries": 0, "retryCauses": {}, "isolated": 0, "slowBusy": 0, "outages": 0, "outageMin": 0,
        "peak": 0, "peakAt": None, "vmin": None, "vmax": None, "tmax": None, "wake": None, "sleep": None,
    }


class EngineError(Exception):
    """Error meant for the user (service call), message already translated."""


class TsunEngine:
    def __init__(
        self,
        *,
        name: str,
        host: str,
        logger_sn: int,
        options: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
        tz: str = "Europe/Paris",
        lang: str = "en",
        location: tuple[float, float] | None = None,
        diag: Callable[[str, str, str], None] | None = None,
        notify: Callable[[str, str], Awaitable[None] | None] | None = None,
        fire: Callable[[str, dict[str, Any]], None] | None = None,
        save: Callable[[], None] | None = None,
        now: Callable[[], float] = time.time,
        client_factory: Callable[[str, int], SolarmanV5Client] | None = None,
        retry_delay: float = RETRY_DELAY_S,
    ) -> None:
        self.name = name
        self.host = host
        self.logger_sn = int(logger_sn)
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.store: dict[str, Any] = store if store is not None else {}
        self.tz = tz
        self.lang = lang
        self.location = location
        self._diag_cb = diag
        self._notify_cb = notify
        self._fire_cb = fire or (lambda e, d: None)
        self._save_cb = save or (lambda: None)
        self.now = now
        self.client_factory = client_factory or (lambda host, sn: SolarmanV5Client(host, sn, timeout=8.0))
        self.retry_delay = retry_delay

        self.failures = 0
        self.standby = False
        self.polling = False
        self.last_status: str | None = None
        self.panel_watch = [{"since": None, "flagged": False} for _ in range(PANELS)]
        self.zero_watch = [{"since": None, "flagged": False} for _ in range(PANELS)]
        self.comm: dict[str, Any] = {"window": [], "lastOk": None, "outage": None, "lastError": None, "lastLatency": None}
        self.started_at = now()
        self.derate_candidate: dict[str, Any] | None = None
        self.derating: dict[str, Any] | None = None  # confirmed derating, until the power recovers
        self.history: list[dict[str, Any]] = []
        self.rate_limit: dict[str, float] = {}
        self.alarm_watch: dict[str, Any] | None = None
        self.cycle_retries = 0
        self.last_reading: dict[str, Any] | None = None
        self.values: dict[str, Any] = dict(self.store.get("values") or {})
        self._restore_last_ok()
        self.day_stats = new_day_stats()
        self._last_saved: dict[str, float] = {}
        self._restore_day_stats()
        self.baseline = PanelBaseline(PANELS, self.store.get("panelBaseline"))
        if self.baseline.migrated:
            self._diag("info", "panel", self._t("diag.baseline_migrated"))
            self.store["panelBaseline"] = self.baseline.to_json()
        self.forecast = SolarForecast(self.store.get("forecastModel"))
        self.forecast_meter: dict[str, Any] | None = None
        self.forecast_error: dict[str, Any] | None = None
        self.rated_power = int(self.store.get("ratedPower") or 0) or None
        self._diag("info", "app", f"{self._t('diag.device_started')} {host}")

    # ---------- Helpers ----------

    def opt(self, key: str) -> Any:
        return self.options.get(key, DEFAULT_OPTIONS.get(key))

    def num(self, key: str, default: float) -> float:
        try:
            v = float(self.opt(key))
        except (TypeError, ValueError):
            return default
        return v if v else default

    def _t(self, key: str, **vars_: Any) -> str:
        return translate(self.lang, key, **vars_)

    def _diag(self, level: str, category: str, message: str) -> None:
        if self._diag_cb:
            self._diag_cb(level, category, message)

    def _diag_once(self, key: str, minutes: float, level: str, category: str, message: str) -> bool:
        now = self.now()
        if key in self.rate_limit and now - self.rate_limit[key] < minutes * 60:
            return False
        self.rate_limit[key] = now
        self._diag(level, category, message)
        return True

    async def _notify(self, kind: str, message: str) -> None:
        if not self.opt(f"notify_{kind}") or not self._notify_cb:
            return
        res = self._notify_cb(kind, f"{self.name} : {message}")
        if res is not None:
            await res

    def _fire(self, event: str, data: dict[str, Any] | None = None) -> None:
        self._fire_cb(event, data or {})

    def _today_key(self, ts: float | None = None) -> str:
        return local_parts(self.now() if ts is None else ts, self.tz)["day"]

    def _time(self, ts: float | None) -> str:
        if not ts:
            return "—"
        return datetime.fromtimestamp(ts, ZoneInfo(self.tz)).strftime("%H:%M")

    def _set(self, key: str, value: Any) -> None:
        if value is None:
            return
        self.values[key] = value

    def _get(self, key: str, default: Any = None) -> Any:
        v = self.values.get(key)
        return default if v is None else v

    def success_rate(self, now: float | None = None) -> float | None:
        now = self.now() if now is None else now
        self.comm["window"] = [e for e in self.comm["window"] if now - e["t"] <= COMM_WINDOW_S]
        n = len(self.comm["window"])
        if not n:
            return None
        return rnd(100 * sum(1 for e in self.comm["window"] if e["ok"]) / n, 1)

    def _save(self, what: str, force: bool = False, every: float = 0) -> None:
        now = self.now()
        if not force and now - self._last_saved.get(what, 0) < every:
            return
        self._last_saved[what] = now
        if what == "dayStats":
            self.store["dayStats"] = {"day": self._today_key(), "stats": dict(self.day_stats)}
            if self.comm["lastOk"]:
                self.store["lastOk"] = dict(self.comm["lastOk"])
        elif what == "baseline":
            self.store["panelBaseline"] = self.baseline.to_json()
        elif what == "forecast":
            self.store["forecastModel"] = self.forecast.to_json()
        self.store["values"] = self._persistent_values()
        self._save_cb()

    def _persistent_values(self) -> dict[str, Any]:
        keys = ["energy_total", "energy_today", "status"] + [f"panel{n}_energy" for n in range(1, PANELS + 1)]
        return {k: self.values[k] for k in keys if k in self.values}

    def snapshot_store(self) -> dict[str, Any]:
        """Full state to save on unload."""
        self.store["dayStats"] = {"day": self._today_key(), "stats": dict(self.day_stats)}
        if self.comm["lastOk"]:
            self.store["lastOk"] = dict(self.comm["lastOk"])
        self.store["panelBaseline"] = self.baseline.to_json()
        self.store["forecastModel"] = self.forecast.to_json()
        self.store["values"] = self._persistent_values()
        return self.store

    def _restore_last_ok(self) -> None:
        saved = self.store.get("lastOk")
        if not saved or not isinstance(saved.get("t"), (int, float)) or self.now() - saved["t"] > LAST_OK_MAX_AGE_S:
            return
        self.comm["lastOk"] = {"t": saved["t"], "power": float(saved.get("power") or 0), "day": saved.get("day")}

    def _restore_day_stats(self) -> None:
        saved = self.store.get("dayStats")
        if not saved or saved.get("day") != self._today_key() or not saved.get("stats"):
            return
        self.day_stats = {**new_day_stats(), **saved["stats"]}
        self._diag("info", "day", self._t("diag.stats_restored", ok=self.day_stats["ok"], fail=self.day_stats["fail"]))

    def update_options(self, options: dict[str, Any]) -> list[str]:
        new = {**DEFAULT_OPTIONS, **options}
        changed = [k for k in new if new.get(k) != self.options.get(k)]
        if not changed:
            return []
        old = self.options
        self.options = new
        if any(k.startswith("underperf_") for k in changed):
            for w in self.panel_watch + self.zero_watch:
                w["since"] = None
        self._diag("info", "app", self._t("diag.settings_changed", keys=", ".join(changed)))
        if any(k in FORECAST_RECALIBRATE_KEYS for k in changed) and self.forecast.glob["n"] > 0:
            self.forecast.reset_calibration()
            self._diag("info", "forecast", self._t("diag.forecast_recalibrate"))
            self._save("forecast", True)
        if old.get("forecast_enabled") and not new.get("forecast_enabled"):
            self.forecast_meter = None
            for k in ("forecast_today", "forecast_remaining", "forecast_tomorrow", "forecast_power"):
                self.values.pop(k, None)
        self.failures = 0
        self.standby = False
        return changed

    def is_underperforming(self) -> bool:
        return any(w["flagged"] for w in self.panel_watch) or any(w["flagged"] for w in self.zero_watch)

    # ---------- Day change and daily counters ----------

    async def handle_day_change(self) -> None:
        today = self._today_key()
        last_day = self.store.get("day")
        if last_day == today:
            return
        self.store["day"] = today
        if not last_day:
            self._save_cb()
            return
        self._log_daily_summary(last_day)
        await self._panel_verdict(last_day)
        self._check_daily_shares(last_day)
        self._forecast_day_check(last_day)
        stale: dict[str, float] = {}
        for key, val in self._today_keys().items():
            v = self._get(val, 0)
            if v > 0:
                stale[key] = v
            self.values[val] = 0.0
        self.store["staleToday"] = stale
        self.day_stats = new_day_stats()
        self._save("dayStats", True)
        self._diag("info", "day", self._t("diag.day_reset", day=today))

    @staticmethod
    def _today_keys() -> dict[str, str]:
        return {"ac": "energy_today", **{f"pv{n}": f"panel{n}_energy" for n in range(1, PANELS + 1)}}

    def _log_daily_summary(self, day: str) -> None:
        s = self.day_stats
        panels = " / ".join(str(rnd(self._get(f"panel{n}_energy", 0), 2)) for n in range(1, PANELS + 1))
        prod = s["okProducing"] + s["failProducing"]
        dash = "—"
        self._diag("info", "day", self._t(
            "diag.summary",
            day=day, energy=rnd(self._get("energy_today", 0), 2), panels=panels, peak=round(s["peak"]),
            peakTime=self._time(s["peakAt"]), wake=self._time(s["wake"]), sleep=self._time(s["sleep"]),
            vmin=dash if s["vmin"] is None else s["vmin"], vmax=dash if s["vmax"] is None else s["vmax"],
            tmax=dash if s["tmax"] is None else s["tmax"], ok=s["ok"], fail=s["fail"],
            rate=rnd(100 * s["okProducing"] / prod, 1) if prod else dash,
            lavg=round(s["latSum"] / s["ok"]) if s["ok"] else dash, lmax=s["latMax"] or dash,
            retries=s["retries"], crc=s["retryCauses"].get("corrupt", 0),
            silent=s["retryCauses"].get("inverter_silent", 0) + s["retryCauses"].get("no_response", 0),
            isolated=s["isolated"], slow=s["slowBusy"], outages=s["outages"], outageMin=rnd(s["outageMin"], 1),
        ))

    def _fresh_today(self, stale: dict[str, float], key: str, raw: float) -> float:
        s = stale.get(key)
        if isinstance(s, (int, float)) and s > 0:
            if raw > 0 and raw >= s:
                return 0.0
            del stale[key]
            if key == "ac":
                self._diag("info", "day", self._t("diag.counter_reset"))
        return raw

    def _today_value(self, vkey: str, stale: dict[str, float], key: str, raw: float) -> float:
        fresh = self._fresh_today(stale, key, raw)
        prev = self.values.get(vkey)
        if not isinstance(prev, (int, float)) or fresh >= prev:
            return fresh
        if key == "ac" and prev - fresh >= 0.05:
            self._diag_once("counter_back", 60, "info", "day", self._t("diag.counter_back", raw=rnd(fresh, 2), kept=rnd(prev, 2)))
        return prev

    # ---------- Polling cycle ----------

    def poll_interval(self) -> float:
        return max(10.0, self.num("poll_interval", 30))

    async def poll(self) -> float:
        """One read cycle. Returns the delay before the next one, in seconds."""
        if self.polling:
            return self.poll_interval()
        self.polling = True
        interval = self.poll_interval()
        nxt = interval
        client = self.client_factory(self.host, self.logger_sn)
        d = None
        t0 = time.monotonic()
        connect_ms = None
        try:
            await self.handle_day_change()
            await client.connect()
            connect_ms = round((time.monotonic() - t0) * 1000)
            self.cycle_retries = 0
            d = await read_all(client, int(self.opt("temp_offset") or 0), self._on_retry, self.retry_delay)
        except SolarmanV5Error as err:
            await client.close()
            await self._on_read_failure(err)
            if self.failures >= STANDBY_AFTER_FAILURES:
                o = self.comm["outage"]
                night = not o or o["powerBefore"] < PRODUCING_W
                nxt = max(interval, STANDBY_POLL_S if night else UNREACHABLE_POLL_S)
        finally:
            await client.close()
            self.polling = False
        if d:
            try:
                await self._on_read_success(d, round((time.monotonic() - t0) * 1000), connect_ms or 0)
            except Exception as err:  # noqa: BLE001 - keep polling whatever happens
                self._diag("error", "app", f"{self._t('diag.update_failed')} {err}")
        return nxt

    async def _on_read_success(self, d: dict[str, Any], latency: int, connect_ms: int) -> None:
        now = self.now()
        s = self.day_stats
        self.comm["lastLatency"] = latency
        self.comm["window"].append({"t": now, "ok": True, "retried": self.cycle_retries > 0})
        s["ok"] += 1
        s["latSum"] += latency
        s["latMax"] = max(s["latMax"], latency)
        if d["power"] >= PRODUCING_W:
            s["okProducing"] += 1

        o = self.comm["outage"]
        if o:
            minutes = rnd((now - o["start"]) / 60, 1)
            if o["powerBefore"] >= PRODUCING_W:
                s["outages"] += 1
                s["outageMin"] += minutes
                cause = self._t(f"diag.cause.{o['cause']}")
                if o["alerted"]:
                    msg = self._t("diag.comm_restored", minutes=minutes, count=o["count"], cause=cause)
                    self._diag("warn", "comm", msg)
                    self._fire("comm_restored", {"minutes": minutes})
                    await self._notify("comm", msg)
                elif o["count"] >= CONFIRM_FAILURES:
                    self._diag("warn", "comm", self._t("diag.micro_cut", minutes=minutes, count=o["count"], cause=cause))
                else:
                    s["isolated"] += 1
                    self._diag("debug", "comm", self._t("diag.isolated", minutes=minutes, count=o["count"], cause=cause))
            else:
                self._diag("info", "comm", self._t("diag.wake", time=self._time(now), hours=rnd(minutes / 60, 1)))
            self.comm["outage"] = None
            self._save("dayStats", True)
        if not s["wake"]:
            s["wake"] = now
        if s["sleep"]:
            s["sleep"] = None
        last = self.comm["lastOk"]
        was_producing = bool(last) and last["power"] >= PRODUCING_W
        self.comm["lastOk"] = {"t": now, "power": d["power"], "day": self._today_key()}
        if was_producing != (d["power"] >= PRODUCING_W):
            self.store["lastOk"] = dict(self.comm["lastOk"])
            self._save_cb()

        if connect_ms > SLOW_CONNECT_MS:
            self._diag_once("slow", RATE_LIMIT_MIN, "warn", "comm", self._t("diag.slow", ms=latency, connect=connect_ms))
        elif latency > SLOW_RESPONSE_MS:
            s["slowBusy"] += 1
            self._diag("debug", "comm", self._t("diag.slow_busy", ms=latency, connect=connect_ms))
        self._check_retry_rate(now)
        self._diag("debug", "comm", self._t(
            "diag.poll_ok", ms=latency, connect=connect_ms, power=d["power"], voltage=d["voltage"], temp=d["temperature"],
            panels="/".join(str(round(p["power"])) for p in d["panels"]),
        ))
        self._set("latency", latency)
        self._set("comm_rate", self.success_rate(now))
        self.failures = 0
        self.standby = False
        await self._update(d)
        self._save("dayStats", every=DAY_STATS_SAVE_S)

    async def _on_read_failure(self, err: SolarmanV5Error) -> None:
        now = self.now()
        cause = classify_error(err)
        cause_text = self._t(f"diag.cause.{cause}")
        last_power = self.comm["lastOk"]["power"] if self.comm["lastOk"] else 0
        producing = last_power >= PRODUCING_W
        self.failures += 1
        self.day_stats["fail"] += 1
        if producing:
            self.day_stats["failProducing"] += 1
            self.comm["window"].append({"t": now, "ok": False})
        self.comm["lastError"] = {
            "t": datetime.fromtimestamp(now).astimezone().isoformat(), "cause": cause, "text": cause_text,
            "code": getattr(err, "code", None), "message": str(err),
        }
        vars_ = {"power": last_power, "cause": cause_text, "code": getattr(err, "code", None) or "?", "message": str(err), "n": self.failures}
        if cause in ("serial", "port_closed"):
            self._diag_once(f"cfg_{cause}", RATE_LIMIT_MIN, "warn", "comm", self._t("diag.read_fail_day", **vars_))

        o = self.comm["outage"]
        if not o:
            o = {"start": now, "powerBefore": last_power, "cause": cause, "count": 0, "alerted": False, "graceLogged": False}
            self.comm["outage"] = o
            self._diag("debug" if producing else "info", "comm", self._t("diag.read_fail_day" if producing else "diag.read_fail_night", **vars_))
        o["count"] += 1
        o["cause"] = cause
        if o["powerBefore"] >= PRODUCING_W and o["count"] == CONFIRM_FAILURES:
            self._diag("warn", "comm", self._t("diag.read_fail_repeated", **vars_, count=o["count"]))
        self._diag("debug", "comm", self._t("diag.read_fail_detail", **vars_))

        delay_min = self.num("comm_alert_delay", 10)
        minutes = rnd((now - o["start"]) / 60, 1)
        starting = now - self.started_at < STARTUP_GRACE_S
        if o["powerBefore"] >= PRODUCING_W and not o["alerted"] and minutes >= delay_min:
            if starting and not o["graceLogged"]:
                o["graceLogged"] = True
                self._diag("info", "comm", self._t("diag.startup_grace", minutes=minutes))
            elif not starting:
                o["alerted"] = True
                msg = self._t("diag.comm_lost", minutes=minutes, cause=cause_text)
                self._diag("error", "comm", msg)
                self._fire("comm_lost", {"minutes": minutes, "cause": cause_text})
                await self._notify("comm", msg)

        rate = self.success_rate(now)
        if rate is not None:
            self._set("comm_rate", rate)
        self._save("dayStats", force=self.failures == STANDBY_AFTER_FAILURES, every=DAY_STATS_SAVE_S)

        if self.failures >= STANDBY_AFTER_FAILURES and not self.standby:
            self.standby = True
            night = o["powerBefore"] < PRODUCING_W
            if night:
                last = self.comm["lastOk"]
                if last and (last.get("day") or self._today_key(last["t"])) == self._today_key():
                    self.day_stats["sleep"] = last["t"]
                    self._save("dayStats", True)
                    self._diag("info", "comm", self._t("diag.sleep", time=self._time(last["t"]), power=o["powerBefore"]))
                    await self._panel_verdict(self._today_key())
                else:
                    self._diag("info", "comm", self._t("diag.standby_start"))
            self._zero_power()
            self._set_status(self._t("status.standby" if night else "status.unreachable"), 0)
            for w in self.panel_watch + self.zero_watch:
                w["since"] = None
            self.derate_candidate = None
            self.derating = None

    def _on_retry(self, err: SolarmanV5Error, attempt: int) -> None:
        cause = classify_error(err)
        self.cycle_retries += 1
        self.day_stats["retries"] += 1
        self.day_stats["retryCauses"][cause] = self.day_stats["retryCauses"].get(cause, 0) + 1
        self._diag("debug", "comm", self._t("diag.retry", attempt=attempt, cause=self._t(f"diag.cause.{cause}"), code=err.code or "?"))

    def _check_retry_rate(self, now: float) -> None:
        w = [e for e in self.comm["window"] if now - e["t"] <= COMM_WINDOW_S]
        if len(w) < 60:
            return
        retried = sum(1 for e in w if e.get("retried") or not e["ok"])
        pct = rnd(100 * retried / len(w), 1)
        if pct >= RETRY_RATE_WARN:
            self._diag_once("retry_rate", 60, "warn", "comm", self._t("diag.retry_rate", pct=pct, n=retried, total=len(w)))

    def _zero_power(self) -> None:
        self.values["power"] = 0.0
        self.values["current"] = 0.0
        self.values["dc_power"] = 0.0
        self.values["efficiency"] = None
        for n in range(1, PANELS + 1):
            self.values[f"panel{n}_power"] = 0.0
            self.values[f"panel{n}_current"] = 0.0

    def _set_status(self, label: str, code: int) -> None:
        self.values["status"] = label
        self.values["status_code"] = code
        if self.last_status is None:
            self.last_status = label
            self._diag("info", "inverter", self._t("diag.status", status=label))
            return
        if label == self.last_status:
            return
        self.last_status = label
        self._diag("info", "inverter", self._t("diag.status", status=label))
        self._fire("status_changed", {"status": label, "code": code})

    async def _update(self, d: dict[str, Any]) -> None:
        now = self.now()
        self._set("power", d["power"])
        self._set("voltage", d["voltage"])
        self._set("current", d["current"])
        self._set("frequency", d["frequency"])
        self._set("temperature", d["temperature"])
        self.values["alarm"] = bool(d["alarms"] or d["faults"])
        self.values["alarm_code"] = f"{d['alarms']:04x}/{d['faults']:04x}"
        self._set_status(status_label(d, self._t), d["status"])

        total = self.values.get("energy_total")
        if d["energy_total"] > 0 and (total is None or d["energy_total"] >= total):
            self.values["energy_total"] = d["energy_total"]

        stale = dict(self.store.get("staleToday") or {})
        before = len(stale)
        self.values["energy_today"] = self._today_value("energy_today", stale, "ac", d["energy_today"])
        for n, p in enumerate(d["panels"], start=1):
            self._set(f"panel{n}_power", p["power"])
            self._set(f"panel{n}_voltage", p["voltage"])
            self._set(f"panel{n}_current", p["current"])
            self._set(f"panel{n}_energy_total", p["energy_total"])
            self.values[f"panel{n}_energy"] = self._today_value(f"panel{n}_energy", stale, f"pv{n}", p["energy_today"])
        if len(stale) != before:
            self.store["staleToday"] = stale

        dc = sum(p["power"] for p in d["panels"])
        self.values["dc_power"] = rnd(dc)
        self.values["efficiency"] = rnd(100 * d["power"] / dc, 1) if dc > 50 and d["power"] else None
        self.values["peak"] = round(max(self.day_stats["peak"], d["power"]))
        if 300 <= d["rated_power"] <= 10000 and d["rated_power"] != self.rated_power:
            self.rated_power = d["rated_power"]
            self.store["ratedPower"] = d["rated_power"]
        self.values["rated_power"] = self.rated_power or DEFAULT_RATED_W
        self._forecast_meter(now)

        await self._check_inverter(d, now)
        await self._check_panels([p["power"] for p in d["panels"]], now, [p["voltage"] for p in d["panels"]])
        self.last_reading = {**d, "t": now}

    # ---------- Grid and inverter checks ----------

    async def _check_inverter(self, d: dict[str, Any], now: float) -> None:
        s = self.day_stats
        if d["power"] > s["peak"]:
            s["peak"] = d["power"]
            s["peakAt"] = now
        if d["power"] >= PRODUCING_W:
            s["vmin"] = d["voltage"] if s["vmin"] is None else min(s["vmin"], d["voltage"])
            s["vmax"] = d["voltage"] if s["vmax"] is None else max(s["vmax"], d["voltage"])
        s["tmax"] = d["temperature"] if s["tmax"] is None else max(s["tmax"], d["temperature"])

        vmax = self.num("voltage_max", 253)
        vmin = self.num("voltage_min", 207)
        if d["voltage"] > vmax:
            msg = self._t("diag.voltage_high", voltage=d["voltage"], limit=vmax)
            if self._diag_once("vhigh", RATE_LIMIT_MIN, "warn", "grid", msg):
                await self._notify("grid", msg)
        elif 0 < d["voltage"] < vmin:
            msg = self._t("diag.voltage_low", voltage=d["voltage"], limit=vmin)
            if self._diag_once("vlow", RATE_LIMIT_MIN, "warn", "grid", msg):
                await self._notify("grid", msg)
        if d["frequency"] > 0 and not FREQ_MIN <= d["frequency"] <= FREQ_MAX:
            self._diag_once("freq", RATE_LIMIT_MIN, "warn", "grid", self._t("diag.freq", freq=d["frequency"]))

        t_limit = self.num("temp_alert", 90)
        if d["temperature"] >= t_limit:
            msg = self._t("diag.temp", temp=d["temperature"], limit=t_limit)
            if self._diag_once("temp", RATE_LIMIT_MIN, "warn", "inverter", msg):
                await self._notify("alarm", msg)

        await self._check_derating(d, now, t_limit)

        dc = sum(p["power"] for p in d["panels"])
        if dc > EFFICIENCY_MIN_DC_W and d["power"] / dc < EFFICIENCY_MIN:
            self._diag_once("eff", RATE_LIMIT_MIN, "warn", "inverter", self._t(
                "diag.efficiency", eff=rnd(100 * d["power"] / dc, 1), dc=round(dc), ac=round(d["power"])))

        key = f"{d['alarms']}/{d['faults']}"
        status = status_label(d, self._t)
        if not (d["alarms"] or d["faults"]):
            if self.alarm_watch and not self.alarm_watch["notified"]:
                self._diag("info", "inverter", self._t("diag.alarm_short", status=self.alarm_watch["status"]))
            self.alarm_watch = None
        else:
            if not self.alarm_watch or self.alarm_watch["key"] != key:
                self.alarm_watch = {"key": key, "since": now, "status": status, "notified": False}
                self._diag("info", "inverter", self._t("diag.alarm_seen", status=status))
            if not self.alarm_watch["notified"] and now - self.alarm_watch["since"] >= ALARM_CONFIRM_S:
                self.alarm_watch["notified"] = True
                msg = self._t("diag.alarm", status=status)
                self._diag("error", "inverter", msg)
                await self._notify("alarm", msg)
        self.values["alarm_confirmed"] = bool(self.alarm_watch and self.alarm_watch["notified"])

    async def _check_derating(self, d: dict[str, Any], now: float, t_limit: float) -> bool:
        self.history.append({"t": now, "temp": d["temperature"], "panels": [{"p": p["power"], "v": p["voltage"]} for p in d["panels"]]})
        self.history = [h for h in self.history if now - h["t"] <= DERATE_WINDOW_S * 2]
        dc_now = sum(p["power"] for p in d["panels"])

        if self.derating and (dc_now >= self.derating["before"] * DERATE_RECOVERED or d["temperature"] < t_limit - 2 * DERATE_TEMP_MARGIN):
            self.derating = None
        self.values["derating"] = self.derating is not None

        c = self.derate_candidate
        if c:
            c["samples"].append(dc_now)
            if dc_now >= c["before"] * DERATE_RECOVERED:
                self.derate_candidate = None
                self._diag("info", "inverter", self._t("diag.derating_cloud", temp=c["temp"], before=round(c["before"]), after=round(c["after"])))
                return False
            if now - c["since"] < DERATE_PLATEAU_S:
                return False
            self.derate_candidate = None
            mx, mn = max(c["samples"]), min(c["samples"])
            flat = mx <= c["before"] * (1 - DERATE_POWER_DROP) and (mx - mn) <= mx * DERATE_PLATEAU_SPREAD
            if not flat:
                self._diag("info", "inverter", self._t("diag.derating_cloud", temp=c["temp"], before=round(c["before"]), after=round(c["after"])))
                return False
            msg = self._t("diag.derating", temp=c["temp"], before=round(c["before"]), after=round(dc_now), panels=c["panels"])
            self.derating = {"since": now, "before": c["before"]}
            self.values["derating"] = True
            self._fire("derating", {"temperature": c["temp"], "before": round(c["before"]), "after": round(dc_now)})
            if self._diag_once("derate", RATE_LIMIT_MIN, "warn", "inverter", msg):
                await self._notify("alarm", msg)
            return True

        if d["temperature"] < t_limit - DERATE_TEMP_MARGIN:
            return False
        ref = next((h for h in self.history if now - h["t"] >= DERATE_WINDOW_S), None)
        if not ref:
            return False
        dc_ref = sum(p["p"] for p in ref["panels"])
        if dc_ref < DERATE_MIN_DC_W or dc_now > dc_ref * (1 - DERATE_POWER_DROP):
            return False
        signature = sum(
            1
            for i, p in enumerate(d["panels"])
            if ref["panels"][i]["p"] > 0 and ref["panels"][i]["v"] > 0
            and p["power"] <= ref["panels"][i]["p"] * (1 - DERATE_POWER_DROP)
            and p["voltage"] >= ref["panels"][i]["v"] * (1 + DERATE_VOLTAGE_RISE)
        )
        if signature < DERATE_MIN_PANELS:
            return False
        self.derate_candidate = {"since": now, "before": dc_ref, "after": dc_now, "temp": d["temperature"], "panels": signature, "samples": [dc_now]}
        self._diag("debug", "inverter", self._t("diag.derating_watch", temp=d["temperature"], before=round(dc_ref), after=round(dc_now), panels=signature))
        return False

    # ---------- Panel checks ----------

    def adaptive(self) -> bool:
        return self.opt("underperf_mode") != "simple"

    async def _check_panels(self, powers: list[float], now: float, voltages: list[float] | None = None) -> None:
        voltages = voltages or []
        threshold = self.num("underperf_threshold", 15)
        delay_s = self.num("underperf_delay", 30) * 60
        min_power = self.num("underperf_min_power", 80)
        adaptive = self.adaptive()
        total = sum(powers)
        mean = total / PANELS
        self.values["panel_expected"] = None
        if mean < min_power:
            for w in self.panel_watch + self.zero_watch:
                w["since"] = None
            self._publish_panel_flags()
            return

        slot = PanelBaseline.slot_of(now)
        best = max(powers)
        exp_shares = self.baseline.expected(slot, powers) if adaptive else None
        if exp_shares:
            self.values["panel_expected"] = [None if x is None else rnd(x * best) for x in exp_shares]
        skip = [False] * PANELS

        for i in range(PANELS):
            others = (total - powers[i]) / (PANELS - 1)
            panel = i + 1
            exp_share = exp_shares[i] if exp_shares else None
            if adaptive:
                expected = exp_share * best if exp_share is not None else None
                deviation = (powers[i] - expected) / expected * 100 if expected else None
            else:
                expected = others
                deviation = (powers[i] - others) / others * 100 if others > 0 else 0

            z = self.zero_watch[i]
            if not adaptive:
                is_zero = powers[i] < max(5, others * 0.05)
            elif exp_share is None:
                is_zero = powers[i] < 5
            else:
                is_zero = exp_share >= 0.2 and powers[i] < max(5, others * 0.05)
            if is_zero:
                skip[i] = True
                if z["since"] is None:
                    z["since"] = now
                if not z["flagged"] and now - z["since"] >= delay_s:
                    z["flagged"] = True
                    v = voltages[i] if i < len(voltages) else None
                    hint = "" if not isinstance(v, (int, float)) else (
                        self._t("diag.panel_zero_hint_v0") if v < 5 else self._t("diag.panel_zero_hint_v", voltage=v))
                    msg = self._t("diag.panel_zero", panel=panel, power=rnd(powers[i]), avg=rnd(others), hint=hint).strip()
                    self._diag("error", "panel", msg)
                    self._fire("panel_not_producing", {"panel": panel, "average": rnd(others)})
                    await self._notify("panel", msg)
            else:
                z["since"] = None
                if z["flagged"] and powers[i] > others * 0.5:
                    z["flagged"] = False
                    self._diag("info", "panel", self._t("diag.panel_ok", panel=panel))

            w = self.panel_watch[i]
            if deviation is None:
                w["since"] = None
                continue
            if deviation <= -threshold:
                if w["since"] is None:
                    w["since"] = now
                if not w["flagged"] and now - w["since"] >= delay_s:
                    w["flagged"] = True
                    vars_ = {"panel": panel, "power": rnd(powers[i]), "avg": rnd(others), "expected": rnd(expected), "dev": rnd(deviation)}
                    msg = self._t("diag.panel_under_adaptive" if adaptive else "diag.panel_under", **vars_)
                    self._diag("info" if adaptive else "warn", "panel", msg)
                    self._fire("panel_underperforming", {**vars_, "average": vars_["avg"], "deviation": vars_["dev"]})
                    if not is_zero and not adaptive:
                        await self._notify("panel", msg)
            else:
                w["since"] = None
                if w["flagged"] and deviation > -threshold / 2:
                    w["flagged"] = False
                    self._diag("info", "panel", self._t("diag.panel_ok", panel=panel))
                    self._fire("panel_recovered", {"panel": panel})

        for i in range(PANELS):
            if self.panel_watch[i]["flagged"] or self.zero_watch[i]["flagged"]:
                skip[i] = True
        self.baseline.learn(slot, powers, self._today_key(now), skip)
        self._publish_panel_flags()
        self._save("baseline", every=BASELINE_SAVE_S)

    def _publish_panel_flags(self) -> None:
        for i in range(PANELS):
            self.values[f"panel{i + 1}_under"] = self.panel_watch[i]["flagged"]
            self.values[f"panel{i + 1}_zero"] = self.zero_watch[i]["flagged"]

    async def _panel_verdict(self, day: str) -> None:
        if not self.adaptive():
            return
        threshold = self.num("daily_threshold", 10)
        res = self.baseline.verdict(day, threshold)
        if res["done"]:
            return
        if not res["sunny"]:
            self._diag("info", "panel", self._t("diag.verdict_cloudy", day=day))
            return
        if not res["ready"]:
            self._diag("info", "panel", self._t("diag.verdict_learning", days=self.baseline.best["days"], min=BEST_MIN_DAYS))
            self._save("baseline", True)
            return
        anomalies = 0
        verdicts = []
        for i, r in enumerate(res["panels"]):
            panel = i + 1
            vars_ = {
                "panel": panel, "day": day, "best": round(r["best"] * 100), "usual": round((r["ref"] or 0) * 100),
                "dev": abs(rnd(r["dev"] or 0)), "threshold": rnd(r["threshold"]), "days": ACCEPT_DAYS,
            }
            verdicts.append(r["state"])
            if r["state"] in ("new", "ongoing"):
                anomalies += 1
                msg = self._t("diag.verdict_under", **vars_)
                self._diag("warn", "panel", msg)
                if r["state"] == "new":
                    await self._notify("panel", msg)
            elif r["state"] == "accepted":
                msg = self._t("diag.verdict_accepted", **vars_)
                self._diag("warn", "panel", msg)
                await self._notify("panel", msg)
            elif r["state"] == "recovered":
                self._diag("info", "panel", self._t("diag.verdict_recovered", **vars_))
                self._fire("panel_recovered", {"panel": panel})
        if not anomalies:
            self._diag("info", "panel", self._t("diag.verdict_ok", day=day))
        self.store["lastVerdict"] = {"day": day, "panels": verdicts}
        self._save("baseline", True)

    def _check_daily_shares(self, day: str) -> None:
        energies = [self._get(f"panel{n}_energy", 0) for n in range(1, PANELS + 1)]
        total = sum(energies)
        if total < DAILY_MIN_KWH:
            return
        threshold = self.num("daily_threshold", 10)
        res = self.baseline.check_day(energies, threshold)
        if not res["ready"]:
            self._diag("info", "panel", self._t("diag.daily_learning", days=self.baseline.daily["days"], min=DAILY_MIN_DAYS))
        elif self.adaptive():
            mean = total / PANELS
            for i in range(PANELS):
                dev = res["deviations"][i]
                if dev is not None and dev <= -threshold:
                    self._diag("info", "panel", self._t(
                        "diag.panel_daily", day=day, panel=i + 1, energy=rnd(energies[i], 2), dev=abs(rnd(dev)),
                        expected=rnd(res["expected_share"][i] * mean, 2)))
        self._save("baseline", True)

    def utc_offset_min(self, ts: float | None = None) -> int:
        dt = datetime.fromtimestamp(self.now() if ts is None else ts, ZoneInfo(self.tz))
        off = dt.utcoffset()
        return int(off.total_seconds() // 60) if off else 0

    def baseline_summary(self) -> dict[str, Any]:
        return {"mode": "adaptive" if self.adaptive() else "simple", **self.baseline.summary(self.utc_offset_min())}

    def import_homey(self, device: dict[str, Any]) -> dict[str, Any]:
        """Seeds the learned state from a Homey app diagnostic report (one entry of `devices`).

        The report only holds summaries: per-hour forecast factors and per-hour panel ratios
        (average of the four 15-minute slots), so the import is close to, not identical to, the
        Homey state. Learning goes on from there as usual.
        """
        from .panelbaseline import SLOT_MIN, SLOTS  # noqa: PLC0415

        out: dict[str, Any] = {}
        fc = (device.get("forecast") or {}).get("calibration")
        if fc:
            f = self.forecast
            f.reset_calibration()
            samples = int(fc.get("samples") or 0)
            gf = float(fc.get("globalFactor") or 1)
            if samples:
                f.glob = {"a": gf, "r": 1.0, "n": samples}
            for h in fc.get("hours") or []:
                if h.get("factor") is None or not h.get("days"):
                    continue
                days = int(h["days"])
                f.hours[int(h["hour"])] = {"a": float(h["factor"]), "r": 1.0, "n": days, "days": days, "last": None}
            f.since = fc.get("since")
            acc = fc.get("accuracy") or {}
            if acc.get("days"):
                f.accuracy = {"days": int(acc["days"]), "mape": acc.get("mape"), "bias": acc.get("bias"), "last": None}
            out["forecast"] = {"samples": samples, "global_factor": gf, "hours": sum(1 for h in f.hours if h["n"])}
            self._save("forecast", True)
            self.forecast_publish()

        bl = device.get("baseline")
        if bl:
            b = self.baseline
            b.reset()
            offset = self.utc_offset_min()
            slots = 0
            for h in bl.get("hours") or []:
                ratios = h.get("ratios") or []
                if len(ratios) != PANELS or all(r is None for r in ratios):
                    continue
                days = int(h.get("days") or 0)
                first = round((((int(h["hour"]) * 60 - offset) % 1440) + 1440) % 1440 / SLOT_MIN)
                for k in range(4):
                    b.slots[(first + k) % SLOTS] = {
                        "r": [None if r is None else r / 100 for r in ratios],
                        "n": max(days * 30, 1), "days": days, "last": None, "peak": None, "peakDay": None,
                    }
                    slots += 1
            daily = bl.get("daily") or {}
            if isinstance(daily.get("share"), list) and len(daily["share"]) == PANELS:
                b.daily = {"share": [x / 100 for x in daily["share"]], "days": int(daily.get("days") or 0)}
            best = bl.get("best") or {}
            if isinstance(best.get("ref"), list) and len(best["ref"]) == PANELS:
                b.best = {
                    "ref": [None if x is None else x / 100 for x in best["ref"]],
                    "spread": [float(x or 0) for x in best.get("spread") or [0] * PANELS],
                    "days": int(best.get("days") or 0),
                    "flagged": [int(x or 0) for x in best.get("flagged") or [0] * PANELS],
                    "last": None,
                }
            b.since = bl.get("since")
            out["baseline"] = {"slots": slots, "daily_days": b.daily["days"], "best_days": b.best["days"]}
            self._save("baseline", True)

        self._diag("info", "app", f"Homey import: {out}")
        return out

    def reset_baseline(self) -> None:
        self.baseline.reset()
        for w in self.panel_watch:
            w["since"] = None
            w["flagged"] = False
        self._publish_panel_flags()
        self._save("baseline", True)
        self._diag("info", "panel", self._t("diag.baseline_reset"))

    # ---------- Production forecast ----------

    def forecast_enabled(self) -> bool:
        return bool(self.opt("forecast_enabled"))

    def forecast_ctx(self) -> dict[str, Any]:
        rated = self.rated_power or DEFAULT_RATED_W
        peak = self.num("forecast_peak", 0) or rated
        return {"peak_kw": peak / 1000, "rated_kw": rated / 1000, "tz": self.tz}

    def forecast_request(self) -> tuple[str, dict[str, str]]:
        if not self.location or not all(isinstance(x, (int, float)) for x in self.location) or self.location == (0, 0):
            raise EngineError(self._t("diag.forecast_no_location"))
        tilt = self.opt("forecast_tilt")
        azimuth = self.opt("forecast_azimuth")
        return build_url(
            self.location[0], self.location[1],
            float(tilt) if tilt is not None else 30, float(azimuth) if azimuth is not None else 180,
            self.opt("forecast_model") or "best_match",
        )

    async def fetch_forecast(self, get_json: Callable[[str, dict[str, str]], Awaitable[dict]]) -> float:
        """Downloads the weather forecast and publishes it. Returns the delay before the next refresh (s)."""
        if not self.forecast_enabled():
            return FORECAST_REFRESH_MIN * 60
        success = False
        try:
            url, params = self.forecast_request()
            js = await get_json(url, params)
            now = self.now()
            self.forecast.set_series(SolarForecast.parse(js), now)
            ctx = self.forecast_ctx()
            tomorrow = next_day(local_parts(now, ctx["tz"])["day"])
            self.forecast.record_day_ahead(tomorrow, self.forecast.day_total(tomorrow, ctx))
            if self.forecast_error:
                self._diag("info", "forecast", self._t("diag.forecast_restored"))
            self.forecast_error = None
            success = True
            v = self.forecast_publish(now)
            dash = "—"
            self._diag("debug", "forecast", self._t(
                "diag.forecast_updated", today=dash if v["today"] is None else v["today"],
                remaining=dash if v["remaining"] is None else v["remaining"], tomorrow=dash if v["tomorrow"] is None else v["tomorrow"]))
            if None not in (v["today"], v["remaining"], v["tomorrow"]):
                self._fire("forecast_updated", {"today": v["today"], "remaining": v["remaining"], "tomorrow": v["tomorrow"]})
            self._save("forecast", True)
        except Exception as err:  # noqa: BLE001 - network, HTTP or parse error
            self.forecast_error = {"t": datetime.fromtimestamp(self.now()).astimezone().isoformat(), "message": str(err)}
            self._diag_once("forecast_fail", 60, "warn", "forecast", self._t("diag.forecast_failed", message=str(err)))
            self.forecast_publish(self.now())
        return (FORECAST_REFRESH_MIN if success else FORECAST_RETRY_MIN) * 60

    def forecast_values(self, now: float | None = None) -> dict[str, float | None]:
        now = self.now() if now is None else now
        ctx = self.forecast_ctx()
        today = local_parts(now, ctx["tz"])["day"]

        def r(x: float | None, d: int = 2) -> float | None:
            return None if x is None else round(x, d)

        return {
            "today": r(self.forecast.day_total(today, ctx)),
            "remaining": r(self.forecast.remaining(now, ctx)),
            "tomorrow": r(self.forecast.day_total(next_day(today), ctx)),
            "power": r(self.forecast.power_at(now, ctx), 0),
        }

    def forecast_publish(self, now: float | None = None) -> dict[str, float | None]:
        v = self.forecast_values(now)
        if self.forecast_enabled():
            for k in ("today", "remaining", "tomorrow", "power"):
                self.values[f"forecast_{k}"] = v[k]
            acc = self.forecast.accuracy
            self.values["forecast_mape"] = None if acc["mape"] is None else round(acc["mape"], 1)
        return v

    def _forecast_meter(self, now: float) -> None:
        if not self.forecast_enabled():
            return
        hour_start = (int(now) // HOUR_S) * HOUR_S
        value = float(self.values.get("energy_today") or 0)
        m = self.forecast_meter
        if not m or m["hourStart"] != hour_start:
            consecutive = bool(m) and m["hourStart"] == hour_start - HOUR_S
            if consecutive and m["valid"]:
                delta = value - m["value"] if value >= m["value"] else value
                res = self.forecast.learn_hour(m["hourStart"], delta, self.forecast_ctx())
                if res["learned"]:
                    self._diag("debug", "forecast", self._t(
                        "diag.forecast_learn", hour=res["hour"], actual=rnd(delta, 2), forecast=rnd(res["forecast"], 2), factor=res["factor"]))
                    self._save("forecast", every=FORECAST_SAVE_S)
            woke = bool(self.day_stats["wake"]) and self.day_stats["wake"] >= hour_start
            self.forecast_meter = (
                {"hourStart": hour_start, "value": value, "valid": True}
                if consecutive
                else {"hourStart": hour_start, "value": 0.0 if woke else value, "valid": woke}
            )
        self.forecast_publish(now)

    def _forecast_day_check(self, day: str) -> None:
        if not self.forecast_enabled():
            return
        actual = float(self.values.get("energy_today") or 0)
        res = self.forecast.check_day(day, actual)
        if not res:
            return
        acc = self.forecast.accuracy
        if res["error"] is None:
            self._diag("info", "forecast", self._t("diag.forecast_day_dull", day=day, forecast=res["forecast"], actual=res["actual"]))
        else:
            self._diag("info", "forecast", self._t(
                "diag.forecast_day", day=day, forecast=res["forecast"], actual=res["actual"],
                error=f"{'+' if res['error'] > 0 else ''}{res['error']}", mape=rnd(acc["mape"], 1), days=acc["days"]))
        self._save("forecast", True)

    def forecast_status(self, now: float | None = None) -> dict[str, Any]:
        v = self.forecast_values(now)
        s = self.forecast.summary()
        return {**{k: v[k] for k in ("today", "remaining", "tomorrow")}, "error": self.forecast_error, **s}

    def forecast_window(self, day: str, start: str, end: str) -> float:
        if not self.forecast_enabled():
            raise EngineError(self._t("errors.forecast_disabled"))
        s = parse_time(start)
        e = parse_time(end, True)
        if s is None or e is None or e <= s:
            raise EngineError(self._t("errors.forecast_window"))
        ctx = self.forecast_ctx()
        today = local_parts(self.now(), ctx["tz"])["day"]
        v = self.forecast.window(next_day(today) if day == "tomorrow" else today, s, e, ctx)
        if v is None:
            raise EngineError(self._t("errors.forecast_unavailable"))
        return round(v, 2)

    # ---------- Snapshots ----------

    def diag_status(self) -> dict[str, Any]:
        now = self.now()
        o = self.comm["outage"]
        s = self.day_stats
        iso = lambda ts: datetime.fromtimestamp(ts).astimezone().isoformat() if ts else None  # noqa: E731
        return {
            "host": self.host,
            "standby": self.standby,
            "status": self.values.get("status"),
            "power": self.values.get("power"),
            "last_ok": iso(self.comm["lastOk"]["t"]) if self.comm["lastOk"] else None,
            "latency_last": self.comm["lastLatency"],
            "latency_avg_today": round(s["latSum"] / s["ok"]) if s["ok"] else None,
            "latency_max_today": s["latMax"] or None,
            "success_rate_1h": self.success_rate(now),
            "reads_today": {"ok": s["ok"], "failed": s["fail"]},
            "retries_today": s["retries"],
            "isolated_today": s["isolated"],
            "outages_today": {"count": s["outages"], "minutes": rnd(s["outageMin"], 1)},
            "current_outage": {
                "since": iso(o["start"]), "minutes": rnd((now - o["start"]) / 60, 1),
                "cause": self._t(f"diag.cause.{o['cause']}"), "during_production": o["powerBefore"] >= PRODUCING_W,
            } if o else None,
            "last_error": self.comm["lastError"],
            "panels_underperforming": [i + 1 for i, w in enumerate(self.panel_watch) if w["flagged"]],
            "panels_not_producing": [i + 1 for i, w in enumerate(self.zero_watch) if w["flagged"]],
            "derating": self.derating is not None,
            "last_verdict": self.store.get("lastVerdict"),
            "forecast": self.forecast_status(now) if self.forecast_enabled() else None,
            "today": {**s, **{k: iso(s[k]) for k in ("peakAt", "wake", "sleep")}},
        }

    @property
    def unreachable(self) -> bool:
        o = self.comm["outage"]
        return bool(o and o["powerBefore"] >= PRODUCING_W and self.failures >= CONFIRM_FAILURES)
