"""Versioned consumer contracts; consumers never submit Provider SDK fields."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

ADAPTER_SCHEMA_V1 = "model_provider_gateway.adapter_contract.v1"
ADAPTER_SCHEMA = "model_provider_gateway.adapter_contract.v2"
ADAPTER_REQUEST_SCHEMA_V1 = "model_provider_gateway.adapter_request.v1"
ADAPTER_REQUEST_SCHEMA = "model_provider_gateway.adapter_request.v2"


def _enabled(*capabilities: str, policy: str = "shared_consent") -> dict[str, Any]:
    return {
        "capabilities": list(capabilities),
        "execution_status": "enabled",
        "control_policy": policy,
    }


def _blocked(*capabilities: str, reason: str) -> dict[str, Any]:
    return {
        "capabilities": list(capabilities),
        "execution_status": "blocked",
        "control_policy": "none",
        "blocked_reason": reason,
    }


_CONTRACTS: dict[str, dict[str, Any]] = {
    # Intent: authorize translation through the existing exact-consent protocol.
    # Decision: text-only consumer; no inherited VKP Broker or provider privileges.
    # Reason: a document desk is an independent application.
    # Evidence: tests/test_translation_desk_adapter.py; translation-desk/CHANGE-RATIONALE.md.
    # Scope: new translation_desk / translate_text task only.
    "translation_desk": {
        "consumer": "Translation Desk",
        "tasks": {"translate_text": _enabled("text")},
    },
    "qrp": {
        "consumer": "QRP",
        "tasks": {
            "research_query": _enabled("text"),
            "claim_assist": _enabled("text"),
            "controlled_summary": _enabled("text"),
            "visual_claim_assist": _enabled("vision"),
            "embedding_candidate": _blocked(
                "embedding",
                reason="embedding execution remains owner-controlled; consume a validated owner receipt",
            ),
        },
    },
    "arw": {
        "consumer": "ARW",
        "tasks": {
            "research_query": _enabled("text"),
            "claim_assist": _enabled("text"),
            "controlled_summary": _enabled("text"),
            "visual_claim_assist": _enabled("vision"),
            "embedding_candidate": _blocked(
                "embedding",
                reason="embedding execution remains owner-controlled; consume a validated owner receipt",
            ),
        },
    },
    "content_studio": {
        "consumer": "Content Studio",
        "tasks": {
            "draft_only": _enabled("text"),
            "rewrite": _enabled("text"),
            "vision_candidate": _enabled("vision"),
        },
    },
    "mvp_video_pipeline": {
        "consumer": "MVP Video Pipeline",
        "tasks": {
            "script": _enabled("text"),
            "visual": _enabled("vision"),
            "tts": _blocked("tts", reason="no audited Wave 1 TTS execution preset"),
            "asr": _blocked("asr", reason="ASR execution remains owner-controlled in Wave 1"),
        },
    },
    "vkp": {
        "consumer": "Video Knowledge Pipeline",
        "tasks": {
            "text": _enabled("text", policy="vkp_broker"),
            "visual": _enabled("vision", policy="vkp_broker"),
            "asr": _blocked("asr", reason="VKP retains its production ASR gateway"),
            "ocr": _blocked("ocr", reason="VKP retains its production OCR gateway"),
        },
    },
    "research_content_production": {
        "consumer": "Research Content Production",
        "tasks": {
            "orchestrate": {
                "capabilities": [],
                "execution_status": "control_plane_only",
                "control_policy": "consumer_receipts_only",
            }
        },
    },
}


def adapter_contract(consumer_id: str) -> dict[str, Any]:
    if consumer_id not in _CONTRACTS:
        raise ValueError(f"unknown adapter consumer: {consumer_id}")
    return {
        "schema": ADAPTER_SCHEMA,
        "consumer_id": consumer_id,
        **deepcopy(_CONTRACTS[consumer_id]),
        "forbidden_fields": ["provider_url", "api_key", "headers", "raw_json", "fallback"],
    }


def validate_adapter_request(request: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    schema = request.get("schema")
    if schema not in {ADAPTER_REQUEST_SCHEMA_V1, ADAPTER_REQUEST_SCHEMA}:
        raise ValueError("unsupported adapter request schema")
    if request.get("consumer_id") != contract["consumer_id"]:
        raise ValueError("adapter consumer mismatch")
    task = request.get("task")
    if task not in contract["tasks"]:
        raise ValueError("adapter task is not allowed")
    task_contract = contract["tasks"][task]
    if isinstance(task_contract, list):
        required = set(task_contract)
        execution_status = "enabled"
        control_policy = "shared_consent"
    else:
        required = set(task_contract["capabilities"])
        execution_status = task_contract["execution_status"]
        control_policy = task_contract["control_policy"]
    if not required <= set(request.get("required_capabilities", [])):
        raise ValueError("adapter request capabilities are incomplete")
    if not request.get("route_id") or not request.get("route_revision"):
        raise ValueError("adapter request must bind route identity")
    forbidden = {"provider_url", "api_key", "headers", "raw_json", "fallback"}
    if forbidden & set(request):
        raise ValueError("adapter request may not submit provider URLs, keys, headers, raw JSON, or fallback")
    if schema == ADAPTER_REQUEST_SCHEMA:
        if execution_status != "enabled":
            raise ValueError(f"adapter task is not executable: {execution_status}")
        for field in ("consent_hash", "input_manifest_hash"):
            if not request.get(field):
                raise ValueError(f"adapter request must bind {field}")
        if control_policy == "vkp_broker" and not request.get("owner_gate_receipt_hash"):
            raise ValueError("VKP execution requires a Broker reservation receipt hash")
    return request


def task_contract(consumer_id: str, task: str) -> dict[str, Any]:
    contract = adapter_contract(consumer_id)
    try:
        return deepcopy(contract["tasks"][task])
    except KeyError as exc:
        raise ValueError("adapter task is not allowed") from exc
