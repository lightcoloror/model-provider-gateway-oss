"""Key-only provider onboarding backed by reviewed catalog presets."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import catalog_list, catalog_show, find_model_preset
from .profile import build_profile, validate_profile
from .secrets import KeyringSecretStore, SecretStore

ONBOARDING_SCHEMA = "model_provider_gateway.onboarding.v1"
PROFILE_STORE_SCHEMA = "model_provider_gateway.profile_store.v1"
PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def default_state_dir() -> Path:
    override = os.environ.get("MPG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "model-provider-gateway"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "model-provider-gateway"


def onboarding_catalog() -> dict[str, Any]:
    providers = []
    for summary in catalog_list():
        provider = catalog_show(summary["provider_id"])
        models = []
        for index, model in enumerate(provider["models"]):
            models.append(
                {
                    "preset_id": model["preset_id"],
                    "model": model["model"],
                    "capabilities": model["capabilities"],
                    "protocol": model["protocol"],
                    "recommended": index == 0,
                    "review_status": model["review_status"],
                }
            )
        providers.append(
            {
                "provider_id": summary["provider_id"],
                "label": provider["label"],
                "base_url": provider["base_url"],
                "destination": provider["destination"],
                "auth_env": provider["auth_env"],
                "protocols": provider["protocols"],
                "models": models,
                "recommended_preset_id": models[0]["preset_id"] if models else None,
                "setup_mode": provider["setup_mode"],
                "onboarding_status": provider["onboarding_status"],
                "required_fields": provider["required_fields"],
                "optional_fields": provider["optional_fields"],
                "advanced_fields_locked": provider["advanced_fields_locked"],
                "group_id": provider["group_id"],
                "group_label": provider["group_label"],
                "search_terms": provider["search_terms"],
                "capability_status": provider["capability_status"],
                "evidence_sources": provider["evidence_sources"],
                "note": provider.get("note", ""),
            }
        )
    return {
        "schema": ONBOARDING_SCHEMA,
        "api_schema": "model_provider_gateway.http.v1",
        "providers": providers,
        "safety": {
            "secret_storage": "system_keyring",
            "profile_contains_secret": False,
            "saving_authorizes_provider_call": False,
            "arbitrary_base_url_allowed": False,
            "silent_fallback_allowed": False,
        },
    }


def redact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    validate_profile(profile)
    return {
        "profile_id": profile["profile_id"],
        "provider_id": profile["provider_id"],
        "model": profile["model"],
        "capabilities": profile["capabilities"],
        "base_url": profile["base_url"],
        "auth_ready": True,
        "auth_storage": profile["auth_ref"].partition(":")[0],
        "route_revision": profile["route_revision"],
        "last_verified": profile["last_verified"],
        "provider_fields": dict(profile.get("provider_fields") or {}),
    }


@dataclass
class ProfileStore:
    state_dir: Path | None = None

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir or default_state_dir())
        self.path = self.state_dir / "profiles.json"

    def _document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": PROFILE_STORE_SCHEMA, "profiles": []}
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if document.get("schema") != PROFILE_STORE_SCHEMA or not isinstance(document.get("profiles"), list):
            raise ValueError("profile store schema is invalid")
        for profile in document["profiles"]:
            validate_profile(profile)
        return document

    def list_profiles(self) -> list[dict[str, Any]]:
        return list(self._document()["profiles"])

    def save(self, profile: dict[str, Any]) -> dict[str, Any]:
        validate_profile(profile)
        document = self._document()
        profiles = [row for row in document["profiles"] if row["profile_id"] != profile["profile_id"]]
        profiles.append(profile)
        document["profiles"] = sorted(profiles, key=lambda row: row["profile_id"])
        document["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="profiles-", suffix=".json", dir=self.state_dir)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return profile


@dataclass
class OnboardingService:
    profile_store: ProfileStore
    secret_store: SecretStore

    @classmethod
    def system_default(cls, state_dir: Path | None = None) -> OnboardingService:
        return cls(ProfileStore(state_dir), KeyringSecretStore())

    def onboard(
        self,
        *,
        provider_id: str,
        api_key: str,
        preset_id: str | None = None,
        profile_id: str | None = None,
        provider_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not api_key or not api_key.strip():
            raise ValueError("api_key is required")
        provider = catalog_show(provider_id)
        if provider["onboarding_status"] != "ready":
            raise ValueError(f"provider onboarding is not executable: {provider['onboarding_status']}")
        if not provider["models"]:
            raise ValueError("provider has no reviewed executable model preset")
        selected_preset = preset_id or provider["models"][0]["preset_id"]
        preset_provider_id, _, _ = find_model_preset(selected_preset)
        if preset_provider_id != provider_id:
            raise ValueError("preset_id does not belong to provider_id")
        selected_profile_id = profile_id or f"{provider_id}-default"
        if not PROFILE_ID.fullmatch(selected_profile_id):
            raise ValueError("profile_id must be 1-64 letters, digits, dots, underscores, or dashes")
        secret_name = f"profile/{selected_profile_id}"
        profile = build_profile(
            selected_preset,
            f"keyring:{secret_name}",
            profile_id=selected_profile_id,
            last_verified=datetime.now(UTC).replace(microsecond=0).isoformat(),
            provider_fields=provider_fields,
        )
        self.secret_store.set(secret_name, api_key)
        self.profile_store.save(profile)
        return {
            "schema": ONBOARDING_SCHEMA,
            "status": "saved_not_authorized_for_provider_call",
            "profile": redact_profile(profile),
            "secret_stored": True,
            "secret_echoed": False,
        }
