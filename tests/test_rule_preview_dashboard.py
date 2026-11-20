from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import parse_rules
from hookscope.store import EventStore


def test_dashboard_and_permalink_preview_where_without_forwarding(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    event_id = store.add("generic", {}, '{"amount": 50}', "valid", "")
    rules = parse_rules([
        {"name": "small", "target_url": "http://localhost/hook",
         "where": [{"path": "$.amount", "op": "lt", "value": 100}]},
        {"name": "<large>", "target_url": "http://localhost/hook",
         "where": [{"path": "$.amount", "op": "gt", "value": 100}]},
    ])
    app = create_app(store=store, rules=rules, admin_password=None)
    with TestClient(app) as client:
        for path in ("/", f"/events/{event_id}"):
            response = client.get(path)
            assert response.status_code == 200
            assert "<strong>small</strong>: Matches" in response.text
            assert "<strong>&lt;large&gt;</strong>: Does not match" in response.text
            assert "$.amount" in response.text
            assert "No requests are sent" in response.text
    assert store.latest_forwards() == []


def test_rejected_capture_and_empty_rules(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    event_id = store.add("generic", {}, "{}", "invalid", "bad signature")
    app = create_app(store=store, rules=[], admin_password=None)
    with TestClient(app) as client:
        assert "No forwarding rules configured" in client.get(f"/events/{event_id}").text
