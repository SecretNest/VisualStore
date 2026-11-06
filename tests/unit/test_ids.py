import uuid

from visualstore.clock import now_iso, parse_iso
from visualstore.ids import id_from_str, id_to_str, new_id


def test_new_id_is_16_bytes_and_unique():
    a, b = new_id(), new_id()
    assert len(a) == 16 and len(b) == 16
    assert a != b


def test_id_round_trips_through_string():
    raw = new_id()
    text = id_to_str(raw)
    assert uuid.UUID(text).bytes == raw
    assert id_from_str(text) == raw


def test_now_iso_is_utc_with_seconds_and_parses_back():
    text = now_iso()
    assert text.endswith("+00:00")
    parsed = parse_iso(text)
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.isoformat(timespec="seconds") == text
