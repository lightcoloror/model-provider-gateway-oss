from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from model_provider_gateway.onboarding import OnboardingService, ProfileStore, onboarding_catalog
from model_provider_gateway.web import create_app


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


def test_catalog_separates_key_only_from_minimal_field_cloud_templates() -> None:
    catalog = onboarding_catalog()
    assert catalog["api_schema"] == "model_provider_gateway.http.v1"
    assert len(catalog["providers"]) == 21
    assert sum(provider["setup_mode"] == "api_key_only" for provider in catalog["providers"]) == 18
    assert sum(provider["setup_mode"] == "minimal_fields" for provider in catalog["providers"]) == 3
    assert all(provider["advanced_fields_locked"] for provider in catalog["providers"])
    assert all(
        set(provider["capability_status"]) == {"text", "vision", "asr", "ocr", "tts", "embedding"}
        for provider in catalog["providers"]
    )
    assert catalog["safety"]["saving_authorizes_provider_call"] is False


def test_onboarding_stores_only_a_keyring_reference(tmp_path) -> None:
    secrets = MemorySecrets()
    service = OnboardingService(ProfileStore(tmp_path), secrets)
    result = service.onboard(provider_id="openai", api_key="synthetic-secret-value")

    document = (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert "synthetic-secret-value" not in document
    assert "keyring:profile/openai-default" in document
    assert secrets.values["profile/openai-default"] == "synthetic-secret-value"
    assert result["secret_echoed"] is False
    assert "synthetic-secret-value" not in json.dumps(result)


def test_dashscope_workspace_overlay_is_allowlisted_and_local_only(tmp_path) -> None:
    secrets = MemorySecrets()
    service = OnboardingService(ProfileStore(tmp_path), secrets)
    result = service.onboard(
        provider_id="dashscope",
        preset_id="dashscope-qwen3-6-flash",
        api_key="synthetic-secret-value",
        provider_fields={"workspace_host": "ws-example.cn-beijing.maas.aliyuncs.com"},
    )
    profile = service.profile_store.list_profiles()[0]
    assert profile["base_url"] == "https://ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    assert profile["allowed_destinations"] == ["ws-example.cn-beijing.maas.aliyuncs.com"]
    assert result["profile"]["provider_fields"] == {"workspace_host": "ws-example.cn-beijing.maas.aliyuncs.com"}


def test_invalid_workspace_or_blocked_cloud_template_never_stores_secret(tmp_path) -> None:
    secrets = MemorySecrets()
    service = OnboardingService(ProfileStore(tmp_path), secrets)
    for provider_id, fields in [
        ("dashscope", {"workspace_host": "evil.example"}),
        ("azure-openai", {}),
    ]:
        try:
            service.onboard(
                provider_id=provider_id,
                api_key="synthetic-secret-value",
                provider_fields=fields,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe provider onboarding unexpectedly succeeded")
    assert secrets.values == {}


def test_ui_bootstrap_and_save_are_secretless(tmp_path) -> None:
    secrets = MemorySecrets()
    service = OnboardingService(ProfileStore(tmp_path), secrets)
    client = TestClient(create_app(service=service))

    bootstrap = client.get("/api/v1/bootstrap")
    assert bootstrap.status_code == 200
    token = bootstrap.json()["csrf_token"]
    assert bootstrap.json()["runtime"]["provider_call_performed"] is False

    rejected = client.post(
        "/api/v1/profiles",
        json={"provider_id": "openai", "api_key": "synthetic-secret-value"},
    )
    assert rejected.status_code == 403

    saved = client.post(
        "/api/v1/profiles",
        headers={"X-MPG-CSRF": token},
        json={"provider_id": "openai", "api_key": "synthetic-secret-value"},
    )
    assert saved.status_code == 200
    assert "synthetic-secret-value" not in saved.text

    refreshed = client.get("/api/v1/bootstrap").json()
    assert refreshed["profiles"][0]["auth_ready"] is True
    assert "auth_ref" not in refreshed["profiles"][0]


def test_provider_console_health_has_exact_service_identity(tmp_path) -> None:
    service = OnboardingService(ProfileStore(tmp_path), MemorySecrets())
    client = TestClient(create_app(service=service))

    health = client.get("/healthz")

    assert health.status_code == 200
    assert health.json() == {
        "ok": True,
        "status": "control_ui_ready",
        "service": "model-provider-gateway-provider-console",
        "api_schema": "model_provider_gateway.http.v1",
        "provider_call_performed": False,
        "proxy_started": False,
    }


def test_provider_console_lifecycle_is_port_scoped_and_fail_closed() -> None:
    script = (
        Path(__file__).parents[1] / "scripts" / "provider-console.ps1"
    ).read_text(encoding="utf-8-sig")

    assert '.local\\provider-console\\$Port' in script
    assert '$response.service -eq "model-provider-gateway-provider-console"' in script
    assert '$response.api_schema -eq "model_provider_gateway.http.v1"' in script
    assert '$portPattern = "--port\\s+$Port(?:\\s|$)"' in script
    assert '"external_matching_service"' in script
    assert "Never stop a process based on port ownership alone" in script


def test_static_console_has_no_browser_secret_persistence(tmp_path) -> None:
    service = OnboardingService(ProfileStore(tmp_path), MemorySecrets())
    client = TestClient(create_app(service=service))
    page = client.get("/")
    script = client.get("/assets/app.js")
    assert page.status_code == 200
    assert "Content-Security-Policy" in page.text
    assert "localStorage" not in script.text
    assert "sessionStorage" not in script.text
    assert "capability-filter" in page.text
    assert "provider.group_label" in script.text
    assert "provider.search_terms" in script.text
    assert "data-provider-field" in script.text
