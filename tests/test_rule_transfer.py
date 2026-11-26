"""Rule transfer is atomic, authenticated, portable, and never sends on import."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import InvalidRuleError, parse_rules
from hookscope.store import EventStore


def rule(name="new", **extra):
    return {"name": name, "target_url": "http://localhost/target", **extra}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    monkeypatch.delenv("HOOKSCOPE_ADMIN_PASSWORD", raising=False)
    return EventStore(str(tmp_path / "events.db"))


def test_round_trip_preserves_normalized_rules_and_activation(store, tmp_path):
    definitions = [
        rule("active", source="generic", where=[{"path": "$.amount", "op": "gt", "value": 10}],
             transform={"amount": "$.amount"}, retry={"attempts": 2}),
        rule("paused", enabled=False, event_types=["special"], format="slack"),
    ]
    calls = []
    target = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200)
    ))
    with target, TestClient(create_app(store=store, http_client=target, admin_password="")) as client:
        response = client.post("/api/rules/import", json={"rules": definitions})
        assert response.status_code == 201
        normalized = [r.to_dict() for r in parse_rules(definitions)]
        assert response.json() == {"rules": normalized}
        assert calls == []
        exported = client.get("/api/rules/export")
        assert exported.json() == {"rules": normalized}
        assert 'filename="hookscope-rules.json"' in exported.headers["content-disposition"]
        assert exported.headers["content-type"] == "application/json"
        assert exported.headers["cache-control"] == "no-store"
        client.post("/hooks/generic", json={"amount": 5})
        assert calls == []
        client.post("/hooks/generic", json={"amount": 20})
        assert len(calls) == 1
        assert json.loads(calls[0].content) == {"amount": 20}
    fresh = EventStore(str(tmp_path / "copy.db"))
    with TestClient(create_app(store=fresh, admin_password="")) as client:
        assert client.post("/api/rules/import", json=exported.json()).status_code == 201
    with TestClient(create_app(store=fresh, admin_password="")) as client:
        assert client.get("/api/rules/export").json() == exported.json()
    assert len(fresh.rule_history()) == 2
    assert all(c["before"] is None and c["actor"] == "anonymous" for c in fresh.rule_history())


@pytest.mark.parametrize("payload", [
    [], {}, {"rules": []}, {"rules": {}}, {"rules": [rule()], "unknown": True},
    {"rules": [None]}, {"rules": [{"target_url": "http://localhost"}]},
    {"rules": [rule(" ")]}, {"rules": [rule(), rule()]},
    {"rules": [rule("valid"), rule("invalid", target_url="file:///tmp/no")]},
    {"rules": [rule(str(i)) for i in range(101)]},
])
def test_invalid_batch_has_no_effect(store, payload):
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.post("/api/rules/import", json=payload).status_code == 422
        assert client.get("/api/rules").json() == []
    assert store.stored_rules() == store.rule_history() == []


@pytest.mark.parametrize("raw", [
    b"not JSON", b"\xff", b'{"rules": [], "rules": []}',
    b'{"rules": [{"name": "new", "name": "other"}]}',
    b'{"rules": [NaN]}',
])
def test_invalid_json_rejected(store, raw):
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.post("/api/rules/import", content=raw).status_code == 422
    assert store.rule_history() == []


def test_conflict_at_end_does_not_import_first_rule(store):
    store.save_rule(rule("existing"))
    history = store.rule_history()
    with TestClient(create_app(store=store, admin_password="")) as client:
        response = client.post("/api/rules/import", json={"rules": [rule(), rule("existing")]})
        assert response.status_code == 409
        assert [r["name"] for r in client.get("/api/rules").json()] == ["existing"]
    assert store.rule_history() == history


def test_store_conflict_not_in_process_snapshot_is_atomic(store):
    app = create_app(store=store, admin_password="")
    store.save_rule(rule("external"))
    with TestClient(app) as client:
        assert client.post("/api/rules/import", json={"rules": [rule(), rule("external")]}).status_code == 409
        assert client.get("/api/rules").json() == []
    assert [r["name"] for r in store.stored_rules()] == ["external"]
    assert len(store.rule_history()) == 1


def test_history_failure_rolls_back_definitions_and_live_snapshot(store):
    with store._connect() as conn:
        conn.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON rule_changes
            WHEN NEW.name = 'fail' BEGIN SELECT RAISE(ABORT, 'history unavailable'); END""")
    with TestClient(create_app(store=store, admin_password=""), raise_server_exceptions=False) as client:
        assert client.post("/api/rules/import", json={"rules": [rule(), rule("fail")]}).status_code == 500
        assert client.get("/api/rules").json() == []
    assert store.stored_rules() == store.rule_history() == []


def test_file_rules_export_but_name_conflicts_are_read_only(store, tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [rule("file")]}))
    monkeypatch.setenv("HOOKSCOPE_RULES", str(path))
    with TestClient(create_app(store=store, admin_password="")) as client:
        exported = client.get("/api/rules/export").json()
        assert exported["rules"][0]["name"] == "file"
        assert client.post("/api/rules/import", json={"rules": [rule(), rule("file")]}).status_code == 409
    assert store.stored_rules() == []


def test_explicit_override_exports_and_refuses_import(store):
    with TestClient(create_app(store=store, rules=parse_rules([rule("app")]), admin_password="")) as client:
        assert client.get("/api/rules/export").json()["rules"][0]["name"] == "app"
        assert client.post("/api/rules/import", json={"rules": [rule()]}).status_code == 409
        assert 'id="rule-import"' not in client.get("/rules").text
    assert store.rule_history() == []


@pytest.mark.parametrize("headers,actor", [
    ({"Authorization": "Bearer password"}, "admin_bearer"),
    ({"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}, "admin_basic"),
])
def test_admin_auth_and_history(store, headers, actor):
    with TestClient(create_app(store=store, admin_password="password")) as client:
        assert client.get("/api/rules/export").status_code == 401
        assert client.post("/api/rules/import", json={"rules": [rule()]}).status_code == 401
        assert client.post("/api/rules/import", headers=headers, json={"rules": [rule()]}).status_code == 201
        assert client.get("/api/rules/export", headers=headers).status_code == 200
    assert store.rule_history()[0]["actor"] == actor


def test_import_byte_limit_counts_stream_and_declared_length(store):
    with TestClient(create_app(store=store, admin_password="", max_body_bytes=60)) as client:
        assert client.post("/api/rules/import", json={"rules": [rule()]}).status_code == 413
        assert client.post("/api/rules/import", content=iter([b" " * 40, b" " * 40])).status_code == 413
    assert store.rule_history() == []


def test_concurrent_batch_imports_have_one_winner(store):
    definitions = [rule("one"), rule("two")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.import_rules(definitions), range(12)))
    assert results.count(True) == 1
    assert len(store.stored_rules()) == len(store.rule_history()) == 2
    with pytest.raises(InvalidRuleError):
        store.import_rules([rule("third"), rule("third")])
    assert len(store.rule_history()) == 2


def test_dashboard_documents_atomic_import_and_export(store):
    with TestClient(create_app(store=store, admin_password="")) as client:
        page = client.get("/rules").text
    assert 'href="/api/rules/export"' in page
    assert 'id="rule-import-json"' in page
    assert "Existing names are never overwritten" in page
    assert "Inspect the rule list before retrying" in page


def test_maximum_batch_and_openapi_contract(store):
    with TestClient(create_app(store=store, admin_password="")) as client:
        response = client.post("/api/rules/import", json={
            "rules": [rule(f"copy-{i}") for i in range(100)]
        })
        assert response.status_code == 201
        assert len(response.json()["rules"]) == 100
        schema = client.get("/openapi.json").json()["paths"]["/api/rules/import"]["post"]
        rules_schema = schema["requestBody"]["content"]["application/json"]["schema"]["properties"]["rules"]
        assert rules_schema["maxItems"] == 100
        assert rules_schema["items"]["$ref"] == "#/components/schemas/RuleOut"
    assert len(store.rule_history(limit=100)) == 100
