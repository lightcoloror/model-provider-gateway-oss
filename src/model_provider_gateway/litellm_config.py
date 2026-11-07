"""Secretless LiteLLM configuration rendering."""

from __future__ import annotations

from typing import Any

from .catalog import catalog_show
from .profile import validate_profile
from .route import validate_route_plan


def _env_name(auth_ref: str) -> str:
    kind, _, name = auth_ref.partition(":")
    if kind != "env":
        raise ValueError("LiteLLM config rendering supports env: auth_ref only; inject other stores into child env")
    return name


def render_litellm_config(route: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    validate_route_plan(route)
    by_id = {profile["profile_id"]: validate_profile(profile) for profile in profiles}
    model_list = []
    for index, member in enumerate(route["members"]):
        profile = by_id.get(member["profile_id"])
        if profile is None:
            raise ValueError("route member profile is missing")
        provider = catalog_show(profile["provider_id"])
        model = profile["model"] or profile["deployment"]
        litellm_model = f"{provider['litellm_provider']}/{model}"
        params: dict[str, Any] = {
            "model": litellm_model,
            "api_base": profile["base_url"],
            "api_key": f"os.environ/{_env_name(profile['auth_ref'])}",
            "timeout": profile["timeout_seconds"],
            "max_retries": max(0, profile["retry_policy"]["max_attempts"] - 1),
        }
        params.update(profile.get("provider_options", {}))
        model_list.append(
            {
                "model_name": (
                    f"mpg-{route['route_id']}-{route['route_revision'][:12]}"
                    if index == 0
                    else f"mpg-{route['route_id']}-fallback-{index:02d}-{route['route_revision'][:12]}"
                ),
                "litellm_params": params,
                "model_info": {
                    "id": profile["route_revision"],
                    "provider_id": profile["provider_id"],
                    "capabilities": profile["capabilities"],
                },
            }
        )
    config: dict[str, Any] = {
        "model_list": model_list,
        "litellm_settings": {
            "telemetry": False,
            "set_verbose": False,
            "json_logs": True,
            "drop_params": False,
            "request_timeout": max(profile["timeout_seconds"] for profile in by_id.values()),
        },
        "general_settings": {"disable_spend_logs": False},
    }
    if route["fallback_policy"]["enabled"]:
        virtual = model_list[0]["model_name"]
        config["router_settings"] = {
            "fallbacks": [{virtual: [item["model_name"] for item in model_list[1:]]}],
            "allowed_fails": 0,
        }
    return config

