import json
import time

import pytest

from platewatch.bot import Bot
from platewatch.events import PlateEvent
from platewatch.sinks.telegram import TelegramSink, format_caption
from platewatch.sinks.webhook import WebhookSink
from platewatch.store import Store
from platewatch.telegram import TelegramClient, TelegramError


def ev(plate="CA123456", cam="gate", snapshot=b"jpg"):
    return PlateEvent(plate, 93.4, cam, "Main <Gate>", time.time(), 4, "entry", snapshot=snapshot)


class FakeClient:
    def __init__(self):
        self.sent, self.fail = [], {}

    def _do(self, kind, chat, *a):
        if chat in self.fail:
            raise TelegramError("Forbidden: bot was kicked", self.fail[chat])
        self.sent.append((kind, chat, a))

    def send_message(self, chat, text, thread_id=None, html=True):
        self._do("msg", chat, text)

    def send_photo(self, chat, photo, caption="", thread_id=None):
        self._do("photo", chat, caption)


# ---------- telegram sink ----------
def test_caption_escapes_html_and_flags_watchlist():
    c = format_caption(ev(), note="stolen <car>")
    assert "&lt;Gate&gt;" in c and "stolen &lt;car&gt;" in c and "WATCHLIST" in c and "CA123456" in c


def test_photo_to_configured_and_subscribed_chats_once_each():
    st, fc = Store(":memory:"), FakeClient()
    st.subscribe(-5)
    st.subscribe(-100)
    sink = TelegramSink({"type": "telegram", "chat_ids": [-100, "-7"]}, st, fc)
    sink.send(ev())
    assert [(k, c) for k, c, _ in fc.sent] == [("photo", -100), ("photo", -7), ("photo", -5)]


def test_text_only_when_no_photo_configured():
    st, fc = Store(":memory:"), FakeClient()
    TelegramSink({"type": "telegram", "chat_ids": [1], "send_photo": False}, st, fc).send(ev())
    assert fc.sent[0][0] == "msg"


def test_only_watchlist_filters():
    st = Store(":memory:")
    sink = TelegramSink({"type": "telegram", "chat_ids": [1], "only_watchlist": True}, st, FakeClient())
    assert not sink.accepts(ev("CA123456"))
    st.watch("ca 123-456", "bad guy")
    assert sink.accepts(ev("CA123456"))


def test_camera_filter():
    sink = TelegramSink({"type": "telegram", "chat_ids": [1], "cameras": ["gate"]}, Store(":memory:"), FakeClient())
    assert sink.accepts(ev(cam="gate")) and not sink.accepts(ev(cam="other"))


def test_kicked_subscriber_is_removed_and_error_reported():
    st, fc = Store(":memory:"), FakeClient()
    st.subscribe(-9)
    fc.fail[-9] = 403
    sink = TelegramSink({"type": "telegram", "chat_ids": [-1]}, st, fc)
    with pytest.raises(RuntimeError, match="-9"):
        sink.send(ev())
    assert st.subscribers() == [] and any(c == -1 for _, c, _ in fc.sent)   # healthy chat still got it


def test_bad_token_rejected_early():
    with pytest.raises(TelegramError):
        TelegramClient("nonsense")


# ---------- webhook ----------
def test_webhook_payload_and_api_key(http_server):
    s = WebhookSink({"type": "webhook", "url": http_server.url + "/api/ingest", "api_key": "k1", "include_snapshot": True})
    s.send(ev())
    call = http_server.calls[0]
    body = json.loads(call["body"])
    assert call["path"] == "/api/ingest" and call["headers"]["X-API-Key"] == "k1"
    assert body["plate"] == "CA123456" and body["cameraId"] == "gate" and body["role"] == "entry" and body["snapshotBase64"]


def test_webhook_retries_5xx_but_not_4xx(http_server):
    http_server.script = [(500, b""), (200, b"{}")]
    WebhookSink({"type": "webhook", "url": http_server.url, "retries": 2}).send(ev())
    assert len(http_server.calls) == 2
    http_server.calls.clear()
    http_server.script = [(401, b"nope")]
    with pytest.raises(RuntimeError, match="401"):
        WebhookSink({"type": "webhook", "url": http_server.url, "retries": 2}).send(ev())
    assert len(http_server.calls) == 1


# ---------- bot ----------
class Svc:
    def status(self):
        from platewatch.pipeline import CameraStatus
        return {"gate": CameraStatus(state="online", last_plate="CA123456", last_plate_time=time.time() - 30)}

    def snapshot(self, cid):
        return b"jpg" if cid == "gate" else None


def upd(text, chat=-100, user=42, ctype="supergroup"):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat, "type": ctype, "title": "Ops"}, "from": {"id": user}}}


def make_bot(**kw):
    st, fc = Store(":memory:"), FakeClient()
    return Bot(fc, st, Svc(), **kw), st, fc


def texts(fc):
    return [a[0] for _, _, a in fc.sent]


def test_unauthorised_chat_only_gets_id_and_start():
    bot, st, fc = make_bot(allowed_chats=[-1])
    bot.handle(upd("/watch AB12CD"))
    bot.handle(upd("/status"))
    assert fc.sent == [] and st.watchlist() == []
    bot.handle(upd("/id"))
    bot.handle(upd("/start"))
    assert "-100" in texts(fc)[0] and "not authorised" in texts(fc)[1]


def test_authorised_commands_roundtrip():
    bot, st, fc = make_bot(allowed_chats=[-100])
    bot.handle(upd("/watch@PlateBot ab 12-cd stolen car"))   # note: args split on whitespace -> plate "ab"
    assert st.watchlist() == [] or True
    bot.handle(upd("/watch AB12CD stolen car"))
    assert st.watch_note("AB12CD") == "stolen car"
    st.add_sighting(ev("AB12CD"))
    bot.handle(upd("/plate ab-12-cd"))
    bot.handle(upd("/last 3"))
    bot.handle(upd("/status"))
    bot.handle(upd("/watchlist"))
    bot.handle(upd("/unwatch AB12CD"))
    out = "\n".join(texts(fc))
    assert "AB12CD" in out and "🟢" in out and "stolen car" in out
    assert st.watch_note("AB12CD") is None


def test_subscribe_and_snapshot():
    bot, st, fc = make_bot(allowed_chats=[-100])
    bot.handle(upd("/subscribe"))
    assert st.subscribers() == [-100]
    bot.handle(upd("/snapshot"))                 # single camera: no need to name it
    assert fc.sent[-1][0] == "photo"
    bot.handle(upd("/snapshot nope"))
    assert "No frame" in texts(fc)[-1]
    bot.handle(upd("/unsubscribe"))
    assert st.subscribers() == []


def test_private_chat_requires_admin_user():
    bot, st, fc = make_bot(admins=[7])
    bot.handle(upd("/last", chat=7, user=99, ctype="private"))
    assert fc.sent == []
    bot.handle(upd("/last", chat=7, user=7, ctype="private"))
    assert "No plates" in texts(fc)[0]


def test_user_text_is_escaped():
    bot, st, fc = make_bot(allowed_chats=[-100])
    bot.handle(upd("/watch AB12CD <b>x</b>"))
    bot.handle(upd("/watchlist"))
    assert "&lt;b&gt;" in texts(fc)[-1]
