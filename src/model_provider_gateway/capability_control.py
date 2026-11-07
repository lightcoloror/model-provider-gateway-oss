"""Shared control-plane contracts for local ASR, OCR, TTS, and embedding owners."""

from __future__ import annotations

import json
import os
import re
import subprocess
from copy import deepcopy
from importlib import metadata, resources
from typing import Any

import jsonschema
from packaging.requirements import Requirement

from .audit import assert_secretless
from .canonical import sha256_json, without_keys

OWNER_PLAN_SCHEMA = "model_provider_gateway.owner_capability_plan.v1"
OWNER_RECEIPT_SCHEMA = "model_provider_gateway.owner_capability_receipt.v1"
OWNER_REGISTRY_SCHEMA = "model_provider_gateway.owner_capability_registry.v1"
OWNER_CAPABILITIES = ("asr", "ocr", "tts", "embedding")
VKP_REVIEWED_SOURCE_COMMIT = "f5d4b3177dc4a613626a50bb41d2eb1aee4aaa50"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


_OWNER_ADAPTERS: dict[str, dict[str, Any]] = {
    "vkp-asr": {
        "capability": "asr",
        "owner": "Video Knowledge Pipeline",
        "repository": "lightcoloror/video-knowledge-pipeline",
        "repository_commit": VKP_REVIEWED_SOURCE_COMMIT,
        "source_provenance_status": "commit_and_evidence_paths_verified",
        "module": "video_knowledge_pipeline.asr_runner",
        "runtime_status": "verified_local_owner",
        "native_gate_required": True,
        "output_kind": "transcript_manifest",
        "evidence": ["src/video_knowledge_pipeline/asr_runner.py", "src/video_knowledge_pipeline/asr_adapter.py"],
    },
    "vkp-ocr": {
        "capability": "ocr",
        "owner": "Video Knowledge Pipeline",
        "repository": "lightcoloror/video-knowledge-pipeline",
        "repository_commit": VKP_REVIEWED_SOURCE_COMMIT,
        "source_provenance_status": "commit_and_evidence_paths_verified",
        "module": "video_knowledge_pipeline.mcp_server",
        "runtime_status": "verified_local_owner",
        "native_gate_required": True,
        "output_kind": "document_text_manifest",
        "evidence": ["src/video_knowledge_pipeline/mcp_server.py"],
    },
    "ebook-document-ocr": {
        "capability": "ocr",
        "owner": "ebook_markdown_pipeline",
        "repository": "lightcoloror/ebook_markdown_pipeline",
        "repository_commit": "8801411d5117eb1286f9d3984e2a48ff98c2776e",
        "source_provenance_status": "commit_and_evidence_paths_verified",
        "module": "ocr_providers",
        "runtime_status": "verified_local_owner",
        "native_gate_required": True,
        "output_kind": "document_text_manifest",
        "evidence": ["ocr_providers.py", "ocr_provider_registry.py", "scripts/test_rapidocr_provider.py"],
    },
    "mvp-tts": {
        "capability": "tts",
        "owner": "MVP Video Pipeline",
        "repository": "lightcoloror/mvp-video-pipeline",
        "repository_commit": "3b572e89c1331efb85333af95a52fed9f63741f4",
        "source_provenance_status": "commit_and_evidence_paths_verified",
        "module": "video_creation_pipeline.tts_pipeline",
        "runtime_status": "contract_verified_runtime_not_verified",
        "native_gate_required": True,
        "output_kind": "audio_manifest",
        "evidence": ["src/video_creation_pipeline/tts_pipeline.py", "tests/test_tts_pipeline.py"],
    },
    "sherpa-onnx-tts": {
        "capability": "tts",
        "owner": "Model Provider Gateway",
        "repository": "k2-fsa/sherpa-onnx",
        "repository_commit": "75e1fc31e747194c546787ec7b40a7e0b390dc4b",
        "source_provenance_status": "fixed_source_and_python_api_reviewed_runtime_model_pending",
        "module": "sherpa_onnx.OfflineTts",
        "runtime_status": "runtime_and_model_probe_required",
        "native_gate_required": True,
        "output_kind": "audio_manifest",
        "evidence": [
            "python-api-examples/offline-tts.py",
            "sherpa-onnx/python/csrc/offline-tts.cc",
            "sherpa-onnx/python/csrc/offline-tts-vits-model-config.cc",
        ],
    },
    "sentence-transformers-local": {
        "capability": "embedding",
        "owner": "Model Provider Gateway",
        "repository": "UKPLab/sentence-transformers",
        "repository_commit": None,
        "source_provenance_status": "installed_distribution_metadata_only_source_commit_unverified",
        "module": "sentence_transformers.SentenceTransformer",
        "runtime_status": "runtime_probe_required",
        "native_gate_required": True,
        "output_kind": "embedding_manifest",
        "evidence": [
            "installed Python distribution metadata",
            "Sentence Transformers local-files-only adapter contract",
        ],
    },
}


def _schema(name: str) -> dict[str, Any]:
    package = resources.files("model_provider_gateway.schemas")
    return json.loads(package.joinpath(name).read_text(encoding="utf-8"))


def _installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def embedding_runtime_probe() -> dict[str, Any]:
    """Inspect main and optional isolated runtimes without downloading models."""
    versions = {
        "sentence-transformers": _installed_version("sentence-transformers"),
        "transformers": _installed_version("transformers"),
        "tokenizers": _installed_version("tokenizers"),
    }
    missing = sorted(name for name, version in versions.items() if version is None)
    tokenizers_requirement: str | None = None
    if versions["transformers"] is not None:
        for raw_requirement in metadata.requires("transformers") or []:
            requirement = Requirement(raw_requirement)
            if requirement.name.lower() == "tokenizers" and requirement.marker is None:
                tokenizers_requirement = str(requirement.specifier)
                break

    if missing:
        status = "blocked_package_missing"
        reason = "required local embedding packages are missing"
    elif tokenizers_requirement is None:
        status = "blocked_dependency_requirement_unknown"
        reason = "transformers tokenizers requirement could not be verified"
    elif versions["tokenizers"] not in Requirement(f"tokenizers{tokenizers_requirement}").specifier:
        status = "blocked_dependency_conflict"
        reason = "installed tokenizers does not satisfy the installed transformers requirement"
    else:
        status = "dependency_metadata_compatible_import_not_executed_model_path_required"
        reason = "package metadata is compatible; model import and local model path remain unverified"

    isolated_python = str(os.environ.get("MPG_EMBEDDING_PYTHON") or "").strip()
    isolated_model = str(os.environ.get("MPG_EMBEDDING_MODEL_PATH") or "").strip()
    isolated_probe: dict[str, Any] = {
        "configured": bool(isolated_python or isolated_model),
        "runtime_ready": False,
        "status": "not_configured",
    }
    if bool(isolated_python) != bool(isolated_model):
        isolated_probe.update(
            {
                "status": "blocked_incomplete_configuration",
                "reason": "python and model path must be configured together",
            }
        )
    elif isolated_python and isolated_model:
        python_path = os.path.abspath(os.path.expanduser(isolated_python))
        model_path = os.path.abspath(os.path.expanduser(isolated_model))
        command = [
            python_path,
            "-m",
            "model_provider_gateway.owner_workers.sentence_transformers_worker",
            "--probe",
            "--model",
            model_path,
        ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            )
            payload = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
            isolated_probe.update(
                {
                    "status": "ready" if completed.returncode == 0 and payload.get("ok") else "blocked_probe_failed",
                    "runtime_ready": bool(completed.returncode == 0 and payload.get("ok")),
                    "reason": str(
                        (payload.get("error") or {}).get("message")
                        or payload.get("status")
                        or completed.stderr
                    )[-1000:],
                    "dependency_versions": payload.get("versions") or {},
                    "python_configured": True,
                    "model_configured": True,
                }
            )
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            isolated_probe.update({"status": "blocked_probe_failed", "reason": f"{type(exc).__name__}: {exc}"})

    result = {
        "status": status,
        "reason": reason,
        "dependency_versions": [
            {"distribution": name, "version": version}
            for name, version in sorted(versions.items())
        ],
        "text_codec_requirement": tokenizers_requirement,
        "model_import_attempted": False,
        "model_download_attempted": False,
        "runtime_ready": False,
        "isolated_runtime": isolated_probe,
    }
    if isolated_probe["runtime_ready"]:
        result.update(
            {
                "status": "ready_isolated_runtime",
                "reason": "isolated process passed local-files-only import and model probe",
                "runtime_ready": True,
            }
        )
    assert_secretless(result)
    return result


def owner_adapter_registry(*, capability: str | None = None) -> dict[str, Any]:
    if capability is not None and capability not in OWNER_CAPABILITIES:
        raise ValueError(f"unsupported owner capability: {capability}")
    adapters = []
    for adapter_id, row in sorted(_OWNER_ADAPTERS.items()):
        if capability is not None and row["capability"] != capability:
            continue
        adapter = {"adapter_id": adapter_id, **deepcopy(row)}
        if adapter_id == "sentence-transformers-local":
            runtime_probe = embedding_runtime_probe()
            adapter["runtime_status"] = runtime_probe["status"]
            adapter["runtime_probe"] = runtime_probe
        adapters.append(adapter)
    result = {
        "schema": OWNER_REGISTRY_SCHEMA,
        "capabilities": list(OWNER_CAPABILITIES),
        "adapters": adapters,
        "invariants": {
            "gateway_grants_execution_authority": False,
            "native_owner_gate_required": True,
            "automatic_fallback_allowed": False,
            "automatic_retry_allowed": False,
            "raw_content_in_receipt_allowed": False,
            "remote_upload_requires_separate_consent": True,
        },
    }
    result["registry_revision"] = sha256_json(without_keys(result, "registry_revision"))
    assert_secretless(result)
    return result


def _adapter(adapter_id: str, capability: str) -> dict[str, Any]:
    try:
        adapter = _OWNER_ADAPTERS[adapter_id]
    except KeyError as exc:
        raise ValueError(f"unknown owner adapter: {adapter_id}") from exc
    if adapter["capability"] != capability:
        raise ValueError("owner adapter capability mismatch")
    return adapter


def build_owner_capability_plan(
    capability: str,
    adapter_id: str,
    *,
    consumer_id: str,
    input_manifest_hash: str,
    options: dict[str, Any] | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    if capability not in OWNER_CAPABILITIES:
        raise ValueError(f"unsupported owner capability: {capability}")
    _adapter(adapter_id, capability)
    if not _SHA256.fullmatch(input_manifest_hash):
        raise ValueError("input_manifest_hash must be a lowercase SHA-256")
    safe_options = deepcopy(options or {})
    assert_secretless(safe_options)
    options_hash = sha256_json(safe_options)
    generated_plan_id = sha256_json(
        {
            "consumer_id": consumer_id,
            "capability": capability,
            "adapter_id": adapter_id,
            "input_manifest_hash": input_manifest_hash,
            "options_hash": options_hash,
        }
    )[:20]
    plan = {
        "schema": OWNER_PLAN_SCHEMA,
        "plan_id": plan_id or f"owner-{capability}-{generated_plan_id}",
        "consumer_id": consumer_id,
        "capability": capability,
        "adapter_id": adapter_id,
        "execution_mode": "owner_adapter",
        "input_manifest_hash": input_manifest_hash,
        "options_hash": options_hash,
        "execution_authorized": False,
        "provider_upload_authorized": False,
        "owner_native_gate_required": True,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }
    plan["plan_revision"] = sha256_json(without_keys(plan, "plan_revision"))
    jsonschema.validate(plan, _schema("owner-capability-plan.v1.schema.json"))
    assert_secretless(plan)
    return plan


def _validate_capability_output(capability: str, output: dict[str, Any]) -> None:
    expected_kind = {
        "asr": "transcript_manifest",
        "ocr": "document_text_manifest",
        "tts": "audio_manifest",
        "embedding": "embedding_manifest",
    }[capability]
    if output.get("kind") != expected_kind:
        raise ValueError("owner receipt output kind does not match capability")
    required = {
        "asr": {"transcript_sha256", "segment_count", "language"},
        "ocr": {"document_text_sha256", "page_count", "block_count"},
        "tts": {"audio_sha256", "mime_type", "duration_ms", "voice_consent_ref"},
        "embedding": {"vectors_sha256", "dimensions", "item_count", "model_revision"},
    }[capability]
    if required - set(output):
        raise ValueError("owner receipt capability output is incomplete")
    for key, value in output.items():
        if (key.endswith("sha256") or key in {"model_revision"}) and (
            not isinstance(value, str) or not _SHA256.fullmatch(value)
        ):
            raise ValueError(f"owner receipt {key} must be a lowercase SHA-256")
    if capability == "tts" and not str(output["voice_consent_ref"]).strip():
        raise ValueError("TTS receipt requires a voice authorization reference")


def validate_owner_capability_receipt(
    receipt: dict[str, Any],
    *,
    plan: dict[str, Any],
) -> dict[str, Any]:
    jsonschema.validate(receipt, _schema("owner-capability-receipt.v1.schema.json"))
    jsonschema.validate(plan, _schema("owner-capability-plan.v1.schema.json"))
    expected = {
        "plan_id": plan["plan_id"],
        "plan_revision": plan["plan_revision"],
        "consumer_id": plan["consumer_id"],
        "capability": plan["capability"],
        "adapter_id": plan["adapter_id"],
        "input_manifest_hash": plan["input_manifest_hash"],
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"owner receipt {key} drift detected")
    _adapter(receipt["adapter_id"], receipt["capability"])
    if receipt["provider_called"]:
        raise ValueError("owner receipt must not claim a remote provider call")
    if receipt["external_io_performed"]:
        raise ValueError("owner receipt must not claim external I/O")
    if receipt["status"] in {"completed", "validated_fixture"} and receipt.get("error") is not None:
        raise ValueError("successful owner receipt must not contain an error")
    if receipt["status"] in {"failed", "blocked"} and receipt.get("output") is not None:
        raise ValueError("failed or blocked owner receipt must not contain output")
    if receipt["status"] == "completed" and not receipt.get("owner_gate_receipt_hash"):
        raise ValueError("completed owner execution requires a native owner gate receipt")
    if receipt["status"] == "validated_fixture" and receipt.get("owner_gate_receipt_hash") is not None:
        raise ValueError("synthetic validation must not claim an owner execution gate")
    if receipt["status"] in {"completed", "validated_fixture"}:
        _validate_capability_output(receipt["capability"], receipt["output"])
    assert_secretless(receipt)
    return receipt


def build_synthetic_owner_receipt(
    capability: str,
    *,
    adapter_id: str | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if plan is None:
        selected = adapter_id or next(
            key for key, value in _OWNER_ADAPTERS.items() if value["capability"] == capability
        )
        input_hash = sha256_json({"fixture": capability})
        plan = build_owner_capability_plan(
            capability,
            selected,
            consumer_id="synthetic-contract-smoke",
            input_manifest_hash=input_hash,
            options={"fixture_only": True},
        )
    else:
        jsonschema.validate(plan, _schema("owner-capability-plan.v1.schema.json"))
        if plan.get("capability") != capability:
            raise ValueError("synthetic owner plan capability drift detected")
        selected = str(plan.get("adapter_id") or "")
        if adapter_id is not None and adapter_id != selected:
            raise ValueError("synthetic owner plan adapter drift detected")
        _adapter(selected, capability)
        if plan.get("execution_authorized") is not False:
            raise ValueError("synthetic owner plan must not authorize execution")
        input_hash = str(plan.get("input_manifest_hash") or "")
    digest = sha256_json({"capability": capability, "result": "synthetic"})
    output: dict[str, Any]
    if capability == "asr":
        output = {"kind": "transcript_manifest", "transcript_sha256": digest, "segment_count": 2, "language": "und"}
    elif capability == "ocr":
        output = {"kind": "document_text_manifest", "document_text_sha256": digest, "page_count": 1, "block_count": 2}
    elif capability == "tts":
        output = {
            "kind": "audio_manifest",
            "audio_sha256": digest,
            "mime_type": "audio/wav",
            "duration_ms": 100,
            "voice_consent_ref": "synthetic://voice/fixture-only",
        }
    elif capability == "embedding":
        output = {
            "kind": "embedding_manifest",
            "vectors_sha256": digest,
            "dimensions": 8,
            "item_count": 2,
            "model_revision": sha256_json({"model": "synthetic-local-fixture"}),
        }
    else:
        raise ValueError(f"unsupported owner capability: {capability}")
    receipt = {
        "schema": OWNER_RECEIPT_SCHEMA,
        "status": "validated_fixture",
        "plan_id": plan["plan_id"],
        "plan_revision": plan["plan_revision"],
        "consumer_id": plan["consumer_id"],
        "capability": capability,
        "adapter_id": selected,
        "input_manifest_hash": input_hash,
        "owner_gate_receipt_hash": None,
        "external_io_performed": False,
        "provider_called": False,
        "automatic_fallback_used": False,
        "output": output,
        "error": None,
    }
    validate_owner_capability_receipt(receipt, plan=plan)
    return {"plan": plan, "receipt": receipt}
