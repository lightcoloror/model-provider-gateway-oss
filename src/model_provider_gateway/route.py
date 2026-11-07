"""Explicit route planning; there is no implicit provider fallback."""

from __future__ import annotations

from typing import Any

from .canonical import sha256_json, without_keys
from .profile import validate_profile

ROUTE_SCHEMA = "model_provider_gateway.route_plan.v1"


def route_revision(route: dict[str, Any]) -> str:
    return sha256_json(without_keys(route, "route_revision"))


def build_route_plan(
    route_id: str,
    capability: str,
    profiles: list[dict[str, Any]],
    *,
    fallback_enabled: bool = False,
    max_calls: int | None = None,
    max_cost_usd: float | None = None,
) -> dict[str, Any]:
    if not profiles:
        raise ValueError("route plan requires at least one profile")
    members = []
    for profile in profiles:
        validate_profile(profile)
        members.append(
            {
                "profile_id": profile["profile_id"],
                "profile_revision": profile["route_revision"],
                "provider_id": profile["provider_id"],
                "destination": profile["allowed_destinations"][0],
                "model": profile.get("model") or profile.get("deployment"),
                "capabilities": list(profile["capabilities"]),
            }
        )
    primary = profiles[0]
    route = {
        "schema": ROUTE_SCHEMA,
        "route_id": route_id,
        "capability": capability,
        "members": members,
        "fallback_policy": {
            "enabled": bool(fallback_enabled),
            "ordered": bool(fallback_enabled),
            "requires_consent": True,
        },
        "limits": {
            "max_calls": max_calls if max_calls is not None else primary["cost_limits"]["max_calls"],
            "max_cost_usd": max_cost_usd if max_cost_usd is not None else primary["cost_limits"]["max_cost_usd"],
        },
    }
    route["route_revision"] = route_revision(route)
    validate_route_plan(route)
    return route


def validate_route_plan(route: dict[str, Any]) -> dict[str, Any]:
    if route.get("schema") != ROUTE_SCHEMA:
        raise ValueError("unsupported route plan schema")
    members = route.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("route plan requires members")
    capability = route.get("capability")
    for member in members:
        if capability not in member.get("capabilities", []):
            raise ValueError("route member does not support requested capability")
        if not member.get("profile_revision") or not member.get("destination"):
            raise ValueError("route member identity is incomplete")
    enabled = bool(route.get("fallback_policy", {}).get("enabled"))
    if len(members) > 1 and not enabled:
        raise ValueError("multiple route members require explicit fallback_enabled")
    if enabled and not route.get("fallback_policy", {}).get("requires_consent"):
        raise ValueError("fallback must require consent")
    limits = route.get("limits", {})
    if int(limits.get("max_calls", 0)) < 1 or float(limits.get("max_cost_usd", -1)) < 0:
        raise ValueError("route limits are invalid")
    if route.get("route_revision") != route_revision(route):
        raise ValueError("route plan drift detected")
    return route
