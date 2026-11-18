"""Solar production forecast (pure logic). Port of lib/forecast.js (v1 format).

Input: hourly global tilted irradiance (GTI, W/m², mean over the hour) on the panel plane and the
air temperature (Open-Meteo). Raw model per hour: E = GTI/1000 x kWp x 0.85 x temperature factor.
Calibration: for each local hour of the day, a factor = moving average of measured energy /
moving average of raw energy, blended with the global factor until the hour has 7 learned days.
Since 1.1.0 the hour factor is refined per sky class of the forecast (cloud cover: clear < 30 %,
variable 30-70 %, overcast > 70 %), blended with the hour factor until the class has 7 learned days:
a weather model's cloud error then no longer averages sunny and overcast days into one factor.
Intraday: the ratio measured / forecast over the last completed hours of the day corrects the
remaining forecast (bounded, decaying towards 1 with the horizon).
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
SKY_CLEAR_MAX = 30
SKY_OVERCAST_MIN = 70
SKY_NAMES = ("clear", "variable", "overcast")
INTRADAY_HOURS = 3
INTRADAY_MIN_KWH = 0.15
INTRADAY_MIN = 0.3
INTRADAY_MAX = 1.5
INTRADAY_HALF_LIFE_H = 3


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


def _empty_sky() -> list[list[dict[str, Any]]]:
    return [[_empty_hour() for _ in SKY_NAMES] for _ in range(24)]


def sky_class(entry: dict[str, Any] | None) -> int | None:
    """0 clear, 1 variable, 2 overcast, None when the cloud cover is unknown."""
    cloud = entry.get("cloud") if entry else None
    if not isinstance(cloud, (int, float)):
        return None
    if cloud < SKY_CLEAR_MAX:
        return 0
    return 1 if cloud <= SKY_OVERCAST_MIN else 2


def _ema_update(acc: dict[str, Any], actual: float, raw: float, alpha_min: float, day: str | None = None) -> None:
    acc["n"] += 1
    a = max(1 / acc["n"], alpha_min)
    acc["a"] += a * (actual - acc["a"])
    acc["r"] += a * (raw - acc["r"])
    if day is not None and acc.get("last") != day:
        acc["days"] += 1
        acc["last"] = day


def _accuracy_update(acc: dict[str, Any], err: float) -> None:
    acc["days"] += 1
    a = max(1 / acc["days"], ACCURACY_ALPHA_MIN)
    acc["mape"] = abs(err) if acc["mape"] is None else acc["mape"] + a * (abs(err) - acc["mape"])
    acc["bias"] = err if acc["bias"] is None else acc["bias"] + a * (err - acc["bias"])


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
            sky = data.get("sky")
            if isinstance(sky, list) and len(sky) == 24 and all(isinstance(h, list) and len(h) == len(SKY_NAMES) for h in sky):
                self.sky = [[{**_empty_hour(), **c} for c in h] for h in sky]
            if isinstance(data.get("todayActual"), dict):
                self.today_actual = {"day": data["todayActual"].get("day"), "hours": dict(data["todayActual"].get("hours") or {})}
            if isinstance(data.get("models"), dict):
                self.models = {k: {**_empty_model(), **v} for k, v in data["models"].items() if isinstance(v, dict)}
            self.compare_done = data.get("compareDone")
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
        self.today_actual: dict[str, Any] = {"day": None, "hours": {}}
        self.models: dict[str, dict[str, Any]] = {}
        self.compare_done: str | None = None

    def reset_calibration(self) -> None:
        self.hours = [_empty_hour() for _ in range(24)]
        self.sky = _empty_sky()
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
        clouds = h.get("cloud_cover") if isinstance(h.get("cloud_cover"), list) else []
        out = []
        for i, ts in enumerate(h["time"]):
            gti = h["global_tilted_irradiance"][i] if i < len(h["global_tilted_irradiance"]) else None
            if not isinstance(ts, (int, float)) or not isinstance(gti, (int, float)):
                continue
            temp = temps[i] if i < len(temps) and isinstance(temps[i], (int, float)) else None
            cloud = clouds[i] if i < len(clouds) and isinstance(clouds[i], (int, float)) else None
            out.append({"t": ts - HOUR_S, "gti": max(0.0, float(gti)), "temp": temp, "cloud": cloud})
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

    @staticmethod
    def _blend(acc: dict[str, Any] | None, base: float) -> float:
        if not acc or not acc["n"] or not acc["r"] > 0:
            return base
        own = clamp(acc["a"] / acc["r"], FACTOR_MIN, FACTOR_MAX)
        w = min(1, acc["days"] / HOUR_FULL_DAYS)
        return w * own + (1 - w) * base

    def factor(self, hour: int, sky: int | None = None) -> float:
        f = self._blend(self.hours[hour], self.global_factor())
        return f if sky is None else self._blend(self.sky[hour][sky], f)

    def energy(self, entry: dict[str, Any], ctx: dict[str, Any]) -> float:
        raw = self.raw(entry, ctx["peak_kw"])
        if not raw:
            return 0.0
        e = raw * self.factor(local_parts(entry["t"], ctx["tz"])["hour"], sky_class(entry))
        return min(e, ctx["rated_kw"]) if ctx["rated_kw"] > 0 else e

    # ---------- Results ----------

    def covers(self, day: str, tz: str, series: list[dict[str, Any]] | None = None) -> bool:
        return sum(1 for e in (self.series if series is None else series) if local_parts(e["t"], tz)["day"] == day) >= 20

    def window(self, day: str, start_min: int, end_min: int, ctx: dict[str, Any],
               series: list[dict[str, Any]] | None = None) -> float | None:
        series = self.series if series is None else series
        if not self.covers(day, ctx["tz"], series):
            return None
        total = 0.0
        for e in series:
            p = local_parts(e["t"], ctx["tz"])
            if p["day"] != day:
                continue
            frm = max(p["minute"], start_min)
            to = min(p["minute"] + 60, end_min)
            if to > frm:
                total += self.energy(e, ctx) * ((to - frm) / 60)
        return total

    def day_total(self, day: str, ctx: dict[str, Any], series: list[dict[str, Any]] | None = None) -> float | None:
        return self.window(day, 0, 24 * 60, ctx, series)

    @staticmethod
    def correction(ratio: float | None, hours_ahead: float) -> float:
        """Intraday correction for an hour starting hours_ahead from now (1 = none)."""
        if ratio is None:
            return 1.0
        return 1 + (ratio - 1) * 0.5 ** (max(0.0, hours_ahead) / INTRADAY_HALF_LIFE_H)

    def remaining(self, now: float, ctx: dict[str, Any], ratio: float | None = None) -> float | None:
        today = local_parts(now, ctx["tz"])["day"]
        if not self.covers(today, ctx["tz"]):
            return None
        total = 0.0
        for e in self.series:
            if local_parts(e["t"], ctx["tz"])["day"] != today or e["t"] + HOUR_S <= now:
                continue
            share = 1 if e["t"] >= now else (e["t"] + HOUR_S - now) / HOUR_S
            e_kwh = self.energy(e, ctx) * self.correction(ratio, (e["t"] - now) / HOUR_S)
            if ctx["rated_kw"] > 0:
                e_kwh = min(e_kwh, ctx["rated_kw"])
            total += e_kwh * share
        return total

    def power_at(self, now: float, ctx: dict[str, Any], ratio: float | None = None) -> float | None:
        e = self.entry_at(now)
        if not e:
            return None
        p = self.energy(e, ctx) * self.correction(ratio, 0)
        return (min(p, ctx["rated_kw"]) if ctx["rated_kw"] > 0 else p) * 1000

    # ---------- Intraday correction ----------

    def record_actual(self, hour_start: float, actual: float, ctx: dict[str, Any]) -> None:
        """Energy measured over a completed hour of today (kept for the intraday correction)."""
        day = local_parts(hour_start, ctx["tz"])["day"]
        if self.today_actual.get("day") != day:
            self.today_actual = {"day": day, "hours": {}}
        self.today_actual["hours"][str(int(hour_start))] = round(max(0.0, actual), 3)

    def intraday_ratio(self, now: float, ctx: dict[str, Any]) -> float | None:
        """Measured / forecast energy over the last completed hours of today, or None if too little light."""
        if self.today_actual.get("day") != local_parts(now, ctx["tz"])["day"]:
            return None
        done = sorted((int(k) for k in self.today_actual["hours"] if int(k) + HOUR_S <= now), reverse=True)
        f_sum = a_sum = 0.0
        used = 0
        for hs in done:
            e = next((x for x in self.series if x["t"] == hs), None)
            if not e:
                continue
            f = self.energy(e, ctx)
            if f <= 0:
                continue
            f_sum += f
            a_sum += self.today_actual["hours"][str(hs)]
            used += 1
            if used >= INTRADAY_HOURS:
                break
        if f_sum < INTRADAY_MIN_KWH:
            return None
        return clamp(a_sum / f_sum, INTRADAY_MIN, INTRADAY_MAX)

    # ---------- Calibration ----------

    def learn_hour(self, hour_start: float, actual: float, ctx: dict[str, Any]) -> dict[str, Any]:
        e = next((x for x in self.series if x["t"] == hour_start), None)
        if not e:
            return {"learned": False, "reason": "no_forecast"}
        p = local_parts(hour_start, ctx["tz"])
        day, hour = p["day"], p["hour"]
        raw = self.raw(e, ctx["peak_kw"])
        forecast = self.energy(e, ctx)
        sky = sky_class(e)
        base = {"hour": hour, "raw": round(raw, 3), "forecast": round(forecast, 3), "sky": None if sky is None else SKY_NAMES[sky]}
        if raw < LEARN_MIN_FRACTION * ctx["peak_kw"]:
            return {"learned": False, "reason": "low", **base}
        if ctx["rated_kw"] > 0 and (raw * self.factor(hour, sky) > CLIP_MARGIN * ctx["rated_kw"] or actual > CLIP_MARGIN * ctx["rated_kw"]):
            return {"learned": False, "reason": "clipping", **base}
        ratio = actual / raw
        if not RATIO_MIN <= ratio <= RATIO_MAX:
            return {"learned": False, "reason": "outlier", **base}
        _ema_update(self.hours[hour], actual, raw, HOUR_ALPHA_MIN, day)
        if sky is not None:
            _ema_update(self.sky[hour][sky], actual, raw, HOUR_ALPHA_MIN, day)
        _ema_update(self.glob, actual, raw, GLOBAL_ALPHA_MIN)
        if not self.since:
            self.since = day
        return {"learned": True, **base, "factor": round(self.factor(hour, sky), 3)}

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
        _accuracy_update(self.accuracy, err)
        self.accuracy["last"] = day
        return {"forecast": fc, "actual": round(actual, 2), "error": round(err, 1)}

    # ---------- Weather model comparison ----------

    def record_model_ahead(self, model: str, day: str, kwh: float | None) -> None:
        if not isinstance(kwh, (int, float)):
            return
        m = self.models.setdefault(model, _empty_model())
        m["ahead"][day] = round(kwh, 2)
        for k in sorted(m["ahead"])[:-3]:
            del m["ahead"][k]

    def check_models(self, day: str, actual: float) -> list[dict[str, Any]]:
        out = []
        for name, m in sorted(self.models.items()):
            fc = m["ahead"].pop(day, None)
            if not isinstance(fc, (int, float)):
                continue
            err = None
            if actual >= ACCURACY_MIN_KWH:
                err = ((fc - actual) / actual) * 100
                _accuracy_update(m, err)
            out.append({"model": name, "forecast": fc, "error": None if err is None else round(err, 1)})
        return out

    # ---------- Summary and persistence ----------

    def summary(self) -> dict[str, Any]:
        return {
            "fetched_at": datetime.fromtimestamp(self.fetched_at, timezone.utc).isoformat() if self.fetched_at else None,
            "since": self.since,
            "samples": self.glob["n"],
            "global_factor": round(self.global_factor(), 3),
            "hours": [{"hour": i, "days": h["days"], "factor": round(self.factor(i), 2) if h["n"] else None} for i, h in enumerate(self.hours) if h["days"] > 0],
            "sky_factors": [
                {"hour": i, **{SKY_NAMES[c]: {"days": self.sky[i][c]["days"], "factor": round(self.factor(i, c), 2)}
                               for c in range(len(SKY_NAMES)) if self.sky[i][c]["days"] > 0}}
                for i in range(24) if any(self.sky[i][c]["days"] > 0 for c in range(len(SKY_NAMES)))
            ],
            "models": {
                k: {"days": m["days"], "mape": None if m["mape"] is None else round(m["mape"], 1),
                    "bias": None if m["bias"] is None else round(m["bias"], 1)}
                for k, m in sorted(self.models.items())
            },
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
            "sky": [[{**c, "a": r(c["a"]), "r": r(c["r"])} for c in h] for h in self.sky],
            "todayActual": self.today_actual,
            "models": self.models,
            "compareDone": self.compare_done,
            "accuracy": self.accuracy,
            "dayAhead": self.day_ahead,
            "series": [{"t": e["t"], "gti": round(e["gti"], 1), "temp": e["temp"], "cloud": e.get("cloud")} for e in self.series],
        }


def _empty_model() -> dict[str, Any]:
    return {"ahead": {}, "days": 0, "mape": None, "bias": None}


COMPARE_MODELS = ["meteofrance_seamless", "icon_seamless", "ecmwf_ifs025"]
MODELS = ["best_match", "meteofrance_seamless", "icon_seamless", "ecmwf_ifs025", "gfs_seamless"]


def build_url(lat: float, lon: float, tilt: float, azimuth: float, model: str = "best_match") -> tuple[str, dict[str, str]]:
    """Open-Meteo request (free for non-commercial use, no key). Location rounded to 2 decimals."""
    params = {
        "latitude": f"{round(lat, 2):.2f}",
        "longitude": f"{round(lon, 2):.2f}",
        "hourly": "global_tilted_irradiance,temperature_2m,cloud_cover",
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
