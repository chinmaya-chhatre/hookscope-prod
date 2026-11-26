import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import InvalidRuleError, forward_event, parse_rules
from hookscope.store import EventStore

TOKEN = "HOOKSCOPE_FORWARD_TEST_TOKEN"
TEMPLATE = "Bearer ${env:HOOKSCOPE_FORWARD_TEST_TOKEN}"
RULE = {"name": "custom", "target_url": "https://receiver.test/hook"}
EVENT = {
    "id": 1,
    "source": "generic",
    "headers": {"X-Integration": "incoming", "Content-Type": "application/json"},
    "body": '{"amount": 42}',
    "verification": "valid",
    "received_at": "2026-01-01T00:00:00+00:00",
}


@pytest.mark.parametrize("mode", [{}, {"format": "slack"}, {"transform": {"total": "$.amount"}}])
def test_headers_override_on_each_forward(mode, monkeypatch):
    rule = parse_rules([{**RULE, **mode, "headers": {"Authorization": TEMPLATE, "X-Integration": "out"}}])[0]
    seen = []

    def receive(request):
        seen.append(request)
        return httpx.Response(200, text=request.headers["authorization"])

    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        for value in ("synthetic-one", "synthetic-two"):
            monkeypatch.setenv(TOKEN, value)
            result = forward_event(EVENT, rule, http)
            assert result.status_code == 200
            assert result.response_body == ""
    assert [r.headers["authorization"] for r in seen] == ["Bearer synthetic-one", "Bearer synthetic-two"]
    assert all(r.headers.get_list("x-integration") == ["out"] for r in seen)
    assert all(r.headers["x-hookscope-forward"] == "custom" for r in seen)
    assert rule.to_dict()["headers"]["authorization"] == TEMPLATE
    if not mode:
        assert seen[0].content == EVENT["body"].encode()
    elif "transform" in mode:
        assert json.loads(seen[0].content) == {"total": 42}


@pytest.mark.parametrize("value", [None, "", "bad\r\nheader", "é", "trailing "])
def test_bad_environment_value_never_sends(value, monkeypatch):
    if value is None:
        monkeypatch.delenv(TOKEN, raising=False)
    else:
        monkeypatch.setenv(TOKEN, value)
    rule = parse_rules([{**RULE, "headers": {"Authorization": TEMPLATE}}])[0]

    def unexpected(_):
        pytest.fail("invalid credential reached transport")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as http:
        result = forward_event(EVENT, rule, http)
    assert result.status_code is None
    assert "custom header" in result.error
    assert TOKEN not in result.error


@pytest.mark.parametrize("headers", [
    None, [], {"X-Multi": 1}, {"X Invalid": "ok"}, {"Host": "other.test"},
    {"Content-Length": "0"}, {"X-HookScope-Forward": "spoof"}, {"X-Request-ID": "spoof"},
    {"X-Key": "x", "x-key": "y"}, {"X-Key": "value\n"}, {"X-Key": " space"},
    {"Authorization": "${env:AWS_SECRET_ACCESS_KEY}"}, {"Authorization": "${env:HOOKSCOPE_FORWARD_}"},
])
def test_invalid_header_definitions(headers):
    with pytest.raises(InvalidRuleError):
        parse_rules([{**RULE, "headers": headers}])


def test_custom_headers_do_not_follow_redirects():
    rule = parse_rules([{**RULE, "headers": {"X-Key": "example"}}])[0]
    destinations = []

    def redirect(request):
        destinations.append(request.url.host)
        return httpx.Response(307, headers={"Location": "https://different.test/redirect"})

    with httpx.Client(follow_redirects=True, transport=httpx.MockTransport(redirect)) as http:
        assert forward_event(EVENT, rule, http).status_code == 307
    assert destinations == ["receiver.test"]


def test_preview_export_and_history_contain_no_resolved_values(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    monkeypatch.delenv(TOKEN, raising=False)
    store = EventStore(str(tmp_path / "events.db"))
    event = store.add("generic", {}, EVENT["body"], "valid", "")
    with TestClient(create_app(store=store, admin_password="")) as client:
        response = client.post("/api/rules", json={**RULE, "headers": {"X-Key": TEMPLATE}})
        assert response.status_code == 201
        assert response.json()["headers"] == {"x-key": TEMPLATE}
        preview_url = f"/api/events/{event}/rule-preview"
        preview = client.post(preview_url, json={"rule": "custom"})
        assert preview.status_code == 200
        assert ["x-key", "[redacted]"] in preview.json()["request"]["headers"]
        assert TEMPLATE not in preview.text
        monkeypatch.setenv(TOKEN, "synthetic-secret")
        for path in ("/api/rules", "/api/rules/export", "/api/rule-history", "/rules"):
            response = client.get(path)
            assert response.status_code == 200
            assert TEMPLATE in response.text
            assert "synthetic-secret" not in response.text
        assert "synthetic-secret" not in client.post(preview_url, json={"rule": "custom"}).text
    assert store.stored_rules()[0]["headers"] == {"x-key": TEMPLATE}
