"""Engine tests against a fake Solarman V5 logger (no Home Assistant needed)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fake_logger import FakeLogger, registers
from tsun import engine as eng
from tsun.forecast import SolarForecast, local_parts
from tsun.mx3000d import read_all, read_identity
from tsun.panelbaseline import PanelBaseline
from tsun.solarmanv5 import SolarmanV5Client, SolarmanV5Error

SN = 2712345678
TZ = "Europe/Paris"


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(TZ)).timestamp()


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Harness:
    def __init__(self, fake: FakeLogger, clock: Clock, options=None, store=None):
        self.diags: list[tuple[str, str, str]] = []
        self.notes: list[tuple[str, str]] = []
        self.events: list[tuple[str, dict]] = []
        self.saves = 0
        self.e = eng.TsunEngine(
            name="TSUN", host="127.0.0.1", logger_sn=SN, options=options, store=store, tz=TZ, lang="fr",
            location=(43.4, 5.3), diag=lambda lv, c, m: self.diags.append((lv, c, m)),
            notify=lambda k, m: self.notes.append((k, m)), fire=lambda ev, d: self.events.append((ev, d)),
            save=self._save, now=clock,
            client_factory=lambda h, sn: SolarmanV5Client("127.0.0.1", sn, port=fake.port, timeout=0.3), retry_delay=0,
        )

    def _save(self):
        self.saves += 1

    def logged(self, level: str) -> list[str]:
        return [m for lv, _c, m in self.diags if lv == level]


def run(coro):
    return asyncio.run(coro)


async def with_fake(regs, fn):
    fake = FakeLogger(SN, regs)
    await fake.start()
    try:
        return await fn(fake)
    finally:
        await fake.stop()


# ---------- Protocol ----------

def test_read_all_and_identity():
    async def go(fake):
        c = SolarmanV5Client("127.0.0.1", SN, port=fake.port, timeout=1)
        await c.connect()
        ident = await read_identity(c)
        d = await read_all(c, 40)
        await c.close()
        return ident, d

    ident, d = run(with_fake(registers(power_w=1500, temp_c=45), go))
    assert ident["inverter_serial"] == "TSN1234567890XYZ"
    assert d["power"] == 1500 and d["voltage"] == 236.4 and d["frequency"] == 50
    assert d["energy_today"] == 3.21 and d["energy_total"] == 1234.56 and d["temperature"] == 45
    assert d["rated_power"] == 3000 and len(d["panels"]) == 6 and d["panels"][0]["voltage"] == 38


def test_retry_on_corrupt_answer_then_wrong_serial():
    async def go(fake):
        c = SolarmanV5Client("127.0.0.1", SN, port=fake.port, timeout=1)
        await c.connect()
        fake.mode = "corrupt_once"
        retries = []
        d = await read_all(c, 40, on_retry=lambda e, a: retries.append(e.code), retry_delay=0)
        fake.mode = "wrong_serial"
        with pytest.raises(SolarmanV5Error) as ei:
            await c.read_holding_registers(0x1100, 7)
        await c.close()
        return d, retries, ei.value.code

    d, retries, code = run(with_fake(registers(), go))
    assert d["power"] == 1500 and retries == ["ECRC"] and code == "ESERIAL"


# ---------- Engine ----------

def test_success_values_and_status():
    clock = Clock(ts("2026-06-21T12:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        nxt = await h.e.poll()
        return h, nxt

    h, nxt = run(with_fake(registers(), go))
    v = h.e.values
    assert nxt == 30
    assert v["power"] == 1500 and v["energy_today"] == 3.21 and v["energy_total"] == 1234.56
    assert v["status"] == "Normal" and v["comm_rate"] == 100 and v["dc_power"] == pytest.approx(1560, abs=1)
    assert v["efficiency"] == pytest.approx(96.2, abs=0.2)
    assert h.e.store["day"] == "2026-06-21"


def test_night_standby():
    clock = Clock(ts("2026-06-21T23:00:00"))

    async def go(fake):
        fake.mode = "silent"
        h = Harness(fake, clock)
        delays = []
        for _ in range(3):
            delays.append(await h.e.poll())
            clock.t += 30
        return h, delays

    h, delays = run(with_fake(registers(), go))
    assert h.e.standby and delays[-1] == 300
    assert h.e.values["status"] == "Veille (nuit)"
    assert not h.notes and not h.e.unreachable


def test_comm_lost_while_producing_and_restored():
    clock = Clock(ts("2026-06-21T12:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        h.e.started_at -= 3600  # past the start-up grace
        await h.e.poll()
        fake.mode = "silent"
        delays = []
        for _ in range(12):
            clock.t += 60
            delays.append(await h.e.poll())
        lost = list(h.notes)
        fake.mode = "ok"
        clock.t += 60
        await h.e.poll()
        return h, delays, lost

    h, delays, lost = run(with_fake(registers(), go))
    assert delays[-1] == 60  # unreachable while producing: 1 min
    assert len(lost) == 1 and lost[0][0] == "comm" and "Onduleur injoignable" in lost[0][1]
    assert ("comm_lost" in [e for e, _ in h.events]) and ("comm_restored" in [e for e, _ in h.events])
    assert h.e.day_stats["outages"] == 1 and not h.e.unreachable and h.e.values["status"] == "Normal"


def test_startup_grace_no_alert():
    clock = Clock(ts("2026-06-21T12:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        await h.e.poll()
        fake.mode = "silent"
        for _ in range(12):
            clock.t += 60
            await h.e.poll()
        return h

    h = run(with_fake(registers(), go))
    assert not h.notes and any("démarrage" in m or "start" in m for m in h.logged("info"))


def test_day_change_stale_counters():
    clock = Clock(ts("2026-06-21T20:00:00"))
    regs = registers(power_w=40, today_kwh=12.5, panel_today=[2.1] * 6)

    async def go(fake):
        h = Harness(fake, clock)
        await h.e.poll()
        assert h.e.values["energy_today"] == 12.5
        clock.t = ts("2026-06-22T07:00:00")
        await h.e.poll()  # inverter still holds yesterday's counter
        first = h.e.values["energy_today"], h.e.values["panel1_energy"]
        fake.registers.update(registers(power_w=60, today_kwh=0.05, panel_today=[0.01] * 6))
        clock.t += 60
        await h.e.poll()
        return h, first

    h, first = run(with_fake(regs, go))
    assert first == (0, 0)
    assert h.e.values["energy_today"] == 0.05 and h.e.values["panel1_energy"] == 0.01
    assert any("Bilan du 2026-06-21" in m for m in h.logged("info"))


def test_daily_counter_never_decreases():
    clock = Clock(ts("2026-06-21T20:30:00"))

    async def go(fake):
        h = Harness(fake, clock)
        await h.e.poll()
        fake.registers.update(registers(power_w=0, today_kwh=0, panel_today=[0] * 6, panels=[0] * 6))
        clock.t += 60
        await h.e.poll()
        return h

    h = run(with_fake(registers(today_kwh=9.87), go))
    assert h.e.values["energy_today"] == 9.87


def test_panel_not_producing_simple_mode():
    clock = Clock(ts("2026-06-21T12:00:00"))
    regs = registers(panels=[300, 300, 300, 0, 300, 300], panel_voltage=[38, 38, 38, 0.5, 38, 38])

    async def go(fake):
        h = Harness(fake, clock, options={"underperf_mode": "simple", "underperf_delay": 5})
        for _ in range(12):
            await h.e.poll()
            clock.t += 30
        return h

    h = run(with_fake(regs, go))
    assert h.e.zero_watch[3]["flagged"] and h.e.values["panel4_zero"]
    assert [k for k, _ in h.notes] == ["panel"] and "4" in h.notes[0][1]
    assert ("panel_not_producing", {"panel": 4, "average": 300.0}) in h.events


def test_underperformance_simple_mode_and_recovery():
    clock = Clock(ts("2026-06-21T12:00:00"))
    regs = registers(panels=[300, 300, 300, 200, 300, 300])

    async def go(fake):
        h = Harness(fake, clock, options={"underperf_mode": "simple", "underperf_delay": 5})
        for _ in range(12):
            await h.e.poll()
            clock.t += 30
        flagged = h.e.panel_watch[3]["flagged"]
        fake.registers.update(registers(panels=[300] * 6))
        await h.e.poll()
        return h, flagged

    h, flagged = run(with_fake(regs, go))
    assert flagged and not h.e.panel_watch[3]["flagged"]
    types = [e for e, _ in h.events]
    assert "panel_underperforming" in types and "panel_recovered" in types


def test_alarm_confirmed_after_5_min():
    clock = Clock(ts("2026-06-21T12:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        for _ in range(12):
            await h.e.poll()
            clock.t += 30
        return h

    h = run(with_fake(registers(alarms=0x0004), go))
    assert h.e.values["alarm"] and h.e.values["status"] == "Alarme 0x0004"
    assert [k for k, _ in h.notes] == ["alarm"]


def test_short_alarm_not_notified():
    clock = Clock(ts("2026-06-21T12:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        await h.e.poll()
        clock.t += 60
        fake.registers.update(registers())
        await h.e.poll()
        return h

    h = run(with_fake(registers(alarms=0x0004), go))
    assert not h.notes and not h.e.values["alarm"]


def test_thermal_derating():
    clock = Clock(ts("2026-06-21T13:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        for _ in range(11):  # 5 min of normal production, hot
            await h.e.poll()
            clock.t += 30
        fake.registers.update(registers(power_w=1150, panels=[200] * 6, panel_voltage=[42] * 6, temp_c=88))
        for _ in range(13):  # drop with a voltage rise, then a 6-min plateau
            await h.e.poll()
            clock.t += 30
        return h

    h = run(with_fake(registers(power_w=1500, panels=[260] * 6, panel_voltage=[38] * 6, temp_c=86), go))
    assert h.e.values["derating"] and h.e.derating
    assert ("alarm" in [k for k, _ in h.notes]) and "derating" in [e for e, _ in h.events]


def test_persistence_round_trip():
    clock = Clock(ts("2026-06-21T12:00:00"))

    async def go(fake):
        h = Harness(fake, clock)
        for _ in range(3):
            await h.e.poll()
            clock.t += 30
        snap = h.e.snapshot_store()
        h2 = Harness(fake, clock, store=snap)
        return h2

    h2 = run(with_fake(registers(), go))
    assert h2.e.day_stats["ok"] == 3 and h2.e.values["energy_today"] == 3.21
    assert h2.e.comm["lastOk"] is not None


def test_options_change_resets_durations():
    clock = Clock(ts("2026-06-21T12:00:00"))
    e = Harness(FakeLogger(SN, {}), clock).e
    e.panel_watch[0]["since"] = 1
    changed = e.update_options({"underperf_threshold": 20})
    assert changed == ["underperf_threshold"] and e.panel_watch[0]["since"] is None


# ---------- Forecast ----------

def fake_open_meteo(start: float, hours: int = 72) -> dict:
    times, gti, temp = [], [], []
    for i in range(hours):
        t = int(start) + i * 3600
        hour = local_parts(t - 3600, TZ)["hour"]
        times.append(t)
        gti.append(max(0, 800 - abs(hour - 13) * 120))
        temp.append(22)
    return {"hourly": {"time": times, "global_tilted_irradiance": gti, "temperature_2m": temp}}


def test_forecast_fetch_values_window_and_learning():
    day0 = ts("2026-06-21T00:00:00")
    clock = Clock(ts("2026-06-21T09:55:00"))

    async def go(fake):
        h = Harness(fake, clock, options={"forecast_enabled": True})
        await h.e.poll()  # wake-up during the 09:00 hour
        clock.t = ts("2026-06-21T10:05:00")

        async def get_json(url, params):
            assert params["azimuth"] == "0" and params["latitude"] == "43.40"
            return fake_open_meteo(day0 - 86400)

        delay = await h.e.fetch_forecast(get_json)
        await h.e.poll()
        fake.registers.update(registers(today_kwh=4.71))
        clock.t = ts("2026-06-21T11:01:00")
        await h.e.poll()
        return h, delay

    h, delay = run(with_fake(registers(today_kwh=3.21), go))
    v = h.e.values
    assert delay == 3600 and v["forecast_today"] > 5 and v["forecast_tomorrow"] > 5 and v["forecast_power"] > 0
    assert 0 < v["forecast_remaining"] < v["forecast_today"]
    assert "forecast_updated" in [e for e, _ in h.events]
    assert h.e.forecast.glob["n"] == 1  # 10:00-11:00 hour learned (1.5 kWh)
    w = h.e.forecast_window("tomorrow", "11:00", "16:00")
    assert 0 < w < v["forecast_tomorrow"]
    with pytest.raises(eng.EngineError):
        h.e.forecast_window("today", "16:00", "11:00")


def test_forecast_disabled_errors():
    e = Harness(FakeLogger(SN, {}), Clock(ts("2026-06-21T12:00:00"))).e
    with pytest.raises(eng.EngineError, match="désactivée"):
        e.forecast_window("today", "10:00", "12:00")


def test_forecast_failure_retry_delay():
    clock = Clock(ts("2026-06-21T12:00:00"))
    e = Harness(FakeLogger(SN, {}), clock, options={"forecast_enabled": True}).e

    async def bad(url, params):
        raise RuntimeError("HTTP 503")

    assert run(e.fetch_forecast(bad)) == 900 and e.forecast_error["message"] == "HTTP 503"


def test_forecast_day_accuracy():
    f = SolarForecast()
    f.record_day_ahead("2026-06-21", 10)
    res = f.check_day("2026-06-21", 8)
    assert res["error"] == 25 and f.accuracy["days"] == 1


def _sky_series(start: float, cloud: float, gti: float = 600, hours: int = 48) -> list[dict]:
    return [{"t": start + i * 3600, "gti": gti if 8 <= local_parts(start + i * 3600, TZ)["hour"] <= 16 else 0,
             "temp": 15, "cloud": cloud} for i in range(hours)]


CTX = {"peak_kw": 3.6, "rated_kw": 3.0, "tz": TZ}


def test_forecast_sky_class_factors_split_clear_and_overcast():
    f = SolarForecast()
    day0 = ts("2026-11-01T00:00:00")
    for d in range(14):  # alternate sunny and overcast days at 12:00 (7 learned days each)
        cloud = 10 if d % 2 == 0 else 90
        start = day0 + d * 86400
        f.set_series(_sky_series(start, cloud), start)
        hs = start + 12 * 3600
        raw = f.raw(f.entry_at(hs), 3.6)
        f.learn_hour(hs, raw * (1.0 if cloud == 10 else 0.3), CTX)
    # one factor per hour averages both skies; the sky classes keep them apart
    assert 0.4 < f.factor(12) < 0.9
    assert f.factor(12, 0) == pytest.approx(1.0, abs=0.05) and f.factor(12, 2) == pytest.approx(0.3, abs=0.05)
    s = f.summary()["sky_factors"]
    assert s[0]["hour"] == 12 and set(s[0]) == {"hour", "clear", "overcast"}
    # round trip keeps the sky calibration
    g = SolarForecast(f.to_json())
    assert g.factor(12, 2) == pytest.approx(f.factor(12, 2), abs=1e-3)


def test_forecast_parse_reads_cloud_cover():
    js = fake_open_meteo(ts("2026-06-21T00:00:00"), 3)
    js["hourly"]["cloud_cover"] = [5, 50, 95]
    series = SolarForecast.parse(js)
    assert [e["cloud"] for e in series] == [5, 50, 95]


def test_forecast_intraday_ratio_corrects_remaining():
    f = SolarForecast()
    day0 = ts("2026-11-18T00:00:00")
    f.set_series(_sky_series(day0, 20), day0)
    now = day0 + 12 * 3600 + 600
    full = f.remaining(now, CTX)
    for h in (9, 10, 11):  # morning produced 40 % of the forecast
        hs = day0 + h * 3600
        f.record_actual(hs, 0.4 * f.energy(f.entry_at(hs), CTX), CTX)
    ratio = f.intraday_ratio(now, CTX)
    assert ratio == pytest.approx(0.4, abs=1e-3)
    corrected = f.remaining(now, CTX, ratio)
    assert 0.4 * full < corrected < full
    assert SolarForecast.correction(0.4, 0) == pytest.approx(0.4)
    assert SolarForecast.correction(0.4, 3) == pytest.approx(0.7)
    assert f.intraday_ratio(now + 86400, CTX) is None  # another day: no correction
    assert SolarForecast.correction(None, 0) == 1.0


def test_forecast_today_is_produced_plus_remaining():
    clock = Clock(ts("2026-11-18T17:30:00"))
    e = Harness(FakeLogger(SN, {}), clock, options={"forecast_enabled": True}).e
    day0 = ts("2026-11-18T00:00:00")
    e.forecast.set_series(_sky_series(day0, 50), day0)
    e.values["energy_today"] = 3.58
    v = e.forecast_values()
    assert v["remaining"] == 0 and v["today"] == 3.58
    clock.t = ts("2026-11-18T05:00:00")
    e.values["energy_today"] = 0
    v = e.forecast_values()
    assert v["today"] == v["remaining"] > 0


def test_forecast_model_comparison():
    clock = Clock(ts("2026-11-18T19:10:00"))
    e = Harness(FakeLogger(SN, {}), clock, options={"forecast_enabled": True}).e
    e.location = (45.0, 1.0)
    calls = []

    async def get_json(url, params):
        calls.append(params.get("models"))
        return fake_open_meteo(ts("2026-11-17T00:00:00"))

    run(e.fetch_forecast(get_json))
    run(e.fetch_forecast(get_json))  # second refresh the same evening: no new comparison
    assert calls.count("icon_seamless") == 1 and calls.count("ecmwf_ifs025") == 1
    assert set(e.forecast.models) == {"meteofrance_seamless", "icon_seamless", "ecmwf_ifs025"}
    fc = e.forecast.models["icon_seamless"]["ahead"]["2026-11-19"]
    res = e.forecast.check_models("2026-11-19", fc / 2)
    assert {r["model"] for r in res} == set(e.forecast.models) and res[0]["error"] == pytest.approx(100, abs=0.5)
    assert e.forecast.summary()["models"]["icon_seamless"]["days"] == 1


# ---------- Panel baseline (adaptive day verdict) ----------

def test_baseline_verdict_detects_defect_and_ignores_shadow():
    b = PanelBaseline(6)
    base = ts("2026-06-01T00:00:00")

    def sunny_day(day_idx: int, shares: list[float], shadow_panel: int | None = None):
        day = local_parts(base + day_idx * 86400, TZ)["day"]
        for k in range(10 * 60, 16 * 60, 3):  # every 3 min from 10:00 to 16:00 UTC
            t = base + day_idx * 86400 + k * 60
            p = [400 * s for s in shares]
            if shadow_panel is not None and k < 12 * 60:
                p[shadow_panel] *= 0.3  # morning shadow
            b.learn(PanelBaseline.slot_of(t), p, day)
        return b.verdict(day, 10)

    for i in range(6):
        r = sunny_day(i, [1, 1, 1, 1, 1, 0.97], shadow_panel=2)
    assert r["ready"] is False or all(p["state"] == "ok" for p in r["panels"])
    r = sunny_day(6, [1, 1, 1, 1, 1, 0.97], shadow_panel=2)
    assert r["ready"] and all(p["state"] == "ok" for p in r["panels"])
    r = sunny_day(7, [1, 1, 1, 0.7, 1, 0.97], shadow_panel=2)
    assert r["panels"][3]["state"] == "new" and r["panels"][2]["state"] == "ok"


def test_import_homey_report():
    clock = Clock(ts("2026-11-01T20:00:00"))
    e = Harness(FakeLogger(SN, {}), clock, options={"forecast_enabled": True}).e
    device = {
        "forecast": {"calibration": {"since": "2026-07-01", "samples": 900, "globalFactor": 0.81,
                                     "hours": [{"hour": 12, "days": 90, "factor": 0.85}, {"hour": 8, "days": 60, "factor": 0.4}],
                                     "accuracy": {"days": 80, "mape": 18.5, "bias": 3.1}}},
        "baseline": {"since": "2026-07-01", "hours": [{"hour": 9, "ratios": [100, 95, 30, 98, 97, 96], "ready": True, "days": 40}],
                     "daily": {"days": 100, "share": [101, 99, 80, 104, 107, 109]},
                     "best": {"days": 95, "ref": [100, 98, 97, 99, 100, 99], "spread": [2.1, 2, 3, 1.5, 1, 1.2], "flagged": [0, 0, 0, 0, 0, 0]}},
    }
    res = e.import_homey(device)
    assert res["forecast"]["hours"] == 2 and res["baseline"]["slots"] == 4
    assert e.forecast.factor(12) == pytest.approx(0.85) and e.forecast.global_factor() == pytest.approx(0.81)
    slot = PanelBaseline.slot_of(ts("2026-11-01T09:00:00"))
    assert e.baseline.slots[slot]["r"][2] == 0.3 and e.baseline.ready(slot)
    assert e.baseline.best["days"] == 95 and e.baseline.best["ref"][0] == 1.0
    assert e.forecast.accuracy["mape"] == 18.5
