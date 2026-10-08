from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Reading:
    text: str                       # upper-case alphanumerics only
    confidence: float               # 0-100, OCR confidence
    box: tuple[int, int, int, int]  # x1,y1,x2,y2 in full-frame pixels
    region: str | None = None


class Recognizer(Protocol):
    def read(self, frame: np.ndarray) -> list[Reading]: ...


def _clean(text: str) -> str:
    return "".join(c for c in (text or "").upper() if c.isalnum())


class FastAlprRecognizer:
    """Plate detection (YOLOv9) + OCR via the open-source fast-alpr package, running on ONNX/CPU (or GPU if onnxruntime-gpu is installed).

    One instance is shared by all camera threads; calls are serialised because the CPU is the bottleneck anyway.
    """

    def __init__(self, detector_model: str, ocr_model: str, detector_conf: float = 0.4, min_plate_width: int = 0):
        from fast_alpr import ALPR  # imported lazily: heavy, and not needed for tests with a fake recognizer
        self._alpr = ALPR(detector_model=detector_model, ocr_model=ocr_model, detector_conf_thresh=detector_conf)
        self._lock = threading.Lock()
        self.min_plate_width = min_plate_width

    def read(self, frame: np.ndarray) -> list[Reading]:
        with self._lock:
            results = self._alpr.predict(frame)
        out: list[Reading] = []
        for r in results:
            if r.ocr is None or not r.ocr.text:
                continue
            bb = r.detection.bounding_box
            if bb.x2 - bb.x1 < self.min_plate_width:
                continue
            c = r.ocr.confidence
            conf = float(np.mean(c)) if isinstance(c, (list, tuple, np.ndarray)) and len(c) else float(c or 0.0)
            text = _clean(r.ocr.text)
            if text:
                out.append(Reading(text, conf * 100, (int(bb.x1), int(bb.y1), int(bb.x2), int(bb.y2)),
                                   getattr(r.ocr, "region", None)))
        return out
