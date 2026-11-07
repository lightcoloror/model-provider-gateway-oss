from __future__ import annotations

import json

import pytest

from model_provider_gateway.execution import LiteLLMProxyTransport
from model_provider_gateway.mock_server import mock_server
from model_provider_gateway.profile import build_profile


def test_proxy_transport_uses_one_loopback_openai_compatible_service(tmp_path) -> None:
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("synthetic fixture", encoding="utf-8")
    profile = build_profile(
        "openai-gpt-5-6-luna",
        "keyring:profile/openai-default",
        profile_id="openai-default",
        last_verified="2026-08-11T00:00:00+00:00",
    )
    with mock_server() as base_url:
        transport = LiteLLMProxyTransport(f"{base_url}/v1", "synthetic-local-master-key")
        result = transport.execute(
            profile=profile,
            capability="text",
            instruction="Summarize this synthetic fixture.",
            artifacts=[
                {
                    "path": str(artifact),
                    "sha256": "0" * 64,
                    "mime_type": "text/plain",
                    "bytes": artifact.stat().st_size,
                }
            ],
            secret="synthetic-provider-key-never-sent",
        )
    assert result["content"] == "mock-ok"
    assert result["usage"]["status"] == "reported"
    assert "synthetic-provider-key-never-sent" not in json.dumps(result)


def test_proxy_transport_rejects_remote_or_unauthenticated_service() -> None:
    with pytest.raises(ValueError, match="loopback"):
        LiteLLMProxyTransport("https://example.com/v1", "synthetic-key")
    with pytest.raises(ValueError, match="master key"):
        LiteLLMProxyTransport("http://127.0.0.1:9243/v1", "")
