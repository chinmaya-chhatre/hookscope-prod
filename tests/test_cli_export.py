import json
from urllib.parse import parse_qs, urlsplit

import pytest

from hookscope import cli


def args(*extra):
    return cli.build_parser().parse_args(["export", *extra])


def test_export_pages_without_loss(capsys):
    seen = []

    def fetch(url, headers):
        query = parse_qs(urlsplit(url).query)
        seen.append(query)
        if "before_id" not in query:
            return 200, [{"id": n, "body": "नमस्कार\\nline"} for n in range(60, 10, -1)]
        return 200, [{"id": n} for n in range(10, 0, -1)]

    assert cli.export_events(args("--source", "github"), fetch) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["id"] for row in rows] == list(range(60, 0, -1))
    assert seen[1]["before_id"] == ["11"]
    assert all(query["source"] == ["github"] for query in seen)


def test_empty_export(capsys):
    assert cli.export_events(args(), lambda *a: (200, [])) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("page", [
    [{"id": 1}, {"id": 2}], [{"id": 1}, {"id": 1}],
    [{"id": True}], [{"id": -1}], ["bad"], [{}],
])
def test_invalid_pages_fail_without_output(page, capsys):
    assert cli.export_events(args(), lambda *a: (200, page)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "incomplete" in captured.err


@pytest.mark.parametrize("status", [None, 401, 403, 500])
def test_errors_are_nonzero(status, capsys):
    assert cli.export_events(args(), lambda *a: (status, "failed")) == 2
    assert capsys.readouterr().err


def test_partial_export_failure_is_reported(capsys):
    pages = iter([(200, [{"id": n} for n in range(50, 0, -1)]), (500, "failed")])
    assert cli.export_events(args(), lambda *a: next(pages)) == 2
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 50
    assert "incomplete" in captured.err


def test_dispatch(monkeypatch):
    monkeypatch.setattr(cli, "export_events", lambda args: 7)
    assert cli.main(["export"]) == 7
