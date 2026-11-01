import json

from hookscope.cli import main


def test_config_shows_effective_settings_and_masks_secrets(monkeypatch, capsys):
    monkeypatch.setenv("HOOKSCOPE_DB", "custom.db")
    monkeypatch.setenv("HOOKSCOPE_SECRET_GITHUB", "provider-secret-value")
    monkeypatch.setenv("HOOKSCOPE_ADMIN_PASSWORD", "admin-password-value")

    assert main(["config"]) == 0

    output = capsys.readouterr().out
    config = json.loads(output)
    assert config["db_path"] == "custom.db"
    assert config["secrets"]["github"] == "********"
    assert config["admin_password"] == "********"
    assert "provider-secret-value" not in output
    assert "admin-password-value" not in output


def test_config_reports_invalid_settings(monkeypatch, capsys):
    monkeypatch.setenv("HOOKSCOPE_MAX_BODY_BYTES", "invalid")

    assert main(["config"]) == 2
    assert "HOOKSCOPE_MAX_BODY_BYTES" in capsys.readouterr().err
