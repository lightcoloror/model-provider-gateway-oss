"""Secret and unsafe configuration audit helpers."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|password|authorization|cookie)", re.IGNORECASE)
_CREDENTIAL_URL = re.compile(r"https://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_SAFE_TELEMETRY_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens"}


def audit_secretless(value: Any, *, path: str = "root") -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if _SECRET_KEY.search(str(key)) and key not in {
                "auth_ref",
                "api_key",
                "secret_logged",
                *_SAFE_TELEMETRY_KEYS,
            }:
                issues.append(f"secret-like key is forbidden: {child}")
            if key == "api_key" and not (isinstance(item, str) and item.startswith("os.environ/")):
                issues.append(f"api_key must be an environment reference: {child}")
            if key == "secret_logged" and item is not False:
                issues.append(f"secret logging assertion must be false: {child}")
            if key in _SAFE_TELEMETRY_KEYS and item is not None and not isinstance(item, int):
                issues.append(f"token telemetry must be numeric or null: {child}")
            issues.extend(audit_secretless(item, path=child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(audit_secretless(item, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        if _CREDENTIAL_URL.search(value):
            issues.append(f"credential-bearing URL is forbidden: {path}")
        if value.startswith(("sk-", "ms-", "AIza")):
            issues.append(f"secret-looking value is forbidden: {path}")
    return issues


def assert_secretless(value: Any) -> None:
    issues = audit_secretless(value)
    if issues:
        raise ValueError("; ".join(issues))
