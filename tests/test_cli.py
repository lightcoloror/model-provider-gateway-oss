from __future__ import annotations

import json

from model_provider_gateway.cli import main


def test_catalog_cli(capsys) -> None:
    assert main(["catalog", "show", "gemini"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider_id"] == "gemini"


def test_profile_cli_rejects_unknown_preset(capsys) -> None:
    assert main(["profile", "build", "unknown", "--auth-ref", "env:KEY"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"


def test_mock_cli_uses_status_code(capsys) -> None:
    assert main(["mock-smoke"]) == 0
    assert json.loads(capsys.readouterr().out)["content"] == "mock-ok"
    assert main(["mock-smoke", "--trigger", "429"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == 429


def test_owner_capability_cli_covers_all_local_first_capabilities(capsys) -> None:
    assert main(["capability", "owners"]) == 0
    registry = json.loads(capsys.readouterr().out)
    assert set(registry["capabilities"]) == {"asr", "ocr", "tts", "embedding"}
    for capability in registry["capabilities"]:
        assert main(["capability", "mock-smoke", "--capability", capability]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["receipt"]["status"] == "validated_fixture"
        assert result["plan"]["execution_authorized"] is False
