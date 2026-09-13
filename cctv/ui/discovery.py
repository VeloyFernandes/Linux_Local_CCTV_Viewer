"""Discovery dialog: scan the local network and add found ONVIF cameras."""
from __future__ import annotations

import concurrent.futures

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..config import AppSettings, CameraConfig
from ..core.onvif_client import probe_onvif_camera
from ..core.onvif_discovery import discover_onvif


class DiscoveryWorker(QThread):
    """Scans the network and probes each found camera over ONVIF."""

    row = Signal(dict)
    scan_done = Signal(int, str)  # device count, error/message

    def __init__(self, username: str, password: str, parent=None):
        super().__init__(parent)
        self._username = username
        self._password = password
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            endpoints = discover_onvif(timeout=3.5)
        except Exception as exc:
            self.scan_done.emit(0, f"Discovery failed: {exc}")
            return
        if self._cancel:
            self.scan_done.emit(0, "Cancelled.")
            return
        if not endpoints:
            self.scan_done.emit(
                0, "No ONVIF cameras responded to the discovery probe. "
                   "Check that the cameras are on the same subnet/VLAN and "
                   "that multicast (port 3702) is not blocked.")
            return

        def probe_one(endpoint: dict) -> dict:
            info = {
                "host": endpoint["host"],
                "port": endpoint["port"],
                "name_hint": endpoint.get("name_hint") or endpoint["host"],
                "ok": False,
                "error": "",
            }
            result = probe_onvif_camera(endpoint["host"], endpoint["port"],
                                        self._username, self._password,
                                        timeout=6.0)
            if result is None:
                info["error"] = "No ONVIF response / wrong credentials"
                return info
            info.update(
                ok=True,
                name=result.get("name") or info["name_hint"],
                manufacturer=result.get("manufacturer", ""),
                model=result.get("model", ""),
                sub_url=result.get("sub_url", ""),
                main_url=result.get("main_url", ""),
            )
            return info

        count = len(endpoints)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(probe_one, ep) for ep in endpoints]
            for future in concurrent.futures.as_completed(futures):
                if self._cancel:
                    break
                try:
                    info = future.result()
                except Exception:
                    continue
                if info:
                    self.row.emit(info)
        self.scan_done.emit(count, "")


class DiscoveryDialog(QDialog):
    """Lists discovered cameras; the user selects which ones to add."""

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._worker: DiscoveryWorker | None = None
        self._rows: list[dict] = []
        self.setWindowTitle("Discover ONVIF Cameras")
        self.resize(720, 460)

        layout = QVBoxLayout(self)

        cred_row = QHBoxLayout()
        cred_row.addWidget(QLabel("Username:"))
        self.user_edit = QLineEdit(settings.default_username)
        self.user_edit.setFixedWidth(140)
        cred_row.addWidget(QLabel("Password:"))
        self.pass_edit = QLineEdit(settings.default_password)
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setFixedWidth(180)
        scan_btn = QPushButton("Scan Again")
        scan_btn.clicked.connect(self._start_scan)
        cred_row.addStretch(1)
        cred_row.addWidget(scan_btn)
        layout.addLayout(cred_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["✓", "Name", "Host", "Details"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table)

        self.status_lbl = QLabel("Scanning the local network…")
        layout.addWidget(self.status_lbl)

        buttons = QDialogButtonBox()
        add_btn = buttons.addButton("Add Selected", QDialogButtonBox.AcceptRole)
        close_btn = buttons.addButton("Close", QDialogButtonBox.RejectRole)
        add_btn.clicked.connect(self._add_selected)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._worker is None:
            self._start_scan()

    def _start_scan(self) -> None:
        self.table.setRowCount(0)
        self._rows = []
        self.status_lbl.setText("Scanning the local network…")
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(10000)
        self._worker = DiscoveryWorker(self.user_edit.text().strip(),
                                       self.pass_edit.text(), parent=self)
        self._worker.row.connect(self._add_row)
        self._worker.scan_done.connect(self._on_done)
        self._worker.start()

    def _add_row(self, info: dict) -> None:
        self._rows.append(info)
        row = self.table.rowCount()
        self.table.insertRow(row)

        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check.setCheckState(Qt.Checked if info.get("ok") else Qt.Unchecked)
        self.table.setItem(row, 0, check)

        name = info.get("name") or info.get("name_hint") or "Camera"
        for col, text in enumerate((
                "", name, info.get("host", ""),
                (info.get("manufacturer", "") + " " + info.get("model", "")).strip()
                or info.get("error", "")),
                start=1):
            item = QTableWidgetItem(str(text))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, col, item)

    def _on_done(self, count: int, message: str) -> None:
        if message:
            self.status_lbl.setText(message)
        else:
            ok = sum(1 for info in self._rows if info.get("ok"))
            self.status_lbl.setText(
                f"Scan complete — {count} device(s) found, {ok} verified "
                "with the credentials above.")

    def _add_selected(self) -> None:
        existing = {cam.host for cam in self._settings.cameras}
        added = 0
        for row, info in enumerate(self._rows):
            item = self.table.item(row, 0)
            if item is None or item.checkState() != Qt.Checked:
                continue
            if not info.get("host") or info.get("host") in existing:
                continue
            self._settings.cameras.append(CameraConfig(
                name=info.get("name") or info.get("name_hint") or "Camera",
                host=info.get("host", ""),
                port=int(info.get("port") or 80),
                username=self.user_edit.text().strip(),
                password=self.pass_edit.text(),
                rtsp_url=info.get("main_url") or info.get("sub_url", ""),
                prefer_substream=True,
            ))
            existing.add(info.get("host"))
            added += 1
        if added == 0:
            self.status_lbl.setText(
                "Nothing to add — select verified cameras (✓ checked).")
            return
        self._settings.save()
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(10000)
        super().closeEvent(event)
