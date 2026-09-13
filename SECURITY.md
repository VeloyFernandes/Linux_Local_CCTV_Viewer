# Security Policy

## Supported versions

Only the latest commit on the default branch (`master`) is supported.

## Scope

This project is a **local network** CCTV monitoring application. Cameras
talk directly to the app over ONVIF / RTSP on your LAN; no cloud service is
involved and no camera data ever leaves your machine through this app.

## What to know before using it

- **Password storage** — camera passwords are stored in
  `~/.config/onvif-cctv/cameras.json` using base64 *obfuscation*, not
  encryption. Anyone with read access to your user account can decode them.
  Treat that file as sensitive and don't back it up to untrusted locations.
- **RTSP credentials in URLs** — when ONVIF returns stream URLs, credentials
  are embedded in the RTSP URL passed to FFmpeg. Credentials may be visible
  in the process list / system logs while a stream is running.
- **Network exposure** — the app listens for camera streams only; it does
  not expose a management interface. However, ONVIF and RTSP traffic is
  unencrypted on the local network. Prefer a VPN (WireGuard / Tailscale)
  over port forwarding when accessing cameras remotely.
- **Camera firmware** — keep your cameras updated and use strong, unique
  passwords. Any camera with weak or default credentials is a risk to your
  whole network, not just to this app.

## Reporting a vulnerability

If you find a security issue, please open an issue in the repository or
contact the maintainers privately if the issue is sensitive. Please do not
publish an unpatched exploit publicly.

- Please include: a description of the issue, affected versions, and steps
  to reproduce.
- Expect an initial response within a reasonable time. While this is a
  personal project with no service-level guarantees, reported issues are
  taken seriously.
- For non-security bugs, just open a normal issue.
