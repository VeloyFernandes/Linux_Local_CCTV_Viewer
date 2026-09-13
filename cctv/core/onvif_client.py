"""ONVIF helpers: resolve RTSP URLs from cameras and open/verify streams."""
from __future__ import annotations

import concurrent.futures
import os
import re
import socket
import threading
import time
import urllib.parse

import cv2

try:  # optional at runtime: URL guessing still works without onvif-zeep
    from onvif import ONVIFCamera  # type: ignore
    HAS_ONVIF = True
except Exception:
    HAS_ONVIF = False

# Serialises VideoCapture opens: OPENCV_FFMPEG_CAPTURE_OPTIONS is read from the
# process environment on every open, so concurrent opens would race.
_OPEN_LOCK = threading.Lock()


def _quote(text: str) -> str:
    return urllib.parse.quote(str(text), safe="")


def _inject_creds(url: str, username: str, password: str) -> str:
    if "://" not in url or "@" in url.split("://", 1)[1]:
        return url  # credentials already embedded
    scheme, rest = url.split("://", 1)
    return f"{scheme}://{_quote(username)}:{_quote(password)}@{rest}"


def _profile_area(profile) -> int:
    try:
        enc = profile.VideoEncoderConfiguration
        if isinstance(enc, (list, tuple)):
            enc = enc[0] if enc else None
        if enc is None:
            return 0
        res = enc.Resolution
        return int(res.Width or 0) * int(res.Height or 0)
    except Exception:
        return 0


def _stream_quality_score(profile, url: str) -> float:
    """Estimate how main-stream-like a profile/URI is.

    Imou/Dahua cameras often omit resolution info, so the URI itself is the
    most reliable signal: ``subtype=0`` = main, ``subtype=1`` = substream
    (Hikvision-style: ``/Channels/101`` vs ``/Channels/102``).
    """
    score = 0.0
    low = url.lower()
    if "subtype=0" in low:
        score += 10.0
    elif "subtype=1" in low:
        score -= 10.0
    if "channels/101" in low:
        score += 5.0
    elif "channels/102" in low:
        score -= 5.0
    token_name = " ".join(str(getattr(profile, attr, "") or "")
                           for attr in ("token", "Name", "name")).lower()
    if any(word in token_name for word in ("sub", "secondary", "minor")):
        score -= 3.0
    elif any(word in token_name for word in ("main", "primary", "major")):
        score += 3.0
    area = _profile_area(profile)
    if area > 0:
        score += min(area, 2_073_600) / 2_073_600 * 0.1  # 1080p = +0.1 max
    return score


def _onvif_probe(host: str, port: int, username: str, password: str) -> dict | None:
    """Query a camera over ONVIF; returns main/substream RTSP URLs."""
    camera = ONVIFCamera(host, port, username, password)
    info = camera.devicemgmt.GetDeviceInformation()
    manufacturer = getattr(info, "Manufacturer", "") or ""
    model = getattr(info, "Model", "") or ""
    serial = getattr(info, "SerialNumber", "") or ""
    media = camera.create_media_service()
    profiles = media.GetProfiles() or []
    scored: list[tuple[float, str]] = []
    for profile in profiles:
        try:
            uri = media.GetStreamUri({
                "StreamSetup": {
                    "Stream": "RTP-Unicast",
                    "Transport": {"Protocol": "RTSP"},
                },
                "ProfileToken": profile.token,
            })
        except Exception:
            continue
        if uri is None or not getattr(uri, "Uri", ""):
            continue
        url = _inject_creds(uri.Uri, username, password)
        scored.append((_stream_quality_score(profile, url), url))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return {
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "name": f"{manufacturer} {model}".strip() or "ONVIF Camera",
        "sub_url": scored[0][1],    # most substream-like
        "main_url": scored[-1][1],  # most main-stream-like
    }


def _run_with_timeout(func, args: tuple, timeout: float):
    """Run ``func`` on a daemon thread and wait at most ``timeout`` seconds.

    A daemon thread is used deliberately: zeep can hang indefinitely on dead
    hosts, and a daemon thread never blocks interpreter shutdown.
    """
    box: dict = {}

    def target():
        try:
            box["result"] = func(*args)
        except Exception:
            box["result"] = None

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    return box.get("result")


def probe_onvif_camera(host: str, port: int, username: str, password: str,
                       timeout: float = 6.0) -> dict | None:
    """ONVIF probe with a hard timeout."""
    if not HAS_ONVIF or not host:
        return None
    return _run_with_timeout(_onvif_probe, (host, port, username, password), timeout)


# --------------------------------------------------------------------------
# Stream opening (OpenCV / FFmpeg backend, low-latency options, HW accel)
# --------------------------------------------------------------------------

def _ffmpeg_options(hw: str, short: bool) -> str:
    opts = [
        "rtsp_transport;tcp",        # reliable transport, no UDP jitter/loss
        "fflags;nobuffer",           # decode immediately, don't buffer
        "flags;low_delay",
        "max_delay;500000",
        "allowed_media_types;video",
        "analyzeduration;1000000",
        "probesize;2000000",
    ]
    opts += (["stimeout;3000000", "rw_timeout;4000000"] if short
             else ["stimeout;8000000", "rw_timeout;15000000"])
    if hw == "cuda":
        opts.append("hwaccel;cuda")
    elif hw == "vaapi":
        opts.append("hwaccel;vaapi")
        if os.path.exists("/dev/dri/renderD128"):
            opts.append("hwaccel_device;/dev/dri/renderD128")
    return "|".join(opts)


def tcp_reachable(host: str, port: int, timeout: float = 1.5) -> str | None:
    """Quick TCP connect probe.

    Returns ``None`` when the port accepts a connection, otherwise a short
    reason string (``"refused"``, ``"timeout"`` or ``"unreachable"``).  Used
    as a preflight so a dead camera fails instantly instead of letting
    OpenCV/FFmpeg flood the log with connection attempts.
    """
    if not host:
        return "unreachable"
    try:
        with socket.create_connection((host, int(port or 554)),
                                      timeout=timeout):
            return None
    except (ConnectionRefusedError, ConnectionResetError):
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError:
        return "unreachable"


def url_tcp_status(url: str) -> str | None:
    """Preflight-check the host/port of a stream URL (None = reachable)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return None
    if parts.scheme not in ("rtsp", "rtmp", "http", "https"):
        return None
    if not parts.hostname:
        return None
    return tcp_reachable(parts.hostname, parts.port or 554)


def open_capture(url: str, hw: str = "off", short: bool = False):
    """Open an RTSP URL with OpenCV's FFmpeg backend (thread-safe).

    Open/read timeouts are passed in the constructor params (the documented
    mechanism) so a dead or silently-filtered remote host cannot stall a
    stream thread for OpenCV's 30 s default.  ``short`` uses tighter bounds
    for quick probes.

    A TCP preflight runs first: when the camera's RTSP port is closed
    (refused) or unreachable this returns ``None`` immediately and OpenCV is
    never invoked, avoiding the FFmpeg warning flood.

    Note: OpenCV 5 rejects ``CAP_PROP_BUFFERSIZE`` in the params list and
    bails out entirely, so it is applied separately after open.
    """
    if url_tcp_status(url) is not None:
        return None
    open_ms = 3000 if short else 8000
    read_ms = 3000 if short else 8000
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_ms,
              cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_ms]
    with _OPEN_LOCK:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _ffmpeg_options(hw, short)
        try:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
        except Exception:
            cap = cv2.VideoCapture()
        if cap.isOpened():
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # keep latency minimal
            except Exception:
                pass
            return cap
        cap.release()
        return None


def check_url(url: str, short: bool = True) -> bool:
    """Open a URL and verify that a video frame actually arrives."""
    cap = open_capture(url, hw="off", short=short)
    if cap is None:
        return False
    try:
        ok, frame = cap.read()
        return bool(ok and frame is not None and frame.size)
    except Exception:
        return False
    finally:
        cap.release()


def find_working_url(candidates: list[str], timeout: float = 8.0) -> str | None:
    """Try candidates in parallel and return the first one that yields video."""
    if not candidates:
        return None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(candidates)))
    try:
        futures = {pool.submit(check_url, url): url for url in candidates}
        deadline = time.monotonic() + timeout
        pending = set(futures)
        while pending and time.monotonic() < deadline:
            done, pending = concurrent.futures.wait(
                pending, timeout=0.5,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                try:
                    if future.result():
                        return futures[future]
                except Exception:
                    pass
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _guess_candidates(host: str, username: str, password: str,
                      prefer_main: bool = False, rtsp_port: int = 554) -> list[str]:
    auth = f"{_quote(username)}:{_quote(password)}"
    base = f"rtsp://{auth}@{host}:{int(rtsp_port or 554)}"
    if prefer_main:
        return [
            f"{base}/cam/realmonitor?channel=1&subtype=0",     # Imou/Dahua main
            f"{base}/Streaming/Channels/101",                  # Hikvision main
            f"{base}/live",
            f"{base}/media/video1",
            f"{base}/onvif1",
            f"{base}/h264",
        ]
    return [
        f"{base}/cam/realmonitor?channel=1&subtype=1",         # Imou/Dahua substream
        f"{base}/Streaming/Channels/102",                      # Hikvision substream
        f"{base}/cam/realmonitor?channel=1&subtype=0",
        f"{base}/Streaming/Channels/101",
        f"{base}/live",
        f"{base}/media/video1",
    ]


def _rebase_url(url: str, host: str, rtsp_port: int) -> str:
    """Rewrite an RTSP URL to point at a different host/port.

    ONVIF GetStreamUri returns the camera's *internal* address; behind a
    DDNS/port-forward this must be rebound to the public endpoint.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        netloc = host or parts.hostname
        if parts.username:
            userinfo = urllib.parse.quote(parts.username, safe="")
            if parts.password is not None:
                userinfo += ":" + urllib.parse.quote(parts.password, safe="")
            netloc = f"{userinfo}@{netloc}"
        port = rtsp_port or parts.port or 554
        new = parts._replace(netloc=f"{netloc}:{port}")
        return urllib.parse.urlunsplit(new)
    except Exception:
        return url


def _url_matches_quality(url: str, prefer_main: bool) -> bool:
    """True when the URL's substream/main markers match the request."""
    low = url.lower()
    if "subtype=1" in low or "channels/102" in low:
        return not prefer_main
    if "subtype=0" in low or "channels/101" in low:
        return prefer_main
    return True  # unknown pattern — trust ONVIF


def _quality_swap_variant(url: str, prefer_main: bool) -> str | None:
    """Return the same URL switched to main/substream, or None if unknown."""
    if "subtype=" in url:
        new = (url.replace("subtype=1", "subtype=0") if prefer_main
               else url.replace("subtype=0", "subtype=1"))
        return new if new != url else None
    match = re.search(r"/Channels/(\d+)", url)
    if match:
        channel = int(match.group(1))
        target = 101 if prefer_main else 102
        if channel != target:
            return url.replace(f"/Channels/{channel}",
                               f"/Channels/{target}")
        return None
    return None


def resolve_rtsp_url(host: str, port: int, username: str, password: str,
                     prefer_main: bool = False, timeout: float = 6.0,
                     rebind_host: str | None = None,
                     rtsp_port: int | None = None) -> str | None:
    """Best-effort RTSP URL: ONVIF first, then well-known URL patterns.

    ``rebind_host`` / ``rtsp_port`` rewrite ONVIF-returned URLs to the public
    endpoint (remote mode behind DDNS/port-forwarding).
    """
    if not host:
        return None
    result = probe_onvif_camera(host, port, username, password, timeout=timeout)
    if result:
        url = result.get("main_url") if prefer_main else result.get("sub_url")
        url = url or result.get("sub_url") or result.get("main_url")
        if url:
            if rebind_host:
                url = _rebase_url(url, rebind_host, rtsp_port or 554)
            if _url_matches_quality(url, prefer_main):
                return url
            # ONVIF profile ordering can be wrong on Imou/Dahua: try the
            # swapped variant (subtype 0<->1) and use it if it streams
            swapped = _quality_swap_variant(url, prefer_main)
            if swapped:
                if rebind_host:
                    swapped = _rebase_url(swapped, rebind_host,
                                          rtsp_port or 554)
                if check_url(swapped, short=True):
                    return swapped
            return url
    candidates = _guess_candidates(host, username, password, prefer_main=prefer_main,
                                   rtsp_port=rtsp_port or 554)
    url = find_working_url(candidates, timeout=8.0)
    return url or candidates[0]
