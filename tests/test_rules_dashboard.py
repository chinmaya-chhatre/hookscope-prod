"""The rules dashboard renders the effective configuration without side effects."""

import json
from html.parser import HTMLParser

import httpx
import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.rules import ForwardRule
from hookscope.store import EventStore


class Cards(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.definition = None
        self.dangerous_tags = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag in {"img", "svg", "iframe", "object"}:
            self.dangerous_tags.append(tag)
        if tag == "details" and attrs.get("class") == "rule-card":
            self.rows.append(
                {
                    "name": attrs["data-rule-name"],
                    "management": attrs["data-management"],
                }
            )
        if tag == "pre" and attrs.get("class") == "rule-definition":
            self.definition = ""

    def handle_data(self, data):
        if self.definition is not None:
            self.definition += data

    def handle_endtag(self, tag):
        if tag == "pre" and self.definition is not None:
            self.rows[-1]["definition"] = json.loads(self.definition)
            self.definition = None


def definition(name="stored", **extra):
    return {"name": name, "target_url": "http://target.test/hook", **extra}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    monkeypatch.delenv("HOOKSCOPE_ADMIN_PASSWORD", raising=False)
    return EventStore(str(tmp_path / "events.db"))


def test_empty_rules_page_and_navigation(store):
    with TestClient(create_app(store=store, admin_password="")) as client:
        response = client.get("/rules")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "No forwarding rules configured." in response.text
        assert "0 effective rules in this process" in response.text
        assert Cards(response.text).rows == []
        assert '<a href="/rules" class="active" aria-current="page">Rules</a>' in response.text
        assert '<a href="/rules"' in client.get("/").text
        assert '<a href="/" class="active" aria-current="page">Events</a>' in client.get("/").text
        spec = client.get("/openapi.json").json()["paths"]["/rules"]["get"]
        assert spec["tags"] == ["dashboard"]
        assert "text/html" in spec["responses"]["200"]["content"]


def test_file_rules_win_collisions_and_keep_management_labels(store, tmp_path, monkeypatch):
    store.save_rule(definition("collision", target_url="http://shadowed.test/"))
    store.save_rule(definition("stored-only"))
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            [
                definition("file-first"),
                definition("collision", target_url="http://file.test/", enabled=False),
            ]
        )
    )
    monkeypatch.setenv("HOOKSCOPE_RULES", str(path))
    with TestClient(create_app(store=store, admin_password="")) as client:
        html = client.get("/rules").text
        rows = Cards(html).rows
        assert [row["name"] for row in rows] == ["file-first", "collision", "stored-only"]
        assert [row["management"] for row in rows] == ["file", "file", "stored"]
        assert [row["definition"] for row in rows] == client.get("/api/rules").json()
        assert "http://shadowed.test/" not in html
        assert "File (read-only)" in html and "Stored" in html
        assert "Paused" in html and "Enabled" in html
        assert "3 effective rules in this process" in html
    assert store.stored_rules()[0]["target_url"] == "http://shadowed.test/"


def test_page_preserves_all_normalized_settings_and_pause_state(store):
    store.save_rule(
        definition(
            source="stripe",
            event_types=["invoice.paid"],
            enabled=False,
            where=[{"path": "$.amount", "op": "gt", "value": 100}],
            transform={"amount": "$.amount"},
            retry={"attempts": 3, "backoff_seconds": 2, "max_backoff_seconds": 30},
        )
    )
    with TestClient(create_app(store=store, admin_password="")) as client:
        html = client.get("/rules").text
        assert Cards(html).rows[0]["definition"] == client.get("/api/rules").json()[0]
        assert "Source: <strong>stripe</strong>" in html
        assert "invoice.paid" in html
        assert "up to 3 attempts" in html
        assert "Paused rules do not match new events." in html
        assert "Already selected forwards and retries can finish." in html


def test_explicit_application_rules_override_file_and_storage(store, tmp_path, monkeypatch):
    store.save_rule(definition())
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([definition("file-only")]))
    monkeypatch.setenv("HOOKSCOPE_RULES", str(path))
    rule = ForwardRule("embedded", "http://target.test/embed")
    with TestClient(create_app(store=store, rules=[rule], admin_password="")) as client:
        html = client.get("/rules").text
        assert Cards(html).rows == [
            {"name": "embedded", "management": "application", "definition": rule.to_dict()}
        ]
        assert "Application (read-only)" in html
        assert "file-only" not in html
    with TestClient(create_app(store=store, rules=[], admin_password="")) as client:
        assert Cards(client.get("/rules").text).rows == []


def test_page_tracks_api_creation_pause_replacement_and_deletion(store):
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.post("/api/rules", json=definition()).status_code == 201
        assert len(Cards(client.get("/rules").text).rows) == 1
        paused = client.put(
            "/api/rules/stored", json=definition(target_url="http://target.test/second", enabled=False)
        ).json()
        html = client.get("/rules").text
        assert Cards(html).rows[0]["definition"] == paused
        assert "Paused" in html and "http://target.test/second" in html
        assert client.delete("/api/rules/stored").status_code == 200
        assert Cards(client.get("/rules").text).rows == []
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert "No forwarding rules configured." in client.get("/rules").text


def test_rendering_never_forwards_or_writes_rules_events_or_history(store):
    store.save_rule(definition())
    before = store.stored_rules(), store.rule_history(), store.count()
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))
    with httpx.Client(transport=transport) as http:
        with TestClient(create_app(store=store, http_client=http, admin_password="")) as client:
            for _ in range(2):
                html = client.get("/rules").text
                assert "Viewing it sends no webhooks and changes no rules." in html
                assert "<form " not in html
                assert "<button " not in html
    assert calls == []
    assert (store.stored_rules(), store.rule_history(), store.count()) == before


def test_rule_values_are_escaped_in_attributes_text_and_json(store):
    attack = '<img src=x onerror="alert(1)">'
    rule = ForwardRule(
        attack,
        "http://target.test/?q=<svg/onload=alert(2)>",
        event_types=("</script><script>alert(3)</script>",),
        transform={"unsafe": attack},
    )
    with TestClient(create_app(store=store, rules=[rule], admin_password="")) as client:
        html = client.get("/rules").text
        parsed = Cards(html)
        assert parsed.rows[0]["name"] == attack
        assert parsed.rows[0]["definition"] == rule.to_dict()
        assert parsed.dangerous_tags == []
        assert "<script>alert(3)</script>" not in html
        assert "&lt;img" in html and "\\u003cimg" in html


@pytest.mark.parametrize("authorization", [None, "Bearer wrong"])
def test_rules_page_is_protected_before_configuration_is_rendered(store, authorization):
    store.save_rule(definition("private-rule"))
    with TestClient(create_app(store=store, admin_password="password")) as client:
        response = client.get("/rules", headers={"Authorization": authorization} if authorization else {})
        assert response.status_code == 401
        assert "private-rule" not in response.text
        assert response.headers["WWW-Authenticate"].startswith("Basic ")


@pytest.mark.parametrize("method", ["basic", "bearer"])
def test_rules_page_accepts_current_admin_authentication(store, method):
    store.save_rule(definition())
    with TestClient(create_app(store=store, admin_password="password")) as client:
        kwargs = (
            {"auth": ("admin", "password")}
            if method == "basic"
            else {"headers": {"Authorization": "Bearer password"}}
        )
        assert len(Cards(client.get("/rules", **kwargs).text).rows) == 1


def test_rule_navigation_is_available_on_event_details(store):
    event = store.add("generic", {}, "{}", "no_secret", "")
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert '<a href="/rules"' in client.get(f"/events/{event}").text
