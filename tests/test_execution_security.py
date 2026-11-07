from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from model_provider_gateway.consent import build_consent_plan
from model_provider_gateway.execution import build_execution_request, execute_model_request
from model_provider_gateway.ledger import ledger_status
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan


class _FakeTransport:
    mode = "test_fake"

    def __init__(self, *, cost: float | None = None, echo_secret: bool = False) -> None:
        self.cost = cost
        self.echo_secret = echo_secret
        self.calls = 0

    def execute(self, *, profile, capability, instruction, artifacts, secret):
        self.calls += 1
        return {
            "content": secret if self.echo_secret else "synthetic result",
            "usage": {
                "status": "reported",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
            "estimated_cost_usd": self.cost,
        }


def _text_contracts(tmp_path: Path, *, max_calls: int = 1):
    artifact = tmp_path / "fixture.txt"
    artifact.write_text("synthetic", encoding="utf-8")
    profile = build_profile(
        "modelscope-glm-5-2",
        "env:MPG_TEST",
        last_verified="2026-08-05T00:00:00+00:00",
        max_calls=max_calls,
        max_cost_usd=1.0,
    )
    route = build_route_plan("security", "text", [profile], max_calls=max_calls, max_cost_usd=1.0)
    consent = build_consent_plan(
        route,
        [artifact],
        capability="text",
        purpose="synthetic",
        data_type="synthetic",
        max_calls=max_calls,
        max_cost_usd=1.0,
    )
    request = build_execution_request(
        profile,
        route,
        consent,
        consumer_id="qrp",
        task="research_query",
        max_estimated_cost_usd=0.1,
        request_id="security-request-a",
    )
    return profile, route, consent, request


def test_reported_cost_above_reserved_ceiling_fails_closed(tmp_path: Path) -> None:
    profile, route, consent, request = _text_contracts(tmp_path)
    result = execute_model_request(
        request,
        profile=profile,
        route=route,
        consent=consent,
        ledger_path=tmp_path / "ledger.json",
        transport=_FakeTransport(cost=0.11),
        secret_resolver=lambda _ref: "synthetic-secret",
    )
    assert result["status"] == "failed"
    assert result["error_class"] == "cost_limit_exceeded"
    assert result["output"] is None
    assert result["cost"]["estimated_usd"] == 0.11


def test_provider_output_cannot_echo_the_secret_into_receipt(tmp_path: Path) -> None:
    profile, route, consent, request = _text_contracts(tmp_path)
    result = execute_model_request(
        request,
        profile=profile,
        route=route,
        consent=consent,
        ledger_path=tmp_path / "ledger.json",
        transport=_FakeTransport(echo_secret=True),
        secret_resolver=lambda _ref: "synthetic-secret-value",
    )
    serialized = json.dumps(result)
    assert "synthetic-secret-value" not in serialized
    assert result["output"]["text"] == "[REDACTED]"


def test_atomic_quota_allows_only_one_concurrent_reservation(tmp_path: Path) -> None:
    profile, route, consent, first = _text_contracts(tmp_path, max_calls=1)
    second = copy.deepcopy(first)
    second["request_id"] = "security-request-b"
    transport = _FakeTransport()

    def run(request):
        return execute_model_request(
            request,
            profile=profile,
            route=route,
            consent=consent,
            ledger_path=tmp_path / "ledger.json",
            transport=transport,
            secret_resolver=lambda _ref: "synthetic-secret",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [first, second]))
    assert sorted(result["status"] for result in results) == ["blocked", "completed"]
    assert transport.calls == 1
    assert len(ledger_status(tmp_path / "ledger.json")["reservations"]) == 1


def test_mime_spoof_is_rejected_before_execution(tmp_path: Path) -> None:
    fake_png = tmp_path / "not-an-image.png"
    fake_png.write_text("not an image", encoding="utf-8")
    profile = build_profile(
        "siliconflow-glm-4-5v",
        "env:MPG_TEST",
        last_verified="2026-08-05T00:00:00+00:00",
        max_cost_usd=1.0,
    )
    route = build_route_plan("vision", "vision", [profile], max_cost_usd=1.0)
    with pytest.raises(ValueError, match="MIME type is not allowed"):
        build_consent_plan(
            route,
            [fake_png],
            capability="vision",
            purpose="synthetic",
            data_type="image",
            max_cost_usd=1.0,
        )


def test_malformed_artifact_still_returns_redacted_blocked_envelope(tmp_path: Path) -> None:
    profile, route, consent, request = _text_contracts(tmp_path)
    request["artifacts"] = [{"path": "sensitive-local-path"}]
    transport = _FakeTransport()

    result = execute_model_request(
        request,
        profile=profile,
        route=route,
        consent=consent,
        ledger_path=tmp_path / "ledger.json",
        transport=transport,
        secret_resolver=lambda _ref: "test-secret",
    )

    assert result["status"] == "blocked"
    assert result["artifact_refs"] == []
    assert "sensitive-local-path" not in json.dumps(result)
    assert transport.calls == 0
