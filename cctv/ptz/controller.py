"""ONVIF PTZ controller using standard ContinuousMove / Stop commands.

Conventions (ONVIF ``PanTilt`` space):
- ``x > 0`` pans right, ``x < 0`` pans left.
- ``y > 0`` tilts up,   ``y < 0`` tilts down.
- ``Zoom.x > 0`` zooms in.

Every movement command carries a short ``Timeout`` (minimum 1 s) so that if
the controlling process dies, the camera stops by itself.  All SOAP calls run
on daemon threads with hard timeouts, so a dead camera can never block the
application.

Imou/Dahua quirks handled here:
- the PTZ profile token may come from the PTZ service *or* the media service
  (the latter is what Imou cameras usually accept),
- a zero ``Zoom`` velocity is omitted (some firmware rejects it),
- ``Stop`` falls back to a pan/tilt-only variant when the full stop fails.
"""
from __future__ import annotations

import math
import threading

from ..config import CameraConfig
from ..core.onvif_client import HAS_ONVIF, _run_with_timeout

try:  # pragma: no cover — import guarded like the rest of the ONVIF stack
    from onvif import ONVIFCamera  # type: ignore
except Exception:
    ONVIFCamera = None


class PTZController:
    """Lazy ONVIF PTZ service wrapper for one camera."""

    def __init__(self, camera: CameraConfig):
        self._cam = camera
        self._lock = threading.Lock()
        self._tokens: list[str] = []
        self._ptz_service = None
        self._available = False
        self._last_error = ""

    # ------------------------------------------------------------ lifecycle

    def setup(self, timeout: float = 8.0) -> bool:
        """Connect over ONVIF and resolve PTZ profile tokens.

        Tokens from the PTZ service are preferred; media-profile tokens are
        used as a fallback (Imou cameras typically accept those).
        """
        if not HAS_ONVIF:
            self._last_error = "onvif-zeep is not installed"
            return False

        result: dict = {}

        def call():
            try:
                camera = ONVIFCamera(self._cam.host, self._cam.port,
                                     self._cam.username, self._cam.password)
                media = camera.create_media_service()
                media_profiles = media.GetProfiles() or []
                media_tokens: list[str] = []
                for profile in media_profiles:
                    try:
                        token = getattr(profile, "token", None)
                        if token:
                            media_tokens.append(str(token))
                    except Exception:
                        continue

                ptz = camera.create_ptz_service()
                ptz_tokens: list[str] = []
                try:
                    for profile in (ptz.GetProfiles() or []):
                        try:
                            token = getattr(profile, "token", None)
                            if token:
                                ptz_tokens.append(str(token))
                        except Exception:
                            continue
                except Exception:
                    pass  # some cameras do not expose PTZ profiles

                tokens: list[str] = []
                for token in ptz_tokens + media_tokens:
                    if token not in tokens:
                        tokens.append(token)
                if not tokens:
                    raise RuntimeError("camera exposes no PTZ profiles")
                result["data"] = (tokens[:2], ptz)
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        thread.join(timeout)
        if "data" in result:
            self._tokens, self._ptz_service = result["data"]
            self._available = True
            return True
        self._last_error = result.get("error") or "ONVIF timeout"
        return False

    def is_available(self) -> bool:
        return self._available

    @property
    def last_error(self) -> str:
        return self._last_error

    # ------------------------------------------------------------ commands

    def continuous_move(self, pan: float, tilt: float, zoom: float = 0.0,
                        duration: float = 0.3) -> bool:
        """Move at the given velocity for ``duration`` seconds (auto-stops)."""
        if not self._available:
            return False
        timeout_s = max(1, int(math.ceil(duration)))
        with self._lock:

            def call():
                velocity = {"PanTilt": {"x": float(pan), "y": float(tilt)}}
                if zoom:  # Imou/Dahua reject a zero Zoom velocity
                    velocity["Zoom"] = {"x": float(zoom)}
                for token in self._tokens:
                    try:
                        self._ptz_service.ContinuousMove({
                            "ProfileToken": token,
                            "Velocity": velocity,
                            "Timeout": f"PT{timeout_s}S",
                        })
                        return True
                    except Exception:
                        continue
                return False

            ok = bool(_run_with_timeout(call, (), 5.0))
            if not ok:
                self._last_error = "ContinuousMove was rejected by the camera"
            return ok

    def stop(self) -> bool:
        if not self._available:
            return False
        with self._lock:

            def call():
                for token in self._tokens:
                    # full stop first; some Imou firmware rejects a Stop
                    # that references zoom — fall back to pan/tilt-only
                    try:
                        self._ptz_service.Stop({
                            "ProfileToken": token,
                            "PanTilt": True,
                            "Zoom": True,
                        })
                        return True
                    except Exception:
                        try:
                            self._ptz_service.Stop({
                                "ProfileToken": token,
                                "PanTilt": True,
                            })
                            return True
                        except Exception:
                            continue
                return False

            ok = bool(_run_with_timeout(call, (), 5.0))
            if not ok:
                self._last_error = "Stop was rejected by the camera"
            return ok

    # ------------------------------------------------------- info / presets

    def get_position(self, timeout: float = 4.0):
        """Return ``(pan, tilt, zoom)`` in degrees, or ``None``."""
        if not self._available or not self._tokens:
            return None
        token = self._tokens[0]
        with self._lock:

            def call():
                status = self._ptz_service.GetStatus({"ProfileToken": token})
                position = getattr(status, "Position", None)
                if position is None:
                    return None
                pan = getattr(getattr(position, "PanTilt", None), "x", None)
                tilt = getattr(getattr(position, "PanTilt", None), "y", None)
                zoom = getattr(getattr(position, "Zoom", None), "x", None)
                return (pan, tilt, zoom)

            return _run_with_timeout(call, (), timeout)

    def set_preset(self, preset_token: str, timeout: float = 4.0) -> bool:
        """Store the current position under ``preset_token``."""
        if not self._available or not self._tokens:
            return False
        token = self._tokens[0]
        with self._lock:

            def call():
                self._ptz_service.SetPreset({
                    "ProfileToken": token,
                    "PresetToken": str(preset_token),
                })
                return True

            return bool(_run_with_timeout(call, (), timeout))

    def goto_preset(self, preset_token: str, speed: float = 0.5,
                    timeout: float = 4.0) -> bool:
        """Move to a stored preset at the given speed (0..1)."""
        if not self._available or not self._tokens:
            return False
        token = self._tokens[0]
        with self._lock:

            def call():
                self._ptz_service.GotoPreset({
                    "ProfileToken": token,
                    "PresetToken": str(preset_token),
                    "Speed": {
                        "PanTilt": {"x": float(speed), "y": float(speed)},
                        "Zoom": {"x": float(speed)},
                    },
                })
                return True

            return bool(_run_with_timeout(call, (), timeout))
