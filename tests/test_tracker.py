import numpy as np

from platewatch.config import CameraConfig, RecognitionConfig
from platewatch.recognizer import Reading
from platewatch.tracker import Aggregator, canon, edit_distance

FRAME = np.zeros((100, 200, 3), np.uint8)
BOX = (10, 10, 100, 40)


def agg(**kw):
    return Aggregator(CameraConfig(id="c1", name="Gate", source="x", role="entry"), RecognitionConfig(**kw))


def feed(a, text, conf, t):
    a.add(Reading(text, conf, BOX), FRAME, now=t)


def test_edit_distance_and_confusables():
    assert edit_distance("ABC", "ABD") == 1
    assert edit_distance("", "AB") == 2
    assert canon("B0S") == canon("8O5")        # 8/B, 0/O, 5/S treated as the same glyph


def test_emits_once_after_settle_with_consensus():
    a = agg()
    for i, (txt, c) in enumerate([("CA123456", 90), ("CA123456", 92), ("CA12345B", 60), ("CA123456", 95)]):
        feed(a, txt, c, 10 + i * 0.25)
    assert a.flush(now=11.0) == []             # vehicle may still be in view
    (ev,) = a.flush(now=14.0)
    assert ev.plate == "CA123456" and ev.reads == 4 and ev.camera_id == "c1" and ev.role == "entry"
    assert ev.snapshot and ev.plate_crop
    assert a.flush(now=30.0) == []


def test_single_misread_frame_is_dropped():
    a = agg(min_reads=2)
    feed(a, "XX99YYGP", 99, 5)
    assert a.flush(now=20) == []


def test_low_confidence_dropped():
    a = agg(min_confidence=80)
    for t in (1, 2, 3):
        feed(a, "AB12CDGP", 60, t)
    assert a.flush(now=20) == []


def test_cooldown_blocks_repeat_then_allows_after():
    a = agg(cooldown_seconds=60)
    for t in (1, 2):
        feed(a, "AB12CDGP", 90, t)
    assert len(a.flush(now=10)) == 1
    for t in (20, 21):
        feed(a, "AB12CDGP", 90, t)             # same car sits at the gate
    assert a.flush(now=30) == []
    for t in (100, 101):
        feed(a, "AB12CDGP", 90, t)
    assert len(a.flush(now=110)) == 1


def test_two_vehicles_at_once_are_separate_events():
    a = agg()
    for t in (1, 2, 3):
        feed(a, "AAA111", 90, t)
        feed(a, "ZZK999", 90, t + 0.1)
    assert sorted(e.plate for e in a.flush(now=20)) == ["AAA111", "ZZK999"]


def test_regex_and_length_filters():
    a = agg(plate_regex=r"^[A-Z]{2}\d{2}[A-Z]{2}GP$")
    for t in (1, 2):
        feed(a, "NOTAPLATE1", 95, t)
        feed(a, "AB12CDGP", 95, t)
    assert [e.plate for e in a.flush(now=20)] == ["AB12CDGP"]
    b = agg()
    feed(b, "AB1", 99, 1)                      # shorter than min_chars: never opens a sighting
    assert b.flush(now=50, force=True) == []


def test_long_sighting_is_force_emitted():
    a = agg(max_sighting_seconds=10, settle_seconds=5)
    for t in range(0, 12):
        feed(a, "AB12CDGP", 90, t)
    assert len(a.flush(now=11.5)) == 1
