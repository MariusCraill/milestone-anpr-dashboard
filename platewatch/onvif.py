"""ONVIF helpers: find cameras on the LAN and ask a camera for its RTSP stream URL.

Implemented directly on ONVIF's SOAP/WS-Discovery (no extra dependency). Cameras vary a lot in how strictly
they follow the spec, so treat this as best-effort and fall back to a manual `source: rtsp://...` URL.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import requests

NS_ENV = "http://www.w3.org/2003/05/soap-envelope"
NS_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
NS_SCHEMA = "http://www.onvif.org/ver10/schema"


class OnvifError(RuntimeError):
    pass


class OnvifAuthError(OnvifError):
    pass


class OnvifUnreachable(OnvifError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(root: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in root.iter() if _local(e.tag) == name]


def _find_text(root: ET.Element, name: str) -> str | None:
    for e in root.iter():
        if _local(e.tag) == name and e.text and e.text.strip():
            return e.text.strip()
    return None


# ---------------- WS-Discovery ----------------

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<e:Header><w:MessageID>uuid:{mid}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>
<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>"""


def parse_probe_match(data: bytes) -> dict | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    xaddrs = (_find_text(root, "XAddrs") or "").split()
    if not xaddrs:
        return None
    scopes = (_find_text(root, "Scopes") or "").split()
    pick = lambda key: next((s.rsplit("/", 1)[-1].replace("%20", " ") for s in scopes if f"/{key}/" in s), None)
    host = urlsplit(xaddrs[0]).hostname
    return {"host": host, "xaddrs": xaddrs, "name": pick("name"), "hardware": pick("hardware")}


def discover(timeout: float = 3.0) -> list[dict]:
    """Multicast WS-Discovery probe; returns one dict per camera that answered (same LAN segment only)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    found: dict[str, dict] = {}
    try:
        sock.sendto(_PROBE.format(mid=uuid.uuid4()).encode(), ("239.255.255.250", 3702))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            m = parse_probe_match(data)
            if m and m["host"]:
                found[m["host"]] = m
    finally:
        sock.close()
    return sorted(found.values(), key=lambda m: m["host"])


# ---------------- SOAP client ----------------

class OnvifClient:
    def __init__(self, host: str, port: int = 80, username: str = "", password: str = "", timeout: float = 8.0,
                 session: requests.Session | None = None, scheme: str = "http"):
        self.host, self.port, self.user, self.pw = host, int(port), username or "", password or ""
        self.timeout = timeout
        self.scheme = scheme
        self.s = session or requests.Session()
        self._skew = 0.0

    @property
    def device_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}/onvif/device_service"

    def _security(self) -> str:
        if not self.user:
            return ""
        nonce = os.urandom(16)
        created = datetime.fromtimestamp(time.time() + self._skew, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.pw.encode()).digest()).decode()
        return (
            '<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            f'<UsernameToken><Username>{_esc(self.user)}</Username>'
            '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f'{digest}</Password><Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f'{base64.b64encode(nonce).decode()}</Nonce>'
            '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            f'{created}</Created></UsernameToken></Security></s:Header>')

    def _call(self, url: str, body: str, auth: bool = True) -> ET.Element:
        env = (f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="{NS_ENV}">'
               f'{self._security() if auth else ""}<s:Body>{body}</s:Body></s:Envelope>')
        try:
            r = self.s.post(url, data=env.encode(), headers={"Content-Type": "application/soap+xml; charset=utf-8"}, timeout=self.timeout)
        except requests.RequestException as e:
            raise OnvifUnreachable(f"cannot reach camera at {self.host}:{self.port} ({type(e).__name__})")
        if r.status_code in (401, 403):
            raise OnvifAuthError("camera rejected the username/password (HTTP %d)" % r.status_code)
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise OnvifError(f"unexpected reply from camera (HTTP {r.status_code}); is this the ONVIF port?")
        fault = _find_text(root, "Text") if _find_all(root, "Fault") else None
        if fault is not None or _find_all(root, "Fault"):
            low = (fault or "").lower()
            if "auth" in low or "sender not authorized" in low:
                raise OnvifAuthError("camera rejected the username/password (not authorized)")
            raise OnvifError(f"camera returned a SOAP fault: {fault}")
        return root

    def sync_clock(self) -> None:
        """Cameras reject digest auth if their clock differs from ours by more than a few minutes; measure the skew."""
        try:
            root = self._call(self.device_url, f'<GetSystemDateAndTime xmlns="{NS_DEVICE}"/>', auth=False)
            u = _find_all(root, "UTCDateTime")
            if u:
                g = lambda n: int(_find_text(u[0], n) or 0)
                dev = datetime(g("Year"), g("Month"), g("Day"), g("Hour"), g("Minute"), g("Second"), tzinfo=timezone.utc)
                self._skew = dev.timestamp() - time.time()
        except (OnvifError, ValueError):
            pass

    def media_url(self) -> str:
        try:
            root = self._call(self.device_url,
                              f'<GetCapabilities xmlns="{NS_DEVICE}"><Category>Media</Category></GetCapabilities>')
            media = next((e for e in _find_all(root, "Media")), None)
            x = _find_text(media, "XAddr") if media is not None else None
            if x:
                return x
        except (OnvifAuthError, OnvifUnreachable):
            raise
        except OnvifError:
            pass  # some cameras don't implement GetCapabilities; fall back to the conventional media URL
        return f"{self.scheme}://{self.host}:{self.port}/onvif/media_service"

    def profiles(self, media_url: str) -> list[dict]:
        root = self._call(media_url, f'<GetProfiles xmlns="{NS_MEDIA}"/>')
        out = []
        for p in _find_all(root, "Profiles"):
            vec = next(iter(_find_all(p, "VideoEncoderConfiguration")), None)
            w = h = 0
            enc = None
            if vec is not None:
                enc = _find_text(vec, "Encoding")
                res = next(iter(_find_all(vec, "Resolution")), None)
                if res is not None:
                    w, h = int(_find_text(res, "Width") or 0), int(_find_text(res, "Height") or 0)
            out.append({"token": p.get("token"), "name": _find_text(p, "Name"), "width": w, "height": h, "encoding": enc})
        return [p for p in out if p["token"]]

    def stream_uri(self, media_url: str, token: str) -> str:
        body = (f'<GetStreamUri xmlns="{NS_MEDIA}"><StreamSetup><Stream xmlns="{NS_SCHEMA}">RTP-Unicast</Stream>'
                f'<Transport xmlns="{NS_SCHEMA}"><Protocol>RTSP</Protocol></Transport></StreamSetup>'
                f'<ProfileToken>{_esc(token)}</ProfileToken></GetStreamUri>')
        uri = _find_text(self._call(media_url, body), "Uri")
        if not uri:
            raise OnvifError("camera did not return a stream URI")
        return uri

    def rtsp_url(self, profile: str | None = None, embed_credentials: bool = True) -> str:
        """Resolve the RTSP URL. `profile` is a profile token or name; default = highest resolution (best for plates)."""
        self.sync_clock()
        media = self.media_url()
        profs = self.profiles(media)
        if not profs:
            raise OnvifError("camera reports no media profiles")
        if profile:
            chosen = next((p for p in profs if profile in (p["token"], p["name"])), None)
            if not chosen:
                raise OnvifError(f"profile {profile!r} not found; available: {[p['token'] for p in profs]}")
        else:
            chosen = max(profs, key=lambda p: p["width"] * p["height"])
        uri = self.stream_uri(media, chosen["token"])
        return normalise_rtsp(uri, self.host, self.user, self.pw, embed_credentials)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def normalise_rtsp(uri: str, host: str, user: str, pw: str, embed: bool = True) -> str:
    """Cameras often report an internal/placeholder host in the URI; use the address we actually reached them on."""
    p = urlsplit(uri)
    netloc = host + (f":{p.port}" if p.port else "")
    if embed and user:
        netloc = f"{quote(user, safe='')}:{quote(pw, safe='')}@{netloc}"
    return urlunsplit((p.scheme or "rtsp", netloc, p.path, p.query, ""))
