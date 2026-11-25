import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import ForwardRule, parse_rules
from hookscope.store import EventStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    return EventStore(str(tmp_path / "events.db"))


def definition(**changes):
    return {"name": "editor", "target_url": "http://target.test/hook", **changes}


def test_validate_create_edit_and_restart(store):
    calls = []
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200)
    )) as http:
        with TestClient(create_app(store=store, http_client=http, admin_password="")) as client:
            original = definition(where=[{"path": "$.amount", "op": "gt", "value": 2}])
            validated = client.post("/api/rule-validation", json=original)
            assert validated.status_code == 200
            assert validated.json() == parse_rules([original])[0].to_dict()
            assert store.stored_rules() == [] and store.rule_history() == []
            assert client.post("/api/rules", json=original).status_code == 201
            page = client.get("/rules", params={"edit": "editor"})
            assert page.status_code == 200
            assert 'data-mode="edit"' in page.text
            assert "The rule name cannot be changed." in page.text
            changed = definition(enabled=False, transform={"amount": "$.amount"})
            assert client.post("/api/rule-validation?edit=editor", json=changed).status_code == 200
            assert len(store.rule_history()) == 1
            assert client.put("/api/rules/editor", json=changed).status_code == 200
            assert client.post("/api/rule-validation", json=changed).status_code == 409
            assert client.post("/api/rule-validation?edit=editor",
                               json=definition(name="renamed")).status_code == 422
    assert calls == [] and store.count() == 0
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.get("/api/rules").json()[0]["enabled"] is False
        assert len(store.rule_history()) == 2


@pytest.mark.parametrize("payload", [
    {}, [], None, definition(name=" "), definition(target_url="file:///tmp/test"),
    definition(source=[]), definition(enabled="yes"), definition(format="wrong"),
    definition(retry={"attempts": 11}), definition(where=[{"path": "$", "op": "wrong"}]),
    definition(transform=[]),
])
def test_invalid_drafts_never_mutate(store, payload):
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.post("/api/rule-validation", json=payload).status_code == 422
    assert store.stored_rules() == [] and store.rule_history() == [] and store.count() == 0


def test_read_only_and_missing_edit(store, tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([definition()]))
    monkeypatch.setenv("HOOKSCOPE_RULES", str(path))
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert 'href="/rules?edit=editor"' not in client.get("/rules").text
        assert client.get("/rules?edit=editor").status_code == 409
        assert client.post("/api/rule-validation?edit=editor", json=definition()).status_code == 409
        assert client.get("/rules?edit=missing").status_code == 404
        assert client.post("/api/rule-validation?edit=missing",
                           json=definition(name="missing")).status_code == 404
    with TestClient(create_app(store=store, rules=[ForwardRule("app", "http://target.test")],
                               admin_password="")) as client:
        assert 'id="rule-editor"' not in client.get("/rules").text
        assert client.post("/api/rule-validation", json=definition()).status_code == 409
    assert store.stored_rules() == []


def test_editor_and_validation_require_admin(store):
    with TestClient(create_app(store=store, admin_password="secret")) as client:
        assert client.get("/rules").status_code == 401
        assert client.post("/api/rule-validation", json=definition()).status_code == 401
        assert client.post("/api/rule-validation", json=definition(),
                           auth=("admin", "secret")).status_code == 200
        assert client.get("/rules", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_conflict_after_validation_does_not_replace_an_existing_rule(store):
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.post("/api/rule-validation", json=definition()).status_code == 200
        assert client.post("/api/rules", json=definition(target_url="http://other.test")).status_code == 201
        assert client.post("/api/rules", json=definition()).status_code == 409
        assert client.get("/api/rules").json()[0]["target_url"] == "http://other.test"


def test_editor_preserves_names_and_escapes_draft_content(store):
    name = "ops/alerts ?&"
    draft = definition(name=name, event_types=["</textarea><img src=x onerror=alert(1)>"])
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.post("/api/rules", json=draft).status_code == 201
        page = client.get("/rules", params={"edit": name})
        assert page.status_code == 200
        assert "</textarea><img" not in page.text
        assert "\\u003c/textarea" in page.text
        assert client.post("/api/rule-validation", params={"edit": name}, json=draft).status_code == 200
        assert client.put("/api/rules/ops%2Falerts%20%3F%26",
                          json={**draft, "enabled": False}).status_code == 200
        assert client.delete("/api/rules/ops%2Falerts%20%3F%26").status_code == 200
