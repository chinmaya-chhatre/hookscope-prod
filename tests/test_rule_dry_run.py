import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import forward_event, parse_rules
from hookscope.store import EventStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    return EventStore(str(tmp_path / "events.db"))


@pytest.mark.parametrize("definition,raw", [
    ({}, '{"text": "café", "amount": 12}'.encode()),
    ({}, b"name=caf" + bytes([233]) + b"&amount=5"),
    ({}, b"Body=A%2BB+%26&Empty="),
    ({"transform": {"total": "$.amount", "source": "@source"}}, b'{"amount":12}'),
    ({"format": "slack"}, b'{"type":"invoice.paid","amount":12}'),
])
def test_preview_equals_actual_prepared_forward_without_side_effects(store, definition, raw):
    rule = parse_rules([{"name": "test", "target_url": "http://target.test/path?route=1", **definition}])[0]
    event_id = store.add("generic", {
        "Content-Type": "application/json", "X-Signature": "preserved",
        "Authorization": "private-original", "Cookie": "session=private-original",
        "Host": "old.test", "Content-Length": "999", "X-HookScope-Replay": "old",
    }, raw.decode("utf-8", errors="replace"), "valid", "", raw_body=raw, request_id="request-123")
    captured = []
    with httpx.Client(
        headers={"X-Client": "configured", "Authorization": "private-client"},
        transport=httpx.MockTransport(lambda req: captured.append(req) or httpx.Response(204)),
    ) as http:
        before = store.count(), store.latest_forwards(), store.rule_history(), store.stored_rules()
        with TestClient(create_app(store=store, rules=[rule], http_client=http, admin_password="")) as client:
            response = client.post(f"/api/events/{event_id}/rule-preview", json={"rule": "test"})
            assert response.status_code == 200
            data = response.json()
            assert data["matches"] is True
            assert "private-original" not in response.text and "private-client" not in response.text
            for path in ("/", f"/events/{event_id}"):
                page = client.get(path)
                assert 'class="rule-dry-run"' in page.text
                assert "Test rule on this event" in page.text
        assert captured == []
        assert (store.count(), store.latest_forwards(), store.rule_history(), store.stored_rules()) == before
        assert forward_event(store.get(event_id), rule, http).status_code == 204
    sent = captured[0]
    preview = data["request"]
    assert preview["method"] == sent.method == "POST"
    assert preview["url"] == str(sent.url)
    assert base64.b64decode(preview["body_base64"], validate=True) == sent.content
    assert preview["body_bytes"] == len(sent.content)
    assert preview["headers"] == [
        [name, "[redacted]" if name == "authorization" else value]
        for name, value in sent.headers.multi_items()
    ]
    assert preview["redacted_headers"] == ["authorization"]
    assert sent.headers["X-HookScope-Forward"] == "test"
    assert sent.headers["X-Request-ID"] == "request-123"
    assert "X-HookScope-Replay" not in sent.headers
    if not definition:
        assert sent.content == raw
        assert sent.headers["X-Signature"] == "preserved"
    else:
        assert "X-Signature" not in sent.headers
        assert sent.headers["Content-Type"] == "application/json"
        assert isinstance(json.loads(sent.content), dict)
    if raw == b"name=caf" + bytes([233]) + b"&amount=5":
        assert preview["body_text"] is None
    else:
        assert preview["body_text"].encode() == sent.content


@pytest.mark.parametrize("rule_changes,verification,source", [
    ({"enabled": False}, "valid", "generic"),
    ({}, "invalid", "generic"), ({}, "unsigned", "generic"),
    ({"source": "stripe"}, "valid", "generic"),
    ({"event_types": ["invoice.paid"]}, "valid", "generic"),
    ({"where": [{"path": "$.amount", "op": "gt", "value": 99}]}, "valid", "generic"),
])
def test_nonmatching_rules_show_hypothetical_request_without_sending(
    store, rule_changes, verification, source
):
    rule = parse_rules([{"name": "test", "target_url": "http://target.test", **rule_changes}])[0]
    event_id = store.add(source, {}, '{"amount":1}', verification, "")
    with TestClient(create_app(store=store, rules=[rule], admin_password="")) as client:
        data = client.post(f"/api/events/{event_id}/rule-preview", json={"rule": "test"}).json()
        assert data["matches"] is False
        assert data["request"]["method"] == "POST"
    assert store.latest_forwards() == []


def test_validation_missing_event_and_rule_and_administrative_auth(store):
    event_id = store.add("generic", {}, "{}", "valid", "")
    with TestClient(create_app(store=store, rules=[], admin_password="secret")) as client:
        path = f"/api/events/{event_id}/rule-preview"
        assert client.post(path, json={"rule": "test"}).status_code == 401
        for payload in ({}, {"rule": ""}, {"rule": 12}):
            assert client.post(path, json=payload, auth=("admin", "secret")).status_code == 422
        assert client.post(path, json={"rule": "test"}, auth=("admin", "secret")).status_code == 404
        assert client.post("/api/events/999/rule-preview", json={"rule": "test"},
                           headers={"Authorization": "Bearer secret"}).status_code == 404


def test_current_stored_rule_is_used_and_url_credentials_are_hidden(store):
    with TestClient(create_app(store=store, admin_password="")) as client:
        rule = {"name": "stored", "target_url": "http://user:private-password@target.test/hook"}
        assert client.post("/api/rules", json=rule).status_code == 201
        event_id = store.add("generic", {}, "{}", "valid", "")
        path = f"/api/events/{event_id}/rule-preview"
        response = client.post(path, json={"rule": "stored"})
        assert response.status_code == 200
        assert "private-password" not in response.text
        assert response.json()["request"]["url"] == "http://target.test/hook"
        rule.update(target_url="http://target.test/new", enabled=False)
        assert client.put("/api/rules/stored", json=rule).status_code == 200
        data = client.post(path, json={"rule": "stored"}).json()
        assert data["matches"] is False and data["request"]["url"] == rule["target_url"]
        assert client.delete("/api/rules/stored").status_code == 200
        assert client.post(path, json={"rule": "stored"}).status_code == 404
    assert store.latest_forwards() == []
