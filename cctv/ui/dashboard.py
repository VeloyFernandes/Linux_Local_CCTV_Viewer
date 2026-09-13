"""Live system dashboard: CPU/GPU/RAM/network/storage + NVR counters."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QGridLayout, QLabel, QVBoxLayout,
                               QWidget)


class _StatCard(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setObjectName("statCardTitle")
        self.value_lbl = QLabel("—")
        self.value_lbl.setObjectName("statCardValue")
        self.value_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.title_lbl)
        layout.addWidget(self.value_lbl)

    def set_value(self, text: str) -> None:
        self.value_lbl.setText(text)


def _gpu_usage() -> str:
    """NVIDIA GPU utilisation via nvidia-smi ('' if unavailable)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5)
        lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        return ", ".join(lines) + "%" if lines else ""
    except Exception:
        return ""


class DashboardDialog(QDialog):
    """Live stats overview, refreshed once per second."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self.setWindowTitle("Dashboard")
        self.resize(560, 320)

        layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setSpacing(10)
        self._cards = {}
        titles = ("CPU", "GPU", "RAM", "Network", "Storage",
                  "Cameras Online", "Recordings")
        positions = [(r, c) for r in range(3) for c in range(3)]
        for title, (row, col) in zip(titles, positions):
            card = _StatCard(title)
            grid.addWidget(card, row, col)
            self._cards[title] = card
        layout.addLayout(grid)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        mw = self._mw
        sample = mw._system.snapshot()
        self._cards["CPU"].set_value(f"{sample['cpu_pct']:.0f} %")
        gpu = _gpu_usage()
        self._cards["GPU"].set_value(gpu or "n/a (no NVIDIA)")
        self._cards["RAM"].set_value(f"{sample['mem_pct']:.0f} %")
        from ..core.stats import format_bps
        self._cards["Network"].set_value(
            f"↓ {format_bps(sample['net_down_bps'])}  ↑ {format_bps(sample['net_up_bps'])}")

        try:
            usage = shutil.disk_usage(Path(mw._settings.record_dir))
            free_gb = usage.free / 2**30
            self._cards["Storage"].set_value(
                f"{free_gb:.0f} GB free\n{usage.used / usage.total * 100:.0f}% used")
        except OSError:
            self._cards["Storage"].set_value("n/a")

        shown = mw._shown_cameras()
        connected = sum(1 for cam in shown
                        if mw._states.get(cam.id) == "connected")
        self._cards["Cameras Online"].set_value(
            f"{connected} / {len(shown)}")

        active_rec = sum(1 for cam in shown if mw._recorder.is_recording(cam.id))
        self._cards["Recordings"].set_value(str(active_rec))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)
