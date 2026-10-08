from __future__ import annotations

from ..store import Store
from .base import Sink
from .jsonl import JsonlSink
from .telegram import TelegramSink
from .webhook import WebhookSink


def build_sinks(specs: list[dict], store: Store) -> list[Sink]:
    out: list[Sink] = []
    for spec in specs:
        t = spec["type"]
        if t == "webhook":
            out.append(WebhookSink(spec))
        elif t == "jsonl":
            out.append(JsonlSink(spec))
        elif t == "telegram":
            out.append(TelegramSink(spec, store))
    return out
