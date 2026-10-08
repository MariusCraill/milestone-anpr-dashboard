from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import CameraConfig, Config
from .events import PlateEvent
from .recognizer import Recognizer
from .sinks.base import Sink
from .sources import make_source, redact
from .store import Store, norm
from .tracker import Aggregator

log = logging.getLogger(__name__)


@dataclass
class CameraStatus:
    state: str = "starting"          # starting | online | offline | ended
    last_frame: float = 0.0
    last_plate: str | None = None
    last_plate_time: float = 0.0
    frames: int = 0
    plates: int = 0
    error: str | None = None


class Dispatcher:
    """Fan-out to sinks. Each sink has its own queue + thread, so one slow Telegram or webhook never stalls recognition or other sinks."""

    def __init__(self, sinks: list[Sink], store: Store, snapshots_dir: str | None):
        self.store, self.sinks, self.snap_dir = store, sinks, snapshots_dir
        self._queues = {s: queue.Queue(maxsize=200) for s in sinks}
        self._threads = [threading.Thread(target=self._run, args=(s,), daemon=True, name=f"sink-{s.name}") for s in sinks]
        self._stop = threading.Event()

    def start(self):
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop.set()

    def emit(self, ev: PlateEvent) -> None:
        if self.snap_dir and ev.snapshot:
            d = Path(self.snap_dir) / time.strftime("%Y-%m-%d", time.localtime(ev.timestamp))
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{time.strftime('%H%M%S', time.localtime(ev.timestamp))}_{ev.camera_id}_{norm(ev.plate)}.jpg"
            p.write_bytes(ev.snapshot)
            ev.snapshot_path = str(p)
        self.store.add_sighting(ev)
        log.info("plate %s (%.0f%%, %d reads) on %s", ev.plate, ev.confidence, ev.reads, ev.camera_name)
        for s in self.sinks:
            if not s.accepts(ev):
                continue
            q = self._queues[s]
            try:
                q.put_nowait(ev)
            except queue.Full:
                q.get_nowait()                       # drop the oldest rather than block recognition
                q.put_nowait(ev)
                log.warning("sink %s is backed up; dropped its oldest event", s.name)

    def _run(self, sink: Sink) -> None:
        q = self._queues[sink]
        while not self._stop.is_set():
            try:
                ev = q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                sink.send(ev)
            except Exception as e:   # a failing receiver must never take down the service
                log.error("sink %s failed for %s: %s", sink.name, ev.plate, e)


class CameraWorker(threading.Thread):
    def __init__(self, cam: CameraConfig, url: str | None, recognizer: Recognizer, agg: Aggregator, dispatcher: Dispatcher,
                 resolve_url=None, stall_timeout: float = 20.0):
        super().__init__(daemon=True, name=f"cam-{cam.id}")
        self.cam, self._url, self.rec, self.agg, self.disp = cam, url, recognizer, agg, dispatcher
        self._resolve = resolve_url
        self.stall_timeout = stall_timeout
        self.status = CameraStatus()
        self._stop_evt = threading.Event()
        self._last_frame: np.ndarray | None = None

    def stop(self):
        self._stop_evt.set()

    def latest_jpeg(self) -> bytes | None:
        f = self._last_frame
        if f is None:
            return None
        ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None

    def _roi(self, frame: np.ndarray) -> tuple[np.ndarray, int, int]:
        if not self.cam.roi:
            return frame, 0, 0
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.cam.roi
        ox, oy = int(x1 * w), int(y1 * h)
        return frame[oy:int(y2 * h), ox:int(x2 * w)], ox, oy

    def process(self, frame: np.ndarray, now: float | None = None) -> None:
        """One analysis pass; split out so tests can drive it without a stream."""
        self._last_frame = frame
        sub, ox, oy = self._roi(frame)
        for r in self.rec.read(sub):
            x1, y1, x2, y2 = r.box
            r.box = (x1 + ox, y1 + oy, x2 + ox, y2 + oy)
            self.agg.add(r, frame, now)
        for ev in self.agg.flush(now):
            self.status.plates += 1
            self.status.last_plate, self.status.last_plate_time = ev.plate, ev.timestamp
            self.disp.emit(ev)

    def run(self):
        backoff = 1.0
        while not self._stop_evt.is_set():
            src = None
            try:
                url = self._url or (self._resolve() if self._resolve else None)
                if not url:
                    raise RuntimeError("no stream URL")
                src = make_source(self.cam, url)
                if not src.open():
                    raise RuntimeError(f"cannot open {redact(url)}")
                log.info("camera %s connected (%s)", self.cam.id, redact(url))
                self.status.state, self.status.error, backoff = "online", None, 1.0
                self.status.last_frame = time.time()
                while not self._stop_evt.is_set():
                    frame = src.read()
                    if frame is None:
                        if src.ended:
                            break
                        if time.time() - self.status.last_frame > self.stall_timeout:
                            raise RuntimeError(f"no frames for {self.stall_timeout:.0f}s")
                        time.sleep(0.05)
                        continue
                    self.status.last_frame = time.time()
                    self.status.frames += 1
                    self.process(frame)
                    if not src.is_file:
                        # live: sleep off the rest of this frame's time budget
                        time.sleep(max(0.0, 1.0 / self.cam.fps))
                if src.ended and src.is_file and not self.cam.loop:
                    for ev in self.agg.flush(force=True):
                        self.status.plates += 1
                        self.disp.emit(ev)
                    self.status.state = "ended"
                    return
                if not self._stop_evt.is_set():
                    raise RuntimeError("stream ended")
            except Exception as e:
                self.status.state, self.status.error = "offline", str(e)
                log.warning("camera %s: %s; retrying in %.0fs", self.cam.id, e, backoff)
                self._stop_evt.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if src:
                    src.close()


class Service:
    """Owns the recognizer, camera workers, sinks and (optionally) the bot."""

    def __init__(self, cfg: Config, recognizer: Recognizer, sinks: list[Sink], store: Store, resolver=None):
        self.cfg, self.store = cfg, store
        self.dispatcher = Dispatcher(sinks, store, cfg.storage.snapshots_dir)
        self.workers: dict[str, CameraWorker] = {}
        for cam in cfg.cameras:
            agg = Aggregator(cam, cfg.recognition)
            url = cam.source
            res = (lambda c=cam: resolver(c)) if (cam.onvif and resolver) else None
            self.workers[cam.id] = CameraWorker(cam, url, recognizer, agg, self.dispatcher, res)

    def start(self):
        self.dispatcher.start()
        for w in self.workers.values():
            w.start()

    def stop(self):
        for w in self.workers.values():
            w.stop()
        self.dispatcher.stop()

    def status(self) -> dict[str, CameraStatus]:
        return {k: w.status for k, w in self.workers.items()}

    def snapshot(self, camera_id: str) -> bytes | None:
        w = self.workers.get(camera_id)
        return w.latest_jpeg() if w else None

    def all_ended(self) -> bool:
        return all(w.status.state == "ended" for w in self.workers.values())
