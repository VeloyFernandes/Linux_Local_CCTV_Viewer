"""Settings persistence for the CCTV monitor.

Camera settings are stored as JSON in ``~/.config/onvif-cctv/cameras.json``.
Passwords are lightly obfuscated (base64) so they are not stored in plain
text, but this is not real encryption — treat the file as sensitive.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

APP_DIR = Path(os.environ.get("CCTV_CONFIG_DIR", Path.home() / ".config" / "onvif-cctv"))
CONFIG_PATH = APP_DIR / "cameras.json"

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = ""


def _obfuscate(text: str) -> str:
    try:
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    except Exception:
        return ""


def _deobfuscate(text: str) -> str:
    try:
        return base64.b64decode(text.encode("ascii")).decode("utf-8")
    except Exception:
        return text or ""


def hash_password(password: str) -> str:
    """Salt + SHA-256 hex, stored as ``salt$digest``."""
    salt = os.urandom(16).hex()
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    """Constant-time check against a hash produced by :func:`hash_password`."""
    if not stored or "$" not in stored:
        return False
    salt, _, digest = stored.partition("$")
    expected = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, expected)


@dataclass
class CameraConfig:
    """Configuration of a single camera."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "Camera"
    host: str = ""
    port: int = 80                    # ONVIF HTTP port
    username: str = DEFAULT_USERNAME
    password: str = DEFAULT_PASSWORD
    rtsp_url: str = ""                # manual override; empty = auto-detect via ONVIF
    serial: str = ""                  # camera hardware serial (unique IP-move matching)
    prefer_substream: bool = True     # low-resolution profile for the grid
    enabled: bool = True
    # -- remote access (off the local network) ----------------------------
    remote_host: str = ""             # hostname/IP used in Remote mode (DDNS/VPN/forwarded)
    remote_port: int = 0              # external ONVIF port (0 = same as local)
    remote_rtsp_port: int = 0         # external RTSP port (0 = default 554)
    remote_rtsp_url: str = ""         # optional full RTSP override for Remote mode

    def to_dict(self) -> dict:
        data = asdict(self)
        data["password"] = _obfuscate(self.password)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CameraConfig":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            name=str(data.get("name") or "Camera"),
            host=str(data.get("host") or ""),
            port=int(data.get("port") or 80),
            username=str(data.get("username") or DEFAULT_USERNAME),
            password=_deobfuscate(str(data.get("password") or "")),
            rtsp_url=str(data.get("rtsp_url") or ""),
            serial=str(data.get("serial") or ""),
            prefer_substream=bool(data.get("prefer_substream", True)),
            enabled=bool(data.get("enabled", True)),
            remote_host=str(data.get("remote_host") or ""),
            remote_port=int(data.get("remote_port") or 0),
            remote_rtsp_port=int(data.get("remote_rtsp_port") or 0),
            remote_rtsp_url=str(data.get("remote_rtsp_url") or ""),
        )


class AppSettings:
    """Application-wide settings plus the camera list."""

    def __init__(self) -> None:
        self.cameras: list[CameraConfig] = []
        self.default_username = DEFAULT_USERNAME
        self.default_password = DEFAULT_PASSWORD
        self.hw_accel = "auto"          # auto | cuda | vaapi | off
        self.grid_width = 1920          # decode width for grid tiles (1920 = 1080p)
        self.fullscreen_width = 1920    # decode width for the fullscreen view
        self.grid_quality = "high"      # universal grid quality: high (main 1080p) | low (substream)
        self.network_mode = "local"     # local | remote (off-LAN access)
        self.login_username: str = ""   # monitor login (top-right of the toolbar)
        self.login_pass_hash: str = ""  # salted SHA-256, see hash_password()
        self.window_size: tuple[int, int] | None = None
        self.show_sidebar: bool = True
        self.record_dir: str = str(Path.home() / "Videos" / "onvif-cctv")
        self.snapshot_dir: str = str(Path.home() / "Pictures" / "onvif-cctv")

    @classmethod
    def load(cls) -> "AppSettings":
        settings = cls()
        if not CONFIG_PATH.exists():
            return settings
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return settings
        settings.default_username = str(raw.get("default_username") or DEFAULT_USERNAME)
        settings.default_password = _deobfuscate(str(raw.get("default_password") or "")) or DEFAULT_PASSWORD
        settings.hw_accel = str(raw.get("hw_accel") or "auto")
        settings.grid_width = int(raw.get("grid_width") or 1920)
        settings.fullscreen_width = int(raw.get("fullscreen_width") or 1920)
        settings.grid_quality = str(raw.get("grid_quality") or "high")
        if settings.grid_quality not in ("low", "high"):
            settings.grid_quality = "high"
        settings.network_mode = str(raw.get("network_mode") or "local")
        if settings.network_mode not in ("local", "remote"):
            settings.network_mode = "local"
        settings.login_username = str(raw.get("login_username") or "")
        settings.login_pass_hash = str(raw.get("login_pass_hash") or "")
        settings.show_sidebar = bool(raw.get("show_sidebar", True))
        settings.record_dir = str(raw.get("record_dir") or settings.record_dir)
        settings.snapshot_dir = str(raw.get("snapshot_dir") or settings.snapshot_dir)
        size = raw.get("window_size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            try:
                settings.window_size = (int(size[0]), int(size[1]))
            except (TypeError, ValueError):
                settings.window_size = None
        for item in raw.get("cameras") or []:
            try:
                settings.cameras.append(CameraConfig.from_dict(item))
            except Exception:
                continue
        return settings

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        raw = {
            "version": 3,
            "default_username": self.default_username,
            "default_password": _obfuscate(self.default_password),
            "hw_accel": self.hw_accel,
            "grid_width": self.grid_width,
            "fullscreen_width": self.fullscreen_width,
            "grid_quality": self.grid_quality,
            "network_mode": self.network_mode,
            "login_username": self.login_username,
            "login_pass_hash": self.login_pass_hash,
            "window_size": list(self.window_size) if self.window_size else None,
            "show_sidebar": self.show_sidebar,
            "record_dir": self.record_dir,
            "snapshot_dir": self.snapshot_dir,
            "cameras": [cam.to_dict() for cam in self.cameras],
        }
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    def has_login(self) -> bool:
        """True once a monitor login (username + password) exists."""
        return bool(self.login_username and self.login_pass_hash)

    def get(self, cam_id: str) -> CameraConfig | None:
        for cam in self.cameras:
            if cam.id == cam_id:
                return cam
        return None

    def remove(self, cam_id: str) -> bool:
        for index, cam in enumerate(self.cameras):
            if cam.id == cam_id:
                del self.cameras[index]
                return True
        return False

    def default_name(self) -> str:
        return f"Camera {len(self.cameras) + 1}"


def effective_camera(cam: CameraConfig, network_mode: str = "local") -> CameraConfig:
    """Return the camera view for the active network mode.

    In Remote mode the remote host/RTSP override (DDNS, VPN IP, forwarded
    port) replaces the local endpoint; the original object is not modified.
    """
    if network_mode != "remote":
        return cam
    copy = replace(cam)
    if copy.remote_host:
        copy.host = copy.remote_host
    if copy.remote_port:
        copy.port = copy.remote_port
    if copy.remote_rtsp_url:
        copy.rtsp_url = copy.remote_rtsp_url
    return copy
