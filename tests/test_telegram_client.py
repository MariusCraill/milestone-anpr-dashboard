import json

import pytest

from platewatch.telegram import TelegramClient, TelegramError

TOKEN = "123456:ABC-test"


def client(srv):
    return TelegramClient(TOKEN, api_base=srv.url)


def test_success_posts_to_bot_path_and_returns_result(http_server):
    http_server.script = [(200, json.dumps({"ok": True, "result": {"username": "mybot"}}).encode())]
    assert client(http_server).get_me() == {"username": "mybot"}
    assert http_server.calls[0]["path"] == f"/bot{TOKEN}/getMe"


def test_429_honours_retry_after_then_succeeds(http_server):
    http_server.script = [
        (429, json.dumps({"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 0}}).encode()),
        (200, json.dumps({"ok": True, "result": 1}).encode())]
    assert client(http_server).call("sendMessage", {"chat_id": 1, "text": "x"}) == 1
    assert len(http_server.calls) == 2


def test_api_error_carries_code_and_description_without_token(http_server):
    http_server.script = [(403, json.dumps({"ok": False, "error_code": 403, "description": "Forbidden: bot was kicked"}).encode())]
    with pytest.raises(TelegramError) as e:
        client(http_server).send_message(-1, "hi")
    assert e.value.code == 403 and "kicked" in str(e.value) and TOKEN not in str(e.value)


def test_photo_is_multipart_upload(http_server):
    http_server.script = [(200, json.dumps({"ok": True, "result": {}}).encode())]
    client(http_server).send_photo(-1, b"\xff\xd8jpegbytes", "cap")
    call = http_server.calls[0]
    assert b"multipart/form-data" in call["headers"]["Content-Type"].encode() and b"jpegbytes" in call["body"]
