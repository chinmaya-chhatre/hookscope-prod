import json

import pytest

from hookscope.cli import main
from hookscope.ndjson import import_ndjson
from hookscope.store import EventStore, body_bytes


def test_round_trip(tmp_path):
    original = EventStore(str(tmp_path / "source.db"))
    original.add("github", {"x-test": "yes"}, "hello", "valid", "", request_id="req-1")
    original.add("generic", {}, "�", "invalid", "bad", raw_body=b"\xff",
                 diagnosis={"cause": "test", "message": "bad", "fix": "retry"})
    exported = original.list()
    target = str(tmp_path / "target.db")
    assert import_ndjson(target, "\n".join(json.dumps(row) for row in reversed(exported))) == 2
    restored = EventStore(target).list()
    assert restored == exported
    assert body_bytes(restored[0]) == b"\xff"
    assert EventStore(target).latest_forwards() == []


def test_invalid_later_line_leaves_existing_events_untouched(tmp_path):
    path = str(tmp_path / "events.db")
    store = EventStore(path)
    store.add("github", {}, "before", "valid", "")
    row = store.list()[0]
    with pytest.raises(ValueError, match="line 2"):
        import_ndjson(path, json.dumps(row) + "\n[]")
    assert store.count() == 1


def test_cli_import_and_fresh_ids(tmp_path, capsys):
    path = str(tmp_path / "events.db")
    store = EventStore(path)
    store.add("github", {}, "hello", "valid", "")
    file = tmp_path / "events.ndjson"
    file.write_text(json.dumps(store.list()[0]), encoding="utf-8")
    assert main(["import", str(file), "--db", path]) == 0
    assert "imported 1 events" in capsys.readouterr().out
    assert [r["id"] for r in store.list()] == [2, 1]
