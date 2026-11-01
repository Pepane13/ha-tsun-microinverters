"""Register map of TSUN 6-in-1 micro-inverters (MX3000D). Port of lib/mx3000d.js.

Based on the tsun-gen3-proxy wiki, validated on a real installation (32-bit values: high word first).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .solarmanv5 import SolarmanV5Client, SolarmanV5Error

# Errors caused by a collision on the logger-inverter serial link (the logger polls the inverter
# for the TSUN cloud at the same time): retried after a short pause on the same connection.
TRANSIENT_CODES = ("ECRC", "ECHECKSUM", "EFRAMETYPE", "EV5", "ETIMEDOUT")
RETRY_DELAY_S = 1.5
MAX_RETRIES = 2


def u32(hi: int, lo: int) -> int:
    return hi * 65536 + lo


def rnd(v: float, d: int = 2) -> float:
    return round(v, d)


def decode_serial(regs: list[int]) -> str:
    s = ""
    for r in regs:
        for c in (r >> 8, r & 0xFF):
            if 32 <= c < 127:
                s += chr(c)
    return s.strip()


async def read_identity(client: SolarmanV5Client) -> dict[str, str]:
    return {"inverter_serial": decode_serial(await client.read_holding_registers(0x1000, 8))}


async def read_with_retry(
    client: SolarmanV5Client,
    address: int,
    quantity: int,
    on_retry: Callable[[SolarmanV5Error, int], None] | None = None,
    retry_delay: float = RETRY_DELAY_S,
) -> list[int]:
    attempt = 0
    while True:
        try:
            return await client.read_holding_registers(address, quantity)
        except SolarmanV5Error as err:
            retries = 1 if err.code == "ETIMEDOUT" else MAX_RETRIES  # a timeout already took 8 s
            if attempt >= retries or err.code not in TRANSIENT_CODES or not client.connected:
                raise
            attempt += 1
            if on_retry:
                on_retry(err, attempt)
            await asyncio.sleep(retry_delay)


async def read_all(
    client: SolarmanV5Client,
    temp_offset: int = 40,
    on_retry: Callable[[SolarmanV5Error, int], None] | None = None,
    retry_delay: float = RETRY_DELAY_S,
) -> dict[str, Any]:
    st = await read_with_retry(client, 0x1100, 7, on_retry, retry_delay)
    g = await read_with_retry(client, 0x1200, 0x19, on_retry, retry_delay)
    pv = await read_with_retry(client, 0x1302, 42, on_retry, retry_delay)
    panels = []
    for n in range(6):
        b = n * 7
        panels.append(
            {
                "voltage": rnd(pv[b] * 0.1, 1),
                "current": rnd(pv[b + 1] * 0.01),
                "power": rnd(pv[b + 2] * 0.1, 1),
                "energy_today": rnd(u32(pv[b + 3], pv[b + 4]) * 0.01),
                "energy_total": rnd(u32(pv[b + 5], pv[b + 6]) * 0.01),
            }
        )
    return {
        "status": st[0],
        "alarms": st[5],
        "faults": st[6],
        "voltage": rnd(g[0x00] * 0.1, 1),
        "current": rnd(g[0x01] * 0.01),
        "power": rnd(g[0x02] * 0.1, 1),
        "frequency": rnd(g[0x09] * 0.01),
        "rated_power": g[0x10],
        "energy_today": rnd(g[0x12] * 0.01),
        "energy_total": rnd(u32(g[0x13], g[0x14]) * 0.01),
        "temperature": g[0x18] - temp_offset,
        "panels": panels,
    }
