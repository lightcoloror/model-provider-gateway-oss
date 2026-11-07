from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator

from model_provider_gateway.cli import main
from model_provider_gateway.coverage import (
    build_consumer_capability_matrix,
    classify_consumer_compatibility,
    mock_task_for,
)
from model_provider_gateway.doctor import doctor
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan, validate_route_plan


def _schema() -> dict:
    path = files("model_provider_gateway").joinpath("schemas/consumer-capability-matrix.v1.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_complete_matrix_is_schema_valid_and_evidence_backed() -> None:
    matrix = build_consumer_capability_matrix()
    Draft202012Validator(_schema()).validate(matrix)
    assert [row["consumer_id"] for row in matrix["consumers"]] == [
        "translation_desk",
        "vkp",
        "mvp_video_pipeline",
        "content_studio",
        "research_content_production",
        "qrp",
        "arw",
    ]
    statuses = {row["integration_status"] for row in matrix["consumers"]}
    assert "real_adapter" in statuses
    assert statuses <= {"real_adapter", "fixture_only", "planned", "blocked", "not_applicable"}
    for row in matrix["consumers"]:
        assert row["evidence"]
        assert row["source_ref"]["commit_is_compatibility_gate"] is False
        assert len(row["source_ref"]["adapter_sha256"]) == 64
        assert isinstance(row["source_ref"]["receipt_refs"], list)
        assert row["interface_contract"]["adapter_schema"] == (
            "model_provider_gateway.adapter_contract.v2"
        )
        assert row["compatibility_assessment"]["status"] in {
            "compatible_verified",
            "compatible_provenance_stale",
            "unasserted",
            "real_incompatible",
            "blocked_by_owner",
        }
        assert row["proposal"]["source_module"]
        assert row["proposal"]["target_landing"]
        assert row["proposal"]["verification_command"]
        assert row["proposal"]["implementation_status"] in {
            "no_code_change_required",
            "owner_review_pending",
            "proposal_only",
        }
        assert isinstance(row["proposal"]["required_changes"], list)
        assert set(row["capabilities"]) == {"text", "vision", "embedding", "asr", "ocr", "tts"}


def test_matrix_matches_reviewed_consumer_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "consumer-capability-expectations.v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    matrix = build_consumer_capability_matrix()
    policies = {row["capability"]: row for row in matrix["capability_policies"]}
    assert {name for name, row in policies.items() if row["shared_control_plane"] == "enabled"} == {
        "text", "vision", "embedding", "asr", "ocr", "tts"
    }
    assert matrix["canonical_gateway_commit"] == fixture["canonical_gateway_commit"]
    assert matrix["canonical_gateway_commit_role"] == fixture["canonical_gateway_commit_role"]
    for status in ("enabled", "blocked"):
        assert {
            name for name, row in policies.items() if row["shared_execution"] == status
        } == set(fixture["shared_execution"][status])
    assert {
        name for name, row in policies.items() if row["default_location"] == "local_first"
    } == set(fixture["shared_execution"]["local_first"])

    consumers = {row["consumer_id"]: row for row in matrix["consumers"]}
    for consumer_id, expected in fixture["consumers"].items():
        row = consumers[consumer_id]
        assert row["integration_status"] == expected["integration_status"]
        assert row["compatibility_assessment"]["status"] == expected["compatibility_status"]
        assert row["source_ref"]["adapter_sha256"] == expected["adapter_sha256"]
        for capability, status in expected.items():
            if capability not in {
                "integration_status",
                "compatibility_status",
                "adapter_sha256",
            }:
                assert row["capabilities"][capability]["status"] == status

    assert matrix["compatibility_summary"]["counts"] == {
        "blocked_by_owner": 0,
        "compatible_provenance_stale": 0,
        "compatible_verified": 7,
        "real_incompatible": 0,
        "unasserted": 0,
    }
    assert matrix["compatibility_summary"]["blocking_consumers"] == []


def test_compatibility_classifier_separates_semantics_from_provenance() -> None:
    base = {
        "asserted": True,
        "schema_matches": True,
        "capability_matches": True,
        "safety_matches": True,
        "exact_release_gate": False,
        "provenance_current": True,
    }
    assert classify_consumer_compatibility(**base) == "compatible_verified"
    assert classify_consumer_compatibility(
        **{**base, "provenance_current": False}
    ) == "compatible_provenance_stale"
    assert classify_consumer_compatibility(
        **{**base, "exact_release_gate": True}
    ) == "real_incompatible"
    assert classify_consumer_compatibility(
        **{**base, "schema_matches": False}
    ) == "real_incompatible"
    assert classify_consumer_compatibility(
        **{**base, "asserted": False}
    ) == "unasserted"
    assert classify_consumer_compatibility(
        **{**base, "owner_blocked": True}
    ) == "blocked_by_owner"


def test_capability_policy_matches_doctor_and_local_first_boundary() -> None:
    matrix = build_consumer_capability_matrix()
    policies = {row["capability"]: row for row in matrix["capability_policies"]}
    assert {name for name, row in policies.items() if row["shared_execution"] == "enabled"} == {"text", "vision"}
    assert {name for name, row in policies.items() if row["default_location"] == "local_first"} == {
        "embedding",
        "asr",
        "ocr",
        "tts",
    }
    report = doctor()
    assert set(report["execution"]["control_plane_capabilities"]) == {
        "text", "vision", "embedding", "asr", "ocr", "tts"
    }
    assert set(report["execution"]["enabled_capabilities"]) == {"text", "vision"}
    assert set(report["execution"]["blocked_capabilities"]) == {"embedding", "asr", "ocr", "tts"}
    assert report["consumer_compatibility"]["classification_basis"] == (
        "semantic_contract_not_git_identity"
    )
    assert report["consumer_compatibility"]["blocking_consumers"] == []


def test_clone_safe_profile_doctor_and_route_plan_need_no_real_key() -> None:
    profile = build_profile(
        "modelscope-glm-5-2",
        "env:MPG_SYNTHETIC_ONLY",
        last_verified="2026-08-09T00:00:00+00:00",
        max_calls=1,
        max_cost_usd=0.1,
    )
    report = doctor([profile], check_secrets=False)
    assert report["status"] == "ready"
    route = build_route_plan("synthetic-text", "text", [profile], max_calls=1, max_cost_usd=0.1)
    assert validate_route_plan(route) is route
    assert route["fallback_policy"]["enabled"] is False


def test_consumer_filter_revision_and_mock_task_are_deterministic() -> None:
    first = build_consumer_capability_matrix(consumer_id="vkp")
    second = build_consumer_capability_matrix(consumer_id="vkp")
    assert first == second
    assert len(first["consumers"]) == 1
    assert first["consumers"][0]["consumer_id"] == "vkp"
    assert mock_task_for("qrp", "text") == "research_query"
    assert mock_task_for("qrp", "vision") == "visual_claim_assist"
    assert mock_task_for("mvp_video_pipeline", "vision") == "visual"


def test_matrix_cli_and_consumer_specific_mock_smoke(capsys) -> None:
    assert main(["capability", "matrix", "--consumer-id", "qrp"]) == 0
    matrix = json.loads(capsys.readouterr().out)
    assert matrix["consumers"][0]["integration_status"] == "real_adapter"

    assert (
        main(
            [
                "execution",
                "mock-smoke",
                "--capability",
                "text",
                "--consumer-id",
                "qrp",
                "--task",
                "controlled_summary",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert result["redacted_audit"]["consumer_id"] == "qrp"
    assert result["redacted_audit"]["automatic_fallback_used"] is False


def test_research_vision_mock_smoke_uses_the_reviewed_task(capsys) -> None:
    assert main(
        ["execution", "mock-smoke", "--capability", "vision", "--consumer-id", "qrp", "--task", "visual_claim_assist"]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert result["redacted_audit"]["consumer_id"] == "qrp"
    assert result["redacted_audit"]["automatic_fallback_used"] is False
