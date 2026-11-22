import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.store import EventStore


def test_create_persist_and_forward(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    calls = []
    target = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200)
    ))
    definition = {"name": "new", "target_url": "http://localhost/hook",
                  "where": [{"path": "$.amount", "op": "gt", "value": 10}]}
    with TestClient(create_app(store=store, http_client=target, admin_password=None)) as client:
        response = client.post("/api/rules", json=definition)
        assert response.status_code == 201
        assert response.json()["where"] == definition["where"]
        assert client.get("/api/rules").json() == [response.json()]
        client.post("/hooks/generic", json={"amount": 5})
        assert calls == []
        client.post("/hooks/generic", json={"amount": 20})
        assert len(calls) == 1
        assert client.post("/api/rules", json=definition).status_code == 409
    with TestClient(create_app(store=store, admin_password=None)) as client:
        assert client.get("/api/rules").json()[0]["name"] == "new"


@pytest.mark.parametrize("payload", [
    {}, {"name": ""}, {"name": "bad", "target_url": "file:///tmp/x"},
    {"name": "bad", "target_url": "http://localhost", "source": "unknown"},
    {"name": "bad", "target_url": "http://localhost", "where": [{"op": "wrong"}]},
])
def test_invalid_rule_has_no_side_effects(tmp_path, monkeypatch, payload):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    with TestClient(create_app(store=store, admin_password=None)) as client:
        response = client.post("/api/rules", json=payload)
        assert response.status_code == 422
        assert "error" in response.json()
        assert client.get("/api/rules").json() == []
    assert store.stored_rules() == []


def test_override_and_authentication(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    definition = {"name": "new", "target_url": "http://localhost/hook"}
    with TestClient(create_app(store=store, rules=[], admin_password="password")) as client:
        assert client.post("/api/rules", json=definition).status_code == 401
        assert client.post("/api/rules", json=definition,
                           auth=("admin", "password")).status_code == 409
    assert store.stored_rules() == []


def test_create_only_storage_preserves_existing_definition(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    assert store.save_rule({"name": "same", "target_url": "http://localhost/first"})
    assert not store.save_rule(
        {"name": "same", "target_url": "http://localhost/second"}, overwrite=False
    )
    assert store.stored_rules()[0]["target_url"] == "http://localhost/first"


def test_empty_predicates_keep_response_compatible(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    with TestClient(create_app(store=store, admin_password=None)) as client:
        response = client.post(
            "/api/rules", json={"name": "plain", "target_url": "http://localhost/hook"}
        )
        assert response.status_code == 201
        assert "where" not in response.json()
        assert client.get("/api/rules").json() == [response.json()]


@pytest.mark.parametrize("where", [[], [{"path": "$.amount", "op": "gt", "value": 10}]])
def test_rule_model_preserves_wire_contract(where):
    import json

    from hookscope.rules import parse_rules
    from hookscope.schemas import RuleOut

    definition = {"name": "example", "target_url": "http://localhost/hook", "where": where}
    expected = parse_rules([definition])[0].to_dict()
    model = RuleOut.model_validate(expected)
    assert model.model_dump() == expected
    assert json.loads(model.model_dump_json()) == expected
    assert RuleOut.model_validate(model.model_dump()).model_dump() == expected
