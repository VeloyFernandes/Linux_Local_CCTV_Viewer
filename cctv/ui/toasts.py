"""Toast notifications with fade animations, stacked bottom-right."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QMainWindow

_KIND_COLORS = {
    "info": ("#2b6cb0", "#dbeafe"),
    "success": ("#276749", "#c6f6d5"),
    "warning": ("#b7791f", "#fefcbf"),
    "error": ("#9b2c2c", "#fed7d7"),
}


class ToastManager(QObject):
    """Shows short-lived notification labels in the corner of a window."""

    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self._window = window
        self._toasts: list[QLabel] = []

    def show(self, text: str, kind: str = "info", duration: int = 3200) -> None:
        bg, fg = _KIND_COLORS.get(kind, _KIND_COLORS["info"])
        label = QLabel(text, self._window)
        label.setObjectName("toast")
        label.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid rgba(255,255,255,0.12);"
            "border-radius: 8px; padding: 8px 14px; font-size: 12px;")
        label.adjustSize()
        label.raise_()
        effect = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effect)
        effect.setOpacity(0.0)

        self._toasts.append(label)
        self._place(label, len(self._toasts) - 1)

        fade_in = QPropertyAnimation(effect, b"opacity", self)
        fade_in.setDuration(220)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)

        def start_hold():
            fade_in.stop()

        fade_in.finished.connect(start_hold)

        def dismiss():
            fade_out = QPropertyAnimation(effect, b"opacity", self)
            fade_out.setDuration(350)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.finished.connect(lambda: self._remove(label))
            fade_out.start(QPropertyAnimation.DeleteWhenStopped)

        QTimer.singleShot(duration, dismiss)
        fade_in.start(QPropertyAnimation.DeleteWhenStopped)

    def _place(self, label: QLabel, index: int) -> None:
        x = self._window.width() - label.width() - 16
        base_y = self._window.height() - 44 - label.height()
        y = base_y - index * (label.height() + 10)
        label.move(QPoint(x, y))

    def _remove(self, label: QLabel) -> None:
        if label in self._toasts:
            self._toasts.remove(label)
        label.deleteLater()
