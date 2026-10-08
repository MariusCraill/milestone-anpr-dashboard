from __future__ import annotations

import logging
import os
import threading
import time
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)


def redact(url: str) -> str:
    """Hide credentials in rtsp://user:pass@host so they never reach logs or chat."""
    try:
        p = urlsplit(url)
        if p.password or p.username:
            host = p.hostname or ""
            if p.port:
                host += f":{p.port}"
            return urlunsplit((p.scheme, f"***:***@{host}", p.path, p.query, ""))
    except ValueError:
        pass
    return url


class FrameSource(Protocol):
    def open(self) -> bool: ...
    def read(self) -> np.ndarray | None: ...   # newest frame, None if nothing new / stream ended
    def close(self) -> None: ...
    ended: bool


class OpenCVSource:
    """Reads any stream OpenCV/FFmpeg can open.

    Live streams are drained by a background thread so we always analyse the *newest* frame and
    never fall behind (a slow ANPR pass would otherwise make an RTSP buffer lag by minutes).
    Files are read inline and paced to real time, skipping frames to hit the target fps.
    """

    def __init__(self, url: str, fps: float, transport: str = "tcp", loop: bool = False):
        self.url, self.fps, self.transport, self.loop = url, fps, transport, loop
        self.is_file = os.path.isfile(url)
        self.cap: cv2.VideoCapture | None = None
        self.ended = False
        self._latest: np.ndarray | None = None
        self._seq = self._taken = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._skip = 1
        self._next = 0.0

    def open(self) -> bool:
        self.close()
        self._stop.clear()
        self.ended = False
        if not self.is_file:
            # must be set before the capture is created; applies to RTSP only
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{self.transport}|stimeout;8000000"
        target = int(self.url) if self.url.isdigit() else self.url
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG) if not isinstance(target, int) else cv2.VideoCapture(target)
        if not cap.isOpened():
            cap.release()
            return False
        self.cap = cap
        if self.is_file:
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            self._skip = max(1, round(src_fps / max(self.fps, 0.1)))
            self._next = time.monotonic()
        else:
            self._thread = threading.Thread(target=self._drain, daemon=True, name="frame-drain")
            self._thread.start()
        return True

    def _drain(self) -> None:
        cap = self.cap
        while not self._stop.is_set() and cap is not None:
            ok, frame = cap.read()
            if not ok:
                self.ended = True
                return
            with self._lock:
                self._latest, self._seq = frame, self._seq + 1

    def read(self) -> np.ndarray | None:
        if self.cap is None:
            return None
        if self.is_file:
            return self._read_file()
        with self._lock:
            if self._seq == self._taken:
                return None
            self._taken = self._seq
            return self._latest

    def _read_file(self) -> np.ndarray | None:
        wait = self._next - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._next = time.monotonic() + 1.0 / max(self.fps, 0.1)
        frame = None
        for _ in range(self._skip):
            ok, f = self.cap.read()
            if not ok:
                if self.loop:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                self.ended = True
                return None
            frame = f
        return frame

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self._latest = None


def make_source(cam: CameraConfig, url: str) -> OpenCVSource:
    return OpenCVSource(url, cam.fps, cam.rtsp_transport, cam.loop)
