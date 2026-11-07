from __future__ import annotations

import copy

import jsonschema
import pytest

from model_provider_gateway.capability_control import (
    OWNER_CAPABILITIES,
    VKP_REVIEWED_SOURCE_COMMIT,
    build_owner_capability_plan,
    build_synthetic_owner_receipt,
    embedding_runtime_probe,
    owner_adapter_registry,
    validate_owner_capability_receipt,
)


def test_all_local_first_capabilities_have_shared_owner_contracts() -> None:
    registry = owner_adapter_registry()
    assert set(registry["capabilities"]) == set(OWNER_CAPABILITIES)
    assert {row["capability"] for row in registry["adapters"]} == set(OWNER_CAPABILITIES)
    assert registry["invariants"]["gateway_grants_execution_authority"] is False
    assert registry["invariants"]["automatic_fallback_allowed"] is False
    assert all("source_provenance_status" in row for row in registry["adapters"])
    assert all(row["evidence"] for row in registry["adapters"])


def test_vkp_owner_adapters_share_the_reviewed_source_commit() -> None:
    vkp_rows = [
        row
        for row in owner_adapter_registry()["adapters"]
        if row["repository"] == "lightcoloror/video-knowledge-pipeline"
    ]
    assert len(vkp_rows) == 2
    assert {row["repository_commit"] for row in vkp_rows} == {VKP_REVIEWED_SOURCE_COMMIT}


def test_embedding_runtime_probe_is_local_only_and_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPG_EMBEDDING_PYTHON", raising=False)
    monkeypatch.delenv("MPG_EMBEDDING_MODEL_PATH", raising=False)
    probe = embedding_runtime_probe()
    assert probe["model_import_attempted"] is False
    assert probe["model_download_attempted"] is False
    assert probe["runtime_ready"] is False
    assert probe["status"].startswith(("blocked_", "dependency_metadata_compatible_"))
    embedding = owner_adapter_registry(capability="embedding")["adapters"][0]
    assert embedding["runtime_status"] == probe["status"]
    assert embedding["runtime_probe"] == probe


@pytest.mark.parametrize("capability", OWNER_CAPABILITIES)
def test_synthetic_owner_receipt_is_pathless_and_deterministic(capability: str) -> None:
    first = build_synthetic_owner_receipt(capability)
    second = build_synthetic_owner_receipt(capability)
    assert first == second
    assert first["plan"]["execution_authorized"] is False
    assert first["receipt"]["status"] == "validated_fixture"
    assert first["receipt"]["external_io_performed"] is False
    assert first["receipt"]["provider_called"] is False
    assert "path" not in str(first).lower()


def test_capability_and_adapter_drift_fail_closed() -> None:
    plan = build_owner_capability_plan(
        "asr",
        "vkp-asr",
        consumer_id="test",
        input_manifest_hash="a" * 64,
    )
    with pytest.raises(ValueError, match="capability mismatch"):
        build_owner_capability_plan(
            "ocr",
            "vkp-asr",
            consumer_id="test",
            input_manifest_hash="a" * 64,
        )
    pair = build_synthetic_owner_receipt("asr")
    drifted = copy.deepcopy(pair["receipt"])
    drifted["input_manifest_hash"] = "b" * 64
    with pytest.raises(ValueError, match="drift detected"):
        validate_owner_capability_receipt(drifted, plan=pair["plan"])
    assert plan["owner_native_gate_required"] is True


def test_generated_plan_id_binds_consumer_adapter_and_options() -> None:
    input_hash = "a" * 64
    first = build_owner_capability_plan("ocr", "vkp-ocr", consumer_id="consumer-a", input_manifest_hash=input_hash)
    other_consumer = build_owner_capability_plan(
        "ocr", "vkp-ocr", consumer_id="consumer-b", input_manifest_hash=input_hash
    )
    other_adapter = build_owner_capability_plan(
        "ocr", "ebook-document-ocr", consumer_id="consumer-a", input_manifest_hash=input_hash
    )
    other_options = build_owner_capability_plan(
        "ocr",
        "vkp-ocr",
        consumer_id="consumer-a",
        input_manifest_hash=input_hash,
        options={"fixture_only": True},
    )
    assert len({row["plan_id"] for row in (first, other_consumer, other_adapter, other_options)}) == 4


def test_completed_owner_receipt_requires_native_gate() -> None:
    pair = build_synthetic_owner_receipt("tts")
    completed = copy.deepcopy(pair["receipt"])
    completed["status"] = "completed"
    with pytest.raises(ValueError, match="native owner gate"):
        validate_owner_capability_receipt(completed, plan=pair["plan"])


@pytest.mark.parametrize("field", ["provider_called", "external_io_performed"])
def test_owner_receipt_rejects_remote_or_external_activity(field: str) -> None:
    pair = build_synthetic_owner_receipt("asr")
    unsafe = copy.deepcopy(pair["receipt"])
    unsafe[field] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_owner_capability_receipt(unsafe, plan=pair["plan"])


def test_receipt_status_controls_output_and_error_shape() -> None:
    pair = build_synthetic_owner_receipt("ocr")
    failed_with_output = copy.deepcopy(pair["receipt"])
    failed_with_output["status"] = "failed"
    failed_with_output["error"] = {"category": "synthetic_failure"}
    with pytest.raises(jsonschema.ValidationError):
        validate_owner_capability_receipt(failed_with_output, plan=pair["plan"])

    blocked = copy.deepcopy(pair["receipt"])
    blocked["status"] = "blocked"
    blocked["output"] = None
    blocked["error"] = {"category": "native_gate_required"}
    assert validate_owner_capability_receipt(blocked, plan=pair["plan"])["status"] == "blocked"

    success_with_error = copy.deepcopy(pair["receipt"])
    success_with_error["error"] = {"category": "must_not_coexist"}
    with pytest.raises(jsonschema.ValidationError):
        validate_owner_capability_receipt(success_with_error, plan=pair["plan"])


def test_receipts_never_embed_raw_transcript_text_or_vectors() -> None:
    asr = build_synthetic_owner_receipt("asr")["receipt"]["output"]
    embedding = build_synthetic_owner_receipt("embedding")["receipt"]["output"]
    assert "text" not in asr
    assert "segments" not in asr
    assert "vectors" not in embedding


def test_synthetic_embedding_receipt_can_bind_an_existing_owner_plan() -> None:
    input_hash = "a" * 64
    plan = build_owner_capability_plan(
        "embedding",
        "sentence-transformers-local",
        consumer_id="qrp",
        input_manifest_hash=input_hash,
        options={"purpose": "synthetic candidate only"},
    )

    bundle = build_synthetic_owner_receipt("embedding", plan=plan)

    assert bundle["plan"] == plan
    assert bundle["receipt"]["consumer_id"] == "qrp"
    assert bundle["receipt"]["input_manifest_hash"] == input_hash
    assert bundle["receipt"]["owner_gate_receipt_hash"] is None


def test_synthetic_owner_receipt_rejects_plan_drift() -> None:
    plan = build_owner_capability_plan(
        "embedding",
        "sentence-transformers-local",
        consumer_id="qrp",
        input_manifest_hash="a" * 64,
    )

    with pytest.raises(ValueError, match="capability drift"):
        build_synthetic_owner_receipt("ocr", plan=plan)
