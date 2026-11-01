import hashlib
import hmac
import json

from hookscope.cli import main


def saved_request(tmp_path, body, headers):
    body_path = tmp_path / "body.bin"
    headers_path = tmp_path / "headers.json"
    body_path.write_bytes(body)
    headers_path.write_text(json.dumps(headers), encoding="utf-8")
    return ["github", "--body", str(body_path), "--headers", str(headers_path)]


def test_verify_valid_request(tmp_path, monkeypatch, capsys):
    body = b'{"hello":"world"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    args = saved_request(tmp_path, body, {"X-Hub-Signature-256": f"sha256={digest}"})
    monkeypatch.setenv("HOOKSCOPE_SECRET_GITHUB", "secret")

    assert main(["verify", *args]) == 0
    assert "verification: valid" in capsys.readouterr().out


def test_verify_tampered_body_reports_diagnosis(tmp_path, monkeypatch, capsys):
    original = b'{"hello":"world"}'
    digest = hmac.new(b"secret", original, hashlib.sha256).hexdigest()
    args = saved_request(tmp_path, original + b"\\n", {"X-Hub-Signature-256": f"sha256={digest}"})
    monkeypatch.setenv("HOOKSCOPE_SECRET_GITHUB", "secret")

    assert main(["verify", *args]) == 1
    output = capsys.readouterr().out
    assert "verification: invalid" in output
    assert "diagnosis:" in output
    assert "fix:" in output


def test_verify_rejects_missing_secret(tmp_path, monkeypatch, capsys):
    args = saved_request(tmp_path, b"{}", {})
    monkeypatch.delenv("HOOKSCOPE_SECRET_GITHUB", raising=False)

    assert main(["verify", *args]) == 2
    assert "HOOKSCOPE_SECRET_GITHUB" in capsys.readouterr().err


def test_verify_rejects_invalid_headers(tmp_path, monkeypatch, capsys):
    args = saved_request(tmp_path, b"{}", {"header": 123})
    monkeypatch.setenv("HOOKSCOPE_SECRET_GITHUB", "secret")

    assert main(["verify", *args]) == 2
    assert "headers must be a JSON object" in capsys.readouterr().err


def test_verify_twilio_requires_url(tmp_path, monkeypatch, capsys):
    args = saved_request(tmp_path, b"{}", {})
    args[0] = "twilio"
    monkeypatch.setenv("HOOKSCOPE_SECRET_TWILIO", "secret")

    assert main(["verify", *args]) == 2
    assert "--url is required" in capsys.readouterr().err
