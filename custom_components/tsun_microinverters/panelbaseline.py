"""Learned production profile of each panel (pure logic). Port of lib/panelbaseline.js (v2).

Intraday: the day is split into 15-minute slots of SOLAR time (UTC). For each slot and panel the
baseline keeps the panel's usual share of production relative to the best panel at that moment
(1.00 = full sun, 0.10 = usually in deep shade), as an exponential moving average. Only sunny
moments are learned and checked (best panel >= 60 % of the slot's recent peak).

Daily: the usual share of each panel in the daily yield (information).

Day verdict: alerts are based on the BEST 15-minute share each panel reaches during the day in
direct sun. A shadow never lowers that best share; a real defect lowers it all day long. The usual
best share and its day-to-day spread are learned; the threshold widens to 3 x the usual spread; a
change confirmed for 7 sunny days is accepted as the new normal.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

SLOT_MIN = 15
SLOTS = (24 * 60) // SLOT_MIN
MIN_DAYS = 3
ALPHA_MIN = 0.01
TOLERANCE_SLOTS = 2
SUNNY_RATIO = 0.6
PEAK_DECAY = 0.95
PEAK_JUMP = 1.5
DAILY_MIN_DAYS = 7
DAILY_ALPHA_MIN = 0.05
VERDICT_MIN_SAMPLES = 5
VERDICT_MIN_SLOTS = 8
BEST_MIN_DAYS = 5
BEST_ALPHA_MIN = 0.2
SPREAD_FACTOR = 3
ACCEPT_DAYS = 7
VERSION = 2


class PanelBaseline:
    def __init__(self, panels: int, data: dict | None = None) -> None:
        self.panels = panels
        self.reset()
        self.migrated = False
        if not data:
            return
        daily = data.get("daily")
        if daily and isinstance(daily.get("share"), list) and len(daily["share"]) == panels:
            self.daily = daily
        best = data.get("best")
        if best and isinstance(best.get("ref"), list) and len(best["ref"]) == panels:
            self.best = {**self.best, **best}
        today = data.get("today")
        if today and today.get("day") and today.get("slots") is not None:
            self.today = {"day": today["day"], "slots": {int(k): v for k, v in today["slots"].items()}}
        if data.get("v") == VERSION and isinstance(data.get("slots"), list) and len(data["slots"]) == SLOTS:
            self.slots = data["slots"]
            self.since = data.get("since")
        elif data.get("v") == 1:
            self.migrated = True

    def reset(self) -> None:
        self.slots: list[dict | None] = [None] * SLOTS
        self.daily = {"share": [1.0] * self.panels, "days": 0}
        self.best = {
            "ref": [None] * self.panels,
            "spread": [0.0] * self.panels,
            "days": 0,
            "flagged": [0] * self.panels,
            "last": None,
        }
        self.today: dict[str, Any] = {"day": None, "slots": {}}
        self.since: str | None = None

    @staticmethod
    def slot_of(ts: float) -> int:
        """Slot index (0..95) in solar time (UTC) for a POSIX timestamp in seconds."""
        minutes = int(ts // 60)
        return ((minutes % 1440) + 1440) % 1440 // SLOT_MIN

    @staticmethod
    def shares(powers: list[float]) -> list[float] | None:
        mean = sum(powers) / len(powers)
        return [p / mean for p in powers] if mean > 0 else None

    @staticmethod
    def shares_of_best(powers: list[float]) -> list[float] | None:
        best = max(powers)
        return [p / best for p in powers] if best > 0 else None

    def sunny(self, slot: int, powers: list[float]) -> bool:
        e = self.slots[slot]
        best = max(powers)
        return not e or not e.get("peak") or best >= SUNNY_RATIO * e["peak"]

    def learn(self, slot: int, powers: list[float], day: str, skip: list[bool] | None = None) -> bool:
        skip = skip or [False] * self.panels
        sh = self.shares_of_best(powers)
        if not sh:
            return False
        best = max(powers)
        e = self.slots[slot]
        if e:
            if e.get("peakDay") != day and e.get("peak"):
                e["peak"] *= PEAK_DECAY
                e["peakDay"] = day
            if e.get("peak") and best > PEAK_JUMP * e["peak"]:
                e = None
        if not e:
            e = {"r": [None if skip[i] else x for i, x in enumerate(sh)], "n": 0, "days": 0, "last": None, "peak": best, "peakDay": day}
            self.slots[slot] = e
        if best > (e.get("peak") or 0):
            e["peak"] = best
        if best < SUNNY_RATIO * e["peak"]:
            return False
        self._accumulate(slot, sh, day)
        e["n"] += 1
        alpha = max(1 / e["n"], ALPHA_MIN)
        for i in range(self.panels):
            if skip[i]:
                continue
            if e["r"][i] is None:
                e["r"][i] = sh[i]
            else:
                e["r"][i] += alpha * (sh[i] - e["r"][i])
        if e["last"] != day:
            e["days"] += 1
            e["last"] = day
        if not self.since:
            self.since = day
        return True

    def _accumulate(self, slot: int, sh: list[float], day: str) -> None:
        if self.today["day"] != day:
            self.today = {"day": day, "slots": {}}
        t = self.today["slots"].get(slot) or {"n": 0, "sum": [0.0] * self.panels}
        t["n"] += 1
        for i in range(self.panels):
            t["sum"][i] += sh[i]
        self.today["slots"][slot] = t

    def best_of_day(self, day: str) -> list[float] | None:
        if self.today["day"] != day:
            return None
        slots = [t for t in self.today["slots"].values() if t["n"] >= VERDICT_MIN_SAMPLES]
        if len(slots) < VERDICT_MIN_SLOTS:
            return None
        return [max(t["sum"][i] / t["n"] for t in slots) for i in range(self.panels)]

    def verdict(self, day: str, threshold_pct: float) -> dict[str, Any]:
        b = self.best
        if b["last"] == day:
            return {"done": True, "sunny": False, "ready": False, "panels": []}
        best = self.best_of_day(day)
        if not best:
            return {"done": False, "sunny": False, "ready": False, "panels": []}
        b["last"] = day
        ready = b["days"] >= BEST_MIN_DAYS
        b["days"] += 1
        alpha = max(1 / b["days"], BEST_ALPHA_MIN)
        panels = []
        for i, v in enumerate(best):
            ref = b["ref"][i]
            dev = ((v - ref) / ref) * 100 if ref else None
            threshold = max(threshold_pct, SPREAD_FACTOR * b["spread"][i])
            r = {"best": v, "ref": ref, "dev": dev, "threshold": threshold, "state": "ok" if ready else "learning"}

            def learn(i=i, v=v, ref=ref, dev=dev) -> None:
                if ref is None:
                    b["ref"][i] = v
                    return
                b["spread"][i] += alpha * (abs(dev) - b["spread"][i])
                b["ref"][i] += alpha * (v - ref)

            if not ready or dev is None:
                learn()
            elif dev <= -threshold:
                b["flagged"][i] += 1
                if b["flagged"][i] >= ACCEPT_DAYS:
                    b["ref"][i] = v
                    b["flagged"][i] = 0
                    r["state"] = "accepted"
                else:
                    r["state"] = "new" if b["flagged"][i] == 1 else "ongoing"
            elif b["flagged"][i] > 0:
                if dev > -threshold / 2:
                    b["flagged"][i] = 0
                    r["state"] = "recovered"
                    learn()
                else:
                    r["state"] = "ongoing"
            else:
                learn()
            panels.append(r)
        return {"done": False, "sunny": True, "ready": ready, "panels": panels}

    def ready(self, slot: int) -> bool:
        e = self.slots[slot]
        return bool(e) and e["days"] >= MIN_DAYS

    def expected(self, slot: int, powers: list[float] | None = None) -> list[float | None] | None:
        if powers is not None and not self.sunny(slot, powers):
            return None
        near = [(slot + k + SLOTS) % SLOTS for k in range(-TOLERANCE_SLOTS, TOLERANCE_SLOTS + 1)]
        near = [s for s in near if self.ready(s)]
        if not near:
            return None
        out: list[float | None] = []
        for i in range(self.panels):
            vals = [self.slots[s]["r"][i] for s in near if self.slots[s]["r"][i] is not None]
            out.append(min(vals) if vals else None)
        return out

    def check_day(self, energies: list[float], threshold_pct: float) -> dict[str, Any]:
        sh = self.shares(energies)
        d = self.daily
        ready = d["days"] >= DAILY_MIN_DAYS
        expected_share = list(d["share"])
        deviations = (
            [((s - expected_share[i]) / expected_share[i]) * 100 if ready and expected_share[i] > 0 else None for i, s in enumerate(sh)]
            if sh
            else []
        )
        learned = [False] * self.panels
        if not sh:
            return {"ready": ready, "deviations": deviations, "expected_share": expected_share, "learned": learned}
        d["days"] += 1
        alpha = max(1 / d["days"], DAILY_ALPHA_MIN)
        for i in range(self.panels):
            anomalous = deviations[i] is not None and deviations[i] <= -threshold_pct
            if not anomalous:
                d["share"][i] += alpha * (sh[i] - d["share"][i])
                learned[i] = True
        return {"ready": ready, "deviations": deviations, "expected_share": expected_share, "learned": learned}

    def summary(self, offset_min: int = 0) -> dict[str, Any]:
        hours = []
        for h in range(24):
            first = round((((h * 60 - offset_min) % 1440) + 1440) % 1440 / SLOT_MIN)
            es = [self.slots[(first + k) % SLOTS] for k in range(4)]
            es = [e for e in es if e]
            if not es:
                continue
            ratios = []
            for i in range(self.panels):
                vals = [e["r"][i] for e in es if e["r"][i] is not None]
                ratios.append(round(100 * sum(vals) / len(vals)) if vals else None)
            hours.append({"hour": h, "ratios": ratios, "ready": all(e["days"] >= MIN_DAYS for e in es), "days": min(e["days"] for e in es)})
        b = self.best
        return {
            "since": self.since,
            "learned_slots": sum(1 for e in self.slots if e),
            "ready_slots": sum(1 for e in self.slots if e and e["days"] >= MIN_DAYS),
            "hours": hours,
            "daily": {"days": self.daily["days"], "ready": self.daily["days"] >= DAILY_MIN_DAYS, "share": [round(s * 100) for s in self.daily["share"]]},
            "best": {
                "days": b["days"],
                "ready": b["days"] >= BEST_MIN_DAYS,
                "ref": [None if x is None else round(x * 100) for x in b["ref"]],
                "spread": [round(x, 1) for x in b["spread"]],
                "flagged": list(b["flagged"]),
            },
        }

    def to_json(self) -> dict[str, Any]:
        r4 = lambda x: round(x, 4)  # noqa: E731
        slots = []
        for e in self.slots:
            if not e:
                slots.append(None)
                continue
            c = copy.deepcopy(e)
            c["r"] = [None if x is None else r4(x) for x in c["r"]]
            if c.get("peak"):
                c["peak"] = round(c["peak"], 1)
            slots.append(c)
        best = copy.deepcopy(self.best)
        best["ref"] = [None if x is None else r4(x) for x in best["ref"]]
        best["spread"] = [r4(x) for x in best["spread"]]
        return {
            "v": VERSION,
            "since": self.since,
            "slots": slots,
            "daily": {"share": [r4(x) for x in self.daily["share"]], "days": self.daily["days"]},
            "best": best,
            "today": {
                "day": self.today["day"],
                "slots": {str(k): {"n": t["n"], "sum": [r4(x) for x in t["sum"]]} for k, t in self.today["slots"].items()},
            },
        }


def utc_offset_min(dt: datetime) -> int:
    off = dt.utcoffset()
    return int(off.total_seconds() // 60) if off else 0
