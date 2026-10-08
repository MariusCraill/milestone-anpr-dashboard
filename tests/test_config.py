import pytest

from platewatch.config import ConfigError, parse


def base(**over):
    d = {"cameras": [{"id": "a", "source": "rtsp://x"}]}
    d.update(over)
    return d


def test_valid_minimal_and_defaults():
    c = parse(base())
    assert c.cameras[0].name == "a" and c.cameras[0].fps == 4.0 and c.recognition.min_reads == 2


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("PW", "s3cret")
    c = parse(base(cameras=[{"id": "a", "source": "rtsp://u:${PW}@h/s"}]))
    assert c.cameras[0].source == "rtsp://u:s3cret@h/s"
    assert parse(base(storage={"db": "${NOPE:-x.db}"})).storage.db == "x.db"


def test_missing_env_is_a_clear_error():
    with pytest.raises(ConfigError, match="NOPE_SECRET"):
        parse(base(cameras=[{"id": "a", "source": "${NOPE_SECRET}"}]))


@pytest.mark.parametrize("bad,msg", [
    ({"cameras": []}, "at least one"),
    ({"cameras": [{"id": "a"}]}, "exactly one"),
    ({"cameras": [{"id": "a", "source": "x", "onvif": {"host": "h"}}]}, "exactly one"),
    ({"cameras": [{"id": "a", "source": "x"}, {"id": "a", "source": "y"}]}, "duplicate"),
    ({"cameras": [{"id": "a", "source": "x", "roi": [0.5, 0, 0.2, 1]}]}, "roi"),
    ({"cameras": [{"id": "a", "source": "x", "role": "gate"}]}, "role"),
    ({"cameras": [{"id": "a", "source": "x", "fpss": 1}]}, "unknown option"),
    ({"cameras": [{"id": "a", "source": "x"}], "recognition": {"plate_regex": "("}}, "regex"),
    ({"cameras": [{"id": "a", "source": "x"}], "sinks": [{"type": "email"}]}, "must be webhook"),
])
def test_invalid(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        parse(bad)
