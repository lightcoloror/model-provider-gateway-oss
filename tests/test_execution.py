from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from model_provider_gateway.adapters import adapter_contract, validate_adapter_request
from model_provider_gateway.consent import CONSENT_SCHEMA_V1, build_consent_plan, consent_revision
from model_provider_gateway.execution import (
    LiteLLMTransport,
    _schema,
    build_execution_request,
    execute_model_request,
)
from model_provider_gateway.ledger import ledger_status
from model_provider_gateway.mock_server import mock_server
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _contracts(
    tmp_path: Path,
    *,
    capability: str = "text",
    preset: str | None = None,
    calls: int = 3,
) -> tuple[dict, dict, dict, dict]:
    if capability == "text":
        artifact = tmp_path / "fixture.txt"
        artifact.write_text("synthetic evidence only", encoding="utf-8")
        preset = preset or "modelscope-glm-5-2"
    else:
        artifact = tmp_path / "fixture.png"
        artifact.write_bytes(PNG_1X1)
        preset = preset or "siliconflow-glm-4-5v"
    profile = build_profile(
        preset,
        "env:MPG_SYNTHETIC_KEY",
        last_verified="2026-08-05T00:00:00+00:00",
        max_calls=calls,
        max_cost_usd=1.0,
        timeout_seconds=1,
    )
    route = build_route_plan("wave1", capability, [profile], max_calls=calls, max_cost_usd=1.0)
    consent = build_consent_plan(
        route,
        [artifact],
        capability=capability,
        purpose="Return a concise synthetic-only result.",
        data_type="synthetic_fixture",
        max_calls=calls,
        max_cost_usd=1.0,
    )
    request = build_execution_request(
        profile,
        route,
        consent,
        consumer_id="content_studio",
        task="rewrite" if capability == "text" else "vision_candidate",
        max_estimated_cost_usd=0.1,
        request_id=f"wave1-{capability}-request",
    )
    return profile, route, consent, request


def _run(tmp_path: Path, profile: dict, route: dict, consent: dict, request: dict, base_url: str) -> dict:
    return execute_model_request(
        request,
        profile=profile,
        route=route,
        consent=consent,
        ledger_path=tmp_path / "ledger.json",
        transport=LiteLLMTransport.loopback_stub(base_url),
        secret_resolver=lambda _ref: "synthetic-secret-never-log",
    )


def test_text_execution_roundtrip_and_result_schema(tmp_path: Path) -> None:
    profile, route, consent, request = _contracts(tmp_path)
    with mock_server() as base_url:
        result = _run(tmp_path, profile, route, consent, request, base_url)
    assert result["ok"] is True
    assert result["output"]["text"] == "mock-ok"
    assert result["route_hash"] == route["route_revision"]
    assert result["consent_hash"] == consent["consent_sha256"]
    assert result["usage"]["total_tokens"] == 2
    assert result["cost"]["status"] == "unknown"
    assert result["redacted_audit"]["automatic_fallback_used"] is False
    jsonschema.validate(result, _schema("execution-result.v1.schema.json"))
    serialized = json.dumps(result)
    assert "sk-synthetic" not in serialized
    assert str(tmp_path) not in serialized


def test_vision_execution_uses_complete_data_url_in_memory(tmp_path: Path) -> None:
    profile, route, consent, request = _contracts(tmp_path, capability="vision")
    with mock_server() as base_url:
        result = _run(tmp_path, profile, route, consent, request, base_url)
    assert result["ok"] is True
    assert result["output"]["text"] == "mock-vision-ok"
    assert result["artifact_refs"][0]["mime_type"] == "image/png"
    assert "base64" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "expected_class", "retryable"),
    [(429, "rate_limited", True), (500, "provider_server_error", True), (400, "provider_client_error", False)],
)
def test_provider_http_failures_are_normalized_and_redacted(
    tmp_path: Path, status: int, expected_class: str, retryable: bool
) -> None:
    profile, route, consent, request = _contracts(tmp_path)
    with mock_server(status=status) as base_url:
        result = _run(tmp_path, profile, route, consent, request, base_url)
    assert result["ok"] is False
    assert result["error_class"] == expected_class
    assert result["retryable"] is retryable
    assert "synthetic-never-log" not in json.dumps(result)


def test_timeout_is_retryable_but_not_retried(tmp_path: Path) -> None:
    profile, route, consent, request = _contracts(tmp_path)
    with mock_server(delay_seconds=1.3) as base_url:
        result = _run(tmp_path, profile, route, consent, request, base_url)
    assert result["status"] == "failed"
    assert result["error_class"] == "timeout"
    assert result["retryable"] is True
    assert len(ledger_status(tmp_path / "ledger.json")["reservations"]) == 1


def test_request_replay_is_blocked_before_provider(tmp_path: Path) -> None:
    profile, route, consent, request = _contracts(tmp_path)
    with mock_server() as base_url:
        first = _run(tmp_path, profile, route, consent, request, base_url)
        second = _run(tmp_path, profile, route, consent, request, base_url)
    assert first["status"] == "completed"
    assert second["status"] == "blocked"
    assert second["error_class"] == "request_replay"
    assert len(ledger_status(tmp_path / "ledger.json")["reservations"]) == 1


def test_artifact_hash_drift_and_consent_v1_fail_closed(tmp_path: Path) -> None:
    profile, route, consent, request = _contracts(tmp_path)
    Path(request["artifacts"][0]["path"]).write_text("changed", encoding="utf-8")
    with mock_server() as base_url:
        result = _run(tmp_path, profile, route, consent, request, base_url)
    assert result["status"] == "blocked"
    assert not (tmp_path / "ledger.json").exists()
    Path(request["artifacts"][0]["path"]).write_text("synthetic evidence only", encoding="utf-8")
    legacy = copy.deepcopy(consent)
    legacy["schema"] = CONSENT_SCHEMA_V1
    legacy.pop("input_policy")
    for artifact in legacy["artifacts"]:
        artifact.pop("mime_type")
        artifact.pop("purpose")
    legacy["consent_sha256"] = consent_revision(legacy)
    with pytest.raises(ValueError, match="requires consent_manifest.v2"):
        build_execution_request(
            profile,
            route,
            legacy,
            consumer_id="content_studio",
            task="rewrite",
            max_estimated_cost_usd=0.1,
        )


def test_missing_budget_or_wrong_capability_is_blocked(tmp_path: Path) -> None:
    profile, route, consent, request = _contracts(tmp_path)
    request["max_estimated_cost_usd"] = 0
    with mock_server() as base_url:
        result = _run(tmp_path, profile, route, consent, request, base_url)
    assert result["status"] == "blocked"
    changed = copy.deepcopy(request)
    changed["max_estimated_cost_usd"] = 0.1
    changed["capability"] = "vision"
    with mock_server() as base_url:
        result = _run(tmp_path, profile, route, consent, changed, base_url)
    assert result["status"] == "blocked"


def test_explicit_route_member_does_not_automatically_fallback(tmp_path: Path) -> None:
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("synthetic", encoding="utf-8")
    first = build_profile(
        "modelscope-glm-5-2", "env:A", last_verified="2026-08-05T00:00:00+00:00", max_cost_usd=1, max_calls=2
    )
    second = build_profile(
        "ark-glm-5-2", "env:B", last_verified="2026-08-05T00:00:00+00:00", max_cost_usd=1, max_calls=2
    )
    route = build_route_plan("explicit", "text", [first, second], fallback_enabled=True, max_calls=2, max_cost_usd=1)
    consent = build_consent_plan(
        route, [artifact], capability="text", purpose="synthetic", data_type="text", max_calls=2, max_cost_usd=1
    )
    request = build_execution_request(
        first,
        route,
        consent,
        consumer_id="qrp",
        task="research_query",
        max_estimated_cost_usd=0.1,
        request_id="no-auto-fallback",
    )
    with mock_server(status=500) as base_url:
        result = _run(tmp_path, first, route, consent, request, base_url)
    assert result["status"] == "failed"
    assert result["redacted_audit"]["automatic_fallback_used"] is False
    assert len(ledger_status(tmp_path / "ledger.json")["reservations"]) == 1


def test_adapter_v2_exposes_enabled_and_blocked_capabilities() -> None:
    mvp = adapter_contract("mvp_video_pipeline")
    assert mvp["tasks"]["visual"]["execution_status"] == "enabled"
    assert mvp["tasks"]["tts"]["execution_status"] == "blocked"
    for consumer_id in ("qrp", "arw"):
        consumer = adapter_contract(consumer_id)
        assert consumer["tasks"]["visual_claim_assist"]["capabilities"] == ["vision"]
        assert consumer["tasks"]["visual_claim_assist"]["execution_status"] == "enabled"
        assert consumer["tasks"]["embedding_candidate"]["capabilities"] == ["embedding"]
        assert consumer["tasks"]["embedding_candidate"]["execution_status"] == "blocked"
    research = adapter_contract("research_content_production")
    assert research["tasks"]["orchestrate"]["execution_status"] == "control_plane_only"
    request = {
        "schema": "model_provider_gateway.adapter_request.v2",
        "consumer_id": "vkp",
        "task": "visual",
        "required_capabilities": ["vision"],
        "route_id": "vision",
        "route_revision": "a" * 64,
        "consent_hash": "b" * 64,
        "input_manifest_hash": "c" * 64,
    }
    with pytest.raises(ValueError, match="Broker reservation"):
        validate_adapter_request(request, adapter_contract("vkp"))
    request["owner_gate_receipt_hash"] = "d" * 64
    assert validate_adapter_request(request, adapter_contract("vkp")) is request
