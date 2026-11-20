import json

import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import InvalidRuleError, load_rules
from hookscope.store import EventStore


def definition(name, target="http://localhost/hook"):
    return {"name": name, "target_url": target}


def test_persist_update_restart_and_file_precedence(tmp_path):
    path = str(tmp_path / "state.db")
    store = EventStore(path)
    store.save_rule(definition("stored"))
    store.save_rule(definition("shared", "http://localhost/stored"))
    store.save_rule(definition("stored", "http://localhost/updated"))
    restored = EventStore(path)
    file = tmp_path / "rules.json"
    file.write_text(json.dumps([definition("shared", "http://localhost/file")]))
    rules = load_rules(str(file), restored.stored_rules())
    assert [(r.name, r.target_url) for r in rules] == [
        ("shared", "http://localhost/file"), ("stored", "http://localhost/updated")
    ]
    assert file.read_text() == json.dumps([definition("shared", "http://localhost/file")])


@pytest.mark.parametrize("raw", [{}, {"name": ""}, {"name": "bad", "target_url": "file:///x"}])
def test_invalid_write_does_not_change_storage(tmp_path, raw):
    store = EventStore(str(tmp_path / "state.db"))
    with pytest.raises(InvalidRuleError):
        store.save_rule(raw)
    assert store.stored_rules() == []


def test_app_loads_stored_rules_and_explicit_override_remains_supported(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "state.db"))
    store.save_rule(definition("saved"))
    with TestClient(create_app(store=store, admin_password=None)) as client:
        assert client.get("/api/rules").json()[0]["name"] == "saved"
    with TestClient(create_app(store=store, rules=[], admin_password=None)) as client:
        assert client.get("/api/rules").json() == []
