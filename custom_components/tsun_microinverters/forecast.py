"""Solar production forecast (pure logic). Port of lib/forecast.js (v1 format).

Input: hourly global tilted irradiance (GTI, W/m², mean over the hour) on the panel plane and the
air temperature (Open-Meteo). Raw model per hour: E = GTI/1000 x kWp x 0.85 x temperature factor.
Calibration: for each local hour of the day, a factor = moving average of measured energy /
moving average of raw energy, blended with the global factor until the hour has 7 learned days.
AC output capped at the inverter rated power. Timestamps are POSIX seconds.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

HOUR_S = 3600
PR0 = 0.85
TEMP_COEF = -0.004
CELL_RISE = 0.03
HOUR_ALPHA_MIN = 0.1
GLOBAL_ALPHA_MIN = 0.02
HOUR_FULL_DAYS = 7
LEARN_MIN_FRACTION = 0.03
CLIP_MARGIN = 0.95
RATIO_MIN = 0.02
RATIO_MAX = 4
FACTOR_MIN = 0.1
FACTOR_MAX = 2.5
ACCURACY_MIN_KWH = 1
ACCURACY_ALPHA_MIN = 0.1
SERIES_MAX_AGE_S = 2 * 24 * HOUR_S


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


@lru_cache(maxsize=8)
def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def local_parts(ts: float, tz: str) -> dict[str, Any]:
    """Local date (YYYY-MM-DD), hour and minute of the day for a timestamp in a time zone."""
    d = datetime.fromtimestamp(ts, _zone(tz))
    return {"day": d.strftime("%Y-%m-%d"), "hour": d.hour, "minute": d.hour * 60 + d.minute}


def next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def parse_time(s: str, is_end: bool = False) -> int | None:
    """'HH:mm' -> minutes of the day; '24:00' and '00:00' as an end time mean the end of the day."""
    try:
        h, m = str(s).strip().split(":")
        v = int(h) * 60 + int(m)
    except (ValueError, TypeError):
        return None
    if len(m) != 2 or v > 24 * 60:
        return None
    if is_end and v in (0, 24 * 60):
        return 24 * 60
    return v


def _empty_hour() -> dict[str, Any]:
    return {"a": 0.0, "r": 0.0, "n": 0, "days": 0, "last": None}


class SolarForecast:
    def __init__(self, data: dict | None = None) -> None:
        self.reset()
        if data and data.get("v") == 1:
            if isinstance(data.get("hours"), list) and len(data["hours"]) == 24:
                self.hours = [{**_empty_hour(), **h} for h in data["hours"]]
            if data.get("global"):
                self.glob = {**self.glob, **data["global"]}
            if data.get("accuracy"):
                self.accuracy = {**self.accuracy, **data["accuracy"]}
            if isinstance(data.get("dayAhead"), dict):
                self.day_ahead = dict(data["dayAhead"])
            if isinstance(data.get("series"), list):
                self.series = [e for e in data["series"] if e and isinstance(e.get("t"), (int, float))]
            self.fetched_at = data.get("fetchedAt")
            self.since = data.get("since")

    def reset(self) -> None:
        self.reset_calibration()
        self.series: list[dict[str, Any]] = []
        self.fetched_at: float | None = None
        self.day_ahead: dict[str, float] = {}
        self.accuracy = {"days": 0, "mape": None, "bias": None, "last": None}

    def reset_calibration(self) -> None:
        self.hours = [_empty_hour() for _ in range(24)]
        self.glob = {"a": 0.0, "r": 0.0, "n": 0}
        self.since: str | None = None

    # ---------- Weather data ----------

    @staticmethod
    def parse(js: dict) -> list[dict[str, Any]]:
        """Open-Meteo answer (timeformat=unixtime); the value stamped t covers [t - 1 h, t)."""
        h = js.get("hourly") if isinstance(js, dict) else None
        if not h or not isinstance(h.get("time"), list) or not isinstance(h.get("global_tilted_irradiance"), list):
            reason = f": {js.get('reason')}" if isinstance(js, dict) and js.get("reason") else ""
            raise ValueError(f"Invalid forecast answer{reason}")
        temps = h.get("temperature_2m") if isinstance(h.get("temperature_2m"), list) else []
        out = []
        for i, ts in enumerate(h["time"]):
            gti = h["global_tilted_irradiance"][i] if i < len(h["global_tilted_irradiance"]) else None
            if not isinstance(ts, (int, float)) or not isinstance(gti, (int, float)):
                continue
            temp = temps[i] if i < len(temps) and isinstance(temps[i], (int, float)) else None
            out.append({"t": ts - HOUR_S, "gti": max(0.0, float(gti)), "temp": temp})
        if not out:
            raise ValueError("Empty forecast answer")
        return out

    def set_series(self, series: list[dict[str, Any]], now: float) -> None:
        self.series = sorted((e for e in series if e["t"] >= now - SERIES_MAX_AGE_S), key=lambda e: e["t"])
        self.fetched_at = now

    def entry_at(self, ts: float) -> dict[str, Any] | None:
        return next((e for e in self.series if e["t"] <= ts < e["t"] + HOUR_S), None)

    # ---------- Model ----------

    @staticmethod
    def raw(entry: dict[str, Any], peak_kw: float) -> float:
        gti = entry.get("gti") or 0
        if gti <= 0 or not peak_kw > 0:
            return 0.0
        cell = (entry["temp"] if isinstance(entry.get("temp"), (int, float)) else 15) + CELL_RISE * gti
        tf = clamp(1 + TEMP_COEF * (cell - 25), 0.7, 1.12)
        return (gti / 1000) * peak_kw * PR0 * tf

    def global_factor(self) -> float:
        g = self.glob
        return clamp(g["a"] / g["r"], FACTOR_MIN, FACTOR_MAX) if g["n"] > 0 and g["r"] > 0 else 1.0

    def factor(self, hour: int) -> float:
        g = self.global_factor()
        h = self.hours[hour]
        if not h or not h["n"] or not h["r"] > 0:
            return g
        own = clamp(h["a"] / h["r"], FACTOR_MIN, FACTOR_MAX)
        w = min(1, h["days"] / HOUR_FULL_DAYS)
        return w * own + (1 - w) * g

    def energy(self, entry: dict[str, Any], ctx: dict[str, Any]) -> float:
        raw = self.raw(entry, ctx["peak_kw"])
        if not raw:
            return 0.0
        e = raw * self.factor(local_parts(entry["t"], ctx["tz"])["hour"])
        return min(e, ctx["rated_kw"]) if ctx["rated_kw"] > 0 else e

    # ---------- Results ----------

    def covers(self, day: str, tz: str) -> bool:
        return sum(1 for e in self.series if local_parts(e["t"], tz)["day"] == day) >= 20

    def window(self, day: str, start_min: int, end_min: int, ctx: dict[str, Any]) -> float | None:
        if not self.covers(day, ctx["tz"]):
            return None
        total = 0.0
        for e in self.series:
            p = local_parts(e["t"], ctx["tz"])
            if p["day"] != day:
                continue
            frm = max(p["minute"], start_min)
            to = min(p["minute"] + 60, end_min)
            if to > frm:
                total += self.energy(e, ctx) * ((to - frm) / 60)
        return total

    def day_total(self, day: str, ctx: dict[str, Any]) -> float | None:
        return self.window(day, 0, 24 * 60, ctx)

    def remaining(self, now: float, ctx: dict[str, Any]) -> float | None:
        today = local_parts(now, ctx["tz"])["day"]
        if not self.covers(today, ctx["tz"]):
            return None
        total = 0.0
        for e in self.series:
            if local_parts(e["t"], ctx["tz"])["day"] != today or e["t"] + HOUR_S <= now:
                continue
            share = 1 if e["t"] >= now else (e["t"] + HOUR_S - now) / HOUR_S
            total += self.energy(e, ctx) * share
        return total

    def power_at(self, now: float, ctx: dict[str, Any]) -> float | None:
        e = self.entry_at(now)
        return self.energy(e, ctx) * 1000 if e else None

    # ---------- Calibration ----------

    def learn_hour(self, hour_start: float, actual: float, ctx: dict[str, Any]) -> dict[str, Any]:
        e = next((x for x in self.series if x["t"] == hour_start), None)
        if not e:
            return {"learned": False, "reason": "no_forecast"}
        p = local_parts(hour_start, ctx["tz"])
        day, hour = p["day"], p["hour"]
        raw = self.raw(e, ctx["peak_kw"])
        forecast = self.energy(e, ctx)
        base = {"hour": hour, "raw": round(raw, 3), "forecast": round(forecast, 3)}
        if raw < LEARN_MIN_FRACTION * ctx["peak_kw"]:
            return {"learned": False, "reason": "low", **base}
        if ctx["rated_kw"] > 0 and (raw * self.factor(hour) > CLIP_MARGIN * ctx["rated_kw"] or actual > CLIP_MARGIN * ctx["rated_kw"]):
            return {"learned": False, "reason": "clipping", **base}
        ratio = actual / raw
        if not RATIO_MIN <= ratio <= RATIO_MAX:
            return {"learned": False, "reason": "outlier", **base}
        h = self.hours[hour]
        h["n"] += 1
        a = max(1 / h["n"], HOUR_ALPHA_MIN)
        h["a"] += a * (actual - h["a"])
        h["r"] += a * (raw - h["r"])
        if h["last"] != day:
            h["days"] += 1
            h["last"] = day
        g = self.glob
        g["n"] += 1
        ag = max(1 / g["n"], GLOBAL_ALPHA_MIN)
        g["a"] += ag * (actual - g["a"])
        g["r"] += ag * (raw - g["r"])
        if not self.since:
            self.since = day
        return {"learned": True, **base, "factor": round(self.factor(hour), 3)}

    # ---------- Day-ahead accuracy ----------

    def record_day_ahead(self, day: str, kwh: float | None) -> None:
        if not isinstance(kwh, (int, float)):
            return
        self.day_ahead[day] = round(kwh, 2)
        for k in sorted(self.day_ahead)[:-3]:
            del self.day_ahead[k]

    def check_day(self, day: str, actual: float) -> dict[str, Any] | None:
        fc = self.day_ahead.pop(day, None)
        if not isinstance(fc, (int, float)):
            return None
        if not actual >= ACCURACY_MIN_KWH:
            return {"forecast": fc, "actual": round(actual, 2), "error": None}
        err = ((fc - actual) / actual) * 100
        acc = self.accuracy
        acc["days"] += 1
        a = max(1 / acc["days"], ACCURACY_ALPHA_MIN)
        acc["mape"] = abs(err) if acc["mape"] is None else acc["mape"] + a * (abs(err) - acc["mape"])
        acc["bias"] = err if acc["bias"] is None else acc["bias"] + a * (err - acc["bias"])
        acc["last"] = day
        return {"forecast": fc, "actual": round(actual, 2), "error": round(err, 1)}

    # ---------- Summary and persistence ----------

    def summary(self) -> dict[str, Any]:
        return {
            "fetched_at": datetime.fromtimestamp(self.fetched_at, timezone.utc).isoformat() if self.fetched_at else None,
            "since": self.since,
            "samples": self.glob["n"],
            "global_factor": round(self.global_factor(), 3),
            "hours": [{"hour": i, "days": h["days"], "factor": round(self.factor(i), 2) if h["n"] else None} for i, h in enumerate(self.hours) if h["days"] > 0],
            "accuracy": {
                "days": self.accuracy["days"],
                "mape": None if self.accuracy["mape"] is None else round(self.accuracy["mape"], 1),
                "bias": None if self.accuracy["bias"] is None else round(self.accuracy["bias"], 1),
            },
        }

    def to_json(self) -> dict[str, Any]:
        r = lambda x: round(x, 5)  # noqa: E731
        return {
            "v": 1,
            "since": self.since,
            "fetchedAt": self.fetched_at,
            "hours": [{**h, "a": r(h["a"]), "r": r(h["r"])} for h in self.hours],
            "global": {**self.glob, "a": r(self.glob["a"]), "r": r(self.glob["r"])},
            "accuracy": self.accuracy,
            "dayAhead": self.day_ahead,
            "series": [{"t": e["t"], "gti": round(e["gti"], 1), "temp": e["temp"]} for e in self.series],
        }


MODELS = ["best_match", "meteofrance_seamless", "icon_seamless", "ecmwf_ifs025", "gfs_seamless"]


def build_url(lat: float, lon: float, tilt: float, azimuth: float, model: str = "best_match") -> tuple[str, dict[str, str]]:
    """Open-Meteo request (free for non-commercial use, no key). Location rounded to 2 decimals."""
    params = {
        "latitude": f"{round(lat, 2):.2f}",
        "longitude": f"{round(lon, 2):.2f}",
        "hourly": "global_tilted_irradiance,temperature_2m",
        "tilt": str(int(min(90, max(0, tilt or 0)))),
        # Open-Meteo: 0 = south, -90 = east, 90 = west (compass bearing 180 = south)
        "azimuth": str(int(((((azimuth or 0) - 180) % 360) + 540) % 360 - 180)),
        "timeformat": "unixtime",
        "timezone": "GMT",
        "past_days": "1",
        "forecast_days": "3",
    }
    if model and model != "best_match" and model in MODELS:
        params["models"] = model
    return "https://api.open-meteo.com/v1/forecast", params
