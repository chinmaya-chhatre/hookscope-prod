import json

import pytest

from hookscope import cli


def test_purge_request_and_counts(capsys, monkeypatch):
    monkeypatch.setenv("HOOKSCOPE_ADMIN_PASSWORD", "test-password")
    seen = []

    def request(url, body, headers):
        seen.append((url, json.loads(body), headers))
        return 200, '{"removed": 3, "remaining": 2}'

    args = cli.build_parser().parse_args(
        ["purge", "--url", "http://server/", "--keep-last", "2"]
    )
    assert cli.purge(args, request) == 0
    assert seen[0][0] == "http://server/api/purge"
    assert seen[0][1] == {"keep_last": 2}
    assert seen[0][2]["Authorization"].startswith("Basic ")
    assert "removed 3 events; 2 remaining" in capsys.readouterr().out


def test_purge_default_policy_json(capsys):
    def request(url, body, headers):
        assert json.loads(body) == {}
        return 200, '{"removed": 0, "remaining": 5}'

    args = cli.build_parser().parse_args(["purge", "--json"])
    assert cli.purge(args, request) == 0
    assert json.loads(capsys.readouterr().out) == {"removed": 0, "remaining": 5}


@pytest.mark.parametrize("status,body", [
    (None, "refused"), (401, "unauthorized"), (403, "forbidden"),
    (400, '{"error":{"message":"no retention policy"}}'),
    (500, "server error"), (200, "invalid"), (200, "[]"),
    (200, '{"removed":true,"remaining":0}'),
])
def test_purge_errors(status, body, capsys):
    args = cli.build_parser().parse_args(["purge"])
    assert cli.purge(args, lambda *a: (status, body)) == 2
    assert capsys.readouterr().err


def test_negative_limit_never_sends():
    args = cli.build_parser().parse_args(["purge", "--keep-last", "-1"])
    assert cli.purge(args, lambda *a: pytest.fail("must not send")) == 2


def test_purge_dispatch(monkeypatch):
    monkeypatch.setattr(cli, "purge", lambda args: 7)
    assert cli.main(["purge"]) == 7
