"""Simple recording (MP4) and snapshot management for individual cameras."""
from __future__ import annotations

import re
import time
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QThread, Signal

from ..config import AppSettings, CameraConfig
from .streamer import FrameBuffer


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_") or "camera"


def snapshot_path(settings: AppSettings, cam: CameraConfig,
                  ext: str = "jpg") -> Path:
    directory = Path(settings.snapshot_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return directory / f"{_safe_name(cam.name)}_{stamp}.{ext}"


def record_path(settings: AppSettings, cam: CameraConfig) -> Path:
    directory = Path(settings.record_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return directory / f"{_safe_name(cam.name)}_{stamp}.mp4"


def save_snapshot(buffer: FrameBuffer, path: Path) -> bool:
    """Write the newest frame in ``buffer`` to ``path`` (jpg/png)."""
    frame, _seq = buffer.latest()
    if frame is None or not frame.size:
        return False
    return cv2.imwrite(str(path), frame)


class RecorderThread(QThread):
    """Writes frames from a FrameBuffer to an MP4 file at a capped FPS.

    A passive consumer: it wakes on new frames and writes at most ``fps``
    frames per second, so recording never back-pressures the decoder.
    """

    finished = Signal(str)   # output path
    error = Signal(str)

    def __init__(self, buffer: FrameBuffer, path: Path, fps: float = 15.0,
                 parent=None):
        super().__init__(parent)
        self._buffer = buffer
        self._path = str(path)
        self._fps = max(1.0, float(fps))
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def wait_for_stop(self, timeout_ms: int = 8000) -> bool:
        return self.wait(timeout_ms)

    def run(self) -> None:
        writer = None
        seq = -1
        last_write = 0.0
        try:
            while not self._stop:
                frame, seq = self._buffer.wait_for_frame(seq, timeout=0.5)
                if self._stop:
                    break
                if frame is None:
                    continue
                if writer is None:
                    height, width = frame.shape[:2]
                    writer = cv2.VideoWriter(
                        self._path, cv2.VideoWriter_fourcc(*"mp4v"),
                        self._fps, (width, height))
                    if not writer.isOpened():
                        self.error.emit(
                            f"Could not open {self._path} for writing "
                            "(missing codec?)")
                        return
                now = time.monotonic()
                if now - last_write >= 1.0 / self._fps:
                    writer.write(frame)
                    last_write = now
                else:
                    time.sleep(0.01)
        except Exception as exc:
            self.error.emit(f"Recording failed: {exc}")
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
        self.finished.emit(self._path)


class RecordingManager(QObject):
    """Per-camera recording lifecycle (continuous MP4) and snapshots."""

    changed = Signal(str, bool, str)  # cam_id, is_recording, path

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._recorders: dict[str, RecorderThread] = {}

    def start(self, cam: CameraConfig, buffer: FrameBuffer) -> str | None:
        if cam.id in self._recorders:
            return None
        path = record_path(self._settings, cam)
        thread = RecorderThread(buffer, path, parent=self)
        thread.finished.connect(
            lambda _path, cid=cam.id: self._on_finished(cid, _path))
        thread.error.connect(
            lambda msg, cid=cam.id: self._on_error(cid, msg))
        self._recorders[cam.id] = thread
        thread.start()
        self.changed.emit(cam.id, True, str(path))
        return str(path)

    def stop(self, cam_id: str) -> str | None:
        thread = self._recorders.pop(cam_id, None)
        if thread is None:
            return None
        thread.request_stop()
        thread.wait_for_stop()
        return thread._path

    def is_recording(self, cam_id: str) -> bool:
        return cam_id in self._recorders

    def stop_all(self) -> None:
        for cam_id in list(self._recorders):
            self.stop(cam_id)

    # ----------------------------------------------------------- callbacks

    def _on_finished(self, cam_id: str, path: str) -> None:
        if self._recorders.get(cam_id) is not None:
            self._recorders.pop(cam_id, None)
        self.changed.emit(cam_id, False, path)

    def _on_error(self, cam_id: str, message: str) -> None:
        if self._recorders.get(cam_id) is not None:
            self._recorders.pop(cam_id, None)
        self.changed.emit(cam_id, False, message)
