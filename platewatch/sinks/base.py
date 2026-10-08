from __future__ import annotations

from ..events import PlateEvent


class Sink:
    """A destination for plate events. Subclasses implement send(); it may raise, the dispatcher logs it."""

    name = "sink"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        cams = cfg.get("cameras")
        self.cameras = {str(c) for c in cams} if cams else None
        self.name = cfg.get("name") or cfg.get("type", "sink")

    def accepts(self, ev: PlateEvent) -> bool:
        return self.cameras is None or ev.camera_id in self.cameras

    def send(self, ev: PlateEvent) -> None:
        raise NotImplementedError
