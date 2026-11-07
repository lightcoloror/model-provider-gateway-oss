from __future__ import annotations

from urllib.parse import urlparse

import pytest

from model_provider_gateway.audit import assert_secretless
from model_provider_gateway.catalog import CAPABILITY_NAMES, catalog_list, catalog_show
from model_provider_gateway.onboarding import onboarding_catalog
from model_provider_gateway.profile import build_profile, validate_profile

KEY_ONLY_PROVIDER_IDS = {
    "anthropic",
    "ark",
    "cerebras",
    "dashscope",
    "deepseek",
    "fireworks",
    "gemini",
    "groq",
    "mistral",
    "modelscope",
    "moonshot",
    "openai",
    "openrouter",
    "perplexity",
    "siliconflow",
    "together",
    "xai",
    "zhipu",
}

MINIMAL_FIELD_PROVIDER_IDS = {"aws-bedrock", "azure-openai", "vertex-ai"}


def test_preset_ids_are_unique_and_ready_endpoints_are_credential_free_https() -> None:
    preset_ids: list[str] = []
    for summary in catalog_list():
        provider = catalog_show(summary["provider_id"])
        assert set(provider["capability_status"]) == set(CAPABILITY_NAMES)
        assert set(provider["capability_status"].values()) <= {"reviewed", "blocked", "unknown"}
        assert provider["group_id"]
        assert provider["group_label"]
        assert provider["evidence_sources"]
        preset_ids.extend(model["preset_id"] for model in provider["models"])
        if provider["onboarding_status"] != "ready":
            continue
        parsed = urlparse(provider["base_url"])
        assert parsed.scheme == "https"
        assert parsed.hostname
        assert parsed.username is None
        assert parsed.password is None
    assert len(preset_ids) == len(set(preset_ids))


def test_key_only_and_minimal_field_sets_are_exact() -> None:
    providers = {row["provider_id"]: row for row in onboarding_catalog()["providers"]}
    assert {
        provider_id for provider_id, row in providers.items() if row["setup_mode"] == "api_key_only"
    } == KEY_ONLY_PROVIDER_IDS
    assert {
        provider_id for provider_id, row in providers.items() if row["setup_mode"] == "minimal_fields"
    } == MINIMAL_FIELD_PROVIDER_IDS
    assert all(providers[provider_id]["onboarding_status"] == "ready" for provider_id in KEY_ONLY_PROVIDER_IDS)
    assert all(
        providers[provider_id]["onboarding_status"] == "blocked_template_not_executable"
        for provider_id in MINIMAL_FIELD_PROVIDER_IDS
    )
    assert all(providers[provider_id]["required_fields"] for provider_id in MINIMAL_FIELD_PROVIDER_IDS)


def test_every_key_only_preset_builds_a_secretless_valid_profile() -> None:
    for provider_id in sorted(KEY_ONLY_PROVIDER_IDS):
        provider = catalog_show(provider_id)
        profile = build_profile(
            provider["models"][0]["preset_id"],
            f"env:{provider['auth_env']}",
            last_verified="2026-08-11T00:00:00+00:00",
        )
        assert validate_profile(profile) == profile
        assert profile["auth_ref"] == f"env:{provider['auth_env']}"
        assert_secretless(profile)


@pytest.mark.parametrize(
    "fields",
    [
        {"workspace_host": "https://ws-example.cn-beijing.maas.aliyuncs.com"},
        {"workspace_host": "ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"},
        {"workspace_host": "ws-example.cn-shanghai.maas.aliyuncs.com"},
        {"workspace_host": "example.com"},
        {"base_url": "https://example.com/v1"},
    ],
)
def test_dashscope_overlay_rejects_scheme_path_wrong_region_and_unknown_fields(fields: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_profile(
            "dashscope-qwen3-6-flash",
            "env:DASHSCOPE_API_KEY",
            provider_fields=fields,
        )


def test_dashscope_openai_compatible_text_does_not_claim_async_asr() -> None:
    provider = catalog_show("dashscope")
    assert provider["capability_status"]["text"] == "reviewed"
    assert provider["capability_status"]["vision"] == "reviewed"
    assert provider["capability_status"]["asr"] == "blocked"
    assert all("asr" not in model["capabilities"] for model in provider["models"])
