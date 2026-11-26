"""Camera health monitoring.

Watches stream statistics for every camera and raises levelled alerts (toasts)
when a camera disconnects or its stream quality degrades.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from .config import AppSettings
from .core.streamer import StreamManager

# alert cooldown per (camera, kind), seconds
_COOLDOWNS = {
    "offline": 60.0,
    "low_fps": 180.0,
    "high_latency": 120.0,
    "reconnect": 120.0,
}


class HealthMonitor(QObject):
    """Periodically evaluates camera health and emits alerts."""

    alert = Signal(str, str, str)  # cam_id, level, message

    def __init__(self, settings: AppSettings, manager: StreamManager,
                 parent=None):
        super().__init__(parent)
        self._settings = settings
        self._manager = manager
        self._last_cooldown: dict[tuple[str, str], float] = {}
        self._reconnects: dict[str, int] = {}
        self._was_connected: dict[str, bool] = {}
        self._threads: dict[str, object] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _cooled(self, cam_id: str, kind: str, now: float) -> bool:
        key = (cam_id, kind)
        if now - self._last_cooldown.get(key, 0.0) < _COOLDOWNS.get(kind, 60.0):
            return False
        self._last_cooldown[key] = now
        return True

    def check(self) -> None:
        now = time.monotonic()
        items = self._manager.items()
        live = {key for key, _record in items}
        for stale in [key for key in self._threads if key not in live]:
            self._threads.pop(stale, None)
        for key, record in items:
            cam = record.get("camera")
            if cam is None:
                continue
            thread = record["thread"]
            stats = thread.stats.snapshot()
            state = stats["state"]
            # a restarted stream is a new thread with fresh counters
            if self._threads.get(key) is not thread:
                self._threads[key] = thread
                self._reconnects[key] = 0
                self._was_connected[key] = False

            if state in ("error", "reconnecting"):
                if self._was_connected.get(key, False) and \
                        self._cooled(key, "offline", now):
                    self.alert.emit(cam.id, "error",
                                    f"{cam.name} went offline — {state}")
                self._was_connected[key] = False
            elif state == "connected":
                self._was_connected[key] = True

            reconnects = int(stats.get("reconnects", 0))
            previous = self._reconnects.get(key, 0)
            if reconnects > previous:
                self._reconnects[key] = reconnects
                if previous > 0 and self._cooled(key, "reconnect", now):
                    self.alert.emit(cam.id, "warning",
                                    f"{cam.name} stream reconnected "
                                    f"({reconnects}×) — check network stability")
            elif reconnects == 0:
                self._reconnects[key] = 0

            # fps is only measured after the first full second of frames
            if state == "connected" and stats.get("measured"):
                if stats["fps"] < 1.0 and self._cooled(key, "low_fps", now):
                    self.alert.emit(cam.id, "warning",
                                    f"{cam.name} stream quality degraded "
                                    "(<1 FPS)")
                if stats["latency_ms"] > 2500.0 and \
                        self._cooled(key, "high_latency", now):
                    self.alert.emit(cam.id, "warning",
                                    f"{cam.name} high latency "
                                    f"({stats['latency_ms']:.0f} ms)")
