from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


class TelegramClient:
    """Thin Bot API client. Handles 429 retry_after and transient 5xx; never logs the token."""

    def __init__(self, token: str, api_base: str = "https://api.telegram.org", session: requests.Session | None = None):
        if not token or ":" not in token:
            raise TelegramError("Telegram bot token looks wrong (expected 123456:ABC...). Get one from @BotFather.")
        self._url = f"{api_base}/bot{token}"
        self.s = session or requests.Session()

    def call(self, method: str, data: dict | None = None, files: dict | None = None, timeout: float = 20, retries: int = 3):
        for attempt in range(retries + 1):
            try:
                r = self.s.post(f"{self._url}/{method}", data=data, files=files, timeout=timeout)
            except requests.RequestException as e:
                if attempt == retries:
                    raise TelegramError(f"{method}: network error: {type(e).__name__}")
                time.sleep(2 ** attempt)
                continue
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 200 and j.get("ok"):
                return j["result"]
            code = j.get("error_code", r.status_code)
            if code == 429 and attempt < retries:
                time.sleep(min(float(j.get("parameters", {}).get("retry_after", 1)) + 0.5, 60))
                continue
            if code >= 500 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise TelegramError(f"{method}: {j.get('description', r.text[:200])}", code)

    def get_me(self):
        return self.call("getMe")

    def send_message(self, chat_id, text: str, thread_id: int | None = None, html: bool = True):
        d = {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": "true"}
        if html:
            d["parse_mode"] = "HTML"
        if thread_id:
            d["message_thread_id"] = thread_id
        return self.call("sendMessage", d)

    def send_photo(self, chat_id, photo: bytes, caption: str = "", thread_id: int | None = None):
        d = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if thread_id:
            d["message_thread_id"] = thread_id
        return self.call("sendPhoto", d, files={"photo": ("plate.jpg", photo, "image/jpeg")})

    def get_updates(self, offset: int | None, timeout: int = 25, allowed: str = '["message"]'):
        d = {"timeout": timeout, "allowed_updates": allowed}
        if offset is not None:
            d["offset"] = offset
        return self.call("getUpdates", d, timeout=timeout + 10)
