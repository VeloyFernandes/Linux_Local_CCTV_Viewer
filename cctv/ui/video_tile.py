"""A single camera tile: video surface, overlays, drag&drop and context menu.

Overlays shown per tile:
- top-left: camera name (+ pulsing REC badge while recording, SUB/MAIN chip)
- top-right: colour-coded connection status with live FPS
- bottom-left: resolution / FPS / bitrate / latency / decode backend

Interactions:
- double-click  → fullscreen
- right-click   → context menu (fullscreen, PTZ controls, snapshot, record,
                  reconnect, settings, remove)
- drag & drop   → rearrange grid positions (persisted between sessions)
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QImage, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMenu, QWidget)

_MIME_TYPE = "application/x-cctv-camera"

_STATE_COLORS = {
    "connected": "#3ddc84",
    "connecting": "#fbbf24",
    "reconnecting": "#fb923c",
    "error": "#f87171",
    "stopped": "#8b8b9a",
}
_STATE_LABELS = {
    "connected": "Connected",
    "connecting": "Connecting",
    "reconnecting": "Reconnecting",
    "error": "Error",
    "stopped": "Stopped",
}


class VideoSurface(QWidget):
    """Paints the newest frame letterboxed, with a 'no signal' placeholder."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: QImage | None = None
        self._frame: np.ndarray | None = None
        self._placeholder = "NO SIGNAL"

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def clear(self) -> None:
        self._image = None
        self._frame = None
        self.update()

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._image = QImage(frame.data, frame.shape[1], frame.shape[0],
                             frame.strides[0], QImage.Format.Format_BGR888)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0d0d12"))
        if self._image is not None and not self._image.isNull():
            scaled = self._image.scaled(self.size(), Qt.KeepAspectRatio,
                                        Qt.FastTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawImage(QPoint(x, y), scaled)
        else:
            painter.setPen(QColor("#63636f"))
            font = painter.font()
            font.setPointSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder)


class VideoTile(QFrame):
    """A grid tile with video, overlays, drag&drop and interaction signals."""

    double_clicked = Signal(str)          # camera id
    add_requested = Signal(int)           # slot index
    edit_requested = Signal(str)
    remove_requested = Signal(str)
    restart_requested = Signal(str)
    fullscreen_requested = Signal(str)
    snapshot_requested = Signal(str)
    record_requested = Signal(str)
    ptz_requested = Signal(str)
    reorder_requested = Signal(str, int)  # camera id, target slot

    def __init__(self, slot_index: int, fullscreen: bool = False, parent=None):
        super().__init__(parent)
        self._slot = slot_index
        self._cam = None
        self._buffer = None
        self._last_seq = -1
        self._state = ""
        self._detail = ""
        self._fps = 0.0
        self._frames = 0
        self._fps_time = time.monotonic()
        self._recording = False
        self._quality = ""
        self._press_pos: QPoint | None = None
        self._drag_started = False

        self.setObjectName("tile")
        self.setProperty("fullscreen", "true" if fullscreen else "false")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setAcceptDrops(True)

        self._surface = VideoSurface(self)

        # top-left overlay: REC badge + name + quality chip
        self._rec_lbl = QLabel("● REC", self)
        self._rec_lbl.setObjectName("recBadge")
        self._rec_lbl.hide()
        self._name_lbl = QLabel(self)
        self._name_lbl.setObjectName("tileName")
        self._qual_lbl = QLabel(self)
        self._qual_lbl.setObjectName("qualChip")
        self._qual_lbl.hide()
        top_left = QWidget(self)
        top_left.setAttribute(Qt.WA_TransparentForMouseEvents)
        top_left.setObjectName("overlayRow")
        top_box = QHBoxLayout(top_left)
        top_box.setContentsMargins(0, 0, 0, 0)
        top_box.setSpacing(6)
        top_box.addWidget(self._rec_lbl)
        top_box.addWidget(self._name_lbl)
        top_box.addWidget(self._qual_lbl)
        top_box.addStretch(1)

        self._status_lbl = QLabel(self)
        self._status_lbl.setObjectName("tileStatus")
        self._status_lbl.setTextFormat(Qt.RichText)
        self._info_lbl = QLabel(self)
        self._info_lbl.setObjectName("tileInfo")
        self._hint_lbl = QLabel("Double-click / right-click to add a camera", self)
        self._hint_lbl.setObjectName("slotHint")
        self._hint_lbl.setAlignment(Qt.AlignCenter)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._surface, 0, 0)
        layout.addWidget(top_left, 0, 0, Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self._status_lbl, 0, 0, Qt.AlignTop | Qt.AlignRight)
        layout.addWidget(self._info_lbl, 0, 0, Qt.AlignBottom | Qt.AlignLeft)
        layout.addWidget(self._hint_lbl, 0, 0,
                         Qt.AlignBottom | Qt.AlignHCenter)

        self.customContextMenuRequested.connect(self._show_menu)

        # pulsing REC badge
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(500)
        self._rec_timer.timeout.connect(self._pulse_rec)
        self._rec_visible = False

    # ------------------------------------------------------------ properties

    @property
    def camera_id(self) -> str:
        return self._cam.id if self._cam is not None else ""

    # -------------------------------------------------------------- binding

    def bind(self, cam) -> None:
        self._cam = cam
        if cam is not None:
            self._name_lbl.setText(cam.name)
            self._hint_lbl.hide()
            self._surface.set_placeholder("NO SIGNAL")
        else:
            self._name_lbl.setText("")
            self._status_lbl.setText("")
            self._info_lbl.setText("")
            self._hint_lbl.show()
            self._surface.set_placeholder("EMPTY SLOT")
            self._qual_lbl.hide()
            self._quality = ""

    def set_buffer(self, buffer) -> None:
        self._buffer = buffer
        self._last_seq = -1
        self._surface.clear()

    def clear(self) -> None:
        self.bind(None)
        self.set_buffer(None)
        self.set_recording(False)
        self.set_quality("")
        self.set_status("", "")

    # -------------------------------------------------------------- status

    def set_status(self, state: str, detail: str = "") -> None:
        self._state = state
        self._detail = detail
        tooltip = detail or (self._cam.name if self._cam is not None else "")
        self.setToolTip(tooltip)
        self._update_status()

    def _update_status(self) -> None:
        if self._cam is None or not self._state:
            self._status_lbl.setText("")
            return
        color = _STATE_COLORS.get(self._state, "#8b8b9a")
        label = _STATE_LABELS.get(self._state, self._state)
        if self._state == "connected" and self._fps > 0:
            self._status_lbl.setText(
                f'<span style="color:{color}">●</span> {label} · {self._fps:.0f} FPS')
        else:
            self._status_lbl.setText(
                f'<span style="color:{color}">●</span> {label}')

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        if recording:
            self._rec_lbl.show()
            self._rec_timer.start()
        else:
            self._rec_lbl.hide()
            self._rec_timer.stop()

    def _pulse_rec(self) -> None:
        self._rec_visible = not self._rec_visible
        self._rec_lbl.setStyleSheet(
            "color: #ffffff; background: #e11d48;"
            if self._rec_visible
            else "color: #fda4af; background: #7f1d1d;")

    def set_quality(self, quality: str) -> None:
        """Show the stream quality chip: '' | 'SUB' | 'MAIN'."""
        self._quality = quality
        if not quality or self._cam is None:
            self._qual_lbl.hide()
            return
        color = "#60a5fa" if quality == "MAIN" else "#fbbf24"
        self._qual_lbl.setText(quality)
        self._qual_lbl.setStyleSheet(
            f"color: {color}; background: rgba(10,10,16,0.78);")
        self._qual_lbl.setToolTip(
            "Main stream (high quality)" if quality == "MAIN"
            else "Substream (low quality)")
        self._qual_lbl.show()

    # ---------------------------------------------------------- frame pump

    def refresh(self, stats: dict | None = None) -> None:
        """Pull the newest frame from the buffer (called by a UI timer)."""
        if self._buffer is None:
            return
        frame, seq = self._buffer.latest()
        now = time.monotonic()
        if frame is None or seq == self._last_seq:
            if now - self._fps_time > 1.2 and self._fps > 0:
                self._fps = 0.0
                self._frames = 0
                self._fps_time = now
                self._update_status()
            return
        self._last_seq = seq
        self._frames += 1
        self._surface.set_frame(frame)
        elapsed = now - self._fps_time
        if elapsed >= 1.0:
            self._fps = self._frames / elapsed
            self._frames = 0
            self._fps_time = now
            self._update_status()
            if stats:
                self._update_info(stats)

    def _update_info(self, stats: dict) -> None:
        if stats.get("state") != "connected":
            self._info_lbl.setText("")
            return
        parts = []
        if self._quality:
            parts.append(self._quality)
        if stats.get("width"):
            parts.append(f"{stats['width']}×{stats['height']}")
        parts.append(f"{stats.get('fps', 0):.0f} fps")
        parts.append(f"{stats.get('bitrate_kbps', 0):.0f} kb/s")
        parts.append(f"{stats.get('latency_ms', 0):.0f} ms")
        hw = stats.get("hw_mode") or "cpu"
        parts.append(hw.upper())
        self._info_lbl.setText(" · ".join(parts))

    # --------------------------------------------------------- interaction

    def flash(self) -> None:
        """Briefly highlight the tile (e.g. when selected in the sidebar)."""
        self.setProperty("flash", "true")
        self.style().unpolish(self)
        self.style().polish(self)
        QTimer.singleShot(700, self._unflash)

    def _unflash(self) -> None:
        self.setProperty("flash", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (self._press_pos is not None
                and event.buttons() & Qt.LeftButton
                and self._cam is not None
                and not self._drag_started):
            if ((event.position().toPoint() - self._press_pos).manhattanLength()
                    >= QApplication.startDragDistance()):
                self._drag_started = True
                self._start_drag()
        super().mouseMoveEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_MIME_TYPE, QByteArray(self._cam.id.encode("utf-8")))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self._press_pos = None

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_pos = None
        self._drag_started = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self._cam is not None:
                self.double_clicked.emit(self._cam.id)
            else:
                self.add_requested.emit(self._slot)
        super().mouseDoubleClickEvent(event)

    # ---------------------------------------------------------- drag & drop

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        data = bytes(event.mimeData().data(_MIME_TYPE)).decode("utf-8")
        if data and data != self.camera_id:
            self.reorder_requested.emit(data, self._slot)
        event.acceptProposedAction()

    # --------------------------------------------------------- context menu

    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        if self._cam is not None:
            full_act = menu.addAction("Fullscreen")
            ptz_act = menu.addAction("PTZ Controls…")
            snap_act = menu.addAction("Take Snapshot")
            if self._recording:
                rec_act = menu.addAction("Stop Recording")
            else:
                rec_act = menu.addAction("Start Recording")
            reconnect_act = menu.addAction("Reconnect")
            menu.addSeparator()
            edit_act = menu.addAction("Camera Settings…")
            remove_act = menu.addAction("Remove Camera")
            action = menu.exec(self.mapToGlobal(pos))
            if action == full_act:
                self.fullscreen_requested.emit(self._cam.id)
            elif action == ptz_act:
                self.ptz_requested.emit(self._cam.id)
            elif action == snap_act:
                self.snapshot_requested.emit(self._cam.id)
            elif action == rec_act:
                self.record_requested.emit(self._cam.id)
            elif action == reconnect_act:
                self.restart_requested.emit(self._cam.id)
            elif action == edit_act:
                self.edit_requested.emit(self._cam.id)
            elif action == remove_act:
                self.remove_requested.emit(self._cam.id)
        else:
            add_act = menu.addAction("Add camera here…")
            action = menu.exec(self.mapToGlobal(pos))
            if action == add_act:
                self.add_requested.emit(self._slot)
