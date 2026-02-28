"""
In-memory log buffer + logging Handler.

  - Keeps last N log lines in a deque (backlog for new UI connections).
  - Broadcasts each new log line to all active WebSocket subscribers.
  - Thread-safe via asyncio.Queue per subscriber.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone


class _LogEntry:
    __slots__ = ("ts", "level", "name", "message", "formatted")

    def __init__(self, record: logging.LogRecord, formatted: str) -> None:
        self.ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        self.level = record.levelname
        self.name = record.name
        self.message = record.getMessage()
        self.formatted = formatted

    def as_json(self) -> str:
        import json
        return json.dumps({
            "ts": self.ts,
            "level": self.level,
            "logger": self.name,
            "msg": self.message,
        })


class LogBuffer:
    """Singleton-ish log buffer attached to the root logger."""

    def __init__(self, maxlen: int = 1000) -> None:
        self._buf: deque[_LogEntry] = deque(maxlen=maxlen)
        self._subscribers: list[asyncio.Queue[str]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit(self, record: logging.LogRecord, formatted: str) -> None:
        entry = _LogEntry(record, formatted)
        self._buf.append(entry)
        if self._loop and self._loop.is_running():
            for q in list(self._subscribers):
                try:
                    self._loop.call_soon_threadsafe(q.put_nowait, entry.as_json())
                except asyncio.QueueFull:
                    pass  # slow consumer — drop

    def backlog(self) -> list[str]:
        return [e.as_json() for e in self._buf]

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass


class BufferHandler(logging.Handler):
    """logging.Handler that feeds into LogBuffer."""

    def __init__(self, buf: LogBuffer) -> None:
        super().__init__()
        self._buf = buf

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            self._buf.emit(record, formatted)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def setup_log_buffer(maxlen: int = 1000) -> LogBuffer:
    """Attach BufferHandler to the root logger and return the buffer."""
    buf = LogBuffer(maxlen=maxlen)
    handler = BufferHandler(buf)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
    )
    logging.getLogger().addHandler(handler)
    return buf
