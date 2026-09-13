"""Clean, modern dark theme (Fusion style + custom stylesheet)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_STYLESHEET = """
* { font-family: 'Ubuntu', 'Noto Sans', 'DejaVu Sans', sans-serif; font-size: 13px; }
QMainWindow, QDialog { background-color: #121218; }
QWidget#central { background-color: #121218; }

QToolBar { background: #1b1b24; border: none; border-bottom: 1px solid #2b2b38;
           padding: 6px; spacing: 8px; }
QToolBar QToolButton { color: #d9d9e3; background: transparent; border: none;
                       border-radius: 6px; padding: 7px 12px; }
QToolBar QToolButton:hover { background: #272734; }
QToolBar QToolButton:pressed { background: #303042; }

QPushButton { background: #2a2a37; color: #e6e6ef; border: 1px solid #383847;
              border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #32323f; border-color: #4a4a5c; }
QPushButton:pressed { background: #272732; }
QPushButton:default { background: #3b82f6; border-color: #3b82f6; color: #ffffff; }
QPushButton:disabled { color: #6c6c7c; background: #22222c; border-color: #2a2a35; }

QLineEdit, QSpinBox { background: #1c1c25; color: #e6e6ef; border: 1px solid #353544;
                      border-radius: 6px; padding: 6px 8px;
                      selection-background-color: #3b82f6; }
QLineEdit:focus, QSpinBox:focus { border-color: #3b82f6; }
QLabel { color: #d6d6e0; background: transparent; }
QCheckBox { color: #d6d6e0; spacing: 6px; }

QGroupBox { color: #c4c4d2; border: 1px solid #2b2b38; border-radius: 8px;
            margin-top: 10px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }

QTableWidget { background: #17171f; alternate-background-color: #1b1b25;
               color: #d6d6e0; gridline-color: #262632; border: 1px solid #2b2b38;
               border-radius: 6px; }
QHeaderView::section { background: #20202b; color: #b7b7c6; border: none;
                       border-bottom: 1px solid #2b2b38; padding: 6px; }
QTableCornerButton::section { background: #20202b; border: none; }

QMenu { background: #1e1e28; color: #e0e0e8; border: 1px solid #343442;
        border-radius: 6px; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 4px; }
QMenu::item:selected { background: #3b82f6; color: #ffffff; }

QStatusBar { background: #1b1b24; color: #9a9aa8; border-top: 1px solid #2b2b38; }
QToolTip { background: #262633; color: #e6e6ef; border: 1px solid #3a3a4c;
           padding: 4px; }

QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #343444; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QFrame#tile { background: #0d0d12; border: 1px solid #262632; border-radius: 8px; }
QFrame#tile[flash="true"] { border: 1px solid #3b82f6; }
QLabel#tileName { background: rgba(10,10,16,0.78); color: #f2f2f6; padding: 3px 9px;
                  border-radius: 5px; font-weight: 600; }
QLabel#tileStatus { background: rgba(10,10,16,0.78); color: #cfcfda; padding: 3px 9px;
                    border-radius: 5px; }
QLabel#tileInfo { background: rgba(10,10,16,0.72); color: #9fa0ad; padding: 2px 8px;
                  border-radius: 5px; font-size: 11px; }
QLabel#recBadge { color: #ffffff; background: #e11d48; padding: 3px 8px;
                  border-radius: 5px; font-weight: 700; font-size: 11px; }
QLabel#qualChip { padding: 3px 8px; border-radius: 5px; font-weight: 700; font-size: 11px; }
QWidget#overlayRow { background: transparent; }
QLabel#slotHint { color: #7d7d8e; background: transparent; }
QFrame#tile[fullscreen="true"] QLabel#tileName { font-size: 17px; padding: 7px 16px;
                                                 border-radius: 7px; }
QFrame#tile[fullscreen="true"] QLabel#tileStatus { font-size: 14px; padding: 7px 16px;
                                                   border-radius: 7px; }
QFrame#tile[fullscreen="true"] QLabel#tileInfo { font-size: 13px; padding: 4px 12px; }
QLabel#fullscreenHint { color: rgba(235,235,242,0.9); background: rgba(10,10,16,0.6);
                        padding: 6px 14px; border-radius: 6px; }

/* ---- sidebar ---- */
QFrame#sidebar { background: #17171f; border: none; border-right: 1px solid #2b2b38; }
QLabel#sideTitle { color: #9a9aa8; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#sideCount { color: #6f6f80; background: #22222c; border-radius: 8px;
                   padding: 1px 8px; font-size: 11px; }
QPushButton#sideAdd { background: #2a2a37; color: #e6e6ef; border: 1px solid #383847;
                      border-radius: 6px; padding: 4px; }
QPushButton#sideAdd:hover { background: #3b82f6; border-color: #3b82f6; color: white; }
QListWidget#camList { background: transparent; border: none; outline: none; }
QListWidget#camList::item { border-radius: 6px; margin: 1px 0; }
QListWidget#camList::item:selected { background: #2a2a38; }
QListWidget#camList::item:hover { background: #22222e; }
QLabel#sideName { color: #e0e0e8; font-weight: 600; }
QLabel#sideHost { color: #7d7d8e; font-size: 11px; }
QLabel#sideRec { color: #fda4af; background: #7f1d1d; border-radius: 4px;
                 padding: 0 5px; font-size: 10px; font-weight: 700; }
QLabel#sideDot { background: transparent; }

/* ---- status bar ---- */
QLabel#statLbl { color: #b7b7c6; background: transparent; padding: 0 10px;
                 border-left: 1px solid #2b2b38; font-size: 12px; }

/* ---- dialogs ---- */
QLabel#aiHint { color: #8f8fa0; font-size: 11px; }

/* ---- PTZ pad ---- */
QFrame#ptzPad { background: rgba(10,10,16,0.82); border: 1px solid #343442;
                border-radius: 10px; }
QLabel#ptzTitle { color: #e6e6ef; font-weight: 700; }
QLabel#ptzStatus { color: #9a9aa8; font-size: 11px; }
QPushButton#ptzBtn { background: #2a2a37; color: #e6e6ef; border: 1px solid #383847;
                     border-radius: 8px; font-size: 17px; font-weight: 700; }
QPushButton#ptzBtn:hover { background: #3b82f6; border-color: #3b82f6; color: #ffffff; }
QPushButton#ptzBtn:pressed { background: #1d4ed8; }
QPushButton#ptzBtn:disabled { color: #5a5a6a; background: #20202a; }

/* ---- lock page & login dialog ---- */
QLabel#lockIcon { font-size: 46px; background: transparent; }
QLabel#lockTitle { color: #e6e6ef; font-size: 20px; font-weight: 700; }
QLabel#lockSub { color: #8f8fa0; }
QPushButton#lockBtn { background: #3b82f6; border-color: #3b82f6; color: #ffffff;
                       padding: 9px 22px; font-weight: 600; }
QPushButton#lockBtn:hover { background: #2f6fe0; border-color: #2f6fe0; }
QLabel#loginNote { color: #8f8fa0; }
QLabel#loginError { color: #f87171; }

/* ---- dashboard ---- */
QWidget#statCard { background: #1b1b24; border: 1px solid #2b2b38;
                   border-radius: 10px; }
QWidget#statCard:hover { border-color: #3b82f6; }
QLabel#statCardTitle { color: #8f8fa0; font-size: 10px; font-weight: 700;
                       letter-spacing: 1px; }
QLabel#statCardValue { color: #eef0f6; font-size: 18px; font-weight: 600; }

/* ---- toasts ---- */
QLabel#toast { font-size: 12px; }
"""


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#121218"))
    palette.setColor(QPalette.WindowText, QColor("#e6e6ef"))
    palette.setColor(QPalette.Base, QColor("#1c1c25"))
    palette.setColor(QPalette.AlternateBase, QColor("#1b1b25"))
    palette.setColor(QPalette.ToolTipBase, QColor("#262633"))
    palette.setColor(QPalette.ToolTipText, QColor("#e6e6ef"))
    palette.setColor(QPalette.Text, QColor("#e6e6ef"))
    palette.setColor(QPalette.Button, QColor("#2a2a37"))
    palette.setColor(QPalette.ButtonText, QColor("#e6e6ef"))
    palette.setColor(QPalette.BrightText, QColor("#ff6b6b"))
    palette.setColor(QPalette.Highlight, QColor("#3b82f6"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, QColor("#3b82f6"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#6c6c7c"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6c6c7c"))
    app.setPalette(palette)
    app.setStyleSheet(_STYLESHEET)
