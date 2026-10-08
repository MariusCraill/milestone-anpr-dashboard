from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import CameraConfig, RecognitionConfig
from .events import PlateEvent
from .recognizer import Reading

# OCR commonly confuses these; treat them as equal when deciding "is this the same plate?"
_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "B": "8", "S": "5", "Z": "2"})


def canon(text: str) -> str:
    return text.translate(_CONFUSABLE)


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class _Candidate:
    score: float = 0.0              # sum of confidences: more frames and better frames both win
    count: int = 0
    best_conf: float = -1.0
    best_frame: np.ndarray | None = None
    best_box: tuple[int, int, int, int] | None = None
    region: str | None = None


@dataclass
class _Sighting:
    first: float
    last: float
    cands: dict[str, _Candidate] = field(default_factory=dict)

    def matches(self, text: str, max_dist: int) -> bool:
        c = canon(text)
        return any(edit_distance(canon(k), c) <= max_dist for k in self.cands)


def _jpeg(img: np.ndarray, quality: int = 85) -> bytes | None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


class Aggregator:
    """Groups the many per-frame reads of one vehicle passing a camera into a single PlateEvent.

    A sighting stays open while reads that look like the same plate keep arriving, then is
    emitted once (a) nothing similar was seen for `settle_seconds`, or (b) it has been open
    for `max_sighting_seconds`. The reported text is the candidate with the highest summed
    confidence, so one bad frame cannot outvote several good ones.
    """

    def __init__(self, camera: CameraConfig, cfg: RecognitionConfig):
        self.cam, self.cfg = camera, cfg
        self._open: list[_Sighting] = []
        self._emitted: list[tuple[str, float]] = []        # (canon plate, time) for cooldown
        self._regex = re.compile(cfg.plate_regex) if cfg.plate_regex else None

    def add(self, reading: Reading, frame: np.ndarray, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not (self.cfg.min_chars <= len(reading.text) <= self.cfg.max_chars):
            return
        s = next((s for s in self._open if s.matches(reading.text, self.cfg.max_edit_distance)), None)
        if s is None:
            s = _Sighting(first=now, last=now)
            self._open.append(s)
        s.last = now
        c = s.cands.setdefault(reading.text, _Candidate())
        c.score += reading.confidence
        c.count += 1
        c.region = reading.region or c.region
        if reading.confidence > c.best_conf:       # keep only the single best frame, not every frame
            c.best_conf, c.best_box, c.best_frame = reading.confidence, reading.box, frame.copy()

    def flush(self, now: float | None = None, force: bool = False) -> list[PlateEvent]:
        now = time.time() if now is None else now
        self._emitted = [(p, t) for p, t in self._emitted if now - t < self.cfg.cooldown_seconds]
        events, still_open = [], []
        for s in self._open:
            done = force or now - s.last >= self.cfg.settle_seconds or now - s.first >= self.cfg.max_sighting_seconds
            if not done:
                still_open.append(s)
                continue
            ev = self._finish(s, now)
            if ev:
                events.append(ev)
        self._open = still_open
        return events

    def _finish(self, s: _Sighting, now: float) -> PlateEvent | None:
        text, cand = max(s.cands.items(), key=lambda kv: kv[1].score)
        reads = sum(c.count for c in s.cands.values())
        avg = sum(c.score for c in s.cands.values()) / reads
        if reads < self.cfg.min_reads or avg < self.cfg.min_confidence:
            return None
        if self._regex and not self._regex.match(text):
            return None
        key = canon(text)
        if any(edit_distance(key, p) <= self.cfg.max_edit_distance for p, _ in self._emitted):
            return None
        self._emitted.append((key, now))
        snapshot = crop = None
        if cand.best_frame is not None and cand.best_box:
            x1, y1, x2, y2 = cand.best_box
            crop = _jpeg(cand.best_frame[max(0, y1):y2, max(0, x1):x2], 90)
            marked = cand.best_frame.copy()
            cv2.rectangle(marked, (x1, y1), (x2, y2), (0, 220, 255), 3)
            snapshot = _jpeg(marked)
        return PlateEvent(plate=text, confidence=avg, camera_id=self.cam.id, camera_name=self.cam.name,
                          timestamp=s.first, reads=reads, role=self.cam.role, region=cand.region,
                          snapshot=snapshot, plate_crop=crop)
