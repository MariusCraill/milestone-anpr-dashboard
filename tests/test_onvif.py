import re

from platewatch.onvif import OnvifClient, OnvifError, normalise_rtsp, parse_probe_match
import pytest

PROBE = b"""<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><s:Body><d:ProbeMatches><d:ProbeMatch>
<d:Scopes>onvif://www.onvif.org/name/Gate%20Cam onvif://www.onvif.org/hardware/DS-2CD2</d:Scopes>
<d:XAddrs>http://192.168.1.64:80/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""

CAPS = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><tds:GetCapabilitiesResponse xmlns:tds="d" xmlns:tt="t">
<tds:Capabilities><tt:Media><tt:XAddr>MEDIA_URL</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse></s:Body></s:Envelope>"""

PROFILES = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><trt:GetProfilesResponse xmlns:trt="m" xmlns:tt="t">
<trt:Profiles token="sub"><tt:Name>Sub</tt:Name><tt:VideoEncoderConfiguration><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles>
<trt:Profiles token="main"><tt:Name>Main</tt:Name><tt:VideoEncoderConfiguration><tt:Encoding>H265</tt:Encoding><tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles>
</trt:GetProfilesResponse></s:Body></s:Envelope>"""

URI = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><trt:GetStreamUriResponse xmlns:trt="m" xmlns:tt="t">
<trt:MediaUri><tt:Uri>rtsp://10.0.0.99:554/Streaming/Channels/TOKEN</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse></s:Body></s:Envelope>"""


def test_parse_probe_match():
    m = parse_probe_match(PROBE)
    assert m["host"] == "192.168.1.64" and m["name"] == "Gate Cam" and m["hardware"] == "DS-2CD2"
    assert parse_probe_match(b"garbage") is None


def test_normalise_rtsp_fixes_host_and_quotes_credentials():
    u = normalise_rtsp("rtsp://10.0.0.99:554/s?x=1", "192.168.1.64", "ad min", "p@ss:w/d")
    assert u == "rtsp://ad%20min:p%40ss%3Aw%2Fd@192.168.1.64:554/s?x=1"
    assert "@" not in normalise_rtsp("rtsp://h/s", "cam", "", "")


def test_full_flow_picks_highest_resolution_and_sends_digest_auth(http_server):
    class Patched(OnvifClient):
        @property
        def device_url(self):
            return http_server.url + "/onvif/device_service"

    # script replies in order of the calls the client makes
    http_server.script = [(200, b"<x/>"), (200, CAPS.replace(b"MEDIA_URL", (http_server.url + "/media").encode())),
                          (200, PROFILES), (200, URI.replace(b"TOKEN", b"main"))]
    c = Patched("192.168.1.64", 554, "admin", "pw")
    url = c.rtsp_url()
    assert url == "rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/main"
    calls = http_server.calls
    assert b"UsernameToken" in calls[1]["body"] and b"PasswordDigest" in calls[1]["body"]
    assert b"pw" not in calls[1]["body"]                       # password is digested, never sent
    assert b"<ProfileToken>main</ProfileToken>" in calls[3]["body"]


def test_unreachable_and_bad_credentials_are_clear():
    with pytest.raises(OnvifError, match="cannot reach"):
        OnvifClient("127.0.0.1", 1, timeout=0.5).media_url()


def test_auth_rejection(http_server):
    http_server.script = [(401, b"")]
    class P(OnvifClient):
        @property
        def device_url(self):
            return http_server.url
    with pytest.raises(OnvifError, match="username/password"):
        P("h", 80, "a", "b").media_url()
