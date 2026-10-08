from __future__ import annotations

import logging
import time

import requests

from ..events import PlateEvent
from .base import Sink

log = logging.getLogger(__name__)


class WebhookSink(Sink):
    """POST each plate as JSON. Works with the bundled dashboard (/api/ingest), Milestone-side scripts, n8n, Zapier, Home Assistant, etc.

    Retries network errors and 5xx/429 with backoff; other 4xx are config errors and are not retried.
    """

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        if not cfg.get("url"):
            raise ValueError("webhook sink needs 'url'")
        self.url = cfg["url"]
        self.headers = dict(cfg.get("headers") or {})
        if cfg.get("api_key"):
            self.headers[cfg.get("api_key_header", "X-API-Key")] = cfg["api_key"]
        self.include_snapshot = bool(cfg.get("include_snapshot", False))
        self.retries = int(cfg.get("retries", 3))
        self.timeout = float(cfg.get("timeout", 10))
        self.session = requests.Session()

    def send(self, ev: PlateEvent) -> None:
        body = ev.to_payload(self.include_snapshot)
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                r = self.session.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
                if r.status_code < 300:
                    return
                if r.status_code < 500 and r.status_code != 429:
                    raise RuntimeError(f"webhook rejected the event: HTTP {r.status_code} {r.text[:200]}")
                err = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                err = str(e)
            if attempt == self.retries:
                raise RuntimeError(f"webhook failed after {attempt + 1} attempts: {err}")
            time.sleep(delay)
            delay = min(delay * 2, 15)
