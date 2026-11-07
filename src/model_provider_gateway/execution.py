"""Canonical, fail-closed LiteLLM execution contract."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import jsonschema

from .adapters import task_contract
from .artifacts import input_manifest_hash, public_artifact_refs, validate_artifact_manifest
from .audit import assert_secretless
from .catalog import catalog_show
from .consent import CONSENT_SCHEMA, validate_consent
from .ledger import ReservationError, finalize_execution, reserve_execution
from .profile import validate_profile
from .route import validate_route_plan
from .secrets import resolve_secret_ref

EXECUTION_REQUEST_SCHEMA = "model_provider_gateway.execution_request.v1"
EXECUTION_RESULT_SCHEMA = "model_provider_gateway.execution_result.v1"
EXECUTABLE_CAPABILITIES = {"text", "vision"}
MAX_TEXT_INPUT_BYTES = 10 * 1024 * 1024
MAX_VISION_INPUT_BYTES = 20 * 1024 * 1024


class ExecutionTransport(Protocol):
    mode: str

    def execute(
        self,
        *,
        profile: dict[str, Any],
        capability: str,
        instruction: str,
        artifacts: list[dict[str, Any]],
        secret: str,
    ) -> dict[str, Any]: ...


def _schema(name: str) -> dict[str, Any]:
    package = resources.files("model_provider_gateway.schemas")
    return json.loads(package.joinpath(name).read_text(encoding="utf-8"))


def _member_for_profile(route: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    matches = [
        member
        for member in route["members"]
        if member["profile_id"] == profile["profile_id"]
        and member["profile_revision"] == profile["route_revision"]
        and member["provider_id"] == profile["provider_id"]
        and member["model"] == (profile.get("model") or profile.get("deployment"))
    ]
    if len(matches) != 1:
        raise ValueError("selected profile identity does not match exactly one route member")
    return matches[0]


def build_execution_request(
    profile: dict[str, Any],
    route: dict[str, Any],
    consent: dict[str, Any],
    *,
    consumer_id: str,
    task: str,
    max_estimated_cost_usd: float,
    request_id: str | None = None,
    owner_gate_receipt_hash: str | None = None,
) -> dict[str, Any]:
    validate_profile(profile)
    validate_route_plan(route)
    validate_consent(consent, route=route)
    if consent.get("schema") != CONSENT_SCHEMA:
        raise ValueError("provider execution requires consent_manifest.v2")
    selected_task = task_contract(consumer_id, task)
    if selected_task["execution_status"] != "enabled":
        raise ValueError(f"adapter task is not executable: {selected_task['execution_status']}")
    if route["capability"] not in selected_task["capabilities"]:
        raise ValueError("adapter task capability does not match the route")
    if selected_task["control_policy"] == "vkp_broker":
        if not owner_gate_receipt_hash or len(owner_gate_receipt_hash) != 64:
            raise ValueError("VKP execution requires a Broker reservation receipt hash")
    elif owner_gate_receipt_hash is not None:
        raise ValueError("owner gate receipt is accepted only for an owner-gated task")
    _member_for_profile(route, profile)
    request = {
        "schema": EXECUTION_REQUEST_SCHEMA,
        "request_id": request_id or str(uuid.uuid4()),
        "consumer_id": consumer_id,
        "task": task,
        "capability": route["capability"],
        "route_id": route["route_id"],
        "route_revision": route["route_revision"],
        "profile_id": profile["profile_id"],
        "profile_revision": profile["route_revision"],
        "provider_id": profile["provider_id"],
        "model": profile.get("model") or profile.get("deployment"),
        "consent_hash": consent["consent_sha256"],
        "instruction": consent["purpose"],
        "artifacts": consent["artifacts"],
        "input_manifest_hash": input_manifest_hash(consent["purpose"], consent["artifacts"]),
        "max_estimated_cost_usd": max_estimated_cost_usd,
        "owner_gate_receipt_hash": owner_gate_receipt_hash,
    }
    validate_execution_request(request, profile=profile, route=route, consent=consent)
    return request


def validate_execution_request(
    request: dict[str, Any],
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    consent: dict[str, Any],
    verify_files: bool = True,
) -> dict[str, Any]:
    jsonschema.validate(request, _schema("execution-request.v1.schema.json"))
    validate_profile(profile)
    validate_route_plan(route)
    validate_consent(consent, route=route, verify_artifacts=verify_files)
    if consent.get("schema") != CONSENT_SCHEMA:
        raise ValueError("provider execution requires consent_manifest.v2")
    _member_for_profile(route, profile)
    capability = str(request["capability"])
    if capability not in EXECUTABLE_CAPABILITIES:
        raise ValueError(f"execution capability is blocked: {capability}")
    expected = {
        "route_id": route["route_id"],
        "route_revision": route["route_revision"],
        "profile_id": profile["profile_id"],
        "profile_revision": profile["route_revision"],
        "provider_id": profile["provider_id"],
        "model": profile.get("model") or profile.get("deployment"),
        "consent_hash": consent["consent_sha256"],
        "capability": route["capability"],
        "instruction": consent["purpose"],
        "artifacts": consent["artifacts"],
        "input_manifest_hash": consent["input_policy"]["input_manifest_sha256"],
    }
    for key, value in expected.items():
        if request.get(key) != value:
            raise ValueError(f"execution request {key} drift detected")
    if capability not in profile["capabilities"]:
        raise ValueError("selected profile does not support the execution capability")
    validate_artifact_manifest(request["artifacts"], capability=capability, verify_files=verify_files)
    total_bytes = sum(int(item["bytes"]) for item in request["artifacts"])
    byte_limit = MAX_TEXT_INPUT_BYTES if capability == "text" else MAX_VISION_INPUT_BYTES
    if total_bytes > byte_limit:
        raise ValueError(f"{capability} input exceeds the canonical byte limit")
    estimate = float(request["max_estimated_cost_usd"])
    if estimate <= 0:
        raise ValueError("execution requires a positive per-request cost ceiling")
    if float(route["limits"]["max_cost_usd"]) <= 0 or float(consent["limits"]["max_cost_usd"]) <= 0:
        raise ValueError("route and consent require a positive cost ceiling")
    if estimate > float(consent["limits"]["max_cost_usd"]):
        raise ValueError("request cost ceiling exceeds consent")
    if int(profile["timeout_seconds"]) < 1:
        raise ValueError("execution requires a positive timeout")
    selected_task = task_contract(str(request["consumer_id"]), str(request["task"]))
    if selected_task["execution_status"] != "enabled":
        raise ValueError(f"adapter task is not executable: {selected_task['execution_status']}")
    if capability not in selected_task["capabilities"]:
        raise ValueError("adapter task capability does not match the route")
    owner_gate = request.get("owner_gate_receipt_hash")
    if selected_task["control_policy"] == "vkp_broker":
        if not isinstance(owner_gate, str) or len(owner_gate) != 64:
            raise ValueError("VKP execution requires a Broker reservation receipt hash")
    elif owner_gate is not None:
        raise ValueError("owner gate receipt is accepted only for an owner-gated task")
    return request


def _usage(value: Any) -> dict[str, Any]:
    if value is None:
        return {"status": "unknown", "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {"status": "unknown", "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {
        "status": "reported",
        "prompt_tokens": value.get("prompt_tokens"),
        "completion_tokens": value.get("completion_tokens"),
        "total_tokens": value.get("total_tokens"),
    }


@dataclass
class LiteLLMTransport:
    """LiteLLM is the only provider protocol adapter used by the executor."""

    _api_base_override: str | None = None
    _synthetic_key: str | None = None
    mode: str = "provider"

    @classmethod
    def loopback_stub(cls, base_url: str) -> LiteLLMTransport:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("stub transport must use a loopback HTTP endpoint")
        return cls(
            _api_base_override=f"{base_url.rstrip('/')}/v1",
            _synthetic_key="synthetic-loopback-only",
            mode="loopback_stub",
        )

    def execute(
        self,
        *,
        profile: dict[str, Any],
        capability: str,
        instruction: str,
        artifacts: list[dict[str, Any]],
        secret: str,
    ) -> dict[str, Any]:
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        try:
            import litellm
        except ImportError as exc:
            raise RuntimeError("LiteLLM execution support is not installed") from exc
        litellm.telemetry = False
        litellm.suppress_debug_info = True
        provider = catalog_show(profile["provider_id"])
        model = f"{provider['litellm_provider']}/{profile['model'] or profile['deployment']}"
        api_base = profile["base_url"]
        api_key = secret
        if self._api_base_override:
            model = "openai/mock-model"
            api_base = self._api_base_override
            api_key = self._synthetic_key or "synthetic-loopback-only"
        messages = _messages(capability, instruction, artifacts)
        response = litellm.completion(
            model=model,
            api_base=api_base,
            api_key=api_key,
            messages=messages,
            timeout=profile["timeout_seconds"],
            max_retries=0,
            stream=False,
            **dict(profile.get("provider_options") or {}),
        )
        content = response.choices[0].message.content
        hidden = getattr(response, "_hidden_params", {}) or {}
        response_cost = hidden.get("response_cost")
        return {
            "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            "finish_reason": getattr(response.choices[0], "finish_reason", None),
            "usage": _usage(getattr(response, "usage", None)),
            "estimated_cost_usd": float(response_cost) if response_cost is not None else None,
        }


class ProxyHttpError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__("local LiteLLM proxy rejected the request")
        self.status_code = status_code


@dataclass
class LiteLLMProxyTransport:
    """Thin HTTP transport for the single loopback LiteLLM service."""

    base_url: str
    master_key: str = field(repr=False)
    mode: str = "litellm_proxy_http"

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("LiteLLM proxy transport requires a loopback HTTP base URL")
        if not self.master_key:
            raise ValueError("LiteLLM proxy master key is required")
        self.base_url = self.base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]

    def execute(
        self,
        *,
        profile: dict[str, Any],
        capability: str,
        instruction: str,
        artifacts: list[dict[str, Any]],
        secret: str,
    ) -> dict[str, Any]:
        from .runtime import model_alias

        _ = secret
        payload = {
            "model": model_alias(profile),
            "messages": _messages(capability, instruction, artifacts),
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.master_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(profile["timeout_seconds"])) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ProxyHttpError(exc.code) from None
        choice = value["choices"][0]["message"]["content"]
        return {
            "content": choice if isinstance(choice, str) else json.dumps(choice, ensure_ascii=False),
            "finish_reason": value["choices"][0].get("finish_reason"),
            "usage": _usage(value.get("usage")),
            "estimated_cost_usd": None,
        }


def _messages(capability: str, instruction: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if capability == "text":
        sections = [instruction]
        for item in artifacts:
            content = Path(item["path"]).read_text(encoding="utf-8-sig")
            sections.append(f'<artifact sha256="{item["sha256"]}">\n{content}\n</artifact>')
        return [{"role": "user", "content": "\n\n".join(sections)}]
    content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    for item in artifacts:
        encoded = base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{item['mime_type']};base64,{encoded}"}})
    return [{"role": "user", "content": content}]


def _error(exc: Exception) -> tuple[str, bool, str]:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    name = exc.__class__.__name__.lower()
    if "timeout" in name or status == 408:
        return "timeout", True, "provider request timed out"
    if status == 429:
        return "rate_limited", True, "provider rate limit"
    if isinstance(status, int) and status >= 500:
        return "provider_server_error", True, "provider server error"
    if isinstance(status, int) and status >= 400:
        return "provider_client_error", False, "provider rejected the request"
    if "timeout" in name:
        return "timeout", True, "provider request timed out"
    if isinstance(exc, RuntimeError) and "LiteLLM" in str(exc):
        return "dependency_unavailable", False, "LiteLLM execution support is unavailable"
    return "transport_error", False, "provider transport failed"


def _envelope(
    request: dict[str, Any],
    *,
    ok: bool,
    status: str,
    latency_ms: int,
    content: str | None,
    usage: dict[str, Any] | None,
    actual_cost_usd: float | None,
    error_class: str | None,
    error_message: str | None,
    retryable: bool,
    transport_mode: str,
) -> dict[str, Any]:
    result = {
        "schema": EXECUTION_RESULT_SCHEMA,
        "ok": ok,
        "status": status,
        "request_id": request.get("request_id"),
        "provider_id": request.get("provider_id"),
        "model": request.get("model"),
        "capability": request.get("capability"),
        "route_hash": request.get("route_revision"),
        "consent_hash": request.get("consent_hash"),
        "input_manifest_hash": request.get("input_manifest_hash"),
        "usage": usage or {"status": "unknown", "prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        "cost": {
            "status": "reported" if actual_cost_usd is not None else "unknown",
            "estimated_usd": actual_cost_usd,
            "reserved_ceiling_usd": request.get("max_estimated_cost_usd"),
        },
        "latency_ms": latency_ms,
        "error_class": error_class,
        "error_message": error_message,
        "retryable": retryable,
        "artifact_refs": public_artifact_refs(request.get("artifacts") or []),
        "output": {"text": content} if content is not None else None,
        "redacted_audit": {
            "consumer_id": request.get("consumer_id"),
            "task": request.get("task"),
            "profile_id": request.get("profile_id"),
            "transport": transport_mode,
            "input_paths_logged": False,
            "input_content_logged": False,
            "secret_logged": False,
            "automatic_fallback_used": False,
        },
    }
    jsonschema.validate(result, _schema("execution-result.v1.schema.json"))
    assert_secretless(result)
    return result


def execute_model_request(
    request: dict[str, Any],
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    consent: dict[str, Any],
    ledger_path: str | Path,
    transport: ExecutionTransport | None = None,
    secret_resolver: Callable[[str], str | None] = resolve_secret_ref,
) -> dict[str, Any]:
    selected_transport = transport or LiteLLMTransport()
    try:
        validate_execution_request(request, profile=profile, route=route, consent=consent)
        secret = secret_resolver(profile["auth_ref"])
        if not secret:
            raise ValueError("credential reference is not ready")
        reservation = reserve_execution(
            ledger_path,
            request_id=request["request_id"],
            consent_hash=consent["consent_sha256"],
            max_calls=int(consent["limits"]["max_calls"]) - int(consent["usage"]["calls_reserved"]),
            max_cost_usd=float(consent["limits"]["max_cost_usd"])
            - float(consent["usage"]["estimated_cost_reserved_usd"]),
            estimated_cost_usd=float(request["max_estimated_cost_usd"]),
        )
    except (jsonschema.ValidationError, ReservationError, RuntimeError, ValueError) as exc:
        error_class = "request_replay" if "replay" in str(exc) else "blocked_validation"
        safe_message = (
            "execution request replay is forbidden"
            if error_class == "request_replay"
            else "execution request or authorization validation failed"
        )
        return _envelope(
            request,
            ok=False,
            status="blocked",
            latency_ms=0,
            content=None,
            usage=None,
            actual_cost_usd=None,
            error_class=error_class,
            error_message=safe_message,
            retryable=False,
            transport_mode=selected_transport.mode,
        )
    started = time.perf_counter()
    try:
        response = selected_transport.execute(
            profile=profile,
            capability=request["capability"],
            instruction=request["instruction"],
            artifacts=request["artifacts"],
            secret=secret,
        )
        if request["consumer_id"] == "translation_desk":
            if response.get("finish_reason") not in {"stop", "eos", "end_turn"}:
                raise ValueError("translation output was truncated or unfinished")
            if not isinstance(response.get("content"), str) or not response["content"].strip():
                raise ValueError("translation output was empty")
        content = str(response["content"]).replace(secret, "[REDACTED]")
        actual_cost = response.get("estimated_cost_usd")
        cost_exceeded = actual_cost is not None and float(actual_cost) > float(request["max_estimated_cost_usd"])
        result = _envelope(
            request,
            ok=not cost_exceeded,
            status="failed" if cost_exceeded else "completed",
            latency_ms=round((time.perf_counter() - started) * 1000),
            content=None if cost_exceeded else content,
            usage=response.get("usage"),
            actual_cost_usd=actual_cost,
            error_class="cost_limit_exceeded" if cost_exceeded else None,
            error_message="reported provider cost exceeded the reserved ceiling" if cost_exceeded else None,
            retryable=False,
            transport_mode=selected_transport.mode,
        )
    except Exception as exc:  # Provider SDK exceptions vary; all messages are normalized and redacted.
        error_class, retryable, message = _error(exc)
        result = _envelope(
            request,
            ok=False,
            status="failed",
            latency_ms=round((time.perf_counter() - started) * 1000),
            content=None,
            usage=None,
            actual_cost_usd=None,
            error_class=error_class,
            error_message=message,
            retryable=retryable,
            transport_mode=selected_transport.mode,
        )
    finalize_execution(
        ledger_path,
        request_id=request["request_id"],
        status=result["status"],
        result=result,
    )
    _ = reservation
    return result
