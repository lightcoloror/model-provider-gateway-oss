from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from model_provider_gateway.consent import build_consent_plan, consent_revision, validate_consent
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan, route_revision, validate_route_plan


def _profile(preset: str, env: str):
    return build_profile(preset, f"env:{env}", last_verified="2026-08-05T00:00:00+00:00", max_cost_usd=1.0, max_calls=3)


def test_single_route_is_deterministic() -> None:
    profile = _profile("gemini-3-6-flash", "GEMINI_API_KEY")
    first = build_route_plan("vision", "vision", [profile])
    second = build_route_plan("vision", "vision", [profile])
    assert first == second
    assert first["fallback_policy"]["enabled"] is False


def test_multiple_members_require_explicit_fallback() -> None:
    profiles = [
        _profile("gemini-3-6-flash", "GEMINI_API_KEY"),
        _profile("siliconflow-glm-4-5v", "SILICONFLOW_API_KEY"),
    ]
    with pytest.raises(ValueError, match="explicit fallback"):
        build_route_plan("vision", "vision", profiles)
    route = build_route_plan("vision", "vision", profiles, fallback_enabled=True)
    assert route["fallback_policy"] == {"enabled": True, "ordered": True, "requires_consent": True}


def test_capability_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not support"):
        build_route_plan("bad", "vision", [_profile("modelscope-glm-5-2", "MODELSCOPE_API_KEY")])


def test_route_drift_is_rejected() -> None:
    route = build_route_plan("text", "text", [_profile("ark-glm-5-2", "ARK_API_KEY")])
    changed = copy.deepcopy(route)
    changed["limits"]["max_calls"] += 1
    with pytest.raises(ValueError, match="drift"):
        validate_route_plan(changed)


def test_consent_binds_artifact_route_and_limits(tmp_path) -> None:
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("synthetic evidence", encoding="utf-8")
    route = build_route_plan("text", "text", [_profile("openai-gpt-5-6-luna", "OPENAI_API_KEY")])
    consent = build_consent_plan(route, [artifact], capability="text", purpose="synthetic test", data_type="text")
    assert consent["artifacts"][0]["bytes"] == len("synthetic evidence")
    assert consent["authorized_deployments"][0]["destination"] == "api.openai.com"
    assert validate_consent(consent, route=route) is consent


def test_expired_consent_is_rejected(tmp_path) -> None:
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("x")
    route = build_route_plan("text", "text", [_profile("anthropic-claude-sonnet-5", "ANTHROPIC_API_KEY")])
    consent = build_consent_plan(route, [artifact], capability="text", purpose="test", data_type="text", ttl_seconds=1)
    with pytest.raises(ValueError, match="expired"):
        validate_consent(consent, route=route, now=datetime.now(UTC) + timedelta(seconds=2))


def test_consent_artifact_route_cost_and_call_drift_are_rejected(tmp_path) -> None:
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("original")
    route = build_route_plan("text", "text", [_profile("modelscope-glm-5-2", "MODELSCOPE_API_KEY")])
    consent = build_consent_plan(route, [artifact], capability="text", purpose="test", data_type="text")
    artifact.write_text("changed")
    with pytest.raises(ValueError, match="artifact.*drift"):
        validate_consent(consent, route=route)
    artifact.write_text("original")
    changed = copy.deepcopy(consent)
    changed["usage"]["calls_reserved"] = changed["limits"]["max_calls"] + 1
    with pytest.raises(ValueError, match="call limit"):
        validate_consent(changed, route=route)
    changed = copy.deepcopy(consent)
    changed["usage"]["estimated_cost_reserved_usd"] = changed["limits"]["max_cost_usd"] + 1
    with pytest.raises(ValueError, match="cost limit"):
        validate_consent(changed, route=route)
    drift_route = copy.deepcopy(route)
    drift_route["limits"]["max_calls"] += 1
    drift_route["route_revision"] = route_revision(drift_route)
    with pytest.raises(ValueError, match="route drift"):
        validate_consent(consent, route=drift_route)


def test_manifest_tamper_is_rejected(tmp_path) -> None:
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("x")
    route = build_route_plan("text", "text", [_profile("groq-qwen3-6-27b", "GROQ_API_KEY")])
    consent = build_consent_plan(route, [artifact], capability="text", purpose="test", data_type="text")
    consent["purpose"] = "changed"
    with pytest.raises(ValueError, match="manifest drift"):
        validate_consent(consent, route=route)
    consent["consent_sha256"] = consent_revision(consent)
    with pytest.raises(ValueError, match="input policy drift"):
        validate_consent(consent, route=route)
