from __future__ import annotations

import json

import pytest

from model_provider_gateway.onboarding import ProfileStore
from model_provider_gateway.profile import build_profile
from model_provider_gateway.runtime import render_service_bundle, write_secretless_config


def profile():
    return build_profile(
        "openai-gpt-5-6-luna",
        "keyring:profile/openai-default",
        profile_id="openai-default",
        last_verified="2026-08-11T00:00:00+00:00",
    )


def test_runtime_bundle_is_one_secretless_litellm_service(tmp_path) -> None:
    bundle = render_service_bundle([profile()])
    assert bundle["api_schema"] == "model_provider_gateway.http.v1"
    assert bundle["base_url"] == "http://127.0.0.1:9243/v1"
    assert bundle["models"] == ["mpg-openai-default"]
    assert bundle["safety"]["consumer_policy_required"] is True
    assert bundle["config"]["model_list"][0]["litellm_params"]["max_retries"] == 0
    serialized = json.dumps(bundle["config"])
    assert "keyring:" not in serialized
    assert "profile/openai-default" not in serialized

    path = write_secretless_config(bundle, tmp_path)
    assert path.exists()
    assert "MPG_CREDENTIAL_" in path.read_text(encoding="utf-8")


def test_runtime_rejects_non_loopback_and_empty_profiles() -> None:
    with pytest.raises(ValueError, match="loopback"):
        render_service_bundle([profile()], host="0.0.0.0")
    with pytest.raises(ValueError, match="at least one"):
        render_service_bundle([])


def test_profile_store_round_trip_is_valid(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    store.save(profile())
    assert store.list_profiles()[0]["profile_id"] == "openai-default"
