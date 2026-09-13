"""Manual PTZ control pad — arrow buttons for pan/tilt/zoom.

Press and hold an arrow to move the camera; release to stop.  Because ONVIF
``ContinuousMove`` carries a 1-second auto-stop timeout, a keep-alive timer
re-sends the current command while a button is held, and ``Stop`` is issued
on release.  PTZ setup runs on a daemon thread so a slow camera never blocks
the UI.
"""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ..config import CameraConfig
from ..ptz.controller import PTZController

_PAN_SPEED = 0.6
_TILT_SPEED = 0.6
_ZOOM_SPEED = 0.5
_KEEPALIVE_MS = 800  # < 1 s so the camera never hits its auto-stop

_STOP = object()  # sentinel for the sender queue


class PTZPad(QFrame):
    """Arrow pad + zoom + stop for one camera."""

    def __init__(self, camera: CameraConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("ptzPad")
        self._camera = camera
        self._ptz = PTZController(camera)
        self._ready = False
        self._command: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._moving = False

        self._keepalive = QTimer(self)
        self._keepalive.setInterval(_KEEPALIVE_MS)
        self._keepalive.timeout.connect(self._resend)

        # Commands are sent from a dedicated thread: ONVIF calls can take
        # seconds on a slow camera and must never block the UI.  The queue
        # keeps only the newest command (latest wins).
        self._queue: queue.Queue = queue.Queue()
        self._sender = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender.start()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        title = QLabel(f"PTZ — {camera.name}")
        title.setObjectName("ptzTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(4)
        self.btn_up = self._button("▲", grid, 0, 1)
        self.btn_left = self._button("◀", grid, 1, 0)
        self.btn_stop = self._button("■", grid, 1, 1)
        self.btn_right = self._button("▶", grid, 1, 2)
        self.btn_down = self._button("▼", grid, 2, 1)
        layout.addLayout(grid)

        zoom_row = QGridLayout()
        self.btn_zoom_in = self._button("+", zoom_row, 0, 0)
        self.btn_zoom_out = self._button("−", zoom_row, 0, 1)
        layout.addLayout(zoom_row)

        self.status_lbl = QLabel("Connecting to PTZ service…")
        self.status_lbl.setObjectName("ptzStatus")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_lbl)

        self._wire(self.btn_up, 0.0, _TILT_SPEED, 0.0)
        self._wire(self.btn_down, 0.0, -_TILT_SPEED, 0.0)
        self._wire(self.btn_left, -_PAN_SPEED, 0.0, 0.0)
        self._wire(self.btn_right, _PAN_SPEED, 0.0, 0.0)
        self._wire(self.btn_zoom_in, 0.0, 0.0, _ZOOM_SPEED)
        self._wire(self.btn_zoom_out, 0.0, 0.0, -_ZOOM_SPEED)
        self.btn_stop.pressed.connect(self.stop)
        self.btn_stop.setToolTip("Stop all movement")

        self.set_ready(False, "Connecting to PTZ service…")

        # ONVIF setup on a daemon thread — never blocks the UI.  The result
        # is written into a plain dict and picked up by a QTimer poll on the
        # GUI thread: emitting Qt signals from the setup thread would crash
        # if the pad is destroyed before setup completes.
        self._setup_state = {"done": False, "ok": False, "detail": ""}
        self._setup_timer = QTimer(self)
        self._setup_timer.setInterval(150)
        self._setup_timer.timeout.connect(self._poll_setup)
        self._setup_timer.start()

        def setup():
            ok = self._ptz.setup(timeout=8.0)
            self._setup_state.update(
                done=True, ok=ok,
                detail="" if ok else self._ptz.last_error)

        threading.Thread(target=setup, daemon=True).start()

    def _poll_setup(self) -> None:
        if not self._setup_state["done"]:
            return
        self._setup_timer.stop()
        self.set_ready(self._setup_state["ok"], self._setup_state["detail"])

    def _button(self, text: str, layout: QGridLayout, row: int, col: int):
        btn = QPushButton(text)
        btn.setObjectName("ptzBtn")
        btn.setFixedSize(46, 42)
        btn.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(btn, row, col)
        return btn

    def _wire(self, btn: QPushButton, pan: float, tilt: float,
              zoom: float) -> None:
        btn.pressed.connect(
            lambda p=pan, t=tilt, z=zoom: self.move(p, t, z))
        btn.released.connect(self.release_move)

    # ------------------------------------------------------------ commands

    def _sender_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if item is _STOP:
                try:
                    self._ptz.stop()
                except Exception:
                    pass
                continue
            pan, tilt, zoom = item
            try:
                self._ptz.continuous_move(pan, tilt, zoom)
            except Exception:
                pass

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _send(self, pan: float, tilt: float, zoom: float) -> None:
        self._drain()  # latest command wins
        self._queue.put((float(pan), float(tilt), float(zoom)))

    def _send_stop(self) -> None:
        self._drain()
        self._queue.put(_STOP)

    def set_ready(self, ok: bool, detail: str) -> None:
        self._ready = ok
        if ok:
            self.status_lbl.setText("PTZ ready")
            self.status_lbl.setStyleSheet("color: #3ddc84;")
        else:
            self.status_lbl.setText(f"PTZ unavailable — {detail}")
            self.status_lbl.setStyleSheet("color: #f87171;")
        for btn in (self.btn_up, self.btn_down, self.btn_left, self.btn_right,
                    self.btn_zoom_in, self.btn_zoom_out, self.btn_stop):
            btn.setEnabled(ok)

    def move(self, pan: float, tilt: float, zoom: float) -> None:
        if not self._ready:
            return
        self._command = (pan, tilt, zoom)
        self._moving = True
        self._send(pan, tilt, zoom)
        self._keepalive.start()

    def _resend(self) -> None:
        if self._moving and self._ready:
            pan, tilt, zoom = self._command
            self._send(pan, tilt, zoom)

    def release_move(self) -> None:
        self._moving = False
        self._keepalive.stop()
        if self._ready:
            self._send_stop()

    def stop(self) -> None:
        self._moving = False
        self._keepalive.stop()
        if self._ready:
            self._send_stop()

    def shutdown(self) -> None:
        self._setup_timer.stop()
        self.stop()


class PTZPadDialog(QDialog):
    """Floating PTZ pad opened from a tile's context menu."""

    def __init__(self, camera: CameraConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"PTZ Controls — {camera.name}")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.pad = PTZPad(camera)
        layout.addWidget(self.pad)
        self.adjustSize()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.pad.shutdown()
        super().closeEvent(event)
