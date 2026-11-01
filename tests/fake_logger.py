"""Fake Solarman V5 logger (asyncio TCP server) answering Modbus reads from a register map."""

from __future__ import annotations

import asyncio
import struct

try:
    from tsun.solarmanv5 import crc16modbus, v5_checksum
except ImportError:  # Home Assistant tests
    from custom_components.tsun_microinverters.solarmanv5 import crc16modbus, v5_checksum


def response_frame(serial: int, seq: int, modbus: bytes) -> bytes:
    payload = bytes([0x02, 0x01]) + bytes(12) + modbus
    header = struct.pack("<BHHBBI", 0xA5, len(payload), 0x1510, seq, 0, serial)
    frame = bytearray(header + payload + bytes([0, 0x15]))
    frame[-2] = v5_checksum(frame)
    return bytes(frame)


def modbus_answer(slave: int, values: list[int]) -> bytes:
    body = struct.pack(">BBB", slave, 0x03, 2 * len(values)) + b"".join(struct.pack(">H", v & 0xFFFF) for v in values)
    return body + struct.pack("<H", crc16modbus(body))


class FakeLogger:
    def __init__(self, serial: int, registers: dict[int, int]) -> None:
        self.serial = serial
        self.registers = registers
        self.mode = "ok"  # ok | silent | corrupt_once | wrong_serial | close
        self.requests = 0
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readexactly(3)
                length = struct.unpack_from("<H", head, 1)[0]
                rest = await reader.readexactly(length + 10)
                frame = head + rest
                self.requests += 1
                seq = frame[5]
                modbus = frame[26:-2]
                slave, _fn, addr, qty = struct.unpack(">BBHH", modbus[:6])
                if self.mode == "silent":
                    continue
                if self.mode == "close":
                    writer.close()
                    return
                values = [self.registers.get(addr + i, 0) for i in range(qty)]
                ans = modbus_answer(slave, values)
                if self.mode == "corrupt_once":
                    self.mode = "ok"
                    ans = ans[:-1] + bytes([ans[-1] ^ 0xFF])
                serial = self.serial + 1 if self.mode == "wrong_serial" else self.serial
                writer.write(response_frame(serial, seq, ans))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


def registers(power_w: float = 1500, panels: list[float] | None = None, today_kwh: float = 3.21, total_kwh: float = 1234.56,
              temp_c: int = 45, voltage: float = 236.4, status: int = 1, alarms: int = 0, faults: int = 0,
              panel_today: list[float] | None = None, panel_voltage: list[float] | None = None) -> dict[int, int]:
    panels = panels if panels is not None else [power_w / 6 * 1.04] * 6
    panel_today = panel_today if panel_today is not None else [today_kwh / 6] * 6
    panel_voltage = panel_voltage if panel_voltage is not None else [38.0] * 6
    r: dict[int, int] = {}
    sn = b"TSN1234567890XYZ"
    for i in range(8):
        r[0x1000 + i] = (sn[2 * i] << 8) | sn[2 * i + 1]
    r[0x1100] = status
    r[0x1105] = alarms
    r[0x1106] = faults
    r[0x1200] = round(voltage * 10)
    r[0x1201] = round(power_w / voltage * 100)
    r[0x1202] = round(power_w * 10)
    r[0x1209] = 5000
    r[0x1210] = 3000
    r[0x1212] = round(today_kwh * 100)
    total = round(total_kwh * 100)
    r[0x1213], r[0x1214] = total >> 16, total & 0xFFFF
    r[0x1218] = temp_c + 40
    for n in range(6):
        b = 0x1302 + n * 7
        v = panel_voltage[n]
        r[b] = round(v * 10)
        r[b + 1] = round(panels[n] / v * 100) if v else 0
        r[b + 2] = round(panels[n] * 10)
        e = round(panel_today[n] * 100)
        r[b + 3], r[b + 4] = e >> 16, e & 0xFFFF
        t = round(total_kwh / 6 * 100)
        r[b + 5], r[b + 6] = t >> 16, t & 0xFFFF
    return r
