"""Fullscreen single-camera view (double-click again or Esc to return).

The fullscreen view automatically switches the camera to its **main**
(high-resolution) stream; returning to the grid switches back to the
sub-stream.  A PTZ control pad sits in the bottom-right corner.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from ..config import CameraConfig, effective_camera
from ..core.streamer import StreamManager
from .ptz_pad import PTZPad
from .video_tile import VideoTile


class FullscreenWindow(QWidget):
    """Shows one camera fullscreen using its high-resolution stream."""

    exit_requested = Signal()

    def __init__(self, settings, manager: StreamManager, camera: CameraConfig,
                 owner=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._manager = manager
        self._camera = camera
        self._owner = owner          # MainWindow — routes tile menu actions
        self._key = f"{camera.id}:full"

        # grid stacking: video fills the cell, hint and PTZ pad overlay it
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tile = VideoTile(0, fullscreen=True)
        self.tile.bind(camera)
        self.tile.set_quality("MAIN")
        layout.addWidget(self.tile, 0, 0)

        hint = QLabel("Double-click or press Esc to return to the grid", self)
        hint.setObjectName("fullscreenHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint, 0, 0, Qt.AlignBottom | Qt.AlignHCenter)

        self.pad = PTZPad(effective_camera(camera, settings.network_mode))
        layout.addWidget(self.pad, 0, 0, Qt.AlignBottom | Qt.AlignRight)

        self.tile.double_clicked.connect(lambda _cid: self.exit_requested.emit())
        self._connect_menu_signals()

        thread, buffer = manager.start(camera, key=self._key, high_res=True)
        self._thread = thread
        self._buffer = buffer
        self.tile.set_buffer(buffer)
        self.tile.set_status("connecting", "Starting fullscreen stream…")
        thread.status.connect(self._on_status)

        # the grid tiles are pumped from MainWindow's refresh timer; the
        # fullscreen tile is not in that list, so it gets its own pump
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(33)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start()

    def _refresh(self) -> None:
        record = self._manager.get(self._key)
        stats = record["thread"].stats.snapshot() if record else None
        self.tile.refresh(stats)

    def _connect_menu_signals(self) -> None:
        if self._owner is None:
            return
        self.tile.edit_requested.connect(self._owner._edit_camera)
        self.tile.remove_requested.connect(self._owner._remove_camera)
        self.tile.snapshot_requested.connect(self._owner._snapshot_camera)
        self.tile.record_requested.connect(self._owner._toggle_recording)
        self.tile.restart_requested.connect(self._restart_stream)

    def _on_status(self, cam_id: str, state: str, detail: str) -> None:
        if cam_id == self._camera.id:
            self.tile.set_status(state, detail)

    def _restart_stream(self, _cam_id: str) -> None:
        thread, buffer = self._manager.restart(
            self._camera, key=self._key, high_res=True)
        self._thread = thread
        self._buffer = buffer
        self.tile.set_buffer(buffer)
        self.tile.set_status("connecting", "Restarting…")
        thread.status.connect(self._on_status)

    def shutdown(self) -> None:
        self.pad.shutdown()
        self._manager.stop(self._key)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.exit_requested.emit()
        else:
            super().keyPressEvent(event)
