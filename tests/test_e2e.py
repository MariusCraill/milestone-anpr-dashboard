"""Whole pipeline with the real detector/OCR on a synthetic video. Skipped if the models can't be loaded (offline)."""
import json

import cv2
import numpy as np
import pytest

from platewatch.cli import main


def _video(path):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (960, 540))
    for i in range(90):
        img = np.full((540, 960, 3), (95, 100, 100), np.uint8)
        x = 100 + i * 3
        cv2.rectangle(img, (x, 250), (x + 380, 440), (150, 40, 40), -1)
        cv2.rectangle(img, (x + 50, 330), (x + 330, 405), (235, 235, 235), -1)
        cv2.rectangle(img, (x + 50, 330), (x + 330, 405), (15, 15, 15), 3)
        cv2.putText(img, "CA 123 456", (x + 62, 390), cv2.FONT_HERSHEY_DUPLEX, 1.75, (10, 10, 10), 4)
        out.write(img)
    out.release()


def test_video_file_to_webhook_and_jsonl(tmp_path, http_server):
    pytest.importorskip("fast_alpr")
    try:
        from platewatch.recognizer import FastAlprRecognizer
        FastAlprRecognizer("yolo-v9-t-384-license-plate-end2end", "cct-xs-v2-global-model")
    except Exception as e:                       # no network for the one-off model download
        pytest.skip(f"models unavailable: {e}")
    vid = tmp_path / "gate.mp4"
    _video(vid)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"""
cameras: [{{id: gate, name: Gate, role: entry, source: {vid}, fps: 5}}]
recognition: {{settle_seconds: 1}}
sinks:
  - {{type: webhook, url: "{http_server.url}/api/ingest", api_key: k}}
  - {{type: jsonl, path: "{tmp_path}/p.jsonl"}}
bot: {{enabled: false}}
storage: {{db: "{tmp_path}/db.sqlite", snapshots_dir: "{tmp_path}/snaps"}}
""")
    assert main(["run", "-c", str(cfg)]) == 0
    assert len(http_server.calls) == 1
    body = json.loads(http_server.calls[0]["body"])
    assert body["cameraId"] == "gate" and body["plate"].startswith("CA123") and body["reads"] >= 2
    assert json.loads((tmp_path / "p.jsonl").read_text())["plate"] == body["plate"]
    assert list((tmp_path / "snaps").rglob("*.jpg"))
