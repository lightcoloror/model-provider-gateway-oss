from __future__ import annotations

import json

from model_provider_gateway.cli import main


def test_execution_mock_cli_text_and_vision(capsys) -> None:
    assert main(["execution", "mock-smoke", "--capability", "text"]) == 0
    text_result = json.loads(capsys.readouterr().out)
    assert text_result["status"] == "completed"
    assert text_result["output"]["text"] == "mock-ok"
    assert text_result["redacted_audit"]["transport"] == "loopback_stub"

    assert main(["execution", "mock-smoke", "--capability", "vision"]) == 0
    vision_result = json.loads(capsys.readouterr().out)
    assert vision_result["status"] == "completed"
    assert vision_result["output"]["text"] == "mock-vision-ok"


def test_execution_mock_cli_uses_structured_failure_exit_codes(capsys) -> None:
    assert main(["execution", "mock-smoke", "--trigger", "429"]) == 2
    limited = json.loads(capsys.readouterr().out)
    assert limited["error_class"] == "rate_limited"
    assert limited["retryable"] is True

    assert main(["execution", "mock-smoke", "--trigger", "500"]) == 2
    server_error = json.loads(capsys.readouterr().out)
    assert server_error["error_class"] == "provider_server_error"


def test_research_content_production_is_control_plane_only(capsys) -> None:
    assert main(["adapter", "research_content_production"]) == 0
    contract = json.loads(capsys.readouterr().out)
    assert contract["tasks"]["orchestrate"]["execution_status"] == "control_plane_only"


def test_execution_cli_does_not_echo_local_path(capsys, tmp_path) -> None:
    private_path = tmp_path / "private-profile-name.json"
    assert (
        main(
            [
                "execution",
                "plan",
                str(private_path),
                str(tmp_path / "route.json"),
                str(tmp_path / "consent.json"),
                "--consumer-id",
                "qrp",
                "--task",
                "research_query",
                "--max-estimated-cost-usd",
                "0.1",
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": False, "status": "blocked", "error": "execution command validation failed"}
    assert "private-profile-name" not in json.dumps(result)
