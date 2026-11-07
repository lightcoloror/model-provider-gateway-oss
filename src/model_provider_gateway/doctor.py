"""Offline configuration and dependency checks."""

from __future__ import annotations

import importlib.util
from typing import Any

from .audit import audit_secretless
from .capability_control import owner_adapter_registry
from .coverage import capability_policies, compatibility_summary
from .owner_execution import owner_runtime_doctor
from .profile import validate_profile
from .secrets import secret_ready


def doctor(
    profiles: list[dict[str, Any]] | None = None,
    *,
    check_secrets: bool = False,
    owner_receipt_root: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append({"name": "litellm", "ok": importlib.util.find_spec("litellm") is not None, "required": False})
    checks.append({"name": "jsonschema", "ok": importlib.util.find_spec("jsonschema") is not None, "required": True})
    for profile in profiles or []:
        try:
            validate_profile(profile)
            secret_issues = audit_secretless(profile)
            ok = not secret_issues
            detail = "profile valid and secretless" if ok else "; ".join(secret_issues)
        except ValueError as exc:
            ok = False
            detail = str(exc)
        checks.append(
            {
                "name": f"profile:{profile.get('profile_id', 'unknown')}",
                "ok": ok,
                "required": True,
                "detail": detail,
            }
        )
        if check_secrets:
            credential_ok = secret_ready(str(profile.get("auth_ref", "")))
            checks.append(
                {
                    "name": f"credential:{profile.get('profile_id', 'unknown')}",
                    "ok": credential_ok,
                    "required": True,
                    "detail": "credential ready" if credential_ok else "credential missing",
                }
            )
    required_failures = [row for row in checks if row.get("required") and not row["ok"]]
    owner_runtime = owner_runtime_doctor(receipt_root=owner_receipt_root)
    return {
        "ok": not required_failures,
        "status": "ready" if not required_failures else "blocked",
        "checks": checks,
        "execution": {
            "control_plane_capabilities": [row["capability"] for row in capability_policies()],
            "enabled_capabilities": [
                row["capability"] for row in capability_policies() if row["shared_execution"] == "enabled"
            ],
            "blocked_capabilities": [
                row["capability"] for row in capability_policies() if row["shared_execution"] == "blocked"
            ],
            "capability_policies": capability_policies(),
            "provider_execution_requires": ["profile", "route", "consent_v2", "request", "ledger", "credential"],
            "real_provider_called": False,
            "owner_adapter_registry": owner_adapter_registry(),
            "owner_runtime": owner_runtime,
        },
        "consumer_compatibility": compatibility_summary(),
    }
