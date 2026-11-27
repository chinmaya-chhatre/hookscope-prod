import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.replay import replay_event
from hookscope.rules import InvalidRuleError, forward_attempts, forward_event, parse_rules
from hookscope.store import EventStore

RULE = {"name": "timed", "target_url": "https://receiver.test/hook"}
EVENT = {
    "id": 1,
    "source": "generic",
    "headers": {"Content-Type": "application/json"},
    "body": '{"amount": 42}',
    "verification": "valid",
    "received_at": "2026-01-01T00:00:00+00:00",
}


@pytest.mark.parametrize("value", [True, "2", 0, 0.0001, -1, 121, float("nan"), float("inf"), 10**400])
def test_invalid_timeout(value):
    with pytest.raises(InvalidRuleError, match="timeout_seconds"):
        parse_rules([{**RULE, "timeout_seconds": value}])


@pytest.mark.parametrize("value", [0.001, 1, 2.5, 120])
def test_timeout_round_trips(value):
    rule = parse_rules([{**RULE, "timeout_seconds": value}])[0]
    assert rule.to_dict()["timeout_seconds"] == value
    assert parse_rules([rule.to_dict()]) == [rule]


@pytest.mark.parametrize(
    "options",
    [{}, {"format": "slack"}, {"transform": {"total": "$.amount"}}, {"headers": {"X-Key": "demo"}}],
)
def test_timeout_isolated_to_rule_and_applied_on_retry(options):
    targeted = parse_rules([{**RULE, **options, "timeout_seconds": 0.5, "retry": 2}])[0]
    default = parse_rules([{**RULE, "timeout_seconds": None}])[0]
    expected_override = httpx.Timeout(0.5).as_dict()
    expected_default = httpx.Timeout(7, connect=4).as_dict()
    observed = []

    def receive(request):
        observed.append(request.extensions["timeout"])
        if len(observed) == 1:
            raise httpx.ReadTimeout("receiver timed out", request=request)
        return httpx.Response(200)

    with httpx.Client(timeout=httpx.Timeout(7, connect=4), transport=httpx.MockTransport(receive)) as client:
        results = list(forward_attempts(EVENT, targeted, client))
        assert results[0][1].status_code is None
        assert "ReadTimeout" in results[0][1].error
        assert results[0][2] is not None
        assert results[1][1].status_code == 200
        assert forward_event(EVENT, default, client).status_code == 200
        assert replay_event(EVENT, RULE["target_url"], client).status_code == 200
    assert observed == [expected_override, expected_override, expected_default, expected_default]
    assert "timeout_seconds" not in default.to_dict()


def test_saved_timeout_survives_restart_and_export(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    path = str(tmp_path / "events.db")
    with TestClient(create_app(store=EventStore(path), admin_password="")) as client:
        response = client.post("/api/rules", json={**RULE, "timeout_seconds": 2.5})
        assert response.status_code == 201
        assert response.json()["timeout_seconds"] == 2.5
        invalid = {**RULE, "name": "bad", "timeout_seconds": True}
        assert client.post("/api/rules", json=invalid).status_code == 422
    with TestClient(create_app(store=EventStore(path), admin_password="")) as client:
        assert client.get("/api/rules/export").json()["rules"][0]["timeout_seconds"] == 2.5
        response = client.put("/api/rules/timed", json={**RULE, "timeout_seconds": None})
        assert response.status_code == 200
        assert "timeout_seconds" not in response.json()
        assert "timeout_seconds" not in client.get("/api/rules/export").json()["rules"][0]
