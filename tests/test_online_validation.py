from __future__ import annotations

import json
import uuid
from hashlib import sha256
from pathlib import Path

import jsonschema
import pytest

from model_provider_gateway.canonical import sha256_json, without_keys
from model_provider_gateway.cli import main
from model_provider_gateway.consent import build_consent_plan, validate_consent
from model_provider_gateway.online_validation import (
    DEFAULT_CANDIDATES,
    prepare_online_validation_package,
    validate_online_validation_package,
)
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan, route_revision

GENERATED_AT = "2026-08-11T09:23:35+08:00"


@pytest.fixture
def work_path() -> Path:
    root = Path(__file__).resolve().parents[1] / ".test-runs" / f"online-validation-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _fixture(tmp_path: Path) -> Path:
    path = tmp_path / "public.txt"
    path.write_text(
        "请将这句公开测试文本压缩为不超过15字的摘要：保险资料整理测试，不包含任何真实客户信息。\n",
        encoding="utf-8",
    )
    return path


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def test_preparation_package_is_non_executable_secretless_and_idempotent(work_path: Path) -> None:
    output = work_path / "package"
    first = prepare_online_validation_package(_fixture(work_path), output, generated_at=GENERATED_AT)
    first_hashes = _tree_hashes(output)
    second = prepare_online_validation_package(_fixture(work_path), output, generated_at=GENERATED_AT)

    assert first == second
    assert first_hashes == _tree_hashes(output)
    assert first["selected_candidate_id"] is None
    assert first["candidate_count"] == len(DEFAULT_CANDIDATES)
    assert all(item["selection_status"] == "candidate_unselected" for item in first["candidates"])
    assert all(item["credential_status"] == "not_checked" for item in first["candidates"])
    assert all(item["fallback_enabled"] is False for item in first["candidates"])
    assert first["safety"] == {
        "real_provider_call_performed": False,
        "upload_performed": False,
        "cost_incurred_usd": 0.0,
        "credential_read_performed": False,
        "active_consent_created": False,
        "automatic_retry_enabled": False,
        "automatic_fallback_enabled": False,
    }
    result = validate_online_validation_package(output / "validation-package.json")
    assert result["ok"] is True
    assert result["status"] == "ready_for_explicit_user_selection"

    for candidate in first["candidates"]:
        consent_draft = json.loads((output / candidate["consent_draft_path"]).read_text(encoding="utf-8"))
        request_draft = json.loads(
            (output / candidate["execution_request_draft_path"]).read_text(encoding="utf-8")
        )
        assert consent_draft["executable"] is False
        assert request_draft["executable"] is False
        assert "consent_sha256" not in consent_draft
        assert "consent_hash" not in request_draft


def test_preparation_package_rejects_fixture_route_and_draft_drift(work_path: Path) -> None:
    fixture = _fixture(work_path)
    output = work_path / "package"
    package = prepare_online_validation_package(fixture, output, generated_at=GENERATED_AT)

    (output / package["fixture"]["relative_path"]).write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="fixture drift"):
        validate_online_validation_package(output / "validation-package.json")

    package = prepare_online_validation_package(fixture, output, generated_at=GENERATED_AT)
    candidate = package["candidates"][0]
    route_path = output / candidate["route_path"]
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["fallback_policy"]["enabled"] = True
    route["fallback_policy"]["ordered"] = True
    route["route_revision"] = route_revision(route)
    route_path.write_text(json.dumps(route), encoding="utf-8")
    with pytest.raises(ValueError, match="fallback disabled"):
        validate_online_validation_package(output / "validation-package.json")

    package = prepare_online_validation_package(fixture, output, generated_at=GENERATED_AT)
    candidate = package["candidates"][0]
    draft_path = output / candidate["consent_draft_path"]
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["purpose"] = "expanded purpose"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="consent draft drift"):
        validate_online_validation_package(output / "validation-package.json")

    package = prepare_online_validation_package(fixture, output, generated_at=GENERATED_AT)
    candidate = package["candidates"][0]
    request_path = output / candidate["execution_request_draft_path"]
    request_draft = json.loads(request_path.read_text(encoding="utf-8"))
    request_draft["instruction"] = "expanded instruction"
    request_path.write_text(json.dumps(request_draft), encoding="utf-8")
    with pytest.raises(ValueError, match="execution request draft drift"):
        validate_online_validation_package(output / "validation-package.json")

    package = prepare_online_validation_package(fixture, output, generated_at=GENERATED_AT)
    package["candidates"][0]["profile_path"] = "../escaped-profile.json"
    package["package_sha256"] = sha256_json(without_keys(package, "package_sha256"))
    package_path = output / "validation-package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes its root"):
        validate_online_validation_package(package_path)


def test_preparation_package_rejects_selected_candidate_even_with_rehashed_package(work_path: Path) -> None:
    output = work_path / "package"
    package = prepare_online_validation_package(_fixture(work_path), output, generated_at=GENERATED_AT)
    package["selected_candidate_id"] = package["candidates"][0]["candidate_id"]
    package["package_sha256"] = sha256_json(without_keys(package, "package_sha256"))
    package_path = output / "validation-package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(jsonschema.ValidationError, match="not of type 'null'"):
        validate_online_validation_package(package_path)


def test_online_validation_cli_prepare_and_validate(work_path: Path, capsys) -> None:
    output = work_path / "package"
    fixture = _fixture(work_path)
    assert (
        main(
            [
                "online-validation",
                "prepare",
                str(fixture),
                str(output),
                "--generated-at",
                GENERATED_AT,
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["real_provider_call_performed"] is False
    assert prepared["active_consent_created"] is False

    assert main(["online-validation", "validate", str(output / "validation-package.json")]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["status"] == "ready_for_explicit_user_selection"


def test_canonical_consent_negatives_and_validation_cli(work_path: Path, capsys) -> None:
    fixture = _fixture(work_path)
    profile = build_profile(
        "ark-deepseek-v4-flash",
        "env:ARK_API_KEY",
        timeout_seconds=30,
        max_attempts=1,
        max_calls=1,
        max_cost_usd=0.01,
        last_verified=GENERATED_AT,
    )
    route = build_route_plan("controlled-validation", "text", [profile], max_calls=1, max_cost_usd=0.01)
    consent = build_consent_plan(
        route,
        [fixture],
        capability="text",
        purpose="synthetic transport validation",
        data_type="public_synthetic_text",
        max_calls=1,
        max_cost_usd=0.01,
    )
    route_path = work_path / "route.json"
    consent_path = work_path / "consent.json"
    route_path.write_text(json.dumps(route), encoding="utf-8")
    consent_path.write_text(json.dumps(consent), encoding="utf-8")

    assert main(["route", "validate", str(route_path)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["consent", "validate", str(consent_path), str(route_path)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    changed_route = json.loads(json.dumps(route))
    changed_route["route_id"] = "drifted-route"
    changed_route["route_revision"] = route_revision(changed_route)
    with pytest.raises(ValueError, match="route drift"):
        validate_consent(consent, route=changed_route)

    changed_consent = json.loads(json.dumps(consent))
    changed_consent["purpose"] = "expanded purpose"
    with pytest.raises(ValueError, match="manifest drift"):
        validate_consent(changed_consent, route=route)

    fixture.write_text("artifact drift", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact manifest drift"):
        validate_consent(consent, route=route)

    second = build_profile(
        "groq-qwen3-6-27b",
        "env:GROQ_API_KEY",
        timeout_seconds=30,
        max_attempts=1,
        max_calls=1,
        max_cost_usd=0.01,
        last_verified=GENERATED_AT,
    )
    with pytest.raises(ValueError, match="explicit fallback"):
        build_route_plan("forbidden-fallback", "text", [profile, second], fallback_enabled=False)
