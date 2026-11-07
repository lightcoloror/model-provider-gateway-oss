from __future__ import annotations

import copy
import os

import pytest

from model_provider_gateway.adapters import adapter_contract, validate_adapter_request
from model_provider_gateway.audit import assert_secretless
from model_provider_gateway.doctor import doctor
from model_provider_gateway.litellm_config import render_litellm_config
from model_provider_gateway.mock_server import mock_server, mock_smoke
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan
from model_provider_gateway.secrets import EnvironmentSecretStore, WindowsDpapiAdapter, secret_ready


def _profile(preset: str, env: str):
    return build_profile(preset, f"env:{env}", last_verified="2026-08-05T00:00:00+00:00")


def test_secretless_litellm_render() -> None:
    profile = _profile("groq-qwen3-6-27b", "GROQ_API_KEY")
    route = build_route_plan("summary", "text", [profile])
    config = render_litellm_config(route, [profile])
    assert config["model_list"][0]["litellm_params"]["api_key"] == "os.environ/GROQ_API_KEY"
    assert config["litellm_settings"]["telemetry"] is False
    assert_secretless(config)


def test_explicit_fallback_renders_distinct_deployments() -> None:
    profiles = [
        _profile("gemini-3-6-flash", "GEMINI_API_KEY"),
        _profile("siliconflow-glm-4-5v", "SILICONFLOW_API_KEY"),
    ]
    route = build_route_plan("vision", "vision", profiles, fallback_enabled=True)
    config = render_litellm_config(route, profiles)
    names = [item["model_name"] for item in config["model_list"]]
    assert len(names) == len(set(names)) == 2
    assert config["router_settings"]["fallbacks"] == [{names[0]: [names[1]]}]


def test_non_env_secret_requires_child_environment_injection() -> None:
    profile = build_profile("mistral-voxtral-mini-2602", "keyring:MISTRAL_API_KEY")
    route = build_route_plan("asr", "asr", [profile])
    with pytest.raises(ValueError, match="inject other stores"):
        render_litellm_config(route, [profile])


def test_mock_parity_and_failures() -> None:
    assert mock_smoke() == {"ok": True, "status": 200, "content": "mock-ok"}
    assert mock_smoke(trigger="429") == {"ok": False, "status": 429, "error": "rate_limit"}
    assert mock_smoke(trigger="500") == {"ok": False, "status": 500, "error": "server_error"}
    assert mock_smoke(trigger="timeout", timeout_seconds=0.01)["status"] == "timeout"


def test_litellm_openai_compatible_parity(monkeypatch) -> None:
    litellm = pytest.importorskip("litellm")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    litellm.telemetry = False
    with mock_server() as base_url:
        response = litellm.completion(
            model="openai/mock-model",
            api_base=f"{base_url}/v1",
            api_key="synthetic-only",
            messages=[{"role": "user", "content": "ping"}],
            timeout=2,
            max_retries=0,
        )
    assert response.choices[0].message.content == "mock-ok"


def test_environment_store_and_doctor(monkeypatch) -> None:
    monkeypatch.setenv("MPG_TEST_KEY", "synthetic-secret")
    assert EnvironmentSecretStore().get("MPG_TEST_KEY") == "synthetic-secret"
    assert secret_ready("env:MPG_TEST_KEY") is True
    profile = _profile("openai-gpt-5-6-luna", "MPG_TEST_KEY")
    result = doctor([profile], check_secrets=True)
    assert result["ok"] is True
    assert "synthetic-secret" not in str(result)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_adapter_roundtrip_does_not_echo_plaintext() -> None:
    adapter = WindowsDpapiAdapter()
    encrypted = adapter.protect("synthetic-secret")
    assert "synthetic-secret" not in encrypted
    assert adapter.unprotect(encrypted) == "synthetic-secret"


@pytest.mark.parametrize("consumer_id", ["qrp", "arw", "content_studio", "mvp_video_pipeline", "vkp"])
def test_adapter_contracts_are_versioned(consumer_id: str) -> None:
    contract = adapter_contract(consumer_id)
    assert contract["schema"] == "model_provider_gateway.adapter_contract.v2"
    assert contract["tasks"]


def test_adapter_request_binds_route_and_rejects_provider_fields() -> None:
    contract = adapter_contract("vkp")
    request = {
        "schema": "model_provider_gateway.adapter_request.v1",
        "consumer_id": "vkp",
        "task": "visual",
        "required_capabilities": ["vision"],
        "route_id": "vision",
        "route_revision": "a" * 64,
        "artifact_refs": [{"sha256": "b" * 64}],
    }
    assert validate_adapter_request(request, contract) is request
    for forbidden in ("provider_url", "api_key", "headers"):
        changed = copy.deepcopy(request)
        changed[forbidden] = "forbidden"
        with pytest.raises(ValueError, match="may not submit"):
            validate_adapter_request(changed, contract)
