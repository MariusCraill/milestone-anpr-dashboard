from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class PlateEvent:
    """One settled plate sighting: the consensus of many frames as a vehicle passed a camera."""

    plate: str
    confidence: float                 # 0-100
    camera_id: str
    camera_name: str
    timestamp: float                  # epoch seconds (first frame of the sighting)
    reads: int = 1                    # frames that agreed on this plate
    role: str | None = None           # entry / exit / area hint, passed through to receivers
    region: str | None = None         # OCR's guess at the plate region/country
    snapshot: bytes | None = field(default=None, repr=False)   # JPEG of the best frame, plate boxed
    plate_crop: bytes | None = field(default=None, repr=False)  # JPEG of just the plate
    snapshot_path: str | None = None

    @property
    def iso_time(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()

    def to_payload(self, include_snapshot: bool = False) -> dict:
        """JSON body for webhooks. Field names match the bundled dashboard's /api/ingest."""
        d = {
            "plate": self.plate,
            "confidence": round(self.confidence, 1),
            "cameraId": self.camera_id,
            "cameraName": self.camera_name,
            "timestamp": self.iso_time,
            "reads": self.reads,
        }
        if self.role:
            d["role"] = self.role
        if self.region:
            d["region"] = self.region
        if include_snapshot and self.snapshot:
            d["snapshotBase64"] = base64.b64encode(self.snapshot).decode()
        return d
