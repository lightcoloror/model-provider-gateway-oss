from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from model_provider_gateway.audit import assert_secretless, audit_secretless
from model_provider_gateway.catalog import catalog_list, catalog_show, find_model_preset
from model_provider_gateway.profile import build_profile, profile_revision, validate_profile


def test_required_provider_catalog_is_present() -> None:
    assert {row["provider_id"] for row in catalog_list()} == {
        "openai",
        "anthropic",
        "gemini",
        "openrouter",
        "siliconflow",
        "modelscope",
        "ark",
        "groq",
        "mistral",
        "deepseek",
        "zhipu",
        "moonshot",
        "dashscope",
        "together",
        "fireworks",
        "perplexity",
        "cerebras",
        "xai",
        "azure-openai",
        "vertex-ai",
        "aws-bedrock",
    }
    assert sum(row["status"] == "reviewed" for row in catalog_list()) == 18
    assert sum(row["status"] == "blocked" for row in catalog_list()) == 3


def test_catalog_show_and_unknown_provider() -> None:
    assert catalog_show("gemini")["destination"] == "generativelanguage.googleapis.com"
    with pytest.raises(ValueError, match="unknown provider_id"):
        catalog_show("arbitrary")


def test_unknown_model_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="unknown or unreviewed"):
        find_model_preset("made-up-model")


def test_profile_build_is_key_only_and_schema_valid() -> None:
    profile = build_profile(
        "gemini-3-6-flash",
        "env:GEMINI_API_KEY",
        last_verified="2026-08-05T00:00:00+00:00",
    )
    schema_path = (
        Path(__file__).parents[1] / "src" / "model_provider_gateway" / "schemas" / "provider-profile.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    jsonschema.validate(profile, schema, format_checker=jsonschema.FormatChecker())
    assert profile["base_url"] == "https://generativelanguage.googleapis.com/v1beta"
    assert "GEMINI_API_KEY" in profile["auth_ref"]
    assert "AIza" not in json.dumps(profile)
    assert_secretless(profile)


def test_profile_build_is_idempotent_with_pinned_verification_time() -> None:
    kwargs = {"last_verified": "2026-08-05T00:00:00+00:00"}
    assert build_profile("groq-whisper-large-v3-turbo", "env:GROQ_API_KEY", **kwargs) == build_profile(
        "groq-whisper-large-v3-turbo", "env:GROQ_API_KEY", **kwargs
    )


def test_arbitrary_url_and_route_drift_are_rejected() -> None:
    profile = build_profile("openai-gpt-5-6-luna", "env:OPENAI_API_KEY")
    changed = copy.deepcopy(profile)
    changed["base_url"] = "https://evil.example/v1"
    changed["allowed_destinations"] = ["evil.example"]
    changed["route_revision"] = profile_revision(changed)
    with pytest.raises(ValueError, match="drifted from reviewed preset"):
        validate_profile(changed)
    changed = copy.deepcopy(profile)
    changed["timeout_seconds"] += 1
    with pytest.raises(ValueError, match="route_revision"):
        validate_profile(changed)


def test_raw_or_credential_bearing_values_are_rejected() -> None:
    profile = build_profile("modelscope-glm-5-2", "env:MODELSCOPE_API_KEY")
    for auth_ref in ["ms-secret", "env:", "header:Authorization"]:
        changed = copy.deepcopy(profile)
        changed["auth_ref"] = auth_ref
        changed["route_revision"] = profile_revision(changed)
        with pytest.raises(ValueError, match="auth_ref"):
            validate_profile(changed)
    assert audit_secretless({"base_url": "https://user:pass@example.com/v1"})
    assert audit_secretless({"api_key": "sk-secret"})


def test_capability_mismatch_and_advanced_profile_are_rejected() -> None:
    profile = build_profile("modelscope-glm-5-2", "env:MODELSCOPE_API_KEY")
    changed = copy.deepcopy(profile)
    changed["capabilities"] = ["vision"]
    changed["route_revision"] = profile_revision(changed)
    with pytest.raises(ValueError, match="does not support"):
        validate_profile(changed)
    changed = copy.deepcopy(profile)
    changed["advanced"] = True
    changed["route_revision"] = profile_revision(changed)
    with pytest.raises(ValueError, match="advanced"):
        validate_profile(changed)


def test_profile_cannot_embed_fallback_pool() -> None:
    profile = build_profile("ark-glm-5-2", "env:ARK_API_KEY")
    profile["fallback_pool"] = ["another-profile"]
    profile["route_revision"] = profile_revision(profile)
    with pytest.raises(ValueError, match="explicit route plan"):
        validate_profile(profile)
