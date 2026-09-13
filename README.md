# ONVIF CCTV Monitor

A clean, simple Linux CCTV viewer built with **Python + PySide6**. Shows all
**ONVIF IP cameras** in a grid that grows with the camera count (2×2 → 2×4 →
3×3 → 4×4 …), with fullscreen viewing, manual PTZ controls, recording,
snapshots, a login lock and a live system dashboard.

## Features

- 🔎 **ONVIF discovery** — finds cameras on the local network via WS-Discovery (UDP multicast).
- ➕ **Manual add / edit / remove** — IP, ONVIF port, username, password.
- 📹 **Automatic RTSP retrieval** over ONVIF (`GetProfiles` / `GetStreamUri`) with
  Hikvision/Dahua/generic URL fallbacks.
- ⚡ **Sub-stream in grid, main stream in fullscreen** — switched automatically.
- 🎚 **Universal quality toggle** — one toolbar switch flips every grid camera
  between low (substream) and high (main stream); each tile shows a `SUB`/`MAIN`
  chip and the stream's true resolution.
- 🌐 **Remote access** — a **Network: Local/Remote** toolbar switch reconnects all
  cameras through their remote endpoint (DDNS hostname, VPN IP or forwarded
  port) when you are away from the local network.
- 🖥 **Fullscreen** — double-click a tile; `Esc` or double-click to return.
- 🕹 **Manual PTZ controls** — arrow pad (pan/tilt), zoom in/out and stop:
  - from the tile context menu (**PTZ Controls…**), and
  - as an overlay in the bottom-right of fullscreen.
- 🔐 **Monitor login** — a Login button on the top-right of the toolbar; once a
  login is set, the camera wall is locked behind it (salted SHA-256).
- 🏷 **Tile overlays** — name, resolution, FPS, bitrate, latency, status and
  decode backend; colour-coded connection status.
- 🔴 **Recording** — record any camera to MP4 with a pulsing REC badge.
- 📸 **Snapshots** — save the current frame as JPG.
- 🗂 **Sidebar** — collapsible camera list with live status dots.
- 🖱 **Drag & drop** — rearrange the grid; the layout is saved between sessions.
- 🔁 **Auto-reconnect** with exponential backoff and URL re-resolution.
- ⚡ **Hardware-accelerated decoding** — CUDA → VA-API → software fallback.
- 📉 **Low latency** — TCP transport, `nobuffer`/`low_delay` FFmpeg flags,
  1-frame buffers and automatic frame dropping.
- 🎛 **Dashboard** — CPU, GPU, RAM, network, storage, cameras online, recordings.
- 🩺 **Camera health** — disconnect / low FPS / high latency alerts as toasts.
- 🎨 **Modern dark theme** with rounded panels, smooth animations and toasts.

## Architecture

```
AI_CCTV/
├── run.py                      # launcher — python run.py [--demo]
├── run.sh                      # desktop launcher — ./run.sh [--install|--demo]
├── requirements.txt
└── cctv/
    ├── app.py                  # QApplication bootstrap
    ├── config.py               # settings persistence, CameraConfig model
    ├── theme.py                # dark theme + stylesheet
    ├── health.py               # camera health monitor + alerts
    ├── core/
    │   ├── streamer.py         # FrameBuffer, StreamThread (stats/reconnect), StreamManager
    │   ├── onvif_client.py     # ONVIF → RTSP resolution, FFmpeg capture (HW accel)
    │   ├── onvif_discovery.py  # WS-Discovery probe (raw UDP multicast)
    │   ├── recorder.py         # MP4 recording + snapshots
    │   └── stats.py            # CPU / RAM / network from /proc (no psutil)
    ├── ptz/
    │   └── controller.py       # ONVIF ContinuousMove / Stop / presets / GetStatus
    └── ui/
        ├── main_window.py      # grid, sidebar, status bar, feature wiring
        ├── video_tile.py       # overlays, drag&drop, context menu
        ├── sidebar.py          # collapsible camera list
        ├── login.py            # login / create-login / change-password dialog
        ├── toasts.py           # animated toast notifications
        ├── ptz_pad.py          # manual PTZ arrow pad (dialog + fullscreen overlay)
        ├── dashboard.py        # live stats dashboard
        ├── dialogs.py          # add/edit camera + connection test
        ├── discovery.py        # discovery dialog + background scan
        └── fullscreen.py       # main-stream fullscreen view with PTZ pad
```

## Installation (Ubuntu / Debian)

```bash
# 1. System dependencies
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
    libgl1 libglib2.0-0 libxkbcommon-x11-0 libegl1 libxcb-cursor0 \
    ffmpeg vainfo

# 2. One-time setup + application-menu entry
cd AI_CCTV
chmod +x run.sh
./run.sh --install

# 3. Launch
./run.sh
```

After `--install` the monitor appears in the application menu as
**ONVIF CCTV Monitor** and starts without a terminal. `./run.sh` also works
when double-clicked in a file manager. The log lives in
`~/.local/state/onvif-cctv/app.log`.

> Manual alternative: `python3 -m venv .venv && .venv/bin/pip install -r
> requirements.txt && .venv/bin/python run.py`

## Usage

| Action | How |
| --- | --- |
| Discover cameras | Toolbar **Discover Cameras** → tick verified devices → **Add Selected** |
| Add a camera | **＋ Add Camera** (or double-click an empty tile) — leave *Manual RTSP URL* empty for ONVIF auto-detection, then **Test Connection** |
| Fullscreen | Double-click a tile / sidebar entry, or right-click → Fullscreen (main stream) |
| PTZ controls | Right-click → **PTZ Controls…** (floating pad), or use the pad overlay in fullscreen — press and hold an arrow to move, release to stop |
| Snapshot | Right-click → **Take Snapshot** |
| Record | Right-click → **Start Recording** (REC badge pulses) → **Stop Recording** saves the MP4 |
| Quality | Toolbar **Grid Quality: Low/High** — switches all cameras between substream and main stream (tiles show SUB/MAIN + real resolution) |
| Login | Top-right **Login** — first login sets the password; afterwards the camera wall stays locked until someone signs in (menu: change password / log out) |
| Remote access | Per camera: set **Remote host / IP** (DDNS/VPN), **Remote ONVIF port** and **Remote RTSP port** in Camera Settings; then toolbar **Network: Local/Remote** to switch |
| Rearrange | Drag a tile onto another tile's position — saved automatically |
| Reconnect | Right-click → **Reconnect**, or toolbar **Reconnect All** |
| Dashboard | Toolbar **Dashboard** |

Settings are stored in `~/.config/onvif-cctv/cameras.json` (passwords are
base64-obfuscated — not encrypted — treat the file as sensitive).

## Remote access (off the local network)

The app cannot reach cameras at `192.168.x.x` when you are away. Two options:

1. **VPN (recommended)** — WireGuard or Tailscale makes the cameras reachable
   at the *same local IPs*; the app keeps working unchanged.
2. **DDNS + port forwarding** — for each camera fill in **Remote host / IP**
   (e.g. `camera1.duckdns.org`), and if you forwarded distinct
   external ports set **Remote ONVIF port** and **Remote RTSP port** (each
   camera behind one public IP needs its own forwarded ports, e.g. camera 1
   ONVIF 8181 → 192.168.1.100:80 and RTSP 1554 → 192.168.1.100:554). Then
   switch the toolbar to **Network: Remote**. The app reconnects through the
   DDNS endpoint and automatically rewrites the internal IPs returned by ONVIF
   to your public host; PTZ controls follow the same endpoint.

> Note: ONVIF and RTSP ports (usually 80/554) must be reachable from outside
> (forwarded or via VPN) for auto-detection and PTZ to work in Remote mode.
> Imou cloud relay is not supported — use a VPN instead.

## Configuration reference (cameras.json)

| Key | Default | Meaning |
| --- | --- | --- |
| `hw_accel` | `auto` | `auto` / `cuda` / `vaapi` / `off` |
| `grid_width` / `fullscreen_width` | `1920` / `1920` | decode widths (1920 = 1080p for 16:9 cameras) |
| `grid_quality` | `high` | universal grid quality: `high` = main stream (1080p), `low` = substream |
| `network_mode` | `local` | `local` or `remote` (off-LAN access via remote host/URL) |
| `login_username` / `login_pass_hash` | — | monitor login; the hash is salted SHA-256 (never store a plaintext password) |
| `record_dir` / `snapshot_dir` | `~/Videos/onvif-cctv`, `~/Pictures/onvif-cctv` | output folders |

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| Discovery finds nothing | Same subnet/VLAN required; UDP 3702 + multicast must not be blocked (Wi-Fi AP isolation is common). Add cameras manually instead. |
| Stuck at "Connecting…" | Wrong credentials or ONVIF port — **Test Connection** shows the resolved URL in a tooltip. |
| High CPU | Keep the quality toggle on **Low**; check the dashboard GPU card — install drivers for CUDA/VA-API. |
| PTZ pad says unavailable | The camera has no ONVIF PTZ service, or the credentials lack PTZ rights. On **Imou** cameras, enable ONVIF (and PTZ) in the Imou Life app under the camera's network/local-service settings. |
| Imou cameras | RTSP patterns are tried Imou/Dahua-first (`/cam/realmonitor?channel=1&subtype=0/1`); the PTZ controller omits zero-zoom velocities and falls back to pan/tilt-only Stop — both quirks of Imou firmware. |
| `libGL.so.1` error | `sudo apt install libgl1` |
| Qt platform plugin error | `sudo apt install libxcb-cursor0 libxkbcommon-x11-0` |

## Security & Disclaimer

- Camera passwords are stored base64-obfuscated — **not encrypted**. Anyone
  with filesystem access to `~/.config/onvif-cctv/cameras.json` can read
  them. Treat that file as sensitive.
- Exposing cameras or this app beyond your local network (port forwarding,
  DDNS, Remote mode) is done entirely **at your own risk**: use strong,
  unique passwords, prefer a VPN, and keep camera firmware up to date.
- This software is provided **"as is", without warranty of any kind**,
  express or implied. Use it at your own risk — the authors are not liable
  for any damage, data loss, or security issues arising from its use.

## Notes

- The grid grows with the number of enabled cameras: 1 → 1×1, up to 4 → 2×2,
  up to 8 → 2×4, 9 → 3×3, then 4/5/6 columns as the wall keeps growing.
- Recording captures the stream currently decoded (sub-stream in the grid,
  main stream when recording from fullscreen).
- `python run.py --demo` starts 8 synthetic test cameras — ideal for
  evaluating the UI without hardware.
