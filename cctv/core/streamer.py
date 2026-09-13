"""Low-latency RTSP streaming: one decode thread per camera + shared frame buffers.

Design goals
------------
- Each camera gets its own :class:`StreamThread` that keeps a
  :class:`FrameBuffer` fresh.  Decoding never blocks the UI.
- The UI pulls the newest frame from each buffer with a QTimer, so a slow or
  stalled camera cannot stall the grid, and decode bursts are coalesced
  automatically (old frames are *dropped* — only the latest is kept).
- Streams reconnect forever with exponential backoff, and re-resolve the RTSP
  URL over ONVIF if repeated connects fail (camera may have moved).
- Hardware-accelerated decoding (CUDA / VA-API) is tried first and the
  actually-used backend is reported per stream.
"""
from __future__ import annotations

import math
import re
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from ..config import AppSettings, CameraConfig
from .onvif_client import (open_capture, probe_onvif_camera,
                           resolve_rtsp_url, url_tcp_status)
from .onvif_discovery import discover_onvif

_HW_MODEL_RE = re.compile(r"IPC-[A-Z0-9-]+", re.IGNORECASE)


def _model_from_name(name: str) -> str:
    """Extract the Imou/Dahua hardware model (e.g. IPC-C22EP-D) from a name."""
    match = _HW_MODEL_RE.search(name or "")
    return match.group(0) if match else ""


class StreamStats:
    """Mutable per-stream statistics (written by the worker, read by the UI)."""

    __slots__ = ("state", "width", "height", "fps", "bitrate_kbps",
                 "latency_ms", "hw_mode", "frames", "reconnects")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = "stopped"
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.bitrate_kbps = 0.0
        self.latency_ms = 0.0
        self.hw_mode = ""
        self.frames = 0
        self.reconnects = 0

    def snapshot(self) -> dict:
        return {key: getattr(self, key) for key in self.__slots__}


class FrameBuffer:
    """Thread-safe buffer holding the newest frame for a camera.

    ``put`` overwrites whatever was there (frame dropping), and waiting
    consumers are notified via a condition so they can sleep instead of poll.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0

    def put(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def latest(self) -> tuple[np.ndarray | None, int]:
        with self._cond:
            return self._frame, self._seq

    def clear(self) -> None:
        with self._cond:
            self._frame = None
            self._seq += 1
            self._cond.notify_all()

    def wait_for_frame(self, after_seq: int, timeout: float = 0.5):
        """Block until a frame newer than ``after_seq`` arrives.

        Returns ``(frame, seq)`` or ``(None, seq)`` on timeout.
        """
        with self._cond:
            if self._seq != after_seq:
                return self._frame, self._seq
            self._cond.wait(timeout)
            if self._seq != after_seq:
                return self._frame, self._seq
            return None, self._seq


class StreamThread(QThread):
    """Decodes one RTSP stream and keeps a FrameBuffer fresh.

    Frames are resized to ``target_width`` before publication so eight
    streams stay cheap to move and paint; stats report the *original*
    resolution.  Reconnects forever until :meth:`request_stop` is called.
    """

    status = Signal(str, str, str)  # camera_id, state, detail
    # camera_id, old_host, new_host — camera answered at a different IP
    relocated = Signal(str, str, str)

    def __init__(self, camera: CameraConfig, buffer: FrameBuffer, *,
                 high_res: bool = False, prefer_main: bool | None = None,
                 hw_accel: str = "auto", remote: bool = False,
                 target_width: int = 640, parent=None):
        super().__init__(parent)
        self._cfg = camera
        self._buf = buffer
        self._high_res = high_res
        # prefer_main is an explicit override; defaults to high_res semantics
        self._prefer_main = high_res if prefer_main is None else prefer_main
        self._hw = hw_accel
        self._width = max(320, int(target_width))
        self._stop = threading.Event()
        # a stored URL is only a fallback: streams are always re-resolved via
        # ONVIF so the substream/main-stream switch follows the quality
        # setting (discovery used to bake in the substream URL)
        self._remote = remote
        resolve_host = camera.host
        onvif_port = camera.port
        rtsp_port: int | None = None
        rebind = False
        manual = camera.rtsp_url
        if remote:
            if camera.remote_host:
                resolve_host = camera.remote_host
                rebind = True  # ONVIF URLs reference the internal IP
            if camera.remote_port:
                onvif_port = camera.remote_port
            if camera.remote_rtsp_port:
                rtsp_port = camera.remote_rtsp_port
            if camera.remote_rtsp_url:
                manual = camera.remote_rtsp_url
        self._resolve_host = resolve_host
        self._onvif_port = onvif_port
        self._rtsp_port = rtsp_port
        self._rebind = rebind
        self._manual_url = manual or None
        self._url = None
        self._resolved = False
        self._demo = self._manual_url.startswith("demo:") if self._manual_url else False
        self.stats = StreamStats()
        self.hw_mode = ""

    # ------------------------------------------------------------------ API

    def request_stop(self) -> None:
        self._stop.set()

    def wait_for_stop(self, timeout_ms: int = 10000) -> bool:
        return self.wait(timeout_ms)

    # ------------------------------------------------------------- main loop

    def run(self) -> None:  # executed in the worker thread
        if self._demo:
            self._run_demo()
            return
        cfg = self._cfg
        backoff = 1.0
        failures = 0
        while not self._stop.is_set():
            if not self._resolved:
                self._set_state("connecting", "Resolving stream URL via ONVIF…")
                url = resolve_rtsp_url(self._resolve_host, self._onvif_port,
                                       cfg.username, cfg.password,
                                       prefer_main=self._prefer_main,
                                       rebind_host=(self._resolve_host
                                                    if self._rebind else None),
                                       rtsp_port=self._rtsp_port)
                if not url and self._manual_url:
                    url = self._manual_url
                if not url:
                    self._set_state("error", "No RTSP URL could be determined")
                    if self._sleep(backoff):
                        break
                    backoff = min(backoff * 1.5, 15.0)
                    continue
                self._url = url
                self._resolved = True

            self._set_state("connecting", "Opening RTSP stream…")
            cap, hw = self._open(self._url)
            if cap is None:
                failures += 1
                self.stats.reconnects += 1
                tcp_status = url_tcp_status(self._url or "")
                if tcp_status == "refused":
                    detail = ("Connection refused — camera unreachable "
                              "(offline or IP changed)")
                    # the host is reachable enough to refuse: don't hammer it
                    backoff = max(backoff, 5.0)
                elif tcp_status == "timeout":
                    detail = "Connection timed out — camera unreachable"
                    backoff = max(backoff, 3.0)
                else:
                    detail = f"Connect failed — retry {failures}"
                self._set_state("reconnecting", detail)
                if failures >= 3:
                    new_host = self._find_relocated_host()
                    if new_host:
                        old_host = self._resolve_host
                        self._resolve_host = new_host
                        self._manual_url = None  # stale URL points elsewhere
                        self._url = None
                        self._resolved = False
                        failures = 0
                        backoff = 1.0
                        self.relocated.emit(self._cfg.id, old_host, new_host)
                        self._set_state(
                            "connecting",
                            f"Camera moved to {new_host} — reconnecting…")
                        continue
                    # Camera may have moved / URL changed: re-resolve.
                    self._url = None
                    self._resolved = False
                    failures = 0
                if self._sleep(backoff):
                    break
                backoff = min(backoff * 1.5, 30.0)
                continue

            self.hw_mode = hw
            backoff = 1.0
            failures = 0
            self._set_state("connected", "Connected")

            # -- decode loop: keep only the newest frame, drop the rest ------
            win_start = time.monotonic()
            frames = 0
            bytes_total = 0
            read_ms_sum = 0.0
            while not self._stop.is_set():
                t0 = time.perf_counter()
                ok, frame = cap.read()
                read_ms = (time.perf_counter() - t0) * 1000.0
                if self._stop.is_set():
                    break
                if ok and frame is not None and frame.size:
                    frames += 1
                    read_ms_sum += read_ms
                    bytes_total += int(frame.nbytes)
                    self.stats.width = int(frame.shape[1])
                    self.stats.height = int(frame.shape[0])
                    try:
                        if frame.shape[1] > self._width:
                            scale = self._width / float(frame.shape[1])
                            frame = cv2.resize(
                                frame, (self._width,
                                        max(1, int(frame.shape[0] * scale))),
                                interpolation=cv2.INTER_AREA)
                        self._buf.put(np.ascontiguousarray(frame).copy())
                    except Exception:
                        pass
                    elapsed = time.monotonic() - win_start
                    if elapsed >= 1.0:
                        self.stats.fps = frames / elapsed
                        self.stats.bitrate_kbps = bytes_total * 8.0 / elapsed / 1000.0
                        self.stats.latency_ms = read_ms_sum / max(1, frames)
                        self.stats.frames += frames
                        win_start = time.monotonic()
                        frames = bytes_total = 0
                        read_ms_sum = 0.0
                else:
                    break

            try:
                cap.release()
            except Exception:
                pass
            if self._stop.is_set():
                break
            self.stats.reconnects += 1
            self._set_state("reconnecting", "Stream lost — reconnecting…")
            if self._sleep(backoff):
                break
            backoff = min(backoff * 1.5, 15.0)
        self._set_state("stopped", "Stopped")

    def _set_state(self, state: str, detail: str) -> None:
        self.stats.state = state
        if state != "connected":
            self.stats.fps = 0.0
            self.stats.bitrate_kbps = 0.0
            self.stats.latency_ms = 0.0
        self.status.emit(self._cfg.id, state, detail)

    def _sleep(self, seconds: float) -> bool:
        """Sleep in small slices so stop stays responsive. True if stopped."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop.wait(min(0.25, end - time.monotonic())):
                return True
        return self._stop.is_set()

    def _open(self, url: str):
        """Open the URL, trying hardware acceleration first when enabled.

        HW attempts use short timeouts so a missing device fails fast; the
        final software attempt gets the normal (generous) timeout.  Returns
        ``(capture, backend_used)``.
        """
        prefs: list[str] = []
        if self._hw != "off":
            prefs = ["cuda", "vaapi"] if self._hw == "auto" else [self._hw]
        for hw in prefs + ["off"]:
            if self._stop.is_set():
                return None, "off"
            cap = open_capture(url, hw=hw, short=(hw != "off"))
            if cap is None:
                continue
            try:
                ok, frame = cap.read()
                if ok and frame is not None and frame.size:
                    return cap, (hw if hw != "off" else "cpu")
            except Exception:
                pass
            try:
                cap.release()
            except Exception:
                pass
        return None, "off"

    # --------------------------------------------------------- relocation

    def _find_relocated_host(self) -> str | None:
        """Find this camera at a new IP after a DHCP reassignment.

        Uses WS-Discovery and matches by the camera's hardware serial when
        known (exact), otherwise by the hardware model in the camera name.
        Relocates only when exactly one candidate remains, so two identical
        cameras are never cross-wired.
        """
        if self._remote:
            return None  # in remote mode discovery only sees the local LAN
        try:
            devices = discover_onvif(timeout=3.5)
        except Exception:
            return None
        hosts = {dev.get("host") for dev in devices}
        if self._resolve_host in hosts:
            return None  # still announced at its configured IP — not a move
        model = _model_from_name(self._cfg.name)
        serial = (self._cfg.serial or "").strip()
        candidates: list[str] = []
        for dev in devices:
            host = dev.get("host")
            if not host or host == self._resolve_host:
                continue
            scopes = (dev.get("scopes") or "").lower()
            if model and model.lower() not in scopes:
                continue
            if serial:
                info = probe_onvif_camera(host, int(dev.get("port") or 80),
                                          self._cfg.username,
                                          self._cfg.password, timeout=5.0)
                if info and (info.get("serial") or "").strip() == serial:
                    return host
                continue
            candidates.append(host)
        return candidates[0] if len(candidates) == 1 else None

    # ----------------------------------------------------------- demo source

    def _run_demo(self) -> None:
        cfg = self._cfg
        self.hw_mode = "cpu"
        self.stats.width, self.stats.height = 1920, 1080
        self._set_state("connected", "Demo source")
        width, height = 1920, 1080
        seed = sum(cfg.id.encode())
        while not self._stop.is_set():
            now = time.monotonic()
            self._buf.put(self._make_demo_frame(width, height, seed, now))
            self.stats.fps = 20.0
            self.stats.bitrate_kbps = 4096.0  # realistic-looking demo value
            self.stats.latency_ms = 2.0
            self._stop.wait(0.05)
        self._set_state("stopped", "Stopped")

    @staticmethod
    def _make_demo_frame(width: int, height: int, seed: int,
                         now: float) -> np.ndarray:
        # vectorised vertical gradient (fast enough for 8 × 1080p)
        gradient = np.linspace(40, 110, height, dtype=np.float32)
        frame = np.empty((height, width, 3), np.uint8)
        frame[:, :, 0] = gradient[:, None].astype(np.uint8)
        frame[:, :, 1] = (gradient * 0.5)[:, None].astype(np.uint8)
        frame[:, :, 2] = (gradient * 0.75)[:, None].astype(np.uint8)
        x = int((now * 220 + seed * 37) % (width + 400)) - 200
        y = int(height / 2 + 220 * math.sin(now + seed))
        cv2.circle(frame, (x, y), 60, (0, 180, 255), -1)
        cv2.rectangle(frame, (20, 20), (width - 20, height - 20),
                      (90, 90, 120), 3)
        cv2.putText(frame, time.strftime("%H:%M:%S"), (width - 340, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.8, (240, 240, 240), 3,
                    cv2.LINE_AA)
        return np.ascontiguousarray(frame)


# Streams that could not be joined in time — kept referenced so a running
# QThread is never destroyed by the garbage collector (Qt aborts on that).
_ZOMBIE_THREADS: list[StreamThread] = []


def _prune_zombies() -> None:
    _ZOMBIE_THREADS[:] = [t for t in _ZOMBIE_THREADS if not t.isFinished()]


class StreamManager(QObject):
    """Owns stream threads and frame buffers, keyed by camera id."""

    # cam_id, old_host, new_host — a camera answered at a new IP and the
    # saved configuration was updated to match.
    camera_relocated = Signal(str, str, str)

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._streams: dict[str, dict] = {}

    def _on_relocated(self, cam_id: str, old_host: str, new_host: str) -> None:
        cam = self._settings.get(cam_id)
        if cam is None:
            return
        cam.host = new_host
        if cam.rtsp_url and old_host in cam.rtsp_url:
            cam.rtsp_url = cam.rtsp_url.replace(old_host, new_host)
        try:
            self._settings.save()
        except Exception:
            pass
        self.camera_relocated.emit(cam_id, old_host, new_host)

    def _make_thread(self, cam: CameraConfig, buffer: FrameBuffer,
                     high_res: bool) -> StreamThread:
        width = (self._settings.fullscreen_width if high_res
                 else self._settings.grid_width)
        # fullscreen always uses the main stream; the grid follows the
        # universal quality toggle (low = substream, high = main stream)
        prefer_main = True if high_res else (
            self._settings.grid_quality == "high")
        thread = StreamThread(cam, buffer, high_res=high_res,
                              prefer_main=prefer_main,
                              hw_accel=self._settings.hw_accel,
                              remote=(self._settings.network_mode == "remote"),
                              target_width=width)
        thread.relocated.connect(self._on_relocated)
        return thread

    def start(self, cam: CameraConfig, key: str | None = None,
              high_res: bool = False) -> tuple[StreamThread, FrameBuffer]:
        key = key or cam.id
        self.stop(key)
        buffer = FrameBuffer()
        thread = self._make_thread(cam, buffer, high_res)
        self._streams[key] = {"thread": thread, "buffer": buffer, "camera": cam}
        thread.start()
        return thread, buffer

    def ensure_start(self, cam: CameraConfig, key: str | None = None,
                     high_res: bool = False) -> tuple[StreamThread, FrameBuffer]:
        key = key or cam.id
        record = self._streams.get(key)
        if record is not None:
            return record["thread"], record["buffer"]
        return self.start(cam, key=key, high_res=high_res)

    def restart(self, cam: CameraConfig, key: str | None = None,
                high_res: bool = False) -> tuple[StreamThread, FrameBuffer]:
        """Restart a stream, keeping the same buffer so tiles stay bound."""
        key = key or cam.id
        record = self._streams.get(key)
        buffer = record["buffer"] if record else FrameBuffer()
        self.stop(key)
        buffer.clear()
        thread = self._make_thread(cam, buffer, high_res)
        self._streams[key] = {"thread": thread, "buffer": buffer, "camera": cam}
        thread.start()
        return thread, buffer

    def get(self, key: str) -> dict | None:
        return self._streams.get(key)

    def get_buffer(self, key: str) -> FrameBuffer | None:
        record = self._streams.get(key)
        return record["buffer"] if record else None

    def records(self) -> list[dict]:
        return list(self._streams.values())

    def stop(self, key: str) -> None:
        record = self._streams.pop(key, None)
        if record is None:
            return
        thread: StreamThread = record["thread"]
        thread.request_stop()
        # every phase inside run() is time-bounded, but a thread can be mid
        # ONVIF-resolve (~14 s worst case); if the join times out, keep a
        # reference so it is never garbage-collected while running (Qt aborts
        # on that) and wait for it in stop_all()
        if not thread.wait_for_stop(timeout_ms=25000):
            _ZOMBIE_THREADS.append(thread)
        _prune_zombies()

    def stop_all(self) -> None:
        for key in list(self._streams):
            self.stop(key)
        # guarantee no unfinished thread reaches interpreter teardown
        for thread in list(_ZOMBIE_THREADS):
            if not thread.isFinished():
                thread.wait_for_stop(timeout_ms=30000)
        _prune_zombies()
