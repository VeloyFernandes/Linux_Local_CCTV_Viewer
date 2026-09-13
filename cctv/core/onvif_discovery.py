"""WS-Discovery based ONVIF camera discovery on the local network.

Implements the ONVIF WS-Discovery ``Probe`` exchange directly over UDP
multicast (239.255.255.250:3702) so discovery works without any SOAP library.
"""
from __future__ import annotations

import socket
import struct
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET

_MCAST_GROUP = "239.255.255.250"
_MCAST_PORT = 3702

_PROBE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<Header>
 <wsa:MessageID>uuid:{message_id}</wsa:MessageID>
 <wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>
 <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>
</Header>
<Body>
 <Probe xmlns="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <Types>dn:NetworkVideoTransmitter</Types>
 </Probe>
</Body>
</Envelope>"""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(root: ET.Element, local_tag: str) -> str:
    for elem in root.iter():
        if _local_name(elem.tag) == local_tag:
            return (elem.text or "").strip()
    return ""


def _name_from_scopes(scopes: str) -> str:
    for part in scopes.split():
        if part.startswith("onvif://www.onvif.org/name/"):
            name = urllib.parse.unquote(part.rsplit("/", 1)[-1])
            if name:
                return name
    return ""


def _parse_response(data: bytes, src_ip: str) -> dict | None:
    try:
        root = ET.fromstring(data)
    except Exception:
        return None
    scopes = _find_text(root, "Scopes")
    types = _find_text(root, "Types")
    if "onvif" not in scopes.lower() and "NetworkVideoTransmitter" not in types:
        return None
    xaddrs_text = _find_text(root, "XAddrs")
    endpoint = _find_text(root, "Address")

    urls = [url for url in xaddrs_text.split() if url.startswith("http")]
    host: str | None = None
    port = 80
    if endpoint.startswith("http"):
        parts = urllib.parse.urlsplit(endpoint)
        if parts.hostname:
            host = parts.hostname
            port = parts.port or 80
    if not host:
        for url in urls:
            parts = urllib.parse.urlsplit(url)
            if parts.hostname:
                host = parts.hostname
                port = parts.port or 80
                break
    if not host:
        host = src_ip
    return {
        "host": host,
        "port": port,
        "scopes": scopes,
        "xaddrs": urls,
        "name_hint": _name_from_scopes(scopes) or host,
    }


def discover_onvif(timeout: float = 4.0) -> list[dict]:
    """Send WS-Discovery probes and return a list of discovered devices.

    Each entry contains ``host``, ``port`` (ONVIF HTTP port), ``scopes``,
    ``xaddrs`` and a ``name_hint`` derived from the scopes.
    """
    found: dict[str, dict] = {}
    probe = _PROBE_TEMPLATE.format(message_id=uuid.uuid4()).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
        except OSError:
            pass
        sock.bind(("", 0))
        sock.settimeout(0.75)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock.sendto(probe, (_MCAST_GROUP, _MCAST_PORT))
            except OSError:
                pass
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    break
                info = _parse_response(data, addr[0])
                if info:
                    found[info["host"]] = info
    finally:
        sock.close()
    return list(found.values())
