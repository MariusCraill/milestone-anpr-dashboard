from __future__ import annotations

import html
import logging
import threading
import time

from .store import Store, norm
from .telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

HELP = """<b>PlateWatch bot</b>
/status – cameras and today's counts
/last [n] – most recent plates
/plate <code>ABC123</code> – history of a plate
/snapshot [camera] – live picture from a camera
/watch <code>ABC123</code> [note] – alert when seen
/unwatch <code>ABC123</code>
/watchlist – list watched plates
/subscribe – send every plate read to this chat
/unsubscribe – stop
/id – show this chat's id"""


def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago" if s < 172800 else f"{s // 86400}d ago"


class Bot:
    """Command handler for the Telegram bot. `service` supplies camera status and live snapshots."""

    def __init__(self, client: TelegramClient, store: Store, service, allowed_chats=(), admins=()):
        self.c, self.store, self.svc = client, store, service
        self.allowed = {int(x) for x in allowed_chats}
        self.admins = {int(x) for x in admins}
        self._stop = threading.Event()

    # ---- auth ----
    def authorised(self, chat_id: int, user_id: int | None, chat_type: str) -> bool:
        return chat_id in self.allowed or (chat_type == "private" and user_id in self.admins)

    # ---- update handling ----
    def handle(self, update: dict) -> None:
        m = update.get("message") or {}
        text = (m.get("text") or "").strip()
        if not text.startswith("/"):
            return
        chat = m.get("chat") or {}
        chat_id, ctype = chat.get("id"), chat.get("type", "private")
        user_id = (m.get("from") or {}).get("id")
        parts = text.split()
        cmd = parts[0][1:].split("@")[0].lower()
        args = parts[1:]
        reply = lambda t: self.c.send_message(chat_id, t, m.get("message_thread_id") if m.get("is_topic_message") else None)

        if cmd == "id":
            reply(f"Chat id: <code>{chat_id}</code>\nYour user id: <code>{user_id}</code>\nType: {html.escape(ctype)}")
            return
        ok = self.authorised(chat_id, user_id, ctype)
        if cmd in ("start", "help"):
            extra = "" if ok else (f"\n\n⛔ This chat is not authorised yet. Add <code>{chat_id}</code> to "
                                   f"<code>bot.allowed_chats</code> in the config and restart.")
            reply(HELP + extra)
            return
        if not ok:
            log.info("ignored /%s from unauthorised chat %s", cmd, chat_id)
            return
        fn = getattr(self, f"cmd_{cmd}", None)
        if fn:
            fn(chat, user_id, args, reply)

    # ---- commands ----
    def cmd_status(self, chat, uid, args, reply):
        lines = []
        for cid, st in self.svc.status().items():
            icon = {"online": "🟢", "offline": "🔴", "ended": "⚪"}.get(st.state, "🟡")
            seen = f" · last plate {html.escape(st.last_plate)} {_ago(st.last_plate_time)}" if st.last_plate else ""
            err = f" ({html.escape(st.error)})" if st.error and st.state != "online" else ""
            lines.append(f"{icon} <b>{html.escape(cid)}</b> {st.state}{err}{seen}")
        reads, distinct = self.store.counts_today()
        reply("\n".join(lines) + f"\n\nToday: {reads} reads, {distinct} distinct plates")

    def cmd_last(self, chat, uid, args, reply):
        n = int(args[0]) if args and args[0].isdigit() else 5
        rows = self.store.last(n)
        if not rows:
            return reply("No plates read yet.")
        reply("\n".join(f"<b>{html.escape(r['plate'])}</b> · {html.escape(r['camera_name'] or r['camera_id'])} · {_ago(r['ts'])} · {r['confidence']:.0f}%" for r in rows))

    def cmd_plate(self, chat, uid, args, reply):
        if not args:
            return reply("Usage: /plate ABC123")
        rows = self.store.find(args[0])
        p = html.escape(norm(args[0]))
        if not rows:
            return reply(f"No sightings of <b>{p}</b>.")
        reply(f"<b>{p}</b>: {len(rows)} most recent sightings\n" + "\n".join(
            f"{time.strftime('%d %b %H:%M', time.localtime(r['ts']))} · {html.escape(r['camera_name'] or r['camera_id'])}" for r in rows))

    def cmd_watch(self, chat, uid, args, reply):
        if not args:
            return reply("Usage: /watch ABC123 [note]")
        if self.store.watch(args[0], " ".join(args[1:])[:100], str(uid)):
            reply(f"👀 Watching <b>{html.escape(norm(args[0]))}</b>. You'll get an alert when it's seen.")
        else:
            reply("That doesn't look like a plate number.")

    def cmd_unwatch(self, chat, uid, args, reply):
        if not args:
            return reply("Usage: /unwatch ABC123")
        reply("Removed." if self.store.unwatch(args[0]) else "That plate wasn't on the watchlist.")

    def cmd_watchlist(self, chat, uid, args, reply):
        rows = self.store.watchlist()
        reply("\n".join(f"<b>{html.escape(r['plate'])}</b> {html.escape(r['note'] or '')}" for r in rows) if rows else "Watchlist is empty.")

    def cmd_subscribe(self, chat, uid, args, reply):
        self.store.subscribe(chat["id"], chat.get("title") or chat.get("username") or "")
        reply("✅ This chat will now receive every plate read (if a Telegram sink is configured).")

    def cmd_unsubscribe(self, chat, uid, args, reply):
        reply("Stopped." if self.store.unsubscribe(chat["id"]) else "This chat wasn't subscribed.")

    def cmd_snapshot(self, chat, uid, args, reply):
        cams = list(self.svc.status())
        cid = args[0] if args else (cams[0] if len(cams) == 1 else None)
        if cid is None:
            return reply("Which camera? " + ", ".join(f"<code>{html.escape(c)}</code>" for c in cams))
        jpg = self.svc.snapshot(cid)
        if not jpg:
            return reply("No frame available from that camera right now.")
        self.c.send_photo(chat["id"], jpg, f"📷 {html.escape(cid)}")

    # ---- polling loop ----
    def stop(self):
        self._stop.set()

    def run(self):
        offset = None
        try:   # skip any backlog so old commands aren't replayed after a restart
            ups = self.c.get_updates(-1, timeout=0)
            if ups:
                offset = ups[-1]["update_id"] + 1
        except TelegramError as e:
            log.warning("bot: %s", e)
        while not self._stop.is_set():
            try:
                for u in self.c.get_updates(offset, timeout=25):
                    offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception as e:
                        log.error("bot: command failed: %s", e)
            except TelegramError as e:
                if e.code == 409:
                    log.error("bot: another process is polling this bot token (409). Only run one PlateWatch per bot.")
                    self._stop.wait(30)
                else:
                    log.warning("bot: %s", e)
                    self._stop.wait(5)
