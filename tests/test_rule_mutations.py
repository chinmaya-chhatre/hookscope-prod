"""Stored rule replacement and deletion preserve forwarding and persistence contracts."""

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import InvalidRuleError
from hookscope.store import EventStore


def definition(name="stored", target="http://localhost/first", **extra):
    return {"name": name, "target_url": target, **extra}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    store.save_rule(definition())
    calls = []
    target = httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(str(request.url)) or httpx.Response(200))
    )
    with target:
        yield store, target, calls


def test_replace_activates_full_definition_and_survives_restart(setup):
    store, target, calls = setup
    replacement = definition(
        target="http://localhost/second", where=[{"path": "$.amount", "op": "gt", "value": 10}]
    )
    with TestClient(create_app(store=store, http_client=target, admin_password="")) as client:
        client.post("/hooks/generic", json={"amount": 20})
        response = client.put("/api/rules/stored", json=replacement)
        assert response.status_code == 200
        assert response.json()["where"] == replacement["where"]
        assert client.get("/api/rules").json() == [response.json()]
        client.post("/hooks/generic", json={"amount": 5})
        client.post("/hooks/generic", json={"amount": 20})
        assert calls == ["http://localhost/first", "http://localhost/second"]
        # PUT is a full replacement. Omitted fields reset to parser defaults.
        response = client.put("/api/rules/stored", json={"target_url": "http://localhost/third"})
        assert response.status_code == 200
        assert "where" not in response.json()
        client.post("/hooks/generic", json={"amount": 5})
        assert calls[-1] == "http://localhost/third"
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.get("/api/rules").json() == [response.json()]


def test_delete_stops_new_forwards_preserves_history_and_survives_restart(setup):
    store, target, calls = setup
    with TestClient(create_app(store=store, http_client=target, admin_password="")) as client:
        received = client.post("/hooks/generic", json={}).json()
        event_id = received["id"]
        response = client.delete("/api/rules/stored")
        assert response.status_code == 200
        assert response.json() == {"name": "stored", "deleted": True}
        assert client.get("/api/rules").json() == []
        client.post("/hooks/generic", json={})
        assert calls == ["http://localhost/first"]
        assert store.get(event_id) is not None
        assert len(store.forwards_by_event([event_id])[event_id]) == 1
        assert client.delete("/api/rules/stored").status_code == 404
        assert client.put("/api/rules/stored", json=definition()).status_code == 404
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.get("/api/rules").json() == []
    assert store.stored_rules() == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "renamed", "target_url": "http://localhost/new"},
        {"target_url": "file:///tmp/x"},
        {"target_url": "http://localhost/new", "where": [{"op": "unknown"}]},
    ],
)
def test_invalid_replacement_has_no_live_or_stored_side_effects(setup, payload):
    store, target, calls = setup
    before = store.stored_rules()
    with TestClient(create_app(store=store, http_client=target, admin_password="")) as client:
        response = client.put("/api/rules/stored", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_rule"
        client.post("/hooks/generic", json={})
        assert calls == ["http://localhost/first"]
    assert store.stored_rules() == before


def test_file_rule_collision_is_read_only(setup, tmp_path, monkeypatch):
    store, target, calls = setup
    file = tmp_path / "rules.json"
    original = json.dumps([definition(target="http://localhost/file")])
    file.write_text(original)
    monkeypatch.setenv("HOOKSCOPE_RULES", str(file))
    before = store.stored_rules()
    with TestClient(create_app(store=store, http_client=target, admin_password="")) as client:
        assert client.put("/api/rules/stored", json=definition()).status_code == 409
        assert client.delete("/api/rules/stored").status_code == 409
        client.post("/hooks/generic", json={})
        assert calls == ["http://localhost/file"]
    assert store.stored_rules() == before
    assert file.read_text() == original


@pytest.mark.parametrize("method", ["put", "delete"])
def test_authentication_and_explicit_override(setup, method):
    store, target, _ = setup
    before = store.stored_rules()
    with TestClient(
        create_app(store=store, rules=[], http_client=target, admin_password="password")
    ) as client:
        kwargs = {"json": definition()} if method == "put" else {}
        request = getattr(client, method)
        assert request("/api/rules/stored", **kwargs).status_code == 401
        response = request("/api/rules/stored", auth=("admin", "password"), **kwargs)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "rules_override"
    assert store.stored_rules() == before


@pytest.mark.parametrize("method,operation", [("put", "replace_rule"), ("delete", "delete_rule")])
def test_failed_persistence_does_not_change_live_rules(setup, monkeypatch, method, operation):
    store, target, calls = setup

    def fail(*args):
        raise sqlite3.OperationalError("write failed")

    monkeypatch.setattr(store, operation, fail)
    with TestClient(
        create_app(store=store, http_client=target, admin_password=""), raise_server_exceptions=False
    ) as client:
        kwargs = {"json": definition(target="http://localhost/new")} if method == "put" else {}
        assert getattr(client, method)("/api/rules/stored", **kwargs).status_code == 500
        client.post("/hooks/generic", json={})
        assert calls == ["http://localhost/first"]
    assert store.stored_rules()[0]["target_url"] == "http://localhost/first"


def test_storage_update_never_inserts_and_delete_is_name_scoped(tmp_path):
    store = EventStore(str(tmp_path / "state.db"))
    assert not store.replace_rule(definition("missing"))
    store.save_rule(definition("keep"))
    store.save_rule(definition())
    with pytest.raises(InvalidRuleError):
        store.replace_rule({"name": "stored", "target_url": "file:///tmp/x"})
    assert store.replace_rule(definition(target="http://localhost/new"))
    assert not store.delete_rule("missing")
    assert store.delete_rule("stored")
    assert not store.delete_rule("stored")
    assert [rule["name"] for rule in store.stored_rules()] == ["keep"]
