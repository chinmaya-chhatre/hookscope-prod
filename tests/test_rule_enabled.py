"""Rules can be paused without losing their definition or forwarding history."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import ForwardRule, InvalidRuleError, deliver, load_rules, parse_rules
from hookscope.schemas import RuleOut
from hookscope.store import EventStore


def definition(**extra):
    return {"name": "paused", "target_url": "http://localhost/hook", **extra}


@pytest.mark.parametrize("enabled", [True, False])
def test_enabled_round_trip_file_and_response_model(tmp_path, enabled):
    raw = definition(enabled=enabled)
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([raw]))
    rule = load_rules(str(path))[0]
    assert rule.enabled is enabled
    assert parse_rules([rule.to_dict()])[0] == rule
    model = RuleOut.model_validate(rule.to_dict())
    assert model.enabled is enabled
    assert model.model_dump() == rule.to_dict()
    assert json.loads(model.model_dump_json()) == rule.to_dict()
    if enabled:
        assert "enabled" not in rule.to_dict()
    else:
        assert rule.to_dict()["enabled"] is False


@pytest.mark.parametrize("invalid", [None, 0, 1, "false", "true", [], {}])
def test_enabled_requires_a_json_boolean(invalid):
    with pytest.raises(InvalidRuleError, match="enabled must be a boolean"):
        parse_rules([definition(enabled=invalid)])


def test_old_definitions_stay_enabled_and_paused_rules_never_match():
    assert parse_rules([definition()])[0].enabled is True
    paused = ForwardRule("paused", "http://localhost/hook", enabled=False)
    for verification in ("valid", "no_secret", "invalid", "unsigned"):
        assert not paused.matches(
            {"source": "generic", "verification": verification, "headers": {}, "body": "{}"}
        )


def test_delivery_scheduler_excludes_paused_rules():
    calls = []
    recorded = []
    rules = [
        ForwardRule("paused", "http://localhost/paused", enabled=False),
        ForwardRule("active", "http://localhost/active"),
    ]
    event = {"id": 1, "source": "generic", "verification": "valid", "headers": {}, "body": "{}"}
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(str(request.url)) or httpx.Response(200))
    ) as target:
        deliver(event, rules, target, lambda rule, attempt, result: recorded.append(rule.name))
    assert calls == ["http://localhost/active"]
    assert recorded == ["active"]


def test_api_pause_resume_restart_and_manual_resend(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500))
    ) as target:
        with TestClient(create_app(store=store, http_client=target, admin_password="")) as client:
            created = client.post("/api/rules", json=definition(enabled=False))
            assert created.status_code == 201
            assert created.json()["enabled"] is False
            assert client.get("/api/rules").json()[0]["enabled"] is False
            assert client.post("/hooks/generic", json={}).json()["forwarded_to"] == []
            assert calls == []
            resumed = client.put("/api/rules/paused", json=definition(enabled=True))
            assert resumed.status_code == 200
            received = client.post("/hooks/generic", json={}).json()
            assert received["forwarded_to"] == ["paused"]
            assert len(calls) == 1
            event_id = received["id"]
            before = client.get(f"/api/events/{event_id}/forwards").json()
            assert client.put("/api/rules/paused", json=definition(enabled=False)).status_code == 200
            assert client.get("/api/dead-letters").json()[0]["resendable"] is False
            response = client.post(f"/api/events/{event_id}/forwards/paused/resend")
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "rule_disabled"
            assert client.get(f"/api/events/{event_id}/forwards").json() == before
            assert len(calls) == 1
        restored = EventStore(store.path)
        with TestClient(create_app(store=restored, http_client=target, admin_password="")) as client:
            assert client.get("/api/rules").json()[0]["enabled"] is False
            assert client.post("/hooks/generic", json={}).json()["forwarded_to"] == []
            assert len(calls) == 1
            assert client.put("/api/rules/paused", json=definition(enabled=True)).status_code == 200
            assert client.post(f"/api/events/{event_id}/forwards/paused/resend").status_code == 200
            assert len(calls) == 2
        assert len(store.stored_rules()) == 1


def test_invalid_enabled_api_updates_leave_rule_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    store.save_rule(definition(enabled=False))
    before = store.stored_rules()
    with TestClient(create_app(store=store, admin_password="")) as client:
        for value in (None, 0, "false", [], {}):
            response = client.put("/api/rules/paused", json=definition(enabled=value))
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_rule"
            assert client.get("/api/rules").json()[0]["enabled"] is False
    assert store.stored_rules() == before


def test_disabled_transform_preview_reports_no_match(tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([definition(enabled=False, transform={"text": "$.text"})]))
    monkeypatch.setenv("HOOKSCOPE_RULES", str(path))
    store = EventStore(str(tmp_path / "events.db"))
    with TestClient(create_app(store=store, admin_password="")) as client:
        event_id = client.post("/hooks/generic", json={"text": "hello"}).json()["id"]
        response = client.post(f"/api/events/{event_id}/transform-preview", json={"rule": "paused"})
        assert response.status_code == 200
        assert response.json()["matches"] is False
        assert response.json()["output"] == {"text": "hello"}
