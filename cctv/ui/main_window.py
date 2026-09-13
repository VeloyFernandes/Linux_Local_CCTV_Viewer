"""Main window: sidebar, camera grid, toolbar, status bar and feature wiring.

Owns and coordinates:
- :class:`StreamManager`  — one decode thread per camera (substream in grid)
- :class:`RecordingManager` — per-camera MP4 recording + snapshots
- :class:`HealthMonitor`  — disconnect / quality alerts
- sidebar, toasts, PTZ controls, drag&drop layout persistence
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QStackedWidget, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from ..config import AppSettings, effective_camera
from ..core.recorder import RecordingManager, save_snapshot, snapshot_path
from ..core.stats import SystemStats, format_bps
from ..core.streamer import StreamManager
from ..health import HealthMonitor
from .dialogs import CameraDialog
from .discovery import DiscoveryDialog
from .fullscreen import FullscreenWindow
from .login import LoginDialog
from .ptz_pad import PTZPadDialog
from .sidebar import Sidebar
from .toasts import ToastManager
from .video_tile import VideoTile


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, demo_mode: bool = False):
        super().__init__()
        self._settings = settings
        self._demo_mode = demo_mode
        self._manager = StreamManager(settings)
        self._manager.camera_relocated.connect(self._on_camera_relocated)
        self._recorder = RecordingManager(settings)
        self._recorder.changed.connect(self._on_recording_changed)
        self._states: dict[str, str] = {}
        self._tiles: list[VideoTile] = []
        self._fullscreen: FullscreenWindow | None = None
        self._ptz_dialogs: dict[str, PTZPadDialog] = {}
        self._system = SystemStats()
        self._logged_in: bool = not settings.has_login()
        self._login_btn: QToolButton | None = None
        self._login_menu: QMenu | None = None

        self._health = HealthMonitor(settings, self._manager)
        self._health.alert.connect(self._on_health_alert)

        self.setWindowTitle("ONVIF CCTV Monitor")
        self.setMinimumSize(1100, 620)
        if settings.window_size:
            self.resize(*settings.window_size)
        else:
            self.resize(1600, 900)

        self._build_ui()
        if self._logged_in:
            self._apply_cameras(restart=True)
        else:
            # a login is stored — show the lock page and ask for it
            self._lock_ui()
            QTimer.singleShot(300, self._open_login)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(33)  # ~30 FPS paint budget
        self._refresh_timer.timeout.connect(self._refresh_tiles)
        self._refresh_timer.start()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._tick_stats)
        self._stats_timer.start()

        self._restore_sidebar_state()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        self._sidebar_act = QAction("☰ Cameras", self)
        self._sidebar_act.setCheckable(True)
        self._sidebar_act.setChecked(self._settings.show_sidebar)
        self._sidebar_act.toggled.connect(self._toggle_sidebar)
        add_act = QAction("＋ Add Camera", self)
        discover_act = QAction("Discover Cameras", self)
        dashboard_act = QAction("Dashboard", self)
        reconnect_act = QAction("Reconnect All", self)
        self._quality_act = QAction("Grid Quality: Low", self)
        self._quality_act.setCheckable(True)
        self._quality_act.setChecked(self._settings.grid_quality == "high")
        self._quality_act.toggled.connect(self._toggle_quality)
        self._network_act = QAction("Network: Local", self)
        self._network_act.setCheckable(True)
        self._network_act.setChecked(self._settings.network_mode == "remote")
        self._network_act.toggled.connect(self._toggle_network)
        add_act.triggered.connect(self._add_camera)
        discover_act.triggered.connect(self._discover)
        dashboard_act.triggered.connect(self._open_dashboard)
        reconnect_act.triggered.connect(self._reconnect_all)
        toolbar.addAction(self._sidebar_act)
        toolbar.addSeparator()
        toolbar.addAction(add_act)
        toolbar.addAction(discover_act)
        toolbar.addSeparator()
        toolbar.addAction(dashboard_act)
        toolbar.addSeparator()
        toolbar.addAction(reconnect_act)
        toolbar.addSeparator()
        toolbar.addAction(self._quality_act)
        toolbar.addSeparator()
        toolbar.addAction(self._network_act)
        self._update_quality_action()
        self._update_network_action()

        # login, pinned to the right edge of the toolbar
        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        self._login_btn = QToolButton(toolbar)
        self._login_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._login_menu = QMenu(self._login_btn)
        self._login_btn.setMenu(self._login_menu)
        self._login_btn.clicked.connect(self._open_login)
        toolbar.addWidget(self._login_btn)
        self._update_login_button()

        central = QWidget()
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar (animated collapse)
        self.sidebar = Sidebar(central)
        self.sidebar.camera_double_clicked.connect(self._open_fullscreen)
        self.sidebar.camera_selected.connect(self._focus_camera)
        self.sidebar.add_clicked.connect(self._add_camera)
        root.addWidget(self.sidebar)
        self._sidebar_anim = QPropertyAnimation(self.sidebar, b"maximumWidth")
        self._sidebar_anim.setDuration(180)
        self._sidebar_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._sidebar_anim.finished.connect(self._on_sidebar_anim_done)

        # camera grid — tile count follows the number of cameras
        grid_widget = QWidget(central)
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        self._grid_widget = grid_widget
        self._grid = grid
        root.addWidget(grid_widget, 1)

        # page 0 = monitor, page 1 = lock screen
        stack = QStackedWidget()
        stack.addWidget(central)
        stack.addWidget(self._build_lock_page())
        self._stack = stack
        self.setCentralWidget(stack)

        # status bar
        self._cam_lbl = QLabel("No cameras")
        self._cpu_lbl = QLabel("CPU —")
        self._ram_lbl = QLabel("RAM —")
        self._net_lbl = QLabel("NET —")
        self._gpu_lbl = QLabel("DECODE —")
        for label in (self._cam_lbl, self._cpu_lbl, self._ram_lbl,
                      self._net_lbl, self._gpu_lbl):
            label.setObjectName("statLbl")
        self.statusBar().addWidget(self._cam_lbl, 1)
        for label in (self._cpu_lbl, self._ram_lbl, self._net_lbl, self._gpu_lbl):
            self.statusBar().addPermanentWidget(label)

        self._toasts = ToastManager(self)

    def _build_lock_page(self) -> QWidget:
        """The full-window page shown while nobody is signed in."""
        page = QWidget()
        page.setObjectName("central")
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        icon = QLabel("🔒")
        icon.setObjectName("lockIcon")
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        title = QLabel("Monitor Locked")
        title.setObjectName("lockTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        sub = QLabel("Log in to view the cameras.")
        sub.setObjectName("lockSub")
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub)
        button = QPushButton("Log In")
        button.setObjectName("lockBtn")
        button.setFixedWidth(160)
        button.clicked.connect(self._open_login)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(2)
        return page

    # ------------------------------------------------------------- sidebar

    def _restore_sidebar_state(self) -> None:
        if not self._settings.show_sidebar:
            self.sidebar.setMaximumWidth(0)
            self.sidebar.hide()

    def _toggle_sidebar(self, visible: bool) -> None:
        self._settings.show_sidebar = visible
        if visible:
            self.sidebar.show()
            self._sidebar_anim.stop()
            self._sidebar_anim.setStartValue(0)
            self._sidebar_anim.setEndValue(240)
        else:
            self._sidebar_anim.stop()
            self._sidebar_anim.setStartValue(self.sidebar.maximumWidth() or 240)
            self._sidebar_anim.setEndValue(0)
        self._sidebar_anim.start()

    def _on_sidebar_anim_done(self) -> None:
        # only the collapse animation hides the sidebar
        if not self._settings.show_sidebar:
            self.sidebar.hide()

    # ------------------------------------------------------------- streams

    def _shown_cameras(self) -> list:
        return [cam for cam in self._settings.cameras if cam.enabled]

    def _grid_shape(self, count: int) -> tuple[int, int]:
        """Rows and columns that keep the wall roughly 16:9."""
        if count <= 1:
            return 1, 1
        if count <= 4:
            return 2, 2
        if count <= 8:
            return 2, 4
        if count == 9:
            return 3, 3
        cols = 4 if count <= 16 else 5 if count <= 20 else 6
        return math.ceil(count / cols), cols

    def _rebuild_grid(self, count: int) -> None:
        """Create enough tiles for every camera, plus trailing empty slots."""
        for tile in self._tiles:
            self._grid.removeWidget(tile)
            tile.deleteLater()
        self._tiles = []
        rows, cols = self._grid_shape(count)
        for index in range(rows * cols):
            tile = VideoTile(index)
            row, col = divmod(index, cols)
            self._grid.addWidget(tile, row, col)
            tile.double_clicked.connect(self._open_fullscreen)
            tile.add_requested.connect(self._add_camera_at)
            tile.edit_requested.connect(self._edit_camera)
            tile.remove_requested.connect(self._remove_camera)
            tile.restart_requested.connect(self._restart_camera)
            tile.fullscreen_requested.connect(self._open_fullscreen)
            tile.snapshot_requested.connect(self._snapshot_camera)
            tile.record_requested.connect(self._toggle_recording)
            tile.ptz_requested.connect(self._open_ptz)
            tile.reorder_requested.connect(self._reorder_camera)
            self._tiles.append(tile)

    def _apply_cameras(self, restart: bool = True) -> None:
        """(Re)bind every enabled camera to the grid, growing it as needed."""
        if restart:
            self._manager.stop_all()
        self._states.clear()
        cameras = self._shown_cameras()
        self._rebuild_grid(len(cameras))

        for index, tile in enumerate(self._tiles):
            cam = cameras[index] if index < len(cameras) else None
            tile.clear()
            tile.bind(cam)
            if cam is None:
                continue
            thread, buffer = self._manager.ensure_start(cam, key=cam.id)
            if not restart:  # keep exactly one status hook on reused threads
                thread.status.disconnect(self._on_status)
            thread.status.connect(self._on_status)
            tile.set_buffer(buffer)
            tile.set_quality(self._grid_quality())
            tile.set_status(thread.stats.state or "connecting", "Starting…")

        self.sidebar.rebuild(cameras)
        self._update_status_bar()

    def _on_status(self, cam_id: str, state: str, detail: str) -> None:
        previous = self._states.get(cam_id)
        self._states[cam_id] = state
        for tile in self._tiles:
            if tile.camera_id == cam_id:
                tile.set_status(state, detail)
                break
        self.sidebar.set_state(cam_id, state)
        if state == "error":
            self._toasts.show(f"Camera error — {detail}", "error")
        elif state == "connected" and previous in ("reconnecting", "error"):
            cam = self._settings.get(cam_id)
            name = cam.name if cam else "Camera"
            self._toasts.show(f"{name} reconnected", "success")
        self._update_status_bar()

    def _on_health_alert(self, cam_id: str, level: str, message: str) -> None:
        kind = "error" if level == "error" else "warning"
        self._toasts.show(message, kind)

    def _on_camera_relocated(self, cam_id: str, old_host: str,
                             new_host: str) -> None:
        cam = self._settings.get(cam_id)
        name = cam.name if cam else "Camera"
        self._toasts.show(
            f"{name} changed IP: {old_host} → {new_host} (config updated)",
            "info")
        self.sidebar.rebuild(self._shown_cameras())
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        if not self._logged_in:
            self._cam_lbl.setText("Locked")
            self.statusBar().showMessage("Log in to view the cameras.")
            return
        shown = self._shown_cameras()
        if not shown:
            self._cam_lbl.setText("No cameras")
            self.statusBar().showMessage(
                "No cameras configured — use “＋ Add Camera” or "
                "“Discover Cameras”.")
            return
        connected = sum(1 for cam in shown
                        if self._states.get(cam.id) == "connected")
        self._cam_lbl.setText(f"● {connected}/{len(shown)} connected")
        self.statusBar().clearMessage()

    def _refresh_tiles(self) -> None:
        for tile in self._tiles:
            stats = None
            record = self._manager.get(tile.camera_id)
            if record is not None:
                stats = record["thread"].stats.snapshot()
            tile.refresh(stats)

    def _tick_stats(self) -> None:
        sample = self._system.snapshot()
        self._cpu_lbl.setText(f"CPU {sample['cpu_pct']:3.0f}%")
        self._ram_lbl.setText(f"RAM {sample['mem_pct']:3.0f}%")
        self._net_lbl.setText(
            f"NET ↓{format_bps(sample['net_down_bps'])} "
            f"↑{format_bps(sample['net_up_bps'])}")
        decode: dict[str, int] = {}
        for record in self._manager.records():
            hw = record["thread"].hw_mode or "cpu"
            decode[hw] = decode.get(hw, 0) + 1
        if decode:
            parts = " ".join(f"{hw.upper()}×{count}"
                             for hw, count in sorted(decode.items()))
            self._gpu_lbl.setText(f"DECODE {parts}")
        else:
            self._gpu_lbl.setText("DECODE —")

    # ------------------------------------------------------------ actions

    def _add_camera(self) -> None:
        self._add_camera_at(None)

    def _add_camera_at(self, slot: int | None = None) -> None:
        dialog = CameraDialog(self._settings, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        cam = dialog.result_camera()
        self._settings.cameras.append(cam)
        # an empty tile knows its slot — park the new camera there
        enabled_idx = [i for i, c in enumerate(self._settings.cameras)
                       if c.enabled]
        if slot is not None and 0 <= slot < len(enabled_idx) - 1:
            index = self._settings.cameras.index(cam)
            target = enabled_idx[slot]
            self._settings.cameras.pop(index)
            self._settings.cameras.insert(target, cam)
        self._settings.save()
        self._apply_cameras(restart=True)

    def _edit_camera(self, cam_id: str) -> None:
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        dialog = CameraDialog(self._settings, camera=cam, parent=self)
        if dialog.exec() == QDialog.Accepted:
            dialog.result_camera()  # mutates the existing config in place
            self._settings.save()
            self._apply_cameras(restart=True)

    def _remove_camera(self, cam_id: str) -> None:
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        answer = QMessageBox.question(
            self, "Remove camera", f"Remove “{cam.name}” from the monitor?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._fullscreen is not None and self._fullscreen._camera.id == cam_id:
            self._close_fullscreen()
        self._recorder.stop(cam_id)
        self._ptz_dialogs.pop(cam_id, None)
        self._settings.remove(cam_id)
        self._settings.save()
        self._apply_cameras(restart=True)

    def _restart_camera(self, cam_id: str) -> None:
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        thread, buffer = self._manager.restart(cam, key=cam_id)
        thread.status.connect(self._on_status)
        for tile in self._tiles:
            if tile.camera_id == cam_id:
                tile.set_buffer(buffer)
                tile.set_quality(self._grid_quality())
                tile.set_status("connecting", "Restarting…")
                break

    def _reconnect_all(self) -> None:
        for tile in self._tiles:
            if tile.camera_id:
                self._restart_camera(tile.camera_id)

    def _discover(self) -> None:
        dialog = DiscoveryDialog(self._settings, parent=self)
        dialog.exec()  # adds selected cameras and saves settings itself
        self._apply_cameras(restart=True)

    # ------------------------------------------------------- drag & drop

    def _reorder_camera(self, cam_id: str, target_slot: int) -> None:
        enabled = [cam for cam in self._settings.cameras if cam.enabled]
        if target_slot < 0 or not enabled:
            return
        # a drop on the trailing empty tile means "move to the end"
        target_slot = min(target_slot, len(enabled) - 1)
        src_index = next((i for i, cam in enumerate(enabled)
                          if cam.id == cam_id), None)
        if src_index is None or src_index == target_slot:
            return
        full_indices = [i for i, cam in enumerate(self._settings.cameras)
                        if cam.enabled]
        src_full = full_indices[src_index]
        dst_full = full_indices[target_slot]
        cam = self._settings.cameras.pop(src_full)
        self._settings.cameras.insert(dst_full, cam)
        self._settings.save()
        self._apply_cameras(restart=False)  # rebind without reconnecting

    def _focus_camera(self, cam_id: str) -> None:
        for tile in self._tiles:
            if tile.camera_id == cam_id:
                tile.flash()
                break

    # ---------------------------------------------------------- fullscreen

    def _open_fullscreen(self, cam_id: str) -> None:
        if self._fullscreen is not None:
            return
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        window = FullscreenWindow(self._settings, self._manager, cam,
                                  owner=self)
        window.exit_requested.connect(self._close_fullscreen)
        self._fullscreen = window
        self.hide()
        window.showFullScreen()
        window.raise_()
        window.activateWindow()

    def _close_fullscreen(self) -> None:
        if self._fullscreen is not None:
            window = self._fullscreen
            self._fullscreen = None
            window.shutdown()
            window.close()
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------- snapshots & record

    def _visible_buffer(self, cam_id: str):
        if self._fullscreen is not None and self._fullscreen._camera.id == cam_id:
            return self._fullscreen._buffer
        return self._manager.get_buffer(cam_id)

    def _snapshot_camera(self, cam_id: str) -> None:
        cam = self._settings.get(cam_id)
        buffer = self._visible_buffer(cam_id)
        if cam is None or buffer is None:
            return
        path = snapshot_path(self._settings, cam)
        if save_snapshot(buffer, path):
            self._toasts.show(f"Snapshot saved: {path.name}", "success")
        else:
            self._toasts.show("Snapshot failed — no video frame available",
                              "error")

    def _toggle_recording(self, cam_id: str) -> None:
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        if self._recorder.is_recording(cam_id):
            self._recorder.stop(cam_id)
            return
        buffer = self._visible_buffer(cam_id)
        if buffer is None:
            return
        path = self._recorder.start(cam, buffer)
        if path:
            self._toasts.show(f"Recording started: {Path(path).name}", "info")

    def _on_recording_changed(self, cam_id: str, recording: bool,
                              path: str) -> None:
        for tile in self._tiles:
            if tile.camera_id == cam_id:
                tile.set_recording(recording)
        self.sidebar.set_recording(cam_id, recording)
        if path and path.startswith(self._settings.record_dir):
            self._toasts.show(f"Recording saved: {Path(path).name}",
                              "success")

    # ------------------------------------------------------------------ PTZ

    def _open_ptz(self, cam_id: str) -> None:
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        dialog = self._ptz_dialogs.get(cam_id)
        if dialog is None or not dialog.isVisible():
            pad_cam = effective_camera(cam, self._settings.network_mode)
            dialog = PTZPadDialog(pad_cam, parent=self)
            self._ptz_dialogs[cam_id] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    # ------------------------------------------------------ universal quality

    def _grid_quality(self) -> str:
        """Current grid stream quality label ('' for empty tiles)."""
        return "MAIN" if self._settings.grid_quality == "high" else "SUB"

    def _update_quality_action(self) -> None:
        high = self._settings.grid_quality == "high"
        self._quality_act.setText("Grid Quality: High" if high
                                  else "Grid Quality: Low")
        self._quality_act.setToolTip(
            "High: all cameras decode the main (full-resolution) stream;\n"
            "Low: all cameras decode the substream (CPU friendly).")

    def _toggle_quality(self, checked: bool) -> None:
        """Switch every grid camera between substream and main stream."""
        self._settings.grid_quality = "high" if checked else "low"
        self._settings.save()
        self._update_quality_action()
        for tile in self._tiles:
            if tile.camera_id:
                self._restart_camera(tile.camera_id)
        quality = self._grid_quality()
        self._toasts.show(
            f"Grid switched to {'high' if quality == 'MAIN' else 'low'} "
            "quality — reconnecting streams…", "info")

    # ---------------------------------------------------------- network mode

    def _update_network_action(self) -> None:
        remote = self._settings.network_mode == "remote"
        self._network_act.setText("Network: Remote" if remote
                                  else "Network: Local")
        self._network_act.setToolTip(
            "Remote: connect through each camera's remote host/URL "
            "(DDNS, VPN IP or forwarded port).")

    def _toggle_network(self, checked: bool) -> None:
        """Switch every camera between the local and remote endpoint."""
        self._settings.network_mode = "remote" if checked else "local"
        self._settings.save()
        self._update_network_action()
        for tile in self._tiles:
            if tile.camera_id:
                self._restart_camera(tile.camera_id)
        # close existing PTZ pads so they reconnect to the right endpoint
        for dialog in list(self._ptz_dialogs.values()):
            dialog.close()
        self._ptz_dialogs.clear()
        mode = self._settings.network_mode
        self._toasts.show(
            f"Switched to {mode} network — reconnecting streams…", "info")

    # --------------------------------------------------------------- login

    def _update_login_button(self) -> None:
        self._login_menu.clear()
        if self._logged_in:
            self._login_btn.setText(f"👤 {self._settings.login_username}")
            self._login_btn.setToolTip(
                f"Signed in as {self._settings.login_username}")
            change_act = self._login_menu.addAction("Change Password…")
            change_act.triggered.connect(self._change_password)
            self._login_menu.addSeparator()
            logout_act = self._login_menu.addAction("Log Out")
            logout_act.triggered.connect(self._logout)
            self._login_btn.setPopupMode(QToolButton.InstantPopup)
        else:
            self._login_btn.setText("Login")
            self._login_btn.setToolTip("Log in to this monitor")
            self._login_btn.setPopupMode(QToolButton.DelayedPopup)

    def _open_login(self) -> None:
        if self._logged_in:
            return
        mode = "create" if not self._settings.has_login() else "login"
        dialog = LoginDialog(self._settings, mode=mode, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._logged_in = True
            self._stack.setCurrentIndex(0)
            self._apply_cameras(restart=True)
            self._update_login_button()
            self._toasts.show(
                f"Signed in as {self._settings.login_username}", "success")

    def _change_password(self) -> None:
        dialog = LoginDialog(self._settings, mode="change", parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._toasts.show("Password updated", "success")

    def _logout(self) -> None:
        self._logged_in = False
        self._lock_ui()
        self._toasts.show("Signed out", "info")

    def _lock_ui(self) -> None:
        """Stop the streams and put the lock page in front of the grid."""
        self._manager.stop_all()
        self._states.clear()
        self._stack.setCurrentIndex(1)
        self._update_login_button()
        self._update_status_bar()

    # ------------------------------------------------------------- dashboard

    def _open_dashboard(self) -> None:
        from .dashboard import DashboardDialog
        dialog = DashboardDialog(self, parent=self)
        dialog.exec()

    # ------------------------------------------------------------ shutdown

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._fullscreen is not None:
            self._close_fullscreen()
        self._settings.window_size = (self.width(), self.height())
        if self._demo_mode:
            # never persist demo cameras into the real configuration
            self._settings.cameras = [
                cam for cam in self._settings.cameras
                if not cam.rtsp_url.startswith("demo:")]
        self._settings.save()
        self._health.stop()
        self._recorder.stop_all()
        for dialog in list(self._ptz_dialogs.values()):
            dialog.close()
        self._manager.stop_all()
        super().closeEvent(event)
