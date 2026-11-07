"""Exact artifact consent manifests and offline validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .artifacts import build_artifact_manifest, input_manifest_hash, validate_artifact_manifest
from .canonical import sha256_file, sha256_json, without_keys
from .route import validate_route_plan

CONSENT_SCHEMA_V1 = "model_provider_gateway.consent_manifest.v1"
CONSENT_SCHEMA = "model_provider_gateway.consent_manifest.v2"


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def consent_revision(consent: dict[str, Any]) -> str:
    return sha256_json(without_keys(consent, "consent_sha256", "usage"))


def build_consent_plan(
    route: dict[str, Any],
    artifact_paths: list[str | Path],
    *,
    capability: str,
    purpose: str,
    data_type: str,
    ttl_seconds: int = 3600,
    max_calls: int | None = None,
    max_cost_usd: float | None = None,
) -> dict[str, Any]:
    validate_route_plan(route)
    if route["capability"] != capability:
        raise ValueError("consent capability does not match route")
    if ttl_seconds < 1 or ttl_seconds > 86400 * 30:
        raise ValueError("consent ttl must be between one second and 30 days")
    artifacts = build_artifact_manifest(artifact_paths, data_type=data_type, purpose=purpose)
    route_calls = int(route["limits"]["max_calls"])
    route_cost = float(route["limits"]["max_cost_usd"])
    consent_calls = max_calls if max_calls is not None else route_calls
    consent_cost = max_cost_usd if max_cost_usd is not None else route_cost
    if consent_calls < 1 or consent_calls > route_calls:
        raise ValueError("consent call limit must be positive and no greater than the route limit")
    if consent_cost < 0 or consent_cost > route_cost:
        raise ValueError("consent cost limit must be non-negative and no greater than the route limit")
    now = datetime.now(UTC)
    consent = {
        "schema": CONSENT_SCHEMA,
        "consent_id": sha256_json({"route": route["route_revision"], "artifacts": artifacts, "purpose": purpose})[:32],
        "route_id": route["route_id"],
        "route_revision": route["route_revision"],
        "capability": capability,
        "purpose": purpose,
        "artifacts": artifacts,
        "input_policy": {
            "instruction_sha256": sha256_json({"instruction": purpose}),
            "input_manifest_sha256": input_manifest_hash(purpose, artifacts),
        },
        "authorized_deployments": [
            {
                "profile_id": member["profile_id"],
                "profile_revision": member["profile_revision"],
                "provider_id": member["provider_id"],
                "destination": member["destination"],
                "model": member["model"],
            }
            for member in route["members"]
        ],
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "limits": {"max_calls": consent_calls, "max_cost_usd": consent_cost},
        "usage": {"calls_reserved": 0, "estimated_cost_reserved_usd": 0.0},
        "fallback_authorized": bool(route["fallback_policy"]["enabled"]),
    }
    consent["consent_sha256"] = consent_revision(consent)
    validate_consent(consent, route=route)
    return consent


def validate_consent(
    consent: dict[str, Any],
    *,
    route: dict[str, Any],
    now: datetime | None = None,
    verify_artifacts: bool = True,
) -> dict[str, Any]:
    validate_route_plan(route)
    schema = consent.get("schema")
    if schema not in {CONSENT_SCHEMA_V1, CONSENT_SCHEMA}:
        raise ValueError("unsupported consent schema")
    if consent.get("route_id") != route["route_id"] or consent.get("route_revision") != route["route_revision"]:
        raise ValueError("consent route drift detected")
    if consent.get("consent_sha256") != consent_revision(consent):
        raise ValueError("consent manifest drift detected")
    current = now or datetime.now(UTC)
    if _utc(consent["expires_at"]) <= current:
        raise ValueError("consent expired")
    if bool(consent.get("fallback_authorized")) != bool(route["fallback_policy"]["enabled"]):
        raise ValueError("consent fallback scope does not match route")
    expected_deployments = [
        {
            "profile_id": member["profile_id"],
            "profile_revision": member["profile_revision"],
            "provider_id": member["provider_id"],
            "destination": member["destination"],
            "model": member["model"],
        }
        for member in route["members"]
    ]
    if consent.get("authorized_deployments") != expected_deployments:
        raise ValueError("consent deployment set drift detected")
    limits = consent["limits"]
    usage = consent["usage"]
    if int(limits.get("max_calls", 0)) < 1 or int(limits["max_calls"]) > int(route["limits"]["max_calls"]):
        raise ValueError("consent call limit is outside the route limit")
    if float(limits.get("max_cost_usd", -1)) < 0 or float(limits["max_cost_usd"]) > float(
        route["limits"]["max_cost_usd"]
    ):
        raise ValueError("consent cost limit is outside the route limit")
    if usage["calls_reserved"] > limits["max_calls"]:
        raise ValueError("consent call limit exceeded")
    if usage["estimated_cost_reserved_usd"] > limits["max_cost_usd"]:
        raise ValueError("consent cost limit exceeded")
    if schema == CONSENT_SCHEMA:
        validate_artifact_manifest(
            consent.get("artifacts", []),
            capability=str(consent.get("capability") or ""),
            verify_files=verify_artifacts,
        )
        expected_policy = {
            "instruction_sha256": sha256_json({"instruction": consent.get("purpose")}),
            "input_manifest_sha256": input_manifest_hash(consent.get("purpose", ""), consent["artifacts"]),
        }
        if consent.get("input_policy") != expected_policy:
            raise ValueError("consent input policy drift detected")
    elif verify_artifacts:
        for artifact in consent.get("artifacts", []):
            path = Path(artifact["path"])
            drifted = (
                not path.is_file()
                or path.stat().st_size != artifact["bytes"]
                or sha256_file(path) != artifact["sha256"]
            )
            if drifted:
                raise ValueError("consent artifact drift detected")
    return consent
