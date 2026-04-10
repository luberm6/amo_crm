from __future__ import annotations

import uuid
from typing import Optional

from app.integrations.browser.audio_bridge import BrowserAudioBridge


class BrowserSessionRegistry:
    def __init__(self) -> None:
        self._bridges: dict[uuid.UUID, BrowserAudioBridge] = {}

    def ensure_bridge(self, call_id: uuid.UUID) -> BrowserAudioBridge:
        bridge = self._bridges.get(call_id)
        if bridge is None:
            bridge = BrowserAudioBridge(call_id=call_id)
            self._bridges[call_id] = bridge
        return bridge

    def get_bridge(self, call_id: uuid.UUID) -> Optional[BrowserAudioBridge]:
        return self._bridges.get(call_id)

    def get_bridge_by_token(
        self,
        call_id: uuid.UUID,
        token: str,
    ) -> Optional[BrowserAudioBridge]:
        bridge = self._bridges.get(call_id)
        if bridge is None or bridge.token != token:
            return None
        return bridge

    def remove_bridge(self, call_id: uuid.UUID) -> None:
        self._bridges.pop(call_id, None)
