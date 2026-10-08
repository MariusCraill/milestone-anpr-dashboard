from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _expand(value: Any) -> Any:
    """Replace ${VAR} / ${VAR:-default} in every string so secrets can stay out of the file."""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            got = os.environ.get(m.group(1), m.group(2))
            if got is None:
                raise ConfigError(f"environment variable {m.group(1)} is not set (used in config)")
            return got
        return _ENV.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


@dataclass
class CameraConfig:
    id: str
    name: str
    source: str | None = None             # rtsp:// http:// file path, or a USB index like "0"
    onvif: dict | None = None             # {host, port, username, password, profile}
    role: str | None = None               # entry | exit | area (hint passed to receivers)
    fps: float = 4.0                      # frames per second analysed (not the stream fps)
    roi: tuple[float, float, float, float] | None = None   # x1,y1,x2,y2 as 0-1 fractions
    loop: bool = False                    # file sources only: restart at the end
    rtsp_transport: str = "tcp"           # tcp is far more reliable than udp over Wi-Fi/VPN


@dataclass
class RecognitionConfig:
    detector_model: str = "yolo-v9-t-384-license-plate-end2end"
    ocr_model: str = "cct-xs-v2-global-model"
    detector_conf: float = 0.4            # plate detector threshold
    min_confidence: float = 70.0          # OCR confidence (0-100) a sighting must average
    min_reads: int = 2                    # frames that must agree; the main false-positive filter
    min_plate_width: int = 60             # px; ignore plates too small to read reliably
    min_chars: int = 4
    max_chars: int = 10
    plate_regex: str | None = None        # e.g. "^[A-Z]{2}[0-9]{2}[A-Z]{2}GP$"; reads that don't match are dropped
    settle_seconds: float = 2.0           # no read for this long = the vehicle has passed
    max_sighting_seconds: float = 20.0    # force-emit a plate that sits in view (queue at a gate)
    cooldown_seconds: float = 60.0        # don't report the same plate on the same camera again within this
    max_edit_distance: int = 1            # reads this close are treated as the same plate


@dataclass
class StorageConfig:
    db: str = "data/platewatch.db"
    snapshots_dir: str = "data/snapshots"
    retention_days: int = 30


@dataclass
class BotConfig:
    enabled: bool = True
    token: str | None = None
    allowed_chats: list[int] = field(default_factory=list)
    admins: list[int] = field(default_factory=list)   # user ids allowed to command the bot in private chat


@dataclass
class Config:
    cameras: list[CameraConfig]
    recognition: RecognitionConfig
    sinks: list[dict]
    storage: StorageConfig
    bot: BotConfig


def _build(cls, raw: dict, where: str):
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping")
    known = set(cls.__dataclass_fields__)
    extra = set(raw) - known
    if extra:
        raise ConfigError(f"{where}: unknown option(s) {sorted(extra)}; valid: {sorted(known)}")
    return cls(**raw)


def parse(raw: dict) -> Config:
    raw = _expand(raw or {})
    cams_raw = raw.get("cameras") or []
    if not cams_raw:
        raise ConfigError("config needs at least one entry under 'cameras'")
    cameras, seen = [], set()
    for i, c in enumerate(cams_raw):
        c = dict(c)
        if "id" not in c:
            raise ConfigError(f"cameras[{i}] needs an 'id'")
        c["id"] = str(c["id"])
        c.setdefault("name", c["id"])
        if "roi" in c and c["roi"] is not None:
            roi = tuple(float(x) for x in c["roi"])
            if len(roi) != 4 or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
                raise ConfigError(f"camera {c['id']}: roi must be [x1,y1,x2,y2] fractions with x1<x2, y1<y2")
            c["roi"] = roi
        cam = _build(CameraConfig, c, f"camera {c['id']}")
        if bool(cam.source) == bool(cam.onvif):
            raise ConfigError(f"camera {cam.id}: set exactly one of 'source' or 'onvif'")
        if cam.role not in (None, "entry", "exit", "area"):
            raise ConfigError(f"camera {cam.id}: role must be entry, exit or area")
        if cam.id in seen:
            raise ConfigError(f"duplicate camera id {cam.id}")
        seen.add(cam.id)
        if cam.source is not None:
            cam.source = str(cam.source)
        cameras.append(cam)
    rec = _build(RecognitionConfig, raw.get("recognition") or {}, "recognition")
    if rec.plate_regex:
        try:
            re.compile(rec.plate_regex)
        except re.error as e:
            raise ConfigError(f"recognition.plate_regex is not a valid regex: {e}")
    sinks = raw.get("sinks") or []
    for i, s in enumerate(sinks):
        if not isinstance(s, dict) or s.get("type") not in ("webhook", "telegram", "jsonl"):
            raise ConfigError(f"sinks[{i}].type must be webhook, telegram or jsonl")
    storage = _build(StorageConfig, raw.get("storage") or {}, "storage")
    bot = _build(BotConfig, raw.get("bot") or {}, "bot")
    return Config(cameras, rec, sinks, storage, bot)


def load(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"config is not valid YAML: {e}")
    return parse(raw)
