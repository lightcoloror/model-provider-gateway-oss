from __future__ import annotations

import copy
import hashlib
import json
import sys
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_provider_gateway.canonical import sha256_json, without_keys
from model_provider_gateway.capability_control import build_owner_capability_plan
from model_provider_gateway.owner_execution import (
    build_capability_truth,
    build_owner_gate,
    build_owner_input_manifest,
    classify_owner_error,
    execute_owner_capability,
    inspect_sherpa_tts_model_bundle,
    probe_sherpa_tts_runtime,
    probe_tts_runtime,
    validate_owner_gate,
    validate_owner_input_manifest,
)


def _fixture(path: Path, text: str = "synthetic gateway fixture") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_manifest_gate_and_plan_bind_the_same_input(tmp_path: Path) -> None:
    source = _fixture(tmp_path / "fixture.txt")
    manifest = build_owner_input_manifest(
        [source], purpose="offline test", data_classification="synthetic_fixture"
    )
    plan = build_owner_capability_plan(
        "embedding",
        "sentence-transformers-local",
        consumer_id="test",
        input_manifest_hash=manifest["manifest_revision"],
    )
    gate = build_owner_gate(plan, manifest)
    assert validate_owner_input_manifest(manifest, artifact_paths=[source]) == manifest
    assert validate_owner_gate(gate, plan=plan, manifest=manifest) == gate
    assert gate["provider_call_authorized"] is False
    assert gate["automatic_fallback_allowed"] is False


def test_hash_and_mime_drift_fail_closed(tmp_path: Path) -> None:
    source = _fixture(tmp_path / "fixture.txt")
    manifest = build_owner_input_manifest(
        [source], purpose="offline test", data_classification="synthetic_fixture"
    )
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="input_hash_mismatch"):
        validate_owner_input_manifest(manifest, artifact_paths=[source])

    source.write_text("synthetic gateway fixture", encoding="utf-8")
    bad_mime = copy.deepcopy(manifest)
    bad_mime["artifacts"][0]["mime_type"] = "image/png"
    bad_mime["manifest_revision"] = sha256_json(without_keys(bad_mime, "manifest_revision"))
    with pytest.raises(ValueError, match="mime_mismatch"):
        validate_owner_input_manifest(bad_mime, artifact_paths=[source])


def test_route_and_consent_drift_fail_closed(tmp_path: Path) -> None:
    source = _fixture(tmp_path / "fixture.txt")
    manifest = build_owner_input_manifest(
        [source], purpose="offline test", data_classification="synthetic_fixture"
    )
    plan = build_owner_capability_plan(
        "embedding",
        "sentence-transformers-local",
        consumer_id="test",
        input_manifest_hash=manifest["manifest_revision"],
    )
    other = copy.deepcopy(manifest)
    other["manifest_revision"] = "b" * 64
    with pytest.raises(ValueError, match="route_drift"):
        build_owner_gate(plan, other)
    gate = build_owner_gate(plan, manifest)
    gate["gate_revision"] = "c" * 64
    with pytest.raises(ValueError, match="consent_invalid"):
        validate_owner_gate(gate, plan=plan, manifest=manifest)


@pytest.mark.parametrize(
    ("kwargs", "category", "retryable"),
    [
        ({"http_status": 429}, "rate_limited", True),
        ({"http_status": 503}, "provider_server_error", True),
        ({"message": "worker timeout"}, "timeout", True),
        ({"message": "model missing"}, "model_missing", False),
        ({"message": "model_hash_mismatch"}, "model_hash_mismatch", False),
        ({"message": "lexicon_missing"}, "lexicon_missing", False),
        ({"message": "invalid_speaker"}, "invalid_speaker", False),
        ({"message": "empty_audio"}, "empty_audio", False),
        ({"message": "module import failed"}, "dependency_missing", False),
        ({"message": "security setting denied voice"}, "runtime_security_context", False),
    ],
)
def test_shared_error_classification(kwargs: dict[str, object], category: str, retryable: bool) -> None:
    error = classify_owner_error(**kwargs)
    assert error["category"] == category
    assert error["retryable"] is retryable
    assert error["automatic_retry_performed"] is False
    assert error["automatic_fallback_performed"] is False


def test_embedding_execution_is_real_local_and_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = tmp_path / "worker.py"
    worker.write_text(
        """  # noqa: E501
import hashlib, json, pathlib, sys
out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
model_revision = sys.argv[sys.argv.index('--model-revision') + 1]
payload = {
    'schema':'model_provider_gateway.embedding_worker_output.v1',
    'item_count':1,
    'dimensions':3,
    'normalized':True,
    'model_revision':model_revision,
    'vectors':[[0.1,0.2,0.3]],
}
out.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
print(json.dumps({'ok': True, 'status': 'completed'}))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = _fixture(tmp_path / "fixture.txt")
    model = tmp_path / "model"
    model.mkdir()
    (model / "modules.json").write_text("{}", encoding="utf-8")

    import model_provider_gateway.owner_execution as module

    original = module._runtime_command

    def fake_runtime_command(capability, adapter_id, *, artifact_paths, output_dir, runtime):  # noqa: ANN001
        assert capability == "embedding"
        assert adapter_id == "sentence-transformers-local"
        model_revision = "d" * 64
        report = output_dir / "embedding-vectors.json"
        return (
            [sys.executable, str(worker), "--output", str(report), "--model-revision", model_revision],
            report,
            "test-isolated-runtime",
            model_revision,
        )

    monkeypatch.setattr(module, "_runtime_command", fake_runtime_command)
    first = execute_owner_capability(
        "embedding",
        "sentence-transformers-local",
        [source],
        output_dir=tmp_path / "run-a",
        consumer_id="test",
        purpose="offline synthetic embedding",
        data_classification="synthetic_fixture",
        runtime={},
        timeout_seconds=20,
    )
    second = execute_owner_capability(
        "embedding",
        "sentence-transformers-local",
        [source],
        output_dir=tmp_path / "run-b",
        consumer_id="test",
        purpose="offline synthetic embedding",
        data_classification="synthetic_fixture",
        runtime={},
        timeout_seconds=20,
    )
    monkeypatch.setattr(module, "_runtime_command", original)
    assert first["report"]["status"] == "completed", first["receipt"]["error"]
    assert first["receipt"] == second["receipt"]
    assert first["report"]["receipt_hash"] == second["report"]["receipt_hash"]
    assert first["receipt"]["provider_called"] is False
    assert first["receipt"]["automatic_fallback_used"] is False


def test_truth_matrix_does_not_turn_fixture_only_or_tts_probe_failure_green() -> None:
    completed = {
        "status": "completed",
        "receipt_hash": "a" * 64,
        "executed_at": "2026-08-12T09:00:00+00:00",
    }
    truth = build_capability_truth(
        run_reports={"asr": completed, "ocr": completed, "embedding": completed},
        tts_probe={"runtime_ready": False, "reason": "native runtime rejected synthesis"},
        now=datetime(2026, 8, 12, 9, 1, tzinfo=UTC),
    )
    assert truth["capabilities"]["asr"]["fresh_e2e_pass"] is True
    assert truth["capabilities"]["embedding"]["real_usage_proof"] is True
    assert truth["capabilities"]["tts"]["status"] == "blocked"
    assert truth["capabilities"]["tts"]["current_online"] is False
    assert all(row["provider_called"] is False for row in truth["capabilities"].values())


def test_truth_matrix_marks_stale_execution_unverified() -> None:
    completed = {
        "status": "completed",
        "receipt_hash": "a" * 64,
        "executed_at": "2026-08-10T09:00:00+00:00",
    }
    truth = build_capability_truth(
        run_reports={"asr": completed},
        now=datetime(2026, 8, 12, 9, 0, tzinfo=UTC),
        freshness_seconds=int(timedelta(hours=24).total_seconds()),
    )
    assert truth["capabilities"]["asr"]["fresh_e2e_pass"] is False
    assert truth["capabilities"]["asr"]["status"] == "configured_unverified"
    assert truth["capabilities"]["asr"]["last_executed_at"] == "2026-08-10T09:00:00+00:00"


def test_tts_probe_is_bounded_and_never_external() -> None:
    result = probe_tts_runtime(timeout_seconds=5)
    assert result["external_io_performed"] is False
    assert result["status"].startswith(("ready", "blocked_"))


def _fake_sherpa_model(path: Path) -> Path:
    path.mkdir()
    (path / "model.onnx").write_bytes(b"synthetic-onnx-fixture")
    (path / "tokens.txt").write_text("_ 0\na 1\n", encoding="utf-8")
    (path / "lexicon.txt").write_text("测 c e\n", encoding="utf-8")
    return path


def test_sherpa_model_bundle_inspection_is_idempotent_and_fail_closed(tmp_path: Path) -> None:
    missing = inspect_sherpa_tts_model_bundle(tmp_path / "missing")
    assert missing["model_bundle_complete"] is False
    assert missing["missing_required_files"] == ["model.onnx", "tokens.txt", "lexicon.txt"]
    model = _fake_sherpa_model(tmp_path / "model")
    first = inspect_sherpa_tts_model_bundle(model)
    second = inspect_sherpa_tts_model_bundle(model)
    assert first == second
    assert first["model_bundle_complete"] is True
    (model / "tokens.txt").write_text("changed", encoding="utf-8")
    assert inspect_sherpa_tts_model_bundle(model)["model_revision"] != first["model_revision"]


def test_sherpa_doctor_distinguishes_runtime_and_model(tmp_path: Path) -> None:
    result = probe_sherpa_tts_runtime(
        python_path=tmp_path / "missing-python.exe",
        model_dir=tmp_path / "missing-model",
    )
    assert result["status"] == "blocked_runtime_missing"
    assert result["python_runtime"]["present"] is False
    assert result["model_bundle"]["model_bundle_complete"] is False
    assert result["external_io_performed"] is False
    assert result["automatic_fallback_performed"] is False


def test_sherpa_worker_is_invoked_by_file_without_importing_gateway_package(tmp_path: Path) -> None:
    import model_provider_gateway.owner_execution as module

    model = _fake_sherpa_model(tmp_path / "model")
    command, _report, _runtime_id, _revision = module._runtime_command(
        "tts",
        "sherpa-onnx-tts",
        artifact_paths=[_fixture(tmp_path / "input.txt", "公开测试")],
        output_dir=tmp_path / "output",
        runtime={
            "sherpa_python": sys.executable,
            "sherpa_model_dir": str(model),
            "sherpa_provider": "cpu",
            "sherpa_speaker": "0",
            "sherpa_speed": "1.0",
            "sherpa_num_threads": "2",
        },
    )
    assert command[1].endswith("owner_workers\\sherpa_onnx_tts_worker.py")
    assert "-m" not in command[:3]


def test_sherpa_tts_execution_receipt_binds_wav_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fixture(tmp_path / "tts.txt", "这是公开的离线语音合成测试。")
    model = _fake_sherpa_model(tmp_path / "model")
    model_revision = inspect_sherpa_tts_model_bundle(model)["model_revision"]

    import model_provider_gateway.owner_execution as module

    def fake_runtime_command(capability, adapter_id, *, artifact_paths, output_dir, runtime):  # noqa: ANN001
        assert capability == "tts"
        assert adapter_id == "sherpa-onnx-tts"
        report = output_dir / "native" / "tts-smoke.json"
        wav = report.with_name("tts-smoke.wav")
        wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(wav), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x01\x00" * 1600)
        payload = {
            "schema": "model_provider_gateway.sherpa_onnx_tts_worker_output.v1",
            "model_revision": model_revision,
            "runtime_version": "1.13.4",
            "provider": "cpu",
            "speaker": 0,
            "speed": 1.0,
            "num_threads": 2,
            "sample_rate": 16000,
            "sample_count": 1600,
            "duration_seconds": 0.1,
            "peak_amplitude": 0.1,
            "rms_amplitude": 0.05,
            "non_silent_ratio": 1.0,
            "wav_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
            "wav_bytes": wav.stat().st_size,
            "real_time_factor": 0.2,
            "quality_status": "technical_smoke_only_human_listening_not_performed",
            "provider_called": False,
            "external_io_performed": False,
        }
        report.write_text(json.dumps(payload), encoding="utf-8")
        return ([sys.executable, "-c", "pass"], report, "sherpa-onnx-test-cpu", model_revision)

    monkeypatch.setattr(module, "_runtime_command", fake_runtime_command)
    runtime = {
        "sherpa_python": sys.executable,
        "sherpa_model_dir": str(model),
        "sherpa_provider": "cpu",
        "sherpa_speaker": "0",
        "sherpa_speed": "1.0",
        "sherpa_num_threads": "2",
    }
    first = execute_owner_capability(
        "tts",
        "sherpa-onnx-tts",
        [source],
        output_dir=tmp_path / "run-a",
        consumer_id="test",
        purpose="offline synthetic TTS",
        data_classification="synthetic_fixture",
        runtime=runtime,
        timeout_seconds=20,
    )
    second = execute_owner_capability(
        "tts",
        "sherpa-onnx-tts",
        [source],
        output_dir=tmp_path / "run-b",
        consumer_id="test",
        purpose="offline synthetic TTS",
        data_classification="synthetic_fixture",
        runtime=runtime,
        timeout_seconds=20,
    )
    assert first["report"]["status"] == "completed", first["receipt"]["error"]
    assert first["receipt"] == second["receipt"]
    assert first["receipt"]["output"]["model_revision"] == model_revision
    assert first["receipt"]["output"]["quality_status"].startswith("technical_smoke_only")
    assert first["report"]["output_refs"] == ["native/tts-smoke.json", "native/tts-smoke.wav"]
    assert first["receipt"]["provider_called"] is False
    assert first["receipt"]["automatic_fallback_used"] is False


def test_sherpa_model_hash_drift_blocks_before_worker(tmp_path: Path) -> None:
    source = _fixture(tmp_path / "tts.txt", "公开测试")
    model = _fake_sherpa_model(tmp_path / "model")
    result = execute_owner_capability(
        "tts",
        "sherpa-onnx-tts",
        [source],
        output_dir=tmp_path / "run",
        consumer_id="test",
        purpose="offline synthetic TTS",
        data_classification="synthetic_fixture",
        runtime={
            "sherpa_python": sys.executable,
            "sherpa_model_dir": str(model),
            "model_revision": "f" * 64,
            "sherpa_provider": "cpu",
            "sherpa_speaker": "0",
            "sherpa_speed": "1.0",
            "sherpa_num_threads": "2",
        },
        timeout_seconds=20,
    )
    assert result["report"]["status"] == "blocked"
    assert result["receipt"]["error"]["category"] == "model_hash_mismatch"
    assert result["receipt"]["error"]["automatic_fallback_performed"] is False
