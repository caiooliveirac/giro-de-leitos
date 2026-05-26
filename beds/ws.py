"""WebSocket connection manager scoped by unit_id.

A single ``UnitConnectionManager`` instance (``unit_manager``) is shared by
all callers, registered in the FastAPI app at startup. Each socket is bound
to one unit; broadcasts only reach sockets connected to the same unit.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class UnitConnectionManager:
    def __init__(self) -> None:
        # websocket -> unit_id
        self._sockets: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, unit_id: str) -> None:
        """Register an already-accepted socket against a unit."""
        self._sockets[websocket] = unit_id

    def disconnect(self, websocket: WebSocket) -> None:
        self._sockets.pop(websocket, None)

    async def broadcast(self, unit_id: str, event_type: str, payload: Any) -> None:
        """Send ``{type, payload}`` to every socket on ``unit_id``."""
        message = {"type": event_type, "payload": payload}
        stale: list[WebSocket] = []
        for ws, uid in list(self._sockets.items()):
            if uid != unit_id:
                continue
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


# Singleton
unit_manager = UnitConnectionManager()


__all__ = ["UnitConnectionManager", "unit_manager"]
