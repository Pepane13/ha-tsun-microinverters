"""Minimal asyncio Solarman V5 client (LSW-3/LSW-5 loggers, TCP port 8899).

Wraps Modbus RTU frames. Read-only: function 0x03 only. Frame format aligned with pysolarmanv5.
Port of lib/solarmanv5.js from the Homey app.
"""

from __future__ import annotations

import asyncio
import random
import struct

V5_START = 0xA5
V5_END = 0x15
CTRL_REQUEST = 0x4510
CTRL_RESPONSE = 0x1510
CTRL_COUNTER = 0x4710  # "counter"/heartbeat frame sent by some loggers, ignored

V5_ERRORS = {0x05: "AcknowledgeError", 0x06: "SlaveBusyError"}


class SolarmanV5Error(Exception):
    """Communication error. `code` mirrors the Node.js error codes of the Homey app."""

    def __init__(self, message: str, code: str, phase: str = "read") -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase


def crc16modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def v5_checksum(frame: bytes) -> int:
    return sum(frame[1:-2]) & 0xFF


def build_modbus_read(slave_id: int, address: int, quantity: int) -> bytes:
    pdu = struct.pack(">BBHH", slave_id, 0x03, address, quantity)
    return pdu + struct.pack("<H", crc16modbus(pdu))


def build_v5_frame(logger_serial: int, sequence: int, modbus_frame: bytes) -> bytes:
    header = struct.pack("<BHHHI", V5_START, 15 + len(modbus_frame), CTRL_REQUEST, sequence & 0xFF, logger_serial & 0xFFFFFFFF)
    payload_header = bytes([0x02]) + bytes(14)  # frame type 0x02, sensor type (2), 3 x time (4)
    frame = bytearray(header + payload_header + modbus_frame + bytes([0x00, V5_END]))
    frame[-2] = v5_checksum(frame)
    return bytes(frame)


class SolarmanV5Client:
    """One TCP connection to the logger; one request at a time."""

    def __init__(self, host: str, serial: int, port: int = 8899, slave_id: int = 1, timeout: float = 8.0) -> None:
        self.host = host
        self.serial = int(serial)
        self.port = port
        self.slave_id = slave_id
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = b""
        self.sequence = random.randint(1, 0xFE)

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except asyncio.TimeoutError as err:
            raise SolarmanV5Error(f"Connection to {self.host}:{self.port} timed out", "ETIMEDOUT", "connect") from err
        except ConnectionRefusedError as err:
            raise SolarmanV5Error(str(err), "ECONNREFUSED", "connect") from err
        except OSError as err:
            code = {113: "EHOSTUNREACH", 101: "ENETUNREACH", 112: "EHOSTDOWN", 104: "ECONNRESET"}.get(err.errno or 0, "EOTHER")
            raise SolarmanV5Error(str(err), code, "connect") from err

    async def close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except Exception:  # noqa: BLE001 - closing best effort
                pass

    async def _read_frame(self, deadline: float) -> bytes:
        """Returns the next complete V5 frame from the stream."""
        loop = asyncio.get_running_loop()
        while True:
            start = self._buffer.find(bytes([V5_START]))
            if start < 0:
                self._buffer = b""
            elif start > 0:
                self._buffer = self._buffer[start:]
            if len(self._buffer) >= 3:
                length = 13 + struct.unpack_from("<H", self._buffer, 1)[0]
                if len(self._buffer) >= length:
                    frame, self._buffer = self._buffer[:length], self._buffer[length:]
                    return frame
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            assert self._reader is not None
            chunk = await asyncio.wait_for(self._reader.read(1024), timeout=remaining)
            if not chunk:
                self._writer = None
                raise SolarmanV5Error("Connection closed by the logger", "ECLOSED")
            self._buffer += chunk

    def _parse_modbus(self, m: bytes) -> list[int]:
        if len(m) < 5:
            name = V5_ERRORS.get(m[0] if m else 0, f"V5 error 0x{(m[0] if m else 0):x}")
            raise SolarmanV5Error(f"V5 Modbus EXCEPTION: {name}", "EV5")
        if m[1] & 0x80:
            raise SolarmanV5Error(f"Modbus exception 0x{m[2]:x}", "EMODBUS")
        crc = struct.unpack_from("<H", m, len(m) - 2)[0]
        if crc != crc16modbus(m[:-2]):
            raise SolarmanV5Error("Invalid Modbus CRC", "ECRC")
        count = m[2]
        return [struct.unpack_from(">H", m, 3 + i)[0] for i in range(0, count, 2)]

    async def read_holding_registers(self, address: int, quantity: int) -> list[int]:
        if self._writer is None:
            raise SolarmanV5Error("Not connected", "ENOTCONN")
        self.sequence = (self.sequence + 1) & 0xFF
        seq = self.sequence
        frame = build_v5_frame(self.serial, seq, build_modbus_read(self.slave_id, address, quantity))
        try:
            self._writer.write(frame)
            await self._writer.drain()
        except OSError as err:
            self._writer = None
            raise SolarmanV5Error(str(err), "ECONNRESET") from err
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        try:
            while True:
                f = await self._read_frame(deadline)
                if f[-1] != V5_END:
                    continue
                ctrl = struct.unpack_from("<H", f, 3)[0]
                if ctrl == CTRL_COUNTER:
                    continue
                if f[-2] != v5_checksum(f):
                    raise SolarmanV5Error("Invalid V5 checksum", "ECHECKSUM")
                if ctrl != CTRL_RESPONSE or f[5] != seq:
                    continue  # other frame type or response to another request
                if struct.unpack_from("<I", f, 7)[0] != (self.serial & 0xFFFFFFFF):
                    raise SolarmanV5Error("Wrong logger serial number in response", "ESERIAL")
                if f[11] != 0x02:
                    raise SolarmanV5Error("Invalid V5 frame type", "EFRAMETYPE")
                return self._parse_modbus(f[25:-2])
        except asyncio.TimeoutError as err:
            raise SolarmanV5Error(f"No response for 0x{address:x}", "ETIMEDOUT") from err
        except OSError as err:
            self._writer = None
            raise SolarmanV5Error(str(err), "ECONNRESET") from err
