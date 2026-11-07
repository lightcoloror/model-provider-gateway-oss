"""Fail-closed execution front door for reviewed local owner adapters.

Intent: reuse the owner runtimes without creating provider SDKs in the gateway.
Decision: the adapter registry is the profile, owner plan is the route, a
bounded local gate is the consent, the file manifest binds input bytes, and the
existing owner receipt is the result/error envelope.
Reason: ASR/OCR/TTS/embedding remain local-owner capabilities while sharing the
same drift, manifest, no-retry and no-fallback invariants as remote execution.
Evidence: VKP ASR, ebook RapidOCR and Sentence Transformers expose stable local
entry points; TTS currently remains blocked when its native runtime cannot run.
Effective scope: synthetic/public/local-authorized files only; never Provider IO.
Rollback: remove this module and the additive schemas/CLI commands; text/vision
and the prior fixture-only owner contracts remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from .artifacts import build_artifact_manifest, validate_artifact_manifest
from .audit import assert_secretless
from .canonical import sha256_json, without_keys
from .capability_control import (
    OWNER_RECEIPT_SCHEMA,
    build_owner_capability_plan,
    validate_owner_capability_receipt,
)

OWNER_INPUT_MANIFEST_SCHEMA = "model_provider_gateway.owner_input_manifest.v1"
OWNER_GATE_SCHEMA = "model_provider_gateway.owner_capability_gate.v1"
OWNER_RUN_REPORT_SCHEMA = "model_provider_gateway.owner_runtime_run_report.v1"
OWNER_TRUTH_SCHEMA = "model_provider_gateway.owner_capability_truth.v1"
ERROR_CATEGORIES = (
    "timeout",
    "rate_limited",
    "provider_server_error",
    "dependency_missing",
    "model_missing",
    "model_hash_mismatch",
    "lexicon_missing",
    "invalid_speaker",
    "empty_audio",
    "route_drift",
    "input_hash_mismatch",
    "mime_mismatch",
    "consent_invalid",
    "automatic_fallback_forbidden",
    "owner_execution_failed",
    "runtime_security_context",
)
DEFAULT_FRESHNESS_SECONDS = 24 * 60 * 60
SHERPA_ONNX_SOURCE_COMMIT = "75e1fc31e747194c546787ec7b40a7e0b390dc4b"
SHERPA_ONNX_RUNTIME_VERSION = "1.13.4"
SHERPA_TTS_MODEL_ID = "vits-icefall-zh-aishell3"
SHERPA_TTS_PUBLIC_VOICE_REF = (
    "public-model-card:csukuangfj/icefall-tts-aishell3-vits-low-2024-04-06@"
    "2c859c3947375a1771a3b9f013ec6e1a20d6803f"
)
SHERPA_TTS_REQUIRED_FILES = ("model.onnx", "tokens.txt", "lexicon.txt")
SHERPA_TTS_REVIEWED_SUPPORT_FILES = (
    "phone.fst",
    "date.fst",
    "number.fst",
    "new_heteronym.fst",
    "rule.far",
    "speakers.txt",
)
SHERPA_TTS_WORKER_PATH = Path(__file__).resolve().parent / "owner_workers" / "sherpa_onnx_tts_worker.py"


def _schema(name: str) -> dict[str, Any]:
    package = resources.files("model_provider_gateway.schemas")
    return json.loads(package.joinpath(name).read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _relative_ref(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def build_owner_input_manifest(
    artifacts: list[str | Path],
    *,
    purpose: str,
    data_classification: str,
    artifact_ids: list[str] | None = None,
    mime_types: list[str] | None = None,
) -> dict[str, Any]:
    if data_classification not in {"synthetic_fixture", "public_fixture", "user_authorized_local"}:
        raise ValueError("unsupported owner input data classification")
    if not artifacts:
        raise ValueError("owner input manifest requires at least one artifact")
    if artifact_ids is not None and len(artifact_ids) != len(artifacts):
        raise ValueError("artifact_ids must match artifacts")
    if mime_types is not None and len(mime_types) != len(artifacts):
        raise ValueError("mime_types must match artifacts")
    rows = []
    for index, value in enumerate(artifacts):
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"owner input artifact not found: {path}")
        mime_type = (
            mime_types[index]
            if mime_types is not None
            else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        )
        rows.append(
            {
                "artifact_id": artifact_ids[index] if artifact_ids is not None else f"artifact-{index + 1:04d}",
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "mime_type": mime_type.lower(),
                "purpose": purpose,
            }
        )
    manifest = {
        "schema": OWNER_INPUT_MANIFEST_SCHEMA,
        "purpose": purpose,
        "data_classification": data_classification,
        "external_io_authorized": False,
        "artifacts": rows,
    }
    manifest["manifest_revision"] = sha256_json(without_keys(manifest, "manifest_revision"))
    jsonschema.validate(manifest, _schema("owner-input-manifest.v1.schema.json"))
    assert_secretless(manifest)
    return manifest


def validate_owner_input_manifest(
    manifest: dict[str, Any],
    *,
    artifact_paths: list[str | Path],
) -> dict[str, Any]:
    jsonschema.validate(manifest, _schema("owner-input-manifest.v1.schema.json"))
    expected_revision = sha256_json(without_keys(manifest, "manifest_revision"))
    if manifest.get("manifest_revision") != expected_revision:
        raise ValueError("input manifest revision drift detected")
    rows = manifest["artifacts"]
    if len(rows) != len(artifact_paths):
        raise ValueError("input manifest artifact count drift detected")
    for row, value in zip(rows, artifact_paths, strict=True):
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size != row["bytes"] or _sha256_file(path) != row["sha256"]:
            raise ValueError("input_hash_mismatch")
        detected = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").lower()
        if row["mime_type"] != detected and not (
            row["mime_type"] == "audio/wav" and detected in {"audio/x-wav", "audio/wav"}
        ):
            raise ValueError("mime_mismatch")
    assert_secretless(manifest)
    return manifest


def _safe_local_path(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except OSError:
            raise ValueError("consent_invalid: local path unavailable") from None
        if component.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("consent_invalid: reparse path")
    return path.resolve()


def build_owner_local_authorization(
    manifest: dict[str, Any], *, artifact_paths: list[str | Path], derived_root: str | Path,
    consumer_id: str, expires_at: str, user_confirmed: bool = False,
) -> dict[str, Any]:
    """Build a local consent record, never infer approval from classification/config.

    The caller must persist it in an access-controlled local file and pin its revision.
    Replacing/deleting that file (including revoked=true) invalidates existing gates.
    This is a bounded extension of artifact consent, not a new authorization store.
    """
    if user_confirmed is not True or manifest.get("data_classification") != "user_authorized_local":
        raise ValueError("consent_invalid: explicit local approval required")
    root = _safe_local_path(derived_root)
    paths = [_safe_local_path(p) for p in artifact_paths]
    if not root.is_dir() or root == Path(root.anchor) or any(not p.is_relative_to(root) for p in paths):
        raise ValueError("consent_invalid: derived root boundary")
    if any(p.suffix.lower() not in {".txt", ".md", ".json"} for p in paths):
        raise ValueError("consent_invalid: only explicitly selected derived text")
    if any(not p.is_file() or not 1 <= p.stat().st_size <= 16384 for p in paths):
        raise ValueError("consent_invalid: derived text size limit")
    validate_owner_input_manifest(manifest, artifact_paths=paths)
    artifacts = build_artifact_manifest(paths, data_type="derived_text", purpose=manifest["purpose"])
    validate_artifact_manifest(artifacts, capability="text")
    grant = {
        "schema": "model_provider_gateway.owner_local_authorization.v1",
        "user_confirmed": True, "revoked": False, "capability": "embedding",
        "adapter_id": "sentence-transformers-local", "consumer_id": consumer_id,
        "purpose": manifest["purpose"], "input_manifest_hash": manifest["manifest_revision"],
        "derived_root": str(root), "artifacts": artifacts, "expires_at": expires_at,
        "external_io_authorized": False,
    }
    grant["authorization_revision"] = sha256_json(grant)
    return grant


def _validate_local_authorization(
    reference: dict[str, Any], plan: dict[str, Any], manifest: dict[str, Any],
    artifact_paths: list[str | Path] | None,
) -> None:
    if artifact_paths is None:
        raise ValueError("consent_invalid: exact artifact paths required")
    path = _safe_local_path(reference["path"])
    if path.stat().st_size > 256 * 1024:
        raise ValueError("consent_invalid: oversized authorization")
    grant = json.loads(path.read_text(encoding="utf-8"))
    revision = sha256_json(without_keys(grant, "authorization_revision"))
    if grant.get("authorization_revision") != revision or revision != reference["revision"]:
        raise ValueError("consent_invalid: authorization revision drift")
    if grant.get("revoked") is not False or grant.get("user_confirmed") is not True:
        raise ValueError("consent_invalid: authorization revoked or unconfirmed")
    expiry = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise ValueError("consent_invalid: authorization expired")
    rebuilt = build_owner_local_authorization(
        manifest, artifact_paths=artifact_paths, derived_root=grant["derived_root"],
        consumer_id=plan["consumer_id"], expires_at=grant["expires_at"], user_confirmed=True,
    )
    if grant != rebuilt or plan["capability"] != "embedding" or plan["adapter_id"] != grant["adapter_id"]:
        raise ValueError("consent_invalid: scope or exact material mismatch")


def build_owner_gate(
    plan: dict[str, Any], manifest: dict[str, Any], *,
    local_authorization_path: str | Path | None = None,
    local_authorization_revision: str | None = None,
    artifact_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    if plan.get("input_manifest_hash") != manifest.get("manifest_revision"):
        raise ValueError("route_drift")
    gate = {
        "schema": OWNER_GATE_SCHEMA,
        "scope": "local_synthetic_or_public_fixture",
        "plan_id": plan["plan_id"],
        "plan_revision": plan["plan_revision"],
        "capability": plan["capability"],
        "adapter_id": plan["adapter_id"],
        "input_manifest_hash": plan["input_manifest_hash"],
        "local_execution_authorized": True,
        "provider_call_authorized": False,
        "external_io_authorized": False,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }
    if manifest.get("data_classification") == "user_authorized_local":
        if not local_authorization_path or not local_authorization_revision:
            raise ValueError("consent_invalid: explicit local authorization required")
        gate.update(schema="model_provider_gateway.owner_capability_gate.v2", scope="user_authorized_local")
        gate["local_consent_ref"] = {
            "path": str(_safe_local_path(local_authorization_path)), "revision": local_authorization_revision,
        }
        _validate_local_authorization(gate["local_consent_ref"], plan, manifest, artifact_paths)
    elif manifest.get("data_classification") not in {"synthetic_fixture", "public_fixture"}:
        raise ValueError("consent_invalid: classification")
    gate["gate_revision"] = sha256_json(without_keys(gate, "gate_revision"))
    jsonschema.validate(gate, _schema("owner-capability-gate.v2.schema.json" if "local_consent_ref" in gate
                                    else "owner-capability-gate.v1.schema.json"))
    assert_secretless(gate)
    return gate


def validate_owner_gate(
    gate: dict[str, Any], *, plan: dict[str, Any], manifest: dict[str, Any],
    artifact_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    private = manifest.get("data_classification") == "user_authorized_local"
    jsonschema.validate(gate, _schema("owner-capability-gate.v2.schema.json" if private
                                    else "owner-capability-gate.v1.schema.json"))
    if manifest.get("manifest_revision") != sha256_json(without_keys(manifest, "manifest_revision")):
        raise ValueError("consent_invalid: input manifest revision")
    if plan.get("plan_revision") != sha256_json(without_keys(plan, "plan_revision")):
        raise ValueError("consent_invalid: plan revision")
    if plan.get("input_manifest_hash") != manifest.get("manifest_revision"):
        raise ValueError("consent_invalid: manifest/plan mismatch")
    if private:
        _validate_local_authorization(gate["local_consent_ref"], plan, manifest, artifact_paths)
    elif manifest.get("data_classification") not in {"synthetic_fixture", "public_fixture"}:
        raise ValueError("consent_invalid: classification")
    if gate.get("gate_revision") != sha256_json(without_keys(gate, "gate_revision")):
        raise ValueError("consent_invalid")
    expected = {
        "plan_id": plan["plan_id"],
        "plan_revision": plan["plan_revision"],
        "capability": plan["capability"],
        "adapter_id": plan["adapter_id"],
        "input_manifest_hash": manifest["manifest_revision"],
    }
    if any(gate.get(key) != value for key, value in expected.items()):
        raise ValueError("consent_invalid")
    return gate


def classify_owner_error(
    *, message: str = "", returncode: int | None = None, http_status: int | None = None
) -> dict[str, Any]:
    lower = message.lower()
    if http_status == 429:
        category, retryable = "rate_limited", True
    elif "consent_invalid" in lower:
        category, retryable = "consent_invalid", False
    elif http_status is not None and 500 <= http_status <= 599:
        category, retryable = "provider_server_error", True
    elif "timeout" in lower or "timed out" in lower:
        category, retryable = "timeout", True
    elif "model_hash_mismatch" in lower or "model hash" in lower:
        category, retryable = "model_hash_mismatch", False
    elif "lexicon_missing" in lower or "lexicon" in lower and "missing" in lower:
        category, retryable = "lexicon_missing", False
    elif "invalid_speaker" in lower or "speaker must be" in lower:
        category, retryable = "invalid_speaker", False
    elif "empty_audio" in lower or "no audio samples" in lower:
        category, retryable = "empty_audio", False
    elif "model" in lower and ("missing" in lower or "not found" in lower):
        category, retryable = "model_missing", False
    elif "import" in lower or "dependency" in lower or "module" in lower:
        category, retryable = "dependency_missing", False
    elif "security" in lower or "0x80045040" in lower or "no voice installed" in lower:
        category, retryable = "runtime_security_context", False
    else:
        category, retryable = "owner_execution_failed", False
    return {
        "category": category,
        "retryable": retryable,
        "returncode": returncode,
        "message": message[-2000:],
        "automatic_retry_performed": False,
        "automatic_fallback_performed": False,
    }


def _receipt(
    plan: dict[str, Any],
    gate: dict[str, Any],
    *,
    status: str,
    output: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    receipt = {
        "schema": OWNER_RECEIPT_SCHEMA,
        "status": status,
        "plan_id": plan["plan_id"],
        "plan_revision": plan["plan_revision"],
        "consumer_id": plan["consumer_id"],
        "capability": plan["capability"],
        "adapter_id": plan["adapter_id"],
        "input_manifest_hash": plan["input_manifest_hash"],
        "owner_gate_receipt_hash": gate["gate_revision"],
        "external_io_performed": False,
        "provider_called": False,
        "automatic_fallback_used": False,
        "output": output,
        "error": error,
    }
    validate_owner_capability_receipt(receipt, plan=plan)
    return receipt


def _run_command(command: list[str], *, cwd: Path, timeout_seconds: int) -> tuple[int, str, str, int]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    value
                    for value in (
                        str(Path(__file__).resolve().parents[1]),
                        str(os.environ.get("PYTHONPATH") or ""),
                    )
                    if value
                ),
            },
        )
        return completed.returncode, completed.stdout, completed.stderr, round((time.monotonic() - started) * 1000)
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        return 124, stdout, (stderr + f"\ntimeout after {timeout_seconds}s").strip(), latency_ms


def _asr_output(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    normalized_path = Path(report["asr_run"]["normalized_json"])
    normalized = json.loads(normalized_path.read_text(encoding="utf-8-sig"))
    canonical_segments = [
        {
            "start": segment.get("start"),
            "end": segment.get("end"),
            "text": segment.get("text"),
            "speaker": segment.get("speaker") or segment.get("speaker_id") or "",
        }
        for segment in normalized.get("segments") or []
    ]
    return {
        "kind": "transcript_manifest",
        "transcript_sha256": sha256_json(canonical_segments),
        "segment_count": len(canonical_segments),
        "language": str(report.get("language") or "und"),
    }


def _ocr_output(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    provider = next((row for row in report.get("providers") or [] if row.get("status") in {"ok", "partial"}), None)
    if provider is None:
        raise ValueError("OCR report has no successful provider result")
    items = provider.get("items") or []
    canonical = [
        {
            "artifact_index": index,
            "text": item.get("text_preview") or "",
            "blocks": item.get("blocks") or [],
        }
        for index, item in enumerate(items, start=1)
    ]
    return {
        "kind": "document_text_manifest",
        "document_text_sha256": sha256_json(canonical),
        "page_count": len(items),
        "block_count": sum(int(item.get("block_count") or 0) for item in items),
    }


def _embedding_output(path: Path, model_revision: str, *, texts: list[str] | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("model_revision") != model_revision:
        raise ValueError("model_hash_mismatch: worker model revision")
    count, dimensions, vectors = payload.get("item_count"), payload.get("dimensions"), payload.get("vectors")
    if (type(count) is not int or not 1 <= count <= 64 or type(dimensions) is not int
            or not 1 <= dimensions <= 65536 or not isinstance(vectors, list) or len(vectors) != count
            or any(not isinstance(v, list) or len(v) != dimensions
                   or any(type(n) not in (float, int) or not math.isfinite(n) for n in v) for v in vectors)
            or texts is not None and len(texts) != count):
        raise ValueError("embedding output invalid vectors or count")
    result = {
        "kind": "embedding_manifest",
        "vectors_sha256": _sha256_file(path),
        "dimensions": int(payload["dimensions"]),
        "item_count": int(payload["item_count"]),
        "model_revision": model_revision,
        "coverage_status": "unproven_legacy_worker",
    }
    if payload.get("schema") == "model_provider_gateway.embedding_worker_output.v1":
        return result
    if payload.get("schema") != "model_provider_gateway.embedding_worker_output.v2":
        raise ValueError("embedding output schema mismatch")
    attestation, identity = payload.get("token_attestation"), payload.get("execution_identity")
    if not isinstance(attestation, dict) or not isinstance(identity, dict) or texts is None:
        raise ValueError("token_coverage_unproven: attestation required")
    maximum, items = attestation.get("max_seq_length"), attestation.get("items")
    if (attestation.get("schema") != "model_provider_gateway.embedding_token_attestation.v1"
            or attestation.get("coverage_status") != "complete" or attestation.get("prompt_policy") != "explicit_empty"
            or attestation.get("special_tokens_included") is not True
            or type(maximum) is not int or not 1 <= maximum <= 1_000_000
            or not isinstance(items, list) or len(items) != count):
        raise ValueError("token_coverage_unproven: invalid attestation")
    for index, (item, text) in enumerate(zip(items, texts, strict=True)):
        if (not isinstance(item, dict) or type(item.get("index")) is not int or item["index"] != index
                or item.get("text_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()
                or type(item.get("raw_token_count")) is not int
                or type(item.get("effective_token_count")) is not int
                or not 1 <= item["raw_token_count"] == item["effective_token_count"] <= maximum
                or item.get("truncated") is not False):
            raise ValueError("token_coverage_unproven: item binding or truncation")
    worker = Path(__file__).parent / "owner_workers" / "sentence_transformers_worker.py"
    if (identity.get("worker_sha256") != _sha256_file(worker)
            or not str(identity.get("model_actual", "")).startswith("local-modules-sha256:")
            or len(str(identity.get("model_actual", ""))) != len("local-modules-sha256:") + 64
            or any(c not in "0123456789abcdef" for c in str(identity.get("model_actual", ""))[21:])
            or identity.get("identity_scope") != "local_modules_config_not_full_weights"
            or not isinstance(identity.get("model_class"), str)
            or not isinstance(identity.get("versions"), dict)
            or not all(isinstance(identity["versions"].get(k), str) and identity["versions"][k]
                       for k in ("sentence-transformers", "transformers", "tokenizers", "huggingface-hub", "torch"))):
        raise ValueError("token_coverage_unproven: worker identity")
    result.update(coverage_status="complete", coverage_proof_sha256=sha256_json(attestation),
                  execution_identity_sha256=sha256_json(identity), model_actual=identity["model_actual"])
    return result


def inspect_sherpa_tts_model_bundle(model_dir: str | Path | None) -> dict[str, Any]:
    """Inspect an explicitly supplied local bundle without importing sherpa-onnx."""
    root = Path(model_dir).expanduser().resolve() if model_dir else None
    rows: list[dict[str, Any]] = []
    for name in (*SHERPA_TTS_REQUIRED_FILES, *SHERPA_TTS_REVIEWED_SUPPORT_FILES):
        path = root / name if root else None
        present = bool(path and path.is_file())
        rows.append(
            {
                "name": name,
                "required": name in SHERPA_TTS_REQUIRED_FILES,
                "present": present,
                "bytes": path.stat().st_size if present and path else None,
                "sha256": _sha256_file(path) if present and path else None,
            }
        )
    missing = [row["name"] for row in rows if row["required"] and not row["present"]]
    canonical = {
        "model_id": SHERPA_TTS_MODEL_ID,
        "source_commit": SHERPA_ONNX_SOURCE_COMMIT,
        "files": rows,
    }
    return {
        **canonical,
        "model_dir_configured": root is not None,
        "model_bundle_complete": not missing,
        "missing_required_files": missing,
        "model_revision": sha256_json(canonical),
    }


def _tts_output(report_path: Path, model_revision: str) -> dict[str, Any]:
    payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    wav_path = report_path.with_name("tts-smoke.wav")
    if payload.get("schema") != "model_provider_gateway.sherpa_onnx_tts_worker_output.v1":
        raise ValueError("TTS worker output schema mismatch")
    if payload.get("model_revision") != model_revision:
        raise ValueError("model_hash_mismatch")
    if not wav_path.is_file() or wav_path.stat().st_size <= 44:
        raise ValueError("empty_audio")
    if _sha256_file(wav_path) != payload.get("wav_sha256"):
        raise ValueError("TTS WAV hash mismatch")
    if float(payload.get("duration_seconds") or 0) <= 0 or int(payload.get("sample_count") or 0) <= 0:
        raise ValueError("empty_audio")
    return {
        "kind": "audio_manifest",
        "audio_sha256": payload["wav_sha256"],
        "mime_type": "audio/wav",
        "duration_ms": int(round(float(payload["duration_seconds"]) * 1000)),
        "voice_consent_ref": SHERPA_TTS_PUBLIC_VOICE_REF,
        "bytes": int(payload["wav_bytes"]),
        "duration_seconds": float(payload["duration_seconds"]),
        "sample_rate": int(payload["sample_rate"]),
        "sample_count": int(payload["sample_count"]),
        "speaker": int(payload["speaker"]),
        "provider": str(payload["provider"]),
        "model_revision": model_revision,
        "quality_status": str(payload["quality_status"]),
        "peak_amplitude": float(payload["peak_amplitude"]),
        "rms_amplitude": float(payload["rms_amplitude"]),
        "non_silent_ratio": float(payload["non_silent_ratio"]),
        "real_time_factor": float(payload["real_time_factor"]),
    }


def _runtime_command(
    capability: str,
    adapter_id: str,
    *,
    artifact_paths: list[Path],
    output_dir: Path,
    runtime: dict[str, str],
) -> tuple[list[str], Path, str, str | None]:
    if capability == "asr":
        root = Path(runtime["vkp_root"]).expanduser().resolve()
        report = output_dir / "native" / "asr-smoke.json"
        return (
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts" / "video-knowledge.ps1"),
                "asr-smoke",
                str(artifact_paths[0]),
                "--output-dir",
                str(output_dir / "native"),
                "--preset",
                runtime.get("asr_preset", "sensevoice"),
                "--language",
                runtime.get("language", "zh"),
                "--duration-seconds",
                runtime.get("duration_seconds", "30"),
                "--execute",
                "--timeout-seconds",
                runtime.get("timeout_seconds", "600"),
            ],
            report,
            "vkp-asr-local",
            None,
        )
    if capability == "ocr":
        root = Path(runtime["ebook_root"]).expanduser().resolve()
        report = output_dir / "native" / "ocr-provider-comparison.json"
        return (
            [
                sys.executable,
                str(root / "scripts" / "compare_ocr_providers.py"),
                *[str(path) for path in artifact_paths],
                "--output",
                str(output_dir / "native"),
                "--providers",
                runtime.get("ocr_provider", "rapidocr"),
            ],
            report,
            "ebook-rapidocr-local",
            None,
        )
    if capability == "embedding":
        python_path = Path(runtime["embedding_python"]).expanduser().resolve()
        model_path = Path(runtime["embedding_model"]).expanduser().resolve()
        model_revision = runtime.get("model_revision") or _sha256_file(model_path / "modules.json")
        worker_input = output_dir / "embedding-input.json"
        texts = [path.read_text(encoding="utf-8-sig") for path in artifact_paths]
        _write_json(worker_input, {"schema": "model_provider_gateway.embedding_worker_input.v1", "texts": texts})
        report = output_dir / "embedding-vectors.json"
        return (
            [
                str(python_path),
                "-m",
                "model_provider_gateway.owner_workers.sentence_transformers_worker",
                "--model",
                str(model_path),
                "--input",
                str(worker_input),
                "--output",
                str(report),
                "--device",
                runtime.get("embedding_device", "cpu"),
                "--model-revision",
                model_revision,
            ],
            report,
            "sentence-transformers-isolated-local",
            model_revision,
        )
    if capability == "tts":
        if adapter_id != "sherpa-onnx-tts":
            raise ValueError("runtime security context: no reviewed local TTS runtime for this adapter")
        python_path = Path(runtime["sherpa_python"]).expanduser().resolve()
        model_path = Path(runtime["sherpa_model_dir"]).expanduser().resolve()
        bundle = inspect_sherpa_tts_model_bundle(model_path)
        if not python_path.is_file():
            raise FileNotFoundError(f"dependency missing: sherpa-onnx Python runtime not found: {python_path}")
        if not bundle["model_bundle_complete"]:
            missing = ", ".join(bundle["missing_required_files"])
            raise FileNotFoundError(f"model missing: incomplete sherpa-onnx TTS bundle: {missing}")
        expected_revision = runtime.get("model_revision") or bundle["model_revision"]
        if expected_revision != bundle["model_revision"]:
            raise ValueError("model_hash_mismatch")
        worker_input = output_dir / "tts-input.json"
        texts = [path.read_text(encoding="utf-8-sig").strip() for path in artifact_paths]
        if len(texts) != 1 or not texts[0]:
            raise ValueError("TTS requires exactly one non-empty UTF-8 text artifact")
        _write_json(
            worker_input,
            {"schema": "model_provider_gateway.sherpa_onnx_tts_worker_input.v1", "text": texts[0]},
        )
        report = output_dir / "native" / "tts-smoke.json"
        wav = output_dir / "native" / "tts-smoke.wav"
        return (
            [
                str(python_path),
                str(SHERPA_TTS_WORKER_PATH),
                "--model-dir",
                str(model_path),
                "--input",
                str(worker_input),
                "--output-wav",
                str(wav),
                "--output-json",
                str(report),
                "--provider",
                runtime.get("sherpa_provider", "cpu"),
                "--num-threads",
                runtime.get("sherpa_num_threads", "2"),
                "--speaker",
                runtime.get("sherpa_speaker", "0"),
                "--speed",
                runtime.get("sherpa_speed", "1.0"),
                "--model-revision",
                bundle["model_revision"],
            ],
            report,
            f"sherpa-onnx-{SHERPA_ONNX_RUNTIME_VERSION}-{runtime.get('sherpa_provider', 'cpu')}",
            str(bundle["model_revision"]),
        )
    raise ValueError(
        "runtime security context: reviewed local TTS synthesizer did not pass its native smoke"
    )


def execute_owner_capability(
    capability: str,
    adapter_id: str,
    artifact_paths: list[str | Path],
    *,
    output_dir: str | Path,
    consumer_id: str,
    purpose: str,
    data_classification: str,
    runtime: dict[str, str],
    timeout_seconds: int,
    local_authorization_path: str | Path | None = None,
    local_authorization_revision: str | None = None,
) -> dict[str, Any]:
    paths = [(_safe_local_path(value) if data_classification == "user_authorized_local"
              else Path(value).expanduser().resolve()) for value in artifact_paths]
    manifest = build_owner_input_manifest(paths, purpose=purpose, data_classification=data_classification)
    plan_options: dict[str, Any] = {"timeout_seconds": int(timeout_seconds), "no_retry": True, "no_fallback": True}
    if capability == "tts":
        bundle = inspect_sherpa_tts_model_bundle(runtime.get("sherpa_model_dir"))
        plan_options["tts"] = {
            "source_commit": SHERPA_ONNX_SOURCE_COMMIT,
            "runtime_version": SHERPA_ONNX_RUNTIME_VERSION,
            "model_id": SHERPA_TTS_MODEL_ID,
            "model_revision": bundle["model_revision"],
            "provider": runtime.get("sherpa_provider", "cpu"),
            "speaker": int(runtime.get("sherpa_speaker", "0")),
            "speed": float(runtime.get("sherpa_speed", "1.0")),
            "num_threads": int(runtime.get("sherpa_num_threads", "2")),
        }
    plan = build_owner_capability_plan(
        capability,
        adapter_id,
        consumer_id=consumer_id,
        input_manifest_hash=manifest["manifest_revision"],
        options=plan_options,
    )
    gate = build_owner_gate(
        plan, manifest, local_authorization_path=local_authorization_path,
        local_authorization_revision=local_authorization_revision, artifact_paths=paths,
    )
    validate_owner_input_manifest(manifest, artifact_paths=paths)
    validate_owner_gate(gate, plan=plan, manifest=manifest, artifact_paths=paths)
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "input-manifest.json", manifest)
    _write_json(root / "owner-plan.json", plan)
    _write_json(root / "owner-gate.json", gate)
    try:
        expected_model_config = None
        if capability == "embedding" and runtime.get("embedding_model"):
            expected_model_config = _sha256_file(Path(runtime["embedding_model"]) / "modules.json")
        command, native_report, runtime_id, model_revision = _runtime_command(
            capability, adapter_id, artifact_paths=paths, output_dir=root, runtime=runtime
        )
        validate_owner_input_manifest(manifest, artifact_paths=paths)
        validate_owner_gate(gate, plan=plan, manifest=manifest, artifact_paths=paths)
        returncode, stdout, stderr, latency_ms = _run_command(command, cwd=root, timeout_seconds=timeout_seconds)
        validate_owner_input_manifest(manifest, artifact_paths=paths)
        validate_owner_gate(gate, plan=plan, manifest=manifest, artifact_paths=paths)
        if returncode != 0 or not native_report.is_file():
            error = classify_owner_error(message=stderr or stdout, returncode=returncode)
            receipt = _receipt(plan, gate, status="failed", output=None, error=error)
            status = "failed"
            output_refs: list[str] = []
        else:
            if capability == "asr":
                output = _asr_output(native_report)
            elif capability == "ocr":
                output = _ocr_output(native_report)
            elif capability == "tts":
                output = _tts_output(native_report, str(model_revision))
            else:
                output = _embedding_output(native_report, str(model_revision),
                                           texts=[p.read_text(encoding="utf-8-sig") for p in paths])
                if data_classification == "user_authorized_local" and output["coverage_status"] != "complete":
                    raise ValueError("coverage_unproven: private input requires current worker proof")
                if output["coverage_status"] == "complete" and runtime.get("embedding_model"):
                    actual = _sha256_file(Path(runtime["embedding_model"]) / "modules.json")
                    if actual != expected_model_config or output["model_actual"] != "local-modules-sha256:" + actual:
                        raise ValueError("model_hash_mismatch: actual model configuration")
            receipt = _receipt(plan, gate, status="completed", output=output, error=None)
            status = "completed"
            output_refs = [_relative_ref(native_report, root)]
            if capability == "tts":
                output_refs.append(_relative_ref(native_report.with_name("tts-smoke.wav"), root))
    except (FileNotFoundError, KeyError, OSError, ValueError, TypeError, jsonschema.ValidationError) as exc:
        latency_ms = 0
        runtime_id = f"{adapter_id}-local"
        error = classify_owner_error(message=str(exc))
        if data_classification == "user_authorized_local":
            error["message"] = "Private local execution rejected; revalidate consent and exact input evidence."
        receipt = _receipt(plan, gate, status="blocked", output=None, error=error)
        status = "blocked"
        output_refs = []
    receipt_hash = sha256_json(receipt)
    _write_json(root / "owner-receipt.json", receipt)
    report = {
        "schema": OWNER_RUN_REPORT_SCHEMA,
        "executed_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": status,
        "capability": capability,
        "adapter_id": adapter_id,
        "plan_revision": plan["plan_revision"],
        "input_manifest_hash": manifest["manifest_revision"],
        "gate_revision": gate["gate_revision"],
        "receipt_hash": receipt_hash,
        "runtime": {
            "execution_location": "local",
            "runtime_id": runtime_id,
            "latency_ms": latency_ms,
            "provider_called": False,
            "external_io_performed": False,
            "automatic_retry_count": 0,
            "automatic_fallback_used": False,
        },
        "output_refs": output_refs,
        "error": deepcopy(receipt["error"]),
    }
    report["report_revision"] = sha256_json(without_keys(report, "report_revision"))
    jsonschema.validate(report, _schema("owner-runtime-run-report.v1.schema.json"))
    assert_secretless(report)
    _write_json(root / "owner-runtime-run-report.json", report)
    return {"manifest": manifest, "plan": plan, "gate": gate, "receipt": receipt, "report": report}


def build_capability_truth(
    *,
    run_reports: dict[str, dict[str, Any] | None] | None = None,
    tts_probe: dict[str, Any] | None = None,
    now: datetime | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
) -> dict[str, Any]:
    reports = run_reports or {}
    observed_at = now or datetime.now(UTC)
    adapters = {
        "asr": "vkp-asr",
        "ocr": "ebook-document-ocr",
        "tts": "sherpa-onnx-tts",
        "embedding": "sentence-transformers-local",
    }
    rows: dict[str, Any] = {}
    for capability, adapter_id in adapters.items():
        report = reports.get(capability)
        if capability == "tts" and report and report.get("adapter_id") != adapter_id:
            report = None
        executed_at_raw = str(report.get("executed_at") or "") if report else ""
        try:
            executed_at = datetime.fromisoformat(executed_at_raw.replace("Z", "+00:00"))
            if executed_at.tzinfo is None:
                executed_at = executed_at.replace(tzinfo=UTC)
            report_age_seconds = max(0, int((observed_at - executed_at).total_seconds()))
        except ValueError:
            executed_at = None
            report_age_seconds = None
        passed = bool(
            report
            and report.get("status") == "completed"
            and report_age_seconds is not None
            and report_age_seconds <= freshness_seconds
        )
        if capability == "tts" and not passed:
            reason = str((tts_probe or {}).get("reason") or "reviewed local TTS runtime has no successful native smoke")
            status = "blocked"
            runtime_ready = False
        elif passed:
            reason = "fresh local fixture execution completed through the reviewed owner adapter"
            status = "ready_local_on_demand"
            runtime_ready = True
        else:
            reason = "runtime is configured but no fresh local execution receipt was supplied"
            status = "configured_unverified"
            runtime_ready = False
        rows[capability] = {
            "capability": capability,
            "adapter_id": adapter_id,
            "contract_ready": True,
            "runtime_ready": runtime_ready,
            "fresh_e2e_pass": passed,
            "real_usage_proof": passed,
            "current_online": False,
            "status": status,
            "reason": reason,
            "last_receipt_hash": report.get("receipt_hash") if report else None,
            "last_executed_at": executed_at.isoformat() if executed_at else None,
            "provider_called": False,
            "automatic_fallback_used": False,
        }
    truth = {"schema": OWNER_TRUTH_SCHEMA, "capabilities": rows}
    truth["matrix_revision"] = sha256_json(without_keys(truth, "matrix_revision"))
    jsonschema.validate(truth, _schema("owner-capability-truth.v1.schema.json"))
    assert_secretless(truth)
    return truth


def probe_tts_runtime(*, timeout_seconds: int = 15) -> dict[str, Any]:
    """Probe Windows SAPI without writing audio or contacting a service."""
    if os.name != "nt":
        return {
            "runtime_ready": False,
            "status": "blocked_platform_unsupported",
            "reason": "reviewed TTS probe currently supports Windows SAPI only",
            "voice_count": 0,
            "synthesis_attempted": False,
            "external_io_performed": False,
        }
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Speech;"
        "$s=[System.Speech.Synthesis.SpeechSynthesizer]::new();"
        "$v=@($s.GetInstalledVoices()|ForEach-Object{$_.VoiceInfo.Name});"
        "$s.SetOutputToNull();$s.Speak('synthetic local runtime probe');$s.Dispose();"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "@{ok=$true;voices=$v}|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
        ready = bool(completed.returncode == 0 and payload.get("ok"))
        return {
            "runtime_ready": ready,
            "status": "ready" if ready else "blocked_runtime_security_context",
            "reason": (
                "native SAPI synthesis probe passed"
                if ready
                else (completed.stderr or "native SAPI synthesis failed")[-1000:]
            ),
            "voice_count": len(payload.get("voices") or []),
            "synthesis_attempted": True,
            "external_io_performed": False,
        }
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {
            "runtime_ready": False,
            "status": "blocked_runtime_probe_failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "voice_count": 0,
            "synthesis_attempted": True,
            "external_io_performed": False,
        }


def probe_sherpa_tts_runtime(
    *,
    python_path: str | Path | None = None,
    model_dir: str | Path | None = None,
    provider: str = "cpu",
    speaker: int = 0,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    """Probe the reviewed sherpa runtime and model without generating audio."""
    selected_python = Path(python_path).expanduser().resolve() if python_path else Path(sys.executable).resolve()
    bundle = inspect_sherpa_tts_model_bundle(model_dir)
    base = {
        "adapter_id": "sherpa-onnx-tts",
        "source_commit": SHERPA_ONNX_SOURCE_COMMIT,
        "runtime_version_expected": SHERPA_ONNX_RUNTIME_VERSION,
        "python_runtime": {
            "configured": bool(python_path),
            "present": selected_python.is_file(),
        },
        "model_bundle": bundle,
        "lexicon_ready": any(row["name"] == "lexicon.txt" and row["present"] for row in bundle["files"]),
        "tokens_ready": any(row["name"] == "tokens.txt" and row["present"] for row in bundle["files"]),
        "voice": {"speaker": speaker, "validated": False, "available_speakers": None},
        "provider": {"requested": provider, "validated": False},
        "output": {"mode": "caller_supplied_run_directory", "validated": False},
        "license": {
            "runtime": "Apache-2.0",
            "model_card": "Apache-2.0",
            "redistribution_review_required": True,
        },
        "external_io_performed": False,
        "automatic_fallback_performed": False,
    }
    if provider not in {"cpu", "cuda"}:
        return {
            **base,
            "runtime_ready": False,
            "status": "blocked_invalid_provider",
            "reason": "provider must be cpu or cuda",
        }
    if not selected_python.is_file():
        return {
            **base,
            "runtime_ready": False,
            "status": "blocked_runtime_missing",
            "reason": "configured Python runtime is missing",
        }
    command = [
        str(selected_python),
        str(SHERPA_TTS_WORKER_PATH),
        "--probe-runtime",
    ]
    returncode, stdout, stderr, latency_ms = _run_command(command, cwd=Path.cwd(), timeout_seconds=timeout_seconds)
    runtime_payload: dict[str, Any] = {}
    if stdout.strip():
        try:
            runtime_payload = json.loads(stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            runtime_payload = {}
    base["python_runtime"] = {
        **base["python_runtime"],
        "import_ready": bool(returncode == 0 and runtime_payload.get("ok")),
        "observed_version": runtime_payload.get("version"),
        "probe_latency_ms": latency_ms,
    }
    if returncode != 0:
        runtime_error = runtime_payload.get("error") if isinstance(runtime_payload.get("error"), dict) else {}
        return {
            **base,
            "runtime_ready": False,
            "status": "blocked_runtime_package_missing",
            "reason": str(runtime_error.get("message") or stderr or "sherpa-onnx runtime import failed")[-1000:],
        }
    if not bundle["model_bundle_complete"]:
        return {
            **base,
            "runtime_ready": False,
            "status": "blocked_model_bundle_missing",
            "reason": "required local TTS model files are missing: " + ", ".join(bundle["missing_required_files"]),
        }
    command = [
        str(selected_python),
        str(SHERPA_TTS_WORKER_PATH),
        "--probe-model",
        "--model-dir",
        str(Path(model_dir).expanduser().resolve()),
        "--provider",
        provider,
        "--speaker",
        str(speaker),
    ]
    returncode, stdout, stderr, model_latency_ms = _run_command(
        command,
        cwd=Path.cwd(),
        timeout_seconds=timeout_seconds,
    )
    model_payload: dict[str, Any] = {}
    if stdout.strip():
        try:
            model_payload = json.loads(stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            model_payload = {}
    if returncode != 0:
        error = model_payload.get("error") if isinstance(model_payload.get("error"), dict) else {}
        return {
            **base,
            "runtime_ready": False,
            "status": "blocked_model_probe_failed",
            "reason": str(error.get("message") or stderr or "sherpa-onnx model probe failed")[-1000:],
            "model_probe_latency_ms": model_latency_ms,
        }
    available = int(model_payload.get("num_speakers") or 0)
    voice_valid = 0 <= speaker < available
    provider_valid = model_payload.get("provider") == provider
    return {
        **base,
        "runtime_ready": bool(voice_valid and provider_valid),
        "status": "ready" if voice_valid and provider_valid else "blocked_model_probe_failed",
        "reason": (
            "sherpa-onnx OfflineTts model load probe passed"
            if voice_valid and provider_valid
            else "speaker or provider validation failed"
        ),
        "voice": {"speaker": speaker, "validated": voice_valid, "available_speakers": available},
        "provider": {"requested": provider, "validated": provider_valid},
        "output": {"mode": "caller_supplied_run_directory", "validated": True},
        "sample_rate": int(model_payload.get("sample_rate") or 0),
        "model_probe_latency_ms": model_latency_ms,
    }


def owner_runtime_doctor(
    *,
    receipt_root: str | Path | None = None,
    sherpa_python: str | Path | None = None,
    sherpa_model_dir: str | Path | None = None,
    sherpa_provider: str = "cpu",
    sherpa_speaker: int = 0,
) -> dict[str, Any]:
    root = Path(receipt_root).expanduser().resolve() if receipt_root else None
    reports: dict[str, dict[str, Any] | None] = {}
    for capability in ("asr", "ocr", "tts", "embedding"):
        path = root / capability / "owner-runtime-run-report.json" if root else None
        if path and path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            jsonschema.validate(payload, _schema("owner-runtime-run-report.v1.schema.json"))
            reports[capability] = payload
        else:
            reports[capability] = None
    sapi_probe = probe_tts_runtime()
    sherpa_probe = probe_sherpa_tts_runtime(
        python_path=sherpa_python,
        model_dir=sherpa_model_dir,
        provider=sherpa_provider,
        speaker=sherpa_speaker,
    )
    truth = build_capability_truth(run_reports=reports, tts_probe=sherpa_probe)
    historical_tts_report = reports.get("tts")
    if not historical_tts_report or historical_tts_report.get("adapter_id") != "mvp-tts":
        historical_tts_report = None
    return {
        "ok": True,
        "status": "ready_with_blocked_capabilities" if not truth["capabilities"]["tts"]["runtime_ready"] else "ready",
        "truth": truth,
        "tts_probe": sherpa_probe,
        "tts_routes": {
            "sherpa_onnx": sherpa_probe,
            "windows_sapi_historical": {
                "probe": sapi_probe,
                "last_run_report": historical_tts_report,
                "does_not_promote_sherpa_route": True,
            },
        },
        "receipt_root_configured": root is not None,
        "provider_called": False,
        "external_io_performed": False,
    }


def write_owner_runtime_doctor(
    output_path: str | Path,
    *,
    receipt_root: str | Path | None = None,
    sherpa_python: str | Path | None = None,
    sherpa_model_dir: str | Path | None = None,
    sherpa_provider: str = "cpu",
    sherpa_speaker: int = 0,
) -> dict[str, Any]:
    result = owner_runtime_doctor(
        receipt_root=receipt_root,
        sherpa_python=sherpa_python,
        sherpa_model_dir=sherpa_model_dir,
        sherpa_provider=sherpa_provider,
        sherpa_speaker=sherpa_speaker,
    )
    _write_json(Path(output_path).expanduser().resolve(), result)
    return result
