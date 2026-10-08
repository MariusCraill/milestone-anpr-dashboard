from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time

from . import __version__
from .config import ConfigError, load


def _log(verbose: bool):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "onnxruntime", "open_image_models", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _make_recognizer(rc):
    from .recognizer import FastAlprRecognizer
    return FastAlprRecognizer(rc.detector_model, rc.ocr_model, rc.detector_conf, rc.min_plate_width)


def _onvif_resolver(cam):
    from .onvif import OnvifClient
    o = cam.onvif
    return OnvifClient(o["host"], o.get("port", 80), o.get("username", ""), o.get("password", ""),
                       scheme=o.get("scheme", "http")).rtsp_url(o.get("profile"))


# ---------------- commands ----------------

def cmd_run(a) -> int:
    from .bot import Bot
    from .pipeline import Service
    from .sinks import build_sinks
    from .store import Store
    from .telegram import TelegramClient

    cfg = load(a.config)
    store = Store(cfg.storage.db)
    sinks = build_sinks(cfg.sinks, store)
    log = logging.getLogger("platewatch")
    log.info("loading plate detector + OCR models (first run downloads ~10 MB)...")
    svc = Service(cfg, _make_recognizer(cfg.recognition), sinks, store, resolver=_onvif_resolver)
    bot = None
    token = cfg.bot.token or next((s.get("token") for s in cfg.sinks if s["type"] == "telegram"), None)
    if cfg.bot.enabled and token:
        bot = Bot(TelegramClient(token), store, svc, cfg.bot.allowed_chats, cfg.bot.admins)
        threading.Thread(target=bot.run, daemon=True, name="telegram-bot").start()
        if not cfg.bot.allowed_chats and not cfg.bot.admins:
            log.warning("Telegram bot is running but bot.allowed_chats/admins are empty: it will only answer /id and /start. See docs/TELEGRAM.md")
    svc.start()
    log.info("running with %d camera(s), %d sink(s). Ctrl+C to stop.", len(cfg.cameras), len(sinks))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    last_purge = 0.0
    while not stop.is_set():
        if time.time() - last_purge > 6 * 3600:
            n = store.purge(cfg.storage.retention_days)
            if n:
                log.info("retention: removed %d old sightings", n)
            last_purge = time.time()
        if svc.all_ended():
            time.sleep(2)           # let sinks drain
            break
        stop.wait(1)
    svc.stop()
    if bot:
        bot.stop()
    return 0


def cmd_check(a) -> int:
    cfg = load(a.config)
    print(f"OK: {len(cfg.cameras)} camera(s), {len(cfg.sinks)} sink(s)")
    for c in cfg.cameras:
        from .sources import redact
        print(f"  - {c.id}: {'ONVIF ' + c.onvif['host'] if c.onvif else redact(c.source)} @ {c.fps} fps"
              + (f" roi={c.roi}" if c.roi else ""))
    return 0


def cmd_discover(a) -> int:
    from .onvif import discover
    print(f"Probing the local network for ONVIF cameras ({a.timeout:.0f}s)...")
    found = discover(a.timeout)
    if not found:
        print("None found. Cameras must be on the same subnet, with ONVIF discovery enabled, and multicast allowed (not Docker bridge / VPN).")
        return 1
    for m in found:
        print(f"  {m['host']:<16} {m['name'] or ''} {m['hardware'] or ''}  {m['xaddrs'][0]}")
    print("\nNext: platewatch onvif-uri HOST --user USER --password PASS")
    return 0


def cmd_onvif_uri(a) -> int:
    from .onvif import OnvifClient, OnvifError
    from .sources import redact
    pw = a.password if a.password is not None else os.environ.get("ONVIF_PASSWORD", "")
    c = OnvifClient(a.host, a.port, a.user or "", pw)
    try:
        c.sync_clock()
        media = c.media_url()
        profs = c.profiles(media)
        print("Profiles:")
        for p in profs:
            print(f"  {p['token']:<14} {p['name'] or '':<16} {p['width']}x{p['height']} {p['encoding'] or ''}")
        url = c.rtsp_url(a.profile)
    except OnvifError as e:
        print(f"ONVIF error: {e}", file=sys.stderr)
        return 1
    print(f"\nStream URL: {redact(url)}  (credentials hidden)")
    print("Use it in config.yaml as either:\n  onvif: {host: %s, port: %d, username: %s, password: ${CAM_PASSWORD}}\nor:\n  source: <the rtsp URL with credentials>" % (a.host, a.port, a.user))
    if a.show:
        print("\nFull URL:", url)
    return 0


def cmd_test_image(a) -> int:
    import cv2
    from .config import RecognitionConfig
    rc = RecognitionConfig()
    img = cv2.imread(a.image)
    if img is None:
        print(f"cannot read image {a.image}", file=sys.stderr)
        return 1
    rec = _make_recognizer(rc)
    reads = rec.read(img)
    if not reads:
        print("No plates found.")
        return 1
    for r in reads:
        print(f"{r.text:<12} {r.confidence:5.1f}%  box={r.box}  region={r.region}")
        if a.out:
            cv2.rectangle(img, r.box[:2], r.box[2:], (0, 220, 255), 3)
            cv2.putText(img, r.text, (r.box[0], max(20, r.box[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 220, 255), 2)
    if a.out:
        cv2.imwrite(a.out, img)
        print(f"annotated image written to {a.out}")
    return 0


def cmd_test_sinks(a) -> int:
    import cv2
    import numpy as np
    from .events import PlateEvent
    from .sinks import build_sinks
    from .store import Store
    cfg = load(a.config)
    store = Store(":memory:")
    img = np.full((360, 640, 3), 70, np.uint8)
    cv2.putText(img, "PLATEWATCH TEST", (60, 190), cv2.FONT_HERSHEY_DUPLEX, 1.6, (255, 255, 255), 3)
    ok, buf = cv2.imencode(".jpg", img)
    ev = PlateEvent("TEST123", 99.0, cfg.cameras[0].id, cfg.cameras[0].name, time.time(), 5, cfg.cameras[0].role, snapshot=buf.tobytes())
    failed = 0
    for s in build_sinks(cfg.sinks, store):
        try:
            s.send(ev)
            print(f"  ✓ {s.name}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {s.name}: {e}")
    return 1 if failed else 0


def cmd_telegram_setup(a) -> int:
    from .telegram import TelegramClient, TelegramError
    token = a.token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Provide the token via the TELEGRAM_BOT_TOKEN environment variable (preferred) or --token.\nGet one: open Telegram, message @BotFather, send /newbot.", file=sys.stderr)
        return 1
    try:
        c = TelegramClient(token)
        me = c.get_me()
        print(f"✓ Token works. Bot is @{me['username']}")
        c.call("deleteWebhook")   # a leftover webhook blocks getUpdates
        print(f"\nNow, within {a.wait}s:\n  1. Add @{me['username']} to your group (or open a private chat with it)\n  2. Send any message there, e.g. /id\n")
        seen: dict[int, dict] = {}
        end, offset = time.time() + a.wait, None
        while time.time() < end:
            for u in c.get_updates(offset, timeout=min(10, max(1, int(end - time.time()))), allowed='["message","my_chat_member"]'):
                offset = u["update_id"] + 1
                m = u.get("message") or u.get("my_chat_member") or {}
                chat = m.get("chat") or {}
                if chat.get("id") is not None and chat["id"] not in seen:
                    seen[chat["id"]] = {"chat": chat, "user": (m.get("from") or {}).get("id")}
                    print(f"  found {chat.get('type'):<11} id={chat['id']:<16} {chat.get('title') or chat.get('username') or chat.get('first_name') or ''}")
        if not seen:
            print("No messages seen. Make sure you messaged the bot/group; in groups, privacy mode only shows /commands and @mentions.")
            return 1
        print("\nPaste into config.yaml:\n")
        gids = [i for i, v in seen.items() if v["chat"].get("type") != "private"]
        users = [v["user"] for v in seen.values() if v["chat"].get("type") == "private" and v["user"]]
        print("sinks:\n  - type: telegram\n    token: ${TELEGRAM_BOT_TOKEN}\n    chat_ids: %s" % (gids or list(seen)))
        print("bot:\n  allowed_chats: %s%s" % (list(seen), f"\n  admins: {users}" if users else ""))
        if a.send_test:
            for cid in seen:
                c.send_message(cid, "✅ PlateWatch is connected to this chat.")
            print("\nSent a confirmation message to each chat above.")
    except TelegramError as e:
        print(f"Telegram error: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="platewatch", description="Read number plates from camera streams and push the results anywhere.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("run", help="start reading cameras"); s.add_argument("-c", "--config", default="config.yaml"); s.set_defaults(fn=cmd_run)
    s = sp.add_parser("check", help="validate a config file"); s.add_argument("-c", "--config", default="config.yaml"); s.set_defaults(fn=cmd_check)
    s = sp.add_parser("discover", help="find ONVIF cameras on the LAN"); s.add_argument("--timeout", type=float, default=4); s.set_defaults(fn=cmd_discover)
    s = sp.add_parser("onvif-uri", help="ask a camera for its RTSP URL")
    s.add_argument("host"); s.add_argument("--port", type=int, default=80); s.add_argument("--user"); s.add_argument("--password", help="or set ONVIF_PASSWORD")
    s.add_argument("--profile"); s.add_argument("--show", action="store_true", help="print the URL including credentials"); s.set_defaults(fn=cmd_onvif_uri)
    s = sp.add_parser("test-image", help="run the plate reader on one image"); s.add_argument("image"); s.add_argument("--out"); s.set_defaults(fn=cmd_test_image)
    s = sp.add_parser("test-sinks", help="send a fake plate to every configured sink"); s.add_argument("-c", "--config", default="config.yaml"); s.set_defaults(fn=cmd_test_sinks)
    s = sp.add_parser("telegram-setup", help="verify a bot token and find your chat ids")
    s.add_argument("--token"); s.add_argument("--wait", type=int, default=60); s.add_argument("--send-test", action="store_true"); s.set_defaults(fn=cmd_telegram_setup)

    a = p.parse_args(argv)
    _log(a.verbose)
    try:
        return a.fn(a)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
