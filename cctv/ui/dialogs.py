"""Add/Edit camera dialog and connection testing."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from ..config import AppSettings, CameraConfig
from ..core.onvif_client import open_capture, resolve_rtsp_url


class UrlTestWorker(QThread):
    """Resolves and verifies a camera's stream in a background thread."""

    result = Signal(bool, str, str)  # ok, message, resolved_url

    def __init__(self, host: str, port: int, username: str, password: str,
                 manual_url: str = "", prefer_main: bool = False, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._manual = manual_url
        self._prefer_main = prefer_main

    def run(self) -> None:
        url = self._manual.strip()
        if not url:
            url = resolve_rtsp_url(self._host, self._port, self._username,
                                   self._password, prefer_main=self._prefer_main)
        if not url:
            self.result.emit(False,
                             "Could not determine an RTSP URL for this camera.",
                             "")
            return
        cap = open_capture(url, hw="off", short=True)
        if cap is None:
            self.result.emit(False, "Failed to open the RTSP stream.", url)
            return
        try:
            ok, frame = cap.read()
        except Exception:
            ok, frame = False, None
        finally:
            cap.release()
        if ok and frame is not None and frame.size:
            self.result.emit(True, "Connection OK — video received.", url)
        else:
            self.result.emit(False,
                             "Stream opened but no video frames arrived.", url)


class CameraDialog(QDialog):
    """Add a new camera or edit an existing one."""

    def __init__(self, settings: AppSettings, camera: CameraConfig | None = None,
                 parent=None):
        super().__init__(parent)
        self._settings = settings
        self._camera = camera
        self._worker: UrlTestWorker | None = None
        self.setWindowTitle("Edit Camera" if camera else "Add Camera")
        self.setMinimumWidth(460)
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g. 192.168.1.100 or camera.local")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(80)
        self.user_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Leave empty to auto-detect via ONVIF")
        form.addRow("Name:", self.name_edit)
        form.addRow("Host / IP:", self.host_edit)
        form.addRow("ONVIF port:", self.port_spin)
        form.addRow("Username:", self.user_edit)
        form.addRow("Password:", self.pass_edit)
        form.addRow("Manual RTSP URL:", self.url_edit)
        self.remote_host_edit = QLineEdit()
        self.remote_host_edit.setPlaceholderText(
            "e.g. camera1.duckdns.org (used in Remote mode)")
        self.remote_port_spin = QSpinBox()
        self.remote_port_spin.setRange(0, 65535)
        self.remote_port_spin.setValue(0)
        self.remote_port_spin.setSpecialValueText("same as local")
        self.remote_rtsp_port_spin = QSpinBox()
        self.remote_rtsp_port_spin.setRange(0, 65535)
        self.remote_rtsp_port_spin.setValue(0)
        self.remote_rtsp_port_spin.setSpecialValueText("default (554)")
        self.remote_url_edit = QLineEdit()
        self.remote_url_edit.setPlaceholderText(
            "Optional remote RTSP URL override (advanced)")
        form.addRow("Remote host / IP:", self.remote_host_edit)
        form.addRow("Remote ONVIF port:", self.remote_port_spin)
        form.addRow("Remote RTSP port:", self.remote_rtsp_port_spin)
        form.addRow("Remote RTSP URL:", self.remote_url_edit)
        layout.addLayout(form)

        self.sub_check = QCheckBox(
            "Prefer low-resolution substream in the grid (saves CPU)")
        self.sub_check.setChecked(True)
        layout.addWidget(self.sub_check)

        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.test_label.setMinimumHeight(20)
        layout.addWidget(self.test_label)

        self._test_btn = QPushButton("Test Connection")
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        row = QHBoxLayout()
        row.addWidget(self._test_btn)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

        self._test_btn.clicked.connect(self._run_test)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

    def _load_values(self) -> None:
        if self._camera is not None:
            cam = self._camera
            self.name_edit.setText(cam.name)
            self.host_edit.setText(cam.host)
            self.port_spin.setValue(cam.port)
            self.user_edit.setText(cam.username)
            self.pass_edit.setText(cam.password)
            self.url_edit.setText(cam.rtsp_url)
            self.remote_host_edit.setText(cam.remote_host)
            self.remote_port_spin.setValue(cam.remote_port)
            self.remote_rtsp_port_spin.setValue(cam.remote_rtsp_port)
            self.remote_url_edit.setText(cam.remote_rtsp_url)
            self.sub_check.setChecked(cam.prefer_substream)
        else:
            self.name_edit.setText(self._settings.default_name())
            self.user_edit.setText(self._settings.default_username)
            self.pass_edit.setText(self._settings.default_password)

    # -------------------------------------------------------------- testing

    def _run_test(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            self.test_label.setText(
                '<span style="color:#fbbf24">⚠ Enter a host / IP first.</span>')
            return
        self._test_btn.setEnabled(False)
        self.test_label.setText("Testing connection…")
        self._worker = UrlTestWorker(
            host, self.port_spin.value(), self.user_edit.text(),
            self.pass_edit.text(), self.url_edit.text().strip(), parent=self)
        self._worker.result.connect(self._on_test_result)
        self._worker.finished.connect(lambda: self._test_btn.setEnabled(True))
        self._worker.start()

    def _on_test_result(self, ok: bool, message: str, url: str) -> None:
        color = "#3ddc84" if ok else "#f87171"
        symbol = "✔" if ok else "✘"
        self.test_label.setText(
            f'<span style="color:{color}">{symbol} {message}</span>')
        if url:
            self.test_label.setToolTip(url)

    # ------------------------------------------------------------- results

    def _validate_and_accept(self) -> None:
        if not self.host_edit.text().strip() and not self.url_edit.text().strip():
            QMessageBox.warning(
                self, "Missing host",
                "Enter the camera IP address / hostname or a manual RTSP URL.")
            return
        self.accept()

    def result_camera(self) -> CameraConfig:
        cam = self._camera if self._camera is not None else CameraConfig()
        host = self.host_edit.text().strip()
        cam.name = self.name_edit.text().strip() or host or "Camera"
        cam.host = host
        cam.port = self.port_spin.value()
        cam.username = self.user_edit.text().strip()
        cam.password = self.pass_edit.text()
        cam.rtsp_url = self.url_edit.text().strip()
        cam.remote_host = self.remote_host_edit.text().strip()
        cam.remote_port = self.remote_port_spin.value()
        cam.remote_rtsp_port = self.remote_rtsp_port_spin.value()
        cam.remote_rtsp_url = self.remote_url_edit.text().strip()
        cam.prefer_substream = self.sub_check.isChecked()
        cam.enabled = True
        return cam

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(15000)
        super().closeEvent(event)
