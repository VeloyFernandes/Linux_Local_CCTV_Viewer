"""Toast notifications with fade animations, stacked bottom-right."""
from __future__ import annotations

import time

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint,
                            QPropertyAnimation, QTimer)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QMainWindow

_KIND_COLORS = {
    "info": ("#2b6cb0", "#dbeafe"),
    "success": ("#276749", "#c6f6d5"),
    "warning": ("#b7791f", "#fefcbf"),
    "error": ("#9b2c2c", "#fed7d7"),
}
_MAX_TOASTS = 5          # older toasts are dropped beyond this
_DUPLICATE_WINDOW = 5.0  # identical messages within this many seconds are merged


class ToastManager(QObject):
    """Shows short-lived notification labels in the corner of a window."""

    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self._window = window
        self._toasts: list[QLabel] = []
        self._recent: dict[str, float] = {}
        window.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._window and event.type() == QEvent.Resize:
            self._restack()
        return False

    def show(self, text: str, kind: str = "info", duration: int = 3200) -> None:
        now = time.monotonic()
        if now - self._recent.get(text, -_DUPLICATE_WINDOW) < _DUPLICATE_WINDOW:
            return  # same message just shown — avoid flooding the corner
        self._recent = {msg: ts for msg, ts in self._recent.items()
                        if now - ts < _DUPLICATE_WINDOW}
        self._recent[text] = now

        while len(self._toasts) >= _MAX_TOASTS:
            self._remove(self._toasts[0])

        bg, fg = _KIND_COLORS.get(kind, _KIND_COLORS["info"])
        label = QLabel(text, self._window)
        label.setObjectName("toast")
        label.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid rgba(255,255,255,0.12);"
            "border-radius: 8px; padding: 8px 14px; font-size: 12px;")
        label.adjustSize()
        effect = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        label.show()
        label.raise_()

        self._toasts.append(label)
        self._restack()

        fade_in = QPropertyAnimation(effect, b"opacity", label)
        fade_in.setDuration(220)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        fade_in.start(QPropertyAnimation.DeleteWhenStopped)

        # the timer is owned by the label, so it dies with it (window closed,
        # toast pushed out) instead of firing on a deleted widget
        timer = QTimer(label)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._fade_out(label, effect))
        timer.start(duration)

    def _fade_out(self, label: QLabel, effect: QGraphicsOpacityEffect) -> None:
        if label not in self._toasts:
            return
        fade_out = QPropertyAnimation(effect, b"opacity", label)
        fade_out.setDuration(350)
        fade_out.setStartValue(effect.opacity())
        fade_out.setEndValue(0.0)
        fade_out.finished.connect(lambda: self._remove(label))
        fade_out.start(QPropertyAnimation.DeleteWhenStopped)

    def _restack(self) -> None:
        """Place toasts bottom-up, newest at the bottom."""
        y = self._window.height() - 44
        for label in reversed(self._toasts):
            y -= label.height()
            x = self._window.width() - label.width() - 16
            label.move(QPoint(x, y))
            y -= 10

    def _remove(self, label: QLabel) -> None:
        if label not in self._toasts:
            return
        self._toasts.remove(label)
        label.hide()
        label.deleteLater()
        self._restack()
