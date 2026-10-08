# PlateWatch

Point it at any **ONVIF or RTSP camera** (or a video file). It finds number plates, reads them, merges the many frames of
each passing vehicle into **one clean result**, and pushes that to your **webhook / dashboard / Telegram groups**.

```
 camera (RTSP / ONVIF / file / USB)
        │  newest frame, ~4 fps
        ▼
 plate detector (YOLOv9) ─► OCR ─► aggregator ──► sinks ─► webhook  (dashboard, Milestone-side scripts, n8n, Home Assistant…)
                                   │  consensus of N frames,        ├► Telegram groups (photo + caption, watchlist alerts)
                                   │  min confidence, cooldown       └► JSONL log
                                   ▼
                          SQLite history + watchlist  ◄─► Telegram bot (/last /plate /snapshot /watch …)
```

* **Any camera**: RTSP/HTTP URLs, ONVIF cameras (stream URL looked up for you; LAN discovery included), local video files, USB webcams.
* **Runs on a normal CPU** (ONNX models, no GPU needed). Multiple cameras share one model.
* **Few false alarms**: a plate is only reported if several frames agree, the confidence is high enough, and it hasn't just been reported.
* **Optional dashboard** in [`dashboard/`](dashboard/README.md): who is on site, entries/exits, turnaround times (also usable with Milestone XProtect through the same webhook).

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                          # Python 3.10+; first run downloads ~10 MB of models

platewatch test-image some_car.jpg --out annotated.jpg    # sanity check the reader on a photo
platewatch discover                                       # find ONVIF cameras on your network
platewatch onvif-uri 192.168.1.64 --user admin            # list stream profiles / get the RTSP URL (asks the camera)

cp config.example.yaml config.yaml        # edit cameras + sinks
platewatch check && platewatch run
```

Telegram: follow [docs/TELEGRAM.md](docs/TELEGRAM.md) (`platewatch telegram-setup` finds your chat ids).
Dashboard: `cd dashboard && npm install && npm run demo` (see its README), then point a `webhook` sink at `http://localhost:3100/api/ingest`.

## What a result looks like

```json
{"plate": "CA123456", "confidence": 94.2, "cameraId": "gate-in", "cameraName": "Main Gate - IN",
 "timestamp": "2026-10-08T05:05:30+00:00", "reads": 11, "role": "entry", "region": "South Africa"}
```
`snapshotBase64` is added when `include_snapshot: true`. Telegram gets the photo with the plate boxed.

## Getting good accuracy (this matters more than any setting)

Software cannot recover what the camera didn't capture. For plates:

* **Plate at least ~120 px wide** in the frame where you want to read it. Use a narrow lens aimed at one lane, not a wide dome.
* **Angle under ~30°** horizontally and vertically; mount at plate height ±1 m, not high up looking down.
* **Fast shutter (1/1000 s or faster) and IR/white light at night**: motion blur and glare are the main cause of misreads. A dedicated ANPR camera helps a lot.
* Use the **main stream** (the highest-resolution profile; `onvif` sources pick it automatically) and `fps: 4–6`.
* Set `roi` to the lane, and `plate_regex` to your country's plate format: that removes most remaining false positives.
* Tune `min_reads` / `min_confidence` on your own footage; run with `-v` to see decisions.

The bundled models are open source (fast-alpr: YOLOv9 detector + CCT OCR trained on many countries). Accuracy on **your** plates and cameras
has not been measured here: test on real footage before relying on it for anything important.

## Configuration

See [`config.example.yaml`](config.example.yaml); every option is commented. Secrets use `${ENV_VAR}`. Unknown options are rejected with a clear error.

| Sink | Notes |
|---|---|
| `webhook` | JSON POST, optional API-key header, retries on network/5xx, no retry on 4xx |
| `telegram` | photo or text, groups/channels/topics, watchlist-only mode, per-camera filter, 429 back-off |
| `jsonl` | append-only file |

Each sink has its own queue and thread: a slow or failing receiver never stalls reading, and never affects other sinks.

## Commands

`run` · `check` · `discover` · `onvif-uri` · `test-image` · `test-sinks` · `telegram-setup` (use `-v` before the command for debug logs)

## Privacy and security

* Plates are personal data (POPIA/GDPR). Keep only what you need: `storage.retention_days` purges history; set `snapshots_dir: null` to keep no pictures.
* Camera and bot credentials: use environment variables. RTSP URLs are redacted in logs and CLI output.
* The Telegram bot answers commands only from `bot.allowed_chats` / `bot.admins`. Protect the dashboard with basic auth + HTTPS if exposed.
* Only point this at cameras you own or are authorised to use.

## Status / what's verified

Tested here: config validation, aggregation logic, webhook retries, Telegram client/sink/bot (against a fake API), ONVIF SOAP flow (against a mock server), and the
whole pipeline on a synthetic video with the real detector + OCR (`pytest`, 44 tests). **Not tested against real hardware or the real Telegram servers**:
physical ONVIF cameras (quirks between brands are common; fall back to a manual `source: rtsp://…`), live RTSP reconnects, and real-world plate accuracy.
GPU use needs `pip install onnxruntime-gpu` in place of `onnxruntime`.

## Development

```bash
pip install -e '.[dev]' && pytest
```
