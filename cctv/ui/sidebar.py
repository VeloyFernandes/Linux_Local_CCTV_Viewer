"""Collapsible camera sidebar with live status indicators."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from ..config import CameraConfig

_STATE_COLORS = {
    "connected": "#3ddc84",
    "connecting": "#fbbf24",
    "reconnecting": "#fb923c",
    "error": "#f87171",
    "stopped": "#8b8b9a",
}


class _CameraRow(QWidget):
    """One sidebar row: status dot, name, host and a REC badge."""

    def __init__(self, cam: CameraConfig, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        self.dot = QLabel("●")
        self.dot.setObjectName("sideDot")
        self.dot.setStyleSheet("color: #8b8b9a; font-size: 11px;")
        self.name = QLabel(cam.name)
        self.name.setObjectName("sideName")
        self.host = QLabel(cam.host or "—")
        self.host.setObjectName("sideHost")
        self.rec = QLabel("REC")
        self.rec.setObjectName("sideRec")
        self.rec.hide()
        layout.addWidget(self.dot)
        layout.addWidget(self.name, 1)
        layout.addWidget(self.host, 0)
        layout.addWidget(self.rec, 0)

    def set_state(self, state: str) -> None:
        color = _STATE_COLORS.get(state, "#8b8b9a")
        self.dot.setStyleSheet(f"color: {color}; font-size: 11px;")

    def set_recording(self, recording: bool) -> None:
        self.rec.setVisible(recording)


class Sidebar(QFrame):
    """List of cameras with live status; double-click opens fullscreen."""

    camera_double_clicked = Signal(str)
    camera_selected = Signal(str)
    add_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(240)
        self._rows: dict[str, _CameraRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("CAMERAS")
        title.setObjectName("sideTitle")
        self._count = QLabel("0")
        self._count.setObjectName("sideCount")
        add_btn = QPushButton("＋")
        add_btn.setObjectName("sideAdd")
        add_btn.setFixedWidth(34)
        add_btn.setToolTip("Add camera")
        add_btn.clicked.connect(self.add_clicked.emit)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._count)
        header.addWidget(add_btn)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setObjectName("camList")
        self.list.setSpacing(2)
        self.list.itemClicked.connect(self._on_clicked)
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self.list, 1)

    # ---------------------------------------------------------------- sync

    def rebuild(self, cameras: list[CameraConfig]) -> None:
        self.list.clear()
        self._rows.clear()
        self._count.setText(str(len(cameras)))
        for cam in cameras:
            row = _CameraRow(cam)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, cam.id)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self._rows[cam.id] = row

    def set_state(self, cam_id: str, state: str) -> None:
        row = self._rows.get(cam_id)
        if row is not None:
            row.set_state(state)

    def set_recording(self, cam_id: str, recording: bool) -> None:
        row = self._rows.get(cam_id)
        if row is not None:
            row.set_recording(recording)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        cam_id = item.data(Qt.UserRole)
        if cam_id:
            self.camera_selected.emit(cam_id)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        cam_id = item.data(Qt.UserRole)
        if cam_id:
            self.camera_double_clicked.emit(cam_id)
