"""One LiteLLM proxy runtime generated from secretless reviewed profiles."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .catalog import catalog_show
from .onboarding import ProfileStore, default_state_dir
from .profile import validate_profile
from .secrets import KeyringSecretStore, SecretStore, resolve_secret_ref

HTTP_API_SCHEMA = "model_provider_gateway.http.v1"
SERVICE_PROFILE_SCHEMA = "model_provider_gateway.service_profile.v1"
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 9242
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 9243
PROXY_MASTER_KEY_NAME = "local-proxy/master-key"


def _require_loopback(host: str) -> None:
    parsed = urlparse(f"http://{host}")
    if parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("the local gateway must bind to a loopback host")


def _credential_env(auth_ref: str) -> str:
    digest = hashlib.sha256(auth_ref.encode("utf-8")).hexdigest()[:16].upper()
    return f"MPG_CREDENTIAL_{digest}"


def model_alias(profile: dict[str, Any]) -> str:
    validate_profile(profile)
    return f"mpg-{profile['profile_id']}"


def render_service_bundle(
    profiles: list[dict[str, Any]],
    *,
    host: str = DEFAULT_PROXY_HOST,
    port: int = DEFAULT_PROXY_PORT,
) -> dict[str, Any]:
    _require_loopback(host)
    if not profiles:
        raise ValueError("at least one saved provider profile is required")
    model_list = []
    bindings = []
    for profile in profiles:
        validate_profile(profile)
        provider = catalog_show(profile["provider_id"])
        env_name = _credential_env(profile["auth_ref"])
        model_list.append(
            {
                "model_name": model_alias(profile),
                "litellm_params": {
                    "model": f"{provider['litellm_provider']}/{profile['model'] or profile['deployment']}",
                    "api_base": profile["base_url"],
                    "api_key": f"os.environ/{env_name}",
                    "timeout": profile["timeout_seconds"],
                    "max_retries": 0,
                    **dict(profile.get("provider_options") or {}),
                },
                "model_info": {
                    "id": profile["route_revision"],
                    "provider_id": profile["provider_id"],
                    "capabilities": profile["capabilities"],
                },
            }
        )
        bindings.append({"env_name": env_name, "auth_ref": profile["auth_ref"], "profile_id": profile["profile_id"]})
    config = {
        "model_list": model_list,
        "litellm_settings": {
            "telemetry": False,
            "set_verbose": False,
            "json_logs": True,
            "drop_params": False,
            "request_timeout": max(int(profile["timeout_seconds"]) for profile in profiles),
        },
        "general_settings": {
            "master_key": "os.environ/MPG_PROXY_MASTER_KEY",
            "disable_spend_logs": False,
        },
    }
    return {
        "schema": SERVICE_PROFILE_SCHEMA,
        "api_schema": HTTP_API_SCHEMA,
        "host": host,
        "port": port,
        "base_url": f"http://{host}:{port}/v1",
        "health_url": f"http://{host}:{port}/health/liveliness",
        "models": [item["model_name"] for item in model_list],
        "config": config,
        "secret_bindings": bindings,
        "safety": {
            "loopback_only": True,
            "config_contains_secret": False,
            "provider_retry_count": 0,
            "silent_fallback": False,
            "consumer_policy_required": True,
        },
    }


def write_secretless_config(bundle: dict[str, Any], state_dir: Path | None = None) -> Path:
    directory = Path(state_dir or default_state_dir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "litellm-config.json"
    fd, temporary_name = tempfile.mkstemp(prefix="litellm-config-", suffix=".json", dir=directory)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(bundle["config"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def proxy_plan(
    profile_store: ProfileStore,
    *,
    host: str = DEFAULT_PROXY_HOST,
    port: int = DEFAULT_PROXY_PORT,
) -> dict[str, Any]:
    bundle = render_service_bundle(profile_store.list_profiles(), host=host, port=port)
    config_path = write_secretless_config(bundle, profile_store.state_dir)
    executable = shutil.which("litellm")
    return {
        **{key: value for key, value in bundle.items() if key not in {"config", "secret_bindings"}},
        "status": "ready_to_start" if executable else "blocked_dependency_missing",
        "command": [executable or "litellm", "--config", str(config_path), "--host", host, "--port", str(port)],
        "config_path": str(config_path),
        "credential_count": len(bundle["secret_bindings"]),
        "credentials_ready": all(bool(resolve_secret_ref(item["auth_ref"])) for item in bundle["secret_bindings"]),
    }


def run_proxy(
    profile_store: ProfileStore,
    *,
    host: str = DEFAULT_PROXY_HOST,
    port: int = DEFAULT_PROXY_PORT,
    secret_store: SecretStore | None = None,
) -> int:
    bundle = render_service_bundle(profile_store.list_profiles(), host=host, port=port)
    config_path = write_secretless_config(bundle, profile_store.state_dir)
    executable = shutil.which("litellm")
    if not executable:
        raise RuntimeError("LiteLLM proxy executable is not installed")
    store = secret_store or KeyringSecretStore()
    master_key = store.get(PROXY_MASTER_KEY_NAME)
    if not master_key:
        master_key = secrets.token_urlsafe(32)
        store.set(PROXY_MASTER_KEY_NAME, master_key)
    environment = os.environ.copy()
    environment["MPG_PROXY_MASTER_KEY"] = master_key
    for binding in bundle["secret_bindings"]:
        secret = resolve_secret_ref(binding["auth_ref"])
        if not secret:
            raise ValueError(f"credential reference is not ready for profile {binding['profile_id']}")
        environment[binding["env_name"]] = secret
    command = [executable, "--config", str(config_path), "--host", host, "--port", str(port)]
    completed = subprocess.run(command, env=environment, check=False)
    return int(completed.returncode)
