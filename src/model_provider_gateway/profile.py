"""Provider-profile construction and fail-closed validation."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .canonical import sha256_json, without_keys
from .catalog import find_model_preset, resolve_provider_fields

PROFILE_SCHEMA = "model_provider_gateway.provider_profile.v1"
CAPABILITIES = {"text", "vision", "asr", "tts", "embedding", "ocr"}
AUTH_REF = re.compile(r"^(env|keyring|dpapi):[A-Za-z0-9_.:/-]+$")


def _revision_payload(profile: dict[str, Any]) -> dict[str, Any]:
    return without_keys(profile, "route_revision", "last_verified")


def profile_revision(profile: dict[str, Any]) -> str:
    return sha256_json(_revision_payload(profile))


def build_profile(
    preset_id: str,
    auth_ref: str,
    *,
    profile_id: str | None = None,
    capabilities: list[str] | None = None,
    timeout_seconds: int = 120,
    max_attempts: int = 1,
    max_calls: int = 1,
    max_cost_usd: float = 0.0,
    last_verified: str | None = None,
    provider_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_id, _provider, model = find_model_preset(preset_id)
    resolved_provider = resolve_provider_fields(provider_id, provider_fields)
    chosen_capabilities = list(capabilities or model["capabilities"])
    result: dict[str, Any] = {
        "schema": PROFILE_SCHEMA,
        "profile_id": profile_id or preset_id,
        "provider_id": provider_id,
        "protocol": model["protocol"],
        "base_url": resolved_provider["base_url"],
        "auth_ref": auth_ref,
        "model": model["model"],
        "deployment": None,
        "capabilities": chosen_capabilities,
        "allowed_destinations": [resolved_provider["destination"]],
        "provider_fields": resolved_provider["provider_fields"],
        "timeout_seconds": timeout_seconds,
        "retry_policy": {
            "max_attempts": max_attempts,
            "backoff_seconds": 0.0,
            "retry_statuses": [429, 500, 502, 503, 504] if max_attempts > 1 else [],
        },
        "cost_limits": {"max_calls": max_calls, "max_cost_usd": max_cost_usd},
        "fallback_pool": [],
        "provider_options": deepcopy(model["provider_options"]),
        "provenance": {
            "preset_id": preset_id,
            "preset_evidence": model["evidence"],
            "catalog_schema": "model_provider_gateway.catalog.v1",
        },
        "last_verified": last_verified or datetime.now(UTC).isoformat(),
        "advanced": False,
    }
    result["route_revision"] = profile_revision(result)
    validate_profile(result)
    return result


def validate_profile(profile: dict[str, Any], *, allow_advanced: bool = False) -> dict[str, Any]:
    required = {
        "schema",
        "profile_id",
        "provider_id",
        "protocol",
        "base_url",
        "auth_ref",
        "model",
        "deployment",
        "capabilities",
        "allowed_destinations",
        "timeout_seconds",
        "retry_policy",
        "cost_limits",
        "fallback_pool",
        "provenance",
        "last_verified",
        "route_revision",
    }
    missing = sorted(required - set(profile))
    if missing:
        raise ValueError(f"provider profile missing fields: {', '.join(missing)}")
    if profile["schema"] != PROFILE_SCHEMA:
        raise ValueError("unsupported provider profile schema")
    if profile.get("advanced") and not allow_advanced:
        raise ValueError("advanced provider profiles are disabled")
    auth_ref = str(profile["auth_ref"])
    if not AUTH_REF.fullmatch(auth_ref):
        raise ValueError("auth_ref must use env:, keyring:, or dpapi: without a secret value")
    parsed = urlparse(str(profile["base_url"]))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("base_url must be a credential-free HTTPS URL")
    preset_id = str(profile.get("provenance", {}).get("preset_id") or "")
    provider_id, _provider, model = find_model_preset(preset_id)
    resolved_provider = resolve_provider_fields(provider_id, profile.get("provider_fields"))
    expected = {
        "provider_id": provider_id,
        "base_url": resolved_provider["base_url"],
        "protocol": model["protocol"],
        "model": model["model"],
    }
    for key, value in expected.items():
        if profile.get(key) != value:
            raise ValueError(f"profile {key} drifted from reviewed preset")
    capabilities = set(profile["capabilities"])
    if not capabilities or not capabilities <= CAPABILITIES:
        raise ValueError("profile capabilities are empty or unsupported")
    if not capabilities <= set(model["capabilities"]):
        raise ValueError("model preset does not support requested capabilities")
    destinations = profile["allowed_destinations"]
    if destinations != [resolved_provider["destination"]]:
        raise ValueError("allowed_destinations must exactly match the reviewed preset")
    retry = profile["retry_policy"]
    attempts = int(retry.get("max_attempts", 0))
    if attempts < 1 or attempts > 5:
        raise ValueError("retry max_attempts must be between 1 and 5")
    limits = profile["cost_limits"]
    if int(limits.get("max_calls", 0)) < 1 or float(limits.get("max_cost_usd", -1)) < 0:
        raise ValueError("cost limits must be non-negative and allow at least one call")
    if profile["fallback_pool"]:
        raise ValueError("fallback_pool belongs in an explicit route plan, not a provider profile")
    expected_revision = profile_revision(profile)
    if profile["route_revision"] != expected_revision:
        raise ValueError("route_revision does not match provider profile content")
    return profile
