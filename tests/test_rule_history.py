"""Rule history records actual committed transitions without credential attribution."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from hookscope.main import create_app
from hookscope.schemas import RuleChangeOut
from hookscope.store import EventStore


def definition(target="http://localhost/first", **extra):
    return {"name": "stored", "target_url": target, **extra}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("HOOKSCOPE_RULES", raising=False)
    monkeypatch.delenv("HOOKSCOPE_ADMIN_PASSWORD", raising=False)
    return EventStore(str(tmp_path / "events.db"))


def test_storage_history_covers_create_replace_pause_delete_and_recreate(store):
    assert store.save_rule(definition())
    first = store.stored_rules()[0]
    assert store.replace_rule(definition(enabled=False))
    paused = store.stored_rules()[0]
    assert store.delete_rule("stored")
    assert store.save_rule(definition("http://localhost/new"))
    recreated = store.stored_rules()[0]
    changes = list(reversed(EventStore(store.path).rule_history("stored")))
    assert [(c["before"], c["after"]) for c in changes] == [
        (None, first),
        (first, paused),
        (paused, None),
        (None, recreated),
    ]
    assert all(c["actor"] == "programmatic" for c in changes)
    for change in changes:
        assert datetime.fromisoformat(change["changed_at"]).utcoffset().total_seconds() == 0
        assert RuleChangeOut.model_validate(change).model_dump() == change
    changes[0]["after"]["target_url"] = "http://localhost/tampered"
    assert store.rule_history("stored")[-1]["after"] == first
    event = store.add("generic", {}, "{}", "no_secret", "")
    assert store.purge(keep_last=0) == 1
    assert store.get(event) is None
    assert len(store.rule_history("stored")) == 4


def test_rejected_or_unchanged_storage_operations_do_not_invent_changes(store):
    assert not store.replace_rule(definition())
    assert not store.delete_rule("missing")
    assert store.rule_history() == []
    assert store.save_rule(definition())
    assert not store.save_rule(definition("http://localhost/other"), overwrite=False)
    assert store.save_rule(definition())
    assert store.replace_rule(definition())
    assert len(store.rule_history()) == 1


@pytest.mark.parametrize("operation", ["save_rule", "replace_rule", "delete_rule"])
def test_history_failure_rolls_back_the_rule_write(store, operation):
    if operation != "save_rule":
        store.save_rule(definition())
    before = store.stored_rules()
    history = store.rule_history()
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER history_failure BEFORE INSERT ON rule_changes "
            "BEGIN SELECT RAISE(ABORT, 'history write failed'); END"
        )
    argument = "stored" if operation == "delete_rule" else definition("http://localhost/new")
    with pytest.raises(sqlite3.IntegrityError, match="history write failed"):
        getattr(store, operation)(argument)
    assert store.stored_rules() == before
    assert store.rule_history() == history


def test_old_database_gets_history_without_fabricating_previous_changes(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE rules (name TEXT PRIMARY KEY, definition TEXT NOT NULL)")
        db.execute("INSERT INTO rules VALUES (?, ?)", ("stored", json.dumps(definition())))
    old = EventStore(str(path))
    assert old.rule_history() == []
    assert old.replace_rule(definition("http://localhost/new"))
    assert old.rule_history()[0]["before"] == definition()


def test_concurrent_replacements_capture_the_actual_previous_commit(store):
    store.save_rule(definition())

    def replace(index):
        return EventStore(store.path).replace_rule(definition(f"http://localhost/{index}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(replace, range(20)))
    history = list(reversed(store.rule_history()))
    assert len(history) == 21
    for before, after in zip(history, history[1:]):
        assert after["before"] == before["after"]
    assert store.stored_rules() == [history[-1]["after"]]


@pytest.mark.parametrize("actor", ["admin_basic", "admin_bearer", "anonymous"])
def test_http_actor_labels_never_record_passwords_or_claimed_usernames(store, actor):
    password = "private-admin-password"
    kwargs = {"admin_password": "" if actor == "anonymous" else password}
    with TestClient(create_app(store=store, **kwargs)) as client:
        if actor == "admin_basic":
            client.auth = ("unverified-person", password)
        else:
            client.headers["Authorization"] = f"Bearer {password}"
        client.headers["X-Actor"] = "unverified-person"
        created = client.post("/api/rules", json={**definition(), "actor": "unverified-person"})
        assert created.status_code == 201
        changed = client.put("/api/rules/stored", json=definition(enabled=False))
        assert changed.status_code == 200
        assert client.delete("/api/rules/stored").status_code == 200
        response = client.get("/api/rule-history")
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 3
        assert {row["actor"] for row in rows} == {actor}
        assert rows[-1]["before"] is None
        assert rows[-1]["after"] == created.json()
        assert rows[1]["before"] == created.json()
        assert rows[1]["after"] == changed.json()
        assert rows[0]["before"] == changed.json()
        assert rows[0]["after"] is None
        assert password not in response.text
        assert "unverified-person" not in response.text
        for row in rows:
            assert RuleChangeOut.model_validate(row).model_dump() == row
        spec = client.get("/openapi.json").json()
        schema = spec["paths"]["/api/rule-history"]["get"]["responses"]["200"]["content"]
        assert schema["application/json"]["schema"]["items"]["$ref"].endswith("/RuleChangeOut")


def test_history_pagination_keeps_older_pages_stable_during_new_writes(store):
    for i in range(4):
        store.save_rule(definition(f"http://localhost/{i}"))
    store.save_rule({"name": "other", "target_url": "http://localhost"})
    with TestClient(create_app(store=store, admin_password="")) as client:
        first = client.get("/api/rule-history", params={"name": "stored", "limit": 2})
        assert first.status_code == 200
        assert [c["id"] for c in first.json()] == [4, 3]
        cursor = first.headers["X-Next-Cursor"]
        store.save_rule(definition("http://localhost/newest"))
        second = client.get("/api/rule-history", params={"name": "stored", "limit": 2, "before_id": cursor})
        assert [c["id"] for c in second.json()] == [2, 1]
        assert "X-Next-Cursor" not in second.headers
        assert client.get("/api/rule-history", params={"name": "missing"}).json() == []
        assert len(client.get("/api/rule-history").json()) == 6


@pytest.mark.parametrize("query", ["limit=0", "limit=-1", "before_id=-1", "before_id=9223372036854775808"])
def test_history_rejects_invalid_page_arguments(store, query):
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.get("/api/rule-history?" + query).status_code == 422


def test_unauthorized_and_invalid_mutations_leave_no_history(store):
    with TestClient(create_app(store=store, admin_password="password")) as client:
        assert client.get("/api/rule-history").status_code == 401
        assert client.post("/api/rules", json=definition()).status_code == 401
        assert client.put("/api/rules/stored", json=definition()).status_code == 401
        assert client.delete("/api/rules/stored").status_code == 401
        client.auth = ("admin", "password")
        assert client.post("/api/rules", json={}).status_code == 422
        assert client.put("/api/rules/missing", json=definition(name="missing")).status_code == 404
        assert client.delete("/api/rules/missing").status_code == 404
        assert client.get("/api/rule-history").json() == []


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_history_failure_keeps_the_active_rule_snapshot(store, method):
    if method != "post":
        store.save_rule(definition())
    before = store.stored_rules()
    history = store.rule_history()
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER history_failure BEFORE INSERT ON rule_changes "
            "BEGIN SELECT RAISE(ABORT, 'history write failed'); END"
        )
    with TestClient(create_app(store=store, admin_password=""), raise_server_exceptions=False) as client:
        path = "/api/rules" if method == "post" else "/api/rules/stored"
        args = {} if method == "delete" else {"json": definition("http://localhost/new")}
        assert getattr(client, method)(path, **args).status_code == 500
        assert client.get("/api/rules").json() == before
        assert client.get("/api/rule-history").json() == history
    assert store.stored_rules() == before


def test_file_and_explicit_rules_are_never_fabricated_as_history(store, tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([definition()]))
    monkeypatch.setenv("HOOKSCOPE_RULES", str(path))
    with TestClient(create_app(store=store, admin_password="")) as client:
        assert client.get("/api/rules").json()
        assert client.put("/api/rules/stored", json=definition()).status_code == 409
        assert client.delete("/api/rules/stored").status_code == 409
        assert client.post("/api/rules", json=definition()).status_code == 409
        assert client.get("/api/rule-history").json() == []
    with TestClient(create_app(store=store, rules=[], admin_password="")) as client:
        assert client.post("/api/rules", json=definition()).status_code == 409
        assert client.get("/api/rule-history").json() == []
