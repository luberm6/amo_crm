from __future__ import annotations

import asyncio
import errno
import socket
from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class EslFrame:
    headers: dict[str, str]
    body: str = ""


class FreeSwitchEslClient:
    """
    Minimal FreeSWITCH ESL inbound client (plain event socket protocol).
    """

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        *,
        connect_timeout: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._connect_timeout = connect_timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        dns_result = await self._resolve_target()
        log.info(
            "freeswitch_esl.connect_attempt",
            host=self._host,
            port=self._port,
            dns=dns_result,
            target=f"{self._host}:{self._port}",
        )
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._connect_timeout,
            )
            greeting = await self.read_frame()
            if greeting.headers.get("Content-Type") != "auth/request":
                raise RuntimeError(f"Unexpected ESL greeting: {greeting.headers}")
            await self.send_raw(f"auth {self._password}\n\n")
            auth_reply = await self.read_frame()
            reply = auth_reply.headers.get("Reply-Text", "")
            if not reply.startswith("+OK"):
                raise RuntimeError(f"ESL auth failed: {reply}")
            log.info(
                "freeswitch_esl.connected",
                host=self._host,
                port=self._port,
                dns=dns_result,
                target=f"{self._host}:{self._port}",
            )
        except Exception as exc:
            log.error(
                "freeswitch_esl.connect_failed",
                host=self._host,
                port=self._port,
                dns=dns_result,
                target=f"{self._host}:{self._port}",
                error=str(exc),
                kind=self._classify_error(exc),
            )
            raise

    @property
    def connected(self) -> bool:
        return self._reader is not None and self._writer is not None

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def send_raw(self, text: str) -> None:
        if self._writer is None:
            raise RuntimeError("ESL not connected")
        async with self._write_lock:
            self._writer.write(text.encode("utf-8"))
            await self._writer.drain()

    async def send_api(self, command: str, background: bool = True) -> str:
        prefix = "bgapi" if background else "api"
        await self.send_raw(f"{prefix} {command}\n\n")
        frame = await self.read_frame()
        reply = frame.headers.get("Reply-Text", "")
        if reply and not reply.startswith("+OK"):
            raise RuntimeError(f"ESL command rejected: {reply}")
        if frame.body:
            return frame.body.strip()
        return reply.strip()

    async def send_bgapi_nowait(self, command: str) -> None:
        """
        Fire-and-forget bgapi command.
        Use when a dedicated event reader task owns read_frame() and
        command replies are not required synchronously.
        """
        await self.send_raw(f"bgapi {command}\n\n")

    async def subscribe_events(self, events: str = "CHANNEL_HANGUP_COMPLETE CUSTOM HEARTBEAT") -> None:
        await self.send_raw(f"event plain {events}\n\n")
        frame = await self.read_frame()
        reply = frame.headers.get("Reply-Text", "")
        if reply and not reply.startswith("+OK"):
            raise RuntimeError(f"ESL event subscribe rejected: {reply}")

    async def _resolve_target(self) -> dict[str, object]:
        loop = asyncio.get_running_loop()
        try:
            addrinfo = await asyncio.wait_for(
                loop.getaddrinfo(self._host, self._port, type=socket.SOCK_STREAM),
                timeout=self._connect_timeout,
            )
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "host": self._host,
                "port": self._port,
            }
        except socket.gaierror as exc:
            return {
                "status": "dns_failure",
                "host": self._host,
                "port": self._port,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "status": "error",
                "host": self._host,
                "port": self._port,
                "error": str(exc),
            }

        targets: list[str] = []
        for item in addrinfo:
            sockaddr = item[4]
            if isinstance(sockaddr, tuple) and len(sockaddr) >= 2:
                targets.append(f"{sockaddr[0]}:{sockaddr[1]}")
        return {
            "status": "resolved",
            "host": self._host,
            "port": self._port,
            "targets": targets,
        }

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if isinstance(exc, socket.gaierror):
            return "dns_failure"
        if isinstance(exc, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(exc, OSError):
            if exc.errno == errno.ECONNREFUSED:
                return "connection_refused"
            if exc.errno in {errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}:
                return "timeout"
        return exc.__class__.__name__.lower()

    async def read_frame(self) -> EslFrame:
        if self._reader is None:
            raise RuntimeError("ESL not connected")
        async with self._read_lock:
            headers = await _read_headers(self._reader)
            length_raw = headers.get("Content-Length")
            body = ""
            if length_raw:
                length = int(length_raw)
                if length > 0:
                    raw = await self._reader.readexactly(length)
                    body = raw.decode("utf-8", errors="replace")
            return EslFrame(headers=headers, body=body)


async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
    lines: list[str] = []
    while True:
        line = await reader.readline()
        if not line:
            break
        s = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if s == "":
            break
        lines.append(s)
    headers: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers
