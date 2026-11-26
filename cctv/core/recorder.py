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

    # NB: must not be called ``finished`` — that would shadow the built-in
    # QThread.finished() signal that Qt emits itself when run() returns.
    saved = Signal(str)      # output path
    error = Signal(str)

    def __init__(self, buffer: FrameBuffer, path: Path, fps: float = 15.0,
                 parent=None):
        super().__init__(parent)
        self._buffer = buffer
        self._path = str(path)
        self._fps = max(1.0, float(fps))
        self._stop = False

    @property
    def path(self) -> str:
        return self._path

    def set_buffer(self, buffer: FrameBuffer) -> None:
        """Follow a new frame source (stream restarted / view switched)."""
        self._buffer = buffer

    def request_stop(self) -> None:
        self._stop = True

    def wait_for_stop(self, timeout_ms: int = 8000) -> bool:
        return self.wait(timeout_ms)

    def run(self) -> None:
        writer = None
        size: tuple[int, int] | None = None
        buffer = None
        seq = -1
        last_write = 0.0
        try:
            while not self._stop:
                if buffer is not self._buffer:
                    buffer = self._buffer
                    seq = -1
                frame, seq = buffer.wait_for_frame(seq, timeout=0.5)
                if self._stop:
                    break
                if frame is None:
                    continue
                if writer is None:
                    height, width = frame.shape[:2]
                    size = (width, height)
                    writer = cv2.VideoWriter(
                        self._path, cv2.VideoWriter_fourcc(*"mp4v"),
                        self._fps, (width, height))
                    if not writer.isOpened():
                        writer = None
                        self.error.emit(
                            f"Could not open {self._path} for writing "
                            "(missing codec?)")
                        return
                now = time.monotonic()
                if now - last_write >= 1.0 / self._fps:
                    # the stream resolution can change mid-recording (quality
                    # switch, fullscreen); VideoWriter silently drops frames
                    # of a different size, so scale them to the file's size
                    if (frame.shape[1], frame.shape[0]) != size:
                        frame = cv2.resize(frame, size,
                                           interpolation=cv2.INTER_AREA)
                    writer.write(frame)
                    last_write = now
                else:
                    time.sleep(0.01)
        except Exception as exc:
            self.error.emit(f"Recording failed: {exc}")
            return
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
        if writer is not None:
            self.saved.emit(self._path)


class RecordingManager(QObject):
    """Per-camera recording lifecycle (continuous MP4) and snapshots."""

    changed = Signal(str, bool, str)  # cam_id, is_recording, path
    failed = Signal(str, str)         # cam_id, error message

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._recorders: dict[str, RecorderThread] = {}

    def start(self, cam: CameraConfig, buffer: FrameBuffer) -> str | None:
        if cam.id in self._recorders:
            return None
        path = record_path(self._settings, cam)
        thread = RecorderThread(buffer, path, parent=self)
        thread.saved.connect(
            lambda _path, cid=cam.id, t=thread: self._on_saved(cid, t, _path))
        thread.error.connect(
            lambda msg, cid=cam.id, t=thread: self._on_error(cid, t, msg))
        thread.finished.connect(thread.deleteLater)
        self._recorders[cam.id] = thread
        thread.start()
        self.changed.emit(cam.id, True, "")
        return str(path)

    def stop(self, cam_id: str) -> str | None:
        thread = self._recorders.pop(cam_id, None)
        if thread is None:
            return None
        thread.request_stop()
        thread.wait_for_stop()
        # the "saved" signal is queued and arrives after this; report the
        # state change now so the UI updates immediately
        self.changed.emit(cam_id, False, "")
        return thread.path

    def set_buffer(self, cam_id: str, buffer: FrameBuffer) -> None:
        """Point an active recording at a new frame buffer."""
        thread = self._recorders.get(cam_id)
        if thread is not None and buffer is not None:
            thread.set_buffer(buffer)

    def is_recording(self, cam_id: str) -> bool:
        return cam_id in self._recorders

    def stop_all(self) -> None:
        for cam_id in list(self._recorders):
            self.stop(cam_id)

    # ----------------------------------------------------------- callbacks

    def _on_saved(self, cam_id: str, thread: RecorderThread,
                  path: str) -> None:
        # only drop the entry if it still belongs to *this* recorder — a new
        # recording may already have been started for the same camera
        if self._recorders.get(cam_id) is thread:
            self._recorders.pop(cam_id, None)
        self.changed.emit(cam_id, self.is_recording(cam_id), path)

    def _on_error(self, cam_id: str, thread: RecorderThread,
                  message: str) -> None:
        if self._recorders.get(cam_id) is thread:
            self._recorders.pop(cam_id, None)
            self.changed.emit(cam_id, False, "")
        self.failed.emit(cam_id, message)
