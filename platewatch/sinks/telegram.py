from __future__ import annotations

import html
import logging
import time

from ..events import PlateEvent
from ..store import Store
from ..telegram import TelegramClient, TelegramError
from .base import Sink

log = logging.getLogger(__name__)


def format_caption(ev: PlateEvent, note: str | None = None) -> str:
    t = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
    d = time.strftime("%d %b", time.localtime(ev.timestamp))
    lines = []
    if note is not None:
        lines.append(f"🚨 <b>WATCHLIST</b>" + (f" · {html.escape(note)}" if note else ""))
    lines.append(f"🚗 <b>{html.escape(ev.plate)}</b>")
    where = html.escape(ev.camera_name)
    if ev.role:
        where += f" ({ev.role})"
    lines.append(f"📍 {where}")
    lines.append(f"🕒 {t} · {d} · {ev.confidence:.0f}% sure")
    return "\n".join(lines)


class TelegramSink(Sink):
    """Send plates to Telegram chats/groups: a photo of the vehicle with the plate boxed, or text only.

    Recipients = `chat_ids` from config + any chat that ran /subscribe with the bot.
    `only_watchlist: true` turns it into an alert channel: only watchlisted plates are sent.
    """

    def __init__(self, cfg: dict, store: Store, client: TelegramClient | None = None):
        super().__init__(cfg)
        self.store = store
        self.client = client or TelegramClient(cfg.get("token", ""))
        self.chat_ids = [self._parse_chat(c) for c in cfg.get("chat_ids", [])]
        self.thread_id = cfg.get("message_thread_id")      # a forum topic inside a supergroup
        self.send_photo = bool(cfg.get("send_photo", True))
        self.only_watchlist = bool(cfg.get("only_watchlist", False))
        self.min_interval = float(cfg.get("min_interval_seconds", 0))  # per-chat throttle during bursts
        self._last_sent: dict = {}

    @staticmethod
    def _parse_chat(c):
        return int(c) if str(c).lstrip("-").isdigit() else str(c)   # numeric id or @channelname

    def recipients(self) -> list:
        seen, out = set(), []
        for c in [*self.chat_ids, *self.store.subscribers()]:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def accepts(self, ev: PlateEvent) -> bool:
        if not super().accepts(ev):
            return False
        return not self.only_watchlist or self.store.watch_note(ev.plate) is not None

    def send(self, ev: PlateEvent) -> None:
        caption = format_caption(ev, self.store.watch_note(ev.plate))
        errors = []
        for chat in self.recipients():
            if self.min_interval and time.monotonic() - self._last_sent.get(chat, 0) < self.min_interval:
                continue
            try:
                if self.send_photo and ev.snapshot:
                    self.client.send_photo(chat, ev.snapshot, caption, self.thread_id)
                else:
                    self.client.send_message(chat, caption, self.thread_id)
                self._last_sent[chat] = time.monotonic()
            except TelegramError as e:
                if e.code in (400, 403) and chat in self.store.subscribers():
                    # bot was kicked / chat gone: stop trying
                    self.store.unsubscribe(chat)
                errors.append(f"{chat}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))
