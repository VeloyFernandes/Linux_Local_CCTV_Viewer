"""Application entry point."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .config import AppSettings, CameraConfig
from .theme import apply_dark_theme
from .ui.main_window import MainWindow


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    demo_mode = "--demo" in argv
    qt_argv = [arg for arg in argv if arg != "--demo"]

    app = QApplication(qt_argv)
    app.setApplicationName("ONVIF CCTV Monitor")
    app.setOrganizationName("AI_CCTV")
    apply_dark_theme(app)

    settings = AppSettings.load()
    if demo_mode and not settings.cameras:
        for index in range(8):
            settings.cameras.append(CameraConfig(
                name=f"Demo Camera {index + 1}",
                host="demo",
                rtsp_url=f"demo://pattern-{index + 1}",
            ))

    window = MainWindow(settings, demo_mode=demo_mode)
    window.show()

    # Headless smoke test hook: closes the window after a few seconds.
    if os.environ.get("CCTV_SMOKE_TEST"):
        QTimer.singleShot(5000, window.close)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
