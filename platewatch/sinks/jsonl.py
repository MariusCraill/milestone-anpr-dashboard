from __future__ import annotations

import json
import threading
from pathlib import Path

from ..events import PlateEvent
from .base import Sink


class JsonlSink(Sink):
    """Append one JSON object per line: easy to tail, ship to a log pipeline, or import later."""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.path = Path(cfg.get("path", "data/plates.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def send(self, ev: PlateEvent) -> None:
        line = json.dumps(ev.to_payload(include_snapshot=False))
        with self._lock, self.path.open("a") as f:
            f.write(line + "\n")
