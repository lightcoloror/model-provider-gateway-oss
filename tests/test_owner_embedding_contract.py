"""Offline contracts only: no model imports, processes or network requests."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from model_provider_gateway import owner_execution as owner
from model_provider_gateway.canonical import sha256_json, without_keys
from model_provider_gateway.capability_control import build_owner_capability_plan
from model_provider_gateway.owner_workers import sentence_transformers_worker as worker


@pytest.fixture(autouse=True)
def deny_execution(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("real process/network forbidden in contract tests")

    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(owner, "_run_command", denied)


def private_case(tmp_path):
    source = tmp_path / "derived.txt"
    source.write_text("合成文本，不含客户资料", encoding="utf-8")
    manifest = owner.build_owner_input_manifest(
        [source], purpose="synthetic private-contract test", data_classification="user_authorized_local"
    )
    plan = build_owner_capability_plan(
        "embedding",
        "sentence-transformers-local",
        consumer_id="test",
        input_manifest_hash=manifest["manifest_revision"],
    )
    grant = owner.build_owner_local_authorization(
        manifest,
        artifact_paths=[source],
        derived_root=tmp_path,
        consumer_id="test",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        user_confirmed=True,
    )
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(grant), encoding="utf-8")
    kwargs = dict(
        local_authorization_path=path,
        local_authorization_revision=grant["authorization_revision"],
        artifact_paths=[source],
    )
    return source, manifest, plan, grant, path, kwargs


def test_private_explicit_gate_and_v1_unchanged(tmp_path):
    source, manifest, plan, grant, path, kwargs = private_case(tmp_path)
    gate = owner.build_owner_gate(plan, manifest, **kwargs)
    assert gate["schema"].endswith(".v2") and gate["scope"] == "user_authorized_local"
    assert owner.validate_owner_gate(gate, plan=plan, manifest=manifest, artifact_paths=[source]) == gate
    with pytest.raises(ValueError, match="explicit local authorization"):
        owner.build_owner_gate(plan, manifest)
    with pytest.raises(ValueError, match="approval"):
        owner.build_owner_local_authorization(
            manifest, artifact_paths=[source], derived_root=tmp_path, consumer_id="test", expires_at=grant["expires_at"]
        )
    fixture = owner.build_owner_input_manifest([source], purpose="fixture", data_classification="synthetic_fixture")
    fixture_plan = build_owner_capability_plan(
        "embedding", "sentence-transformers-local", consumer_id="test", input_manifest_hash=fixture["manifest_revision"]
    )
    old = owner.build_owner_gate(fixture_plan, fixture)
    assert old["schema"].endswith(".v1") and "local_consent_ref" not in old
    with pytest.raises(jsonschema.ValidationError):
        owner.validate_owner_gate(old, plan=plan, manifest=manifest, artifact_paths=[source])
    with pytest.raises(jsonschema.ValidationError):
        owner.validate_owner_gate(gate, plan=fixture_plan, manifest=fixture, artifact_paths=[source])


@pytest.mark.parametrize(
    "change",
    [
        "revoke",
        "expire",
        "purpose",
        "consumer",
        "external",
        "revision",
        "delete",
        "source",
        "same_bytes_other_path",
        "root",
        "plan",
    ],
)
def test_private_drift_and_revocation(tmp_path, change):
    source, manifest, plan, grant, path, kwargs = private_case(tmp_path)
    gate = owner.build_owner_gate(plan, manifest, **kwargs)
    if change == "delete":
        # Simulate deletion without deleting a fixture file.
        gate["local_consent_ref"]["path"] = str(tmp_path / "missing.json")
    elif change == "source":
        source.write_text("changed", encoding="utf-8")
    elif change == "same_bytes_other_path":
        other = tmp_path / "other.txt"
        other.write_bytes(source.read_bytes())
        source = other
    elif change == "plan":
        plan["consumer_id"] = "other"
    else:
        if change == "revoke":
            grant["revoked"] = True
        if change == "expire":
            grant["expires_at"] = "2000-01-01T00:00:00+00:00"
        if change == "purpose":
            grant["purpose"] = "other"
        if change == "consumer":
            grant["consumer_id"] = "other"
        if change == "external":
            grant["external_io_authorized"] = True
        if change == "root":
            grant["derived_root"] = str(tmp_path / "missing-root")
        if change != "revision":
            grant["authorization_revision"] = sha256_json(without_keys(grant, "authorization_revision"))
        else:
            grant["authorization_revision"] = "0" * 64
        path.write_text(json.dumps(grant), encoding="utf-8")
    with pytest.raises((ValueError, OSError, jsonschema.ValidationError)):
        owner.validate_owner_gate(gate, plan=plan, manifest=manifest, artifact_paths=[source])


def test_private_boundaries_and_before_execution(tmp_path, monkeypatch):
    source, manifest, plan, grant, path, kwargs = private_case(tmp_path)
    outside = tmp_path / "subdir"
    outside.mkdir()
    with pytest.raises(ValueError, match="boundary"):
        owner.build_owner_local_authorization(
            manifest,
            artifact_paths=[source],
            derived_root=outside,
            consumer_id="test",
            expires_at=grant["expires_at"],
            user_confirmed=True,
        )
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == source)
    with pytest.raises(ValueError, match="reparse"):
        owner.build_owner_gate(plan, manifest, **kwargs)


def test_no_grant_execution_never_prepares_worker(tmp_path, monkeypatch):
    source, manifest, plan, grant, path, kwargs = private_case(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("runtime not allowed")

    monkeypatch.setattr(owner, "_runtime_command", forbidden)
    with pytest.raises(ValueError, match="explicit local authorization"):
        owner.execute_owner_capability(
            "embedding",
            "sentence-transformers-local",
            [source],
            output_dir=tmp_path / "out",
            consumer_id="test",
            purpose=manifest["purpose"],
            data_classification="user_authorized_local",
            runtime={},
            timeout_seconds=1,
        )
    assert not (tmp_path / "out").exists()


class Array:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class FakeTokenizer:
    def __call__(self, texts, **kwargs):
        assert kwargs == dict(padding=False, truncation=False, add_special_tokens=True)
        return {"input_ids": [[101] + [ord(c) for c in t] + [102] for t in texts]}


class FakeModel:
    max_seq_length = 8
    tokenizer = FakeTokenizer()

    def _first_module(self):
        return SimpleNamespace(tokenizer=self.tokenizer, do_lower_case=False)

    def tokenize(self, texts):
        ids = self.tokenizer([t.strip() for t in texts], padding=False, truncation=False, add_special_tokens=True)[
            "input_ids"
        ]
        ids = [row if len(row) <= self.max_seq_length else row[: self.max_seq_length - 1] + [102] for row in ids]
        return {"input_ids": Array(ids), "attention_mask": Array([[1] * len(row) for row in ids])}


@pytest.mark.parametrize(
    "texts,counts,truncated",
    [(["甲", "乙丙"], [3, 4], False), (["一二三四五六"], [8], False), (["一二三四五六末尾答案"], [12], True)],
)
def test_real_tokenizer_path_contract(texts, counts, truncated):
    result = worker.measure_tokens(FakeModel(), texts)
    assert [row["raw_token_count"] for row in result["items"]] == counts
    assert any(row["truncated"] for row in result["items"]) is truncated
    assert result["coverage_status"] == ("truncated_rejected" if truncated else "complete")
    assert [row["index"] for row in result["items"]] == list(range(len(texts)))


def output_payload(texts):
    return {
        "schema": worker.OUTPUT_SCHEMA,
        "model_revision": "a" * 64,
        "item_count": len(texts),
        "dimensions": 2,
        "normalized": True,
        "vectors": [[1.0, 0.0] for _ in texts],
        "token_attestation": worker.measure_tokens(FakeModel(), texts),
        "execution_identity": {
            "model_class": "fixture.Mock",
            "model_actual": "local-modules-sha256:" + "b" * 64,
            "identity_scope": "local_modules_config_not_full_weights",
            "worker_sha256": hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
            "versions": {key: "mock" for key in worker._versions()},
        },
    }


def test_embedding_receipt_keeps_proof(tmp_path):
    texts = ["甲", "乙"]
    payload = output_payload(texts)
    path = tmp_path / "vectors.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = owner._embedding_output(path, "a" * 64, texts=texts)
    assert result["coverage_status"] == "complete"
    assert result["coverage_proof_sha256"] == sha256_json(payload["token_attestation"])
    payload["schema"] = "model_provider_gateway.embedding_worker_output.v1"
    del payload["token_attestation"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert owner._embedding_output(path, "a" * 64, texts=texts)["coverage_status"] == "unproven_legacy_worker"


@pytest.mark.parametrize(
    "change",
    ["missing", "truncated", "hash", "order", "max", "count", "model", "worker", "versions", "nan", "bool_count"],
)
def test_worker_attestation_negative(tmp_path, change):
    payload = output_payload(["甲", "乙"])
    att = payload["token_attestation"]
    if change == "missing":
        del payload["token_attestation"]
    if change == "truncated":
        att["items"][0]["truncated"] = True
    if change == "hash":
        att["items"][0]["text_sha256"] = "f" * 64
    if change == "order":
        att["items"].reverse()
    if change == "max":
        att["max_seq_length"] = 1
    if change == "count":
        att["items"][0]["effective_token_count"] = 1
    if change == "model":
        payload["model_revision"] = "f" * 64
    if change == "worker":
        payload["execution_identity"]["worker_sha256"] = "f" * 64
    if change == "versions":
        payload["execution_identity"]["versions"] = {}
    if change == "nan":
        payload["vectors"][0][0] = float("nan")
    if change == "bool_count":
        att["items"][0]["raw_token_count"] = True
    path = tmp_path / "vectors.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        owner._embedding_output(path, "a" * 64, texts=["甲", "乙"])


def test_worker_main_rejects_long_before_encode(tmp_path, monkeypatch):
    import sys

    class Model(FakeModel):
        def __init__(self, *args, **kwargs):
            assert kwargs["local_files_only"] is True

        def encode(self, *args, **kwargs):
            raise AssertionError("long input must never encode")

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    (tmp_path / "modules.json").write_text("{}")
    request, output = tmp_path / "input.json", tmp_path / "output.json"
    request.write_text(json.dumps({"schema": worker.INPUT_SCHEMA, "texts": ["一二三四五六末尾答案"]}), encoding="utf-8")
    assert (
        worker.main(
            ["--model", str(tmp_path), "--input", str(request), "--output", str(output), "--model-revision", "a" * 64]
        )
        == 2
    )
    report = json.loads(output.read_text())
    assert report["token_attestation"]["coverage_status"] == "truncated_rejected"
    assert "vectors" not in report


@pytest.mark.parametrize("when", ["success", "before", "during", "legacy", "model"])
def test_execution_pipeline_with_inprocess_mock(tmp_path, monkeypatch, when):
    source, manifest, plan, grant, path, kwargs = private_case(tmp_path)
    calls = []
    text = source.read_text(encoding="utf-8")
    # A short synthetic input under the mock model's six-character content limit.
    source.write_text("合成文本", encoding="utf-8")
    manifest = owner.build_owner_input_manifest(
        [source], purpose=manifest["purpose"], data_classification="user_authorized_local"
    )
    grant = owner.build_owner_local_authorization(
        manifest,
        artifact_paths=[source],
        derived_root=tmp_path,
        consumer_id="test",
        expires_at=grant["expires_at"],
        user_confirmed=True,
    )
    path.write_text(json.dumps(grant), encoding="utf-8")
    original = source.read_bytes()

    def revoke():
        grant["revoked"] = True
        grant["authorization_revision"] = sha256_json(without_keys(grant, "authorization_revision"))
        path.write_text(json.dumps(grant), encoding="utf-8")

    def prepare(capability, adapter_id, *, artifact_paths, output_dir, runtime):
        payload = output_payload([source.read_text(encoding="utf-8")])
        if when == "legacy":
            payload["schema"] = "model_provider_gateway.embedding_worker_output.v1"
            del payload["token_attestation"]
        if when == "model":
            payload["model_revision"] = "b" * 64
        report = output_dir / "vectors.json"
        report.write_text(json.dumps(payload), encoding="utf-8")
        if when == "before":
            revoke()
        return ["mock-not-executed"], report, "mock", "a" * 64

    def execute(*args, **kwargs):
        calls.append("mock")
        if when == "during":
            revoke()
        return 0, "", "", 1

    monkeypatch.setattr(owner, "_runtime_command", prepare)
    monkeypatch.setattr(owner, "_run_command", execute)
    result = owner.execute_owner_capability(
        "embedding",
        "sentence-transformers-local",
        [source],
        output_dir=tmp_path / "result",
        consumer_id="test",
        purpose=manifest["purpose"],
        data_classification="user_authorized_local",
        runtime={},
        timeout_seconds=1,
        local_authorization_path=path,
        local_authorization_revision=grant["authorization_revision"],
    )
    assert result["report"]["status"] == ("completed" if when == "success" else "blocked")
    assert result["report"]["runtime"]["automatic_retry_count"] == 0
    assert source.read_bytes() == original
    assert len(calls) == (0 if when == "before" else 1)
    if when == "success":
        assert result["receipt"]["output"]["coverage_status"] == "complete"
        assert text not in json.dumps(result["receipt"], ensure_ascii=False)


def test_worker_main_success_uses_measured_text_and_empty_prompt(tmp_path, monkeypatch):
    import sys

    calls = []

    class Model(FakeModel):
        def __init__(self, *args, **kwargs):
            assert kwargs == {"local_files_only": True, "device": "cpu"}

        def encode(self, texts, **kwargs):
            assert kwargs == {"convert_to_numpy": True, "normalize_embeddings": True, "prompt": ""}
            calls.extend(texts)
            result = Array([[1.0, 0.0] for _ in texts])
            result.shape = (len(texts), 2)
            return result

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    monkeypatch.setattr(
        worker,
        "_versions",
        lambda: dict.fromkeys(
            ["sentence-transformers", "transformers", "tokenizers", "huggingface-hub", "torch"], "mock"
        ),
    )
    (tmp_path / "modules.json").write_text("{}")
    request, output = tmp_path / "input.json", tmp_path / "output.json"
    request.write_text(json.dumps({"schema": worker.INPUT_SCHEMA, "texts": ["甲", "乙"]}), encoding="utf-8")
    assert (
        worker.main(
            ["--model", str(tmp_path), "--input", str(request), "--output", str(output), "--model-revision", "a" * 64]
        )
        == 0
    )
    assert calls == ["甲", "乙"]
    assert owner._embedding_output(output, "a" * 64, texts=calls)["coverage_status"] == "complete"


def test_token_preprocessor_mismatch_fails_closed():
    class DifferentModel(FakeModel):
        def tokenize(self, texts):
            result = super().tokenize(texts)
            result["input_ids"].values[0][1] = 999
            return result

    with pytest.raises(ValueError, match="preprocessing mismatch"):
        worker.measure_tokens(DifferentModel(), ["甲"])
