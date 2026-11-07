"""Evidence-backed consumer and capability coverage for the shared gateway."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .adapters import adapter_contract
from .canonical import sha256_json, without_keys

COVERAGE_SCHEMA = "model_provider_gateway.consumer_capability_matrix.v1"
INTEGRATION_STATUSES = {"real_adapter", "fixture_only", "planned", "blocked", "not_applicable"}
COMPATIBILITY_STATUSES = {
    "compatible_verified",
    "compatible_provenance_stale",
    "unasserted",
    "real_incompatible",
    "blocked_by_owner",
}

_SEMANTIC_BASELINE = {
    "http_api_schema": "model_provider_gateway.http.v1",
    "adapter_schema": "model_provider_gateway.adapter_contract.v2",
    "adapter_request_schema": "model_provider_gateway.adapter_request.v2",
    "execution_request_schema": "model_provider_gateway.execution_request.v1",
    "execution_result_schema": "model_provider_gateway.execution_result.v1",
    "consent_schema": "model_provider_gateway.consent_manifest.v2",
    "error_fields": ["error_class", "error_message", "retryable"],
    "silent_fallback_allowed": False,
    "automatic_retry_allowed": False,
}


def classify_consumer_compatibility(
    *,
    asserted: bool,
    schema_matches: bool,
    capability_matches: bool,
    safety_matches: bool,
    exact_release_gate: bool,
    provenance_current: bool,
    owner_blocked: bool = False,
) -> str:
    """Classify runtime semantics independently from commit provenance.

    An exact package version or Git commit gate is a real runtime blocker when
    the consumer actually rejects an otherwise compatible Gateway checkout.
    A stale evidence commit alone is not a semantic incompatibility.
    """

    if owner_blocked:
        return "blocked_by_owner"
    if not asserted:
        return "unasserted"
    if not all((schema_matches, capability_matches, safety_matches)):
        return "real_incompatible"
    if exact_release_gate:
        return "real_incompatible"
    if not provenance_current:
        return "compatible_provenance_stale"
    return "compatible_verified"


def semantic_baseline() -> dict[str, Any]:
    return deepcopy(_SEMANTIC_BASELINE)

_CAPABILITY_POLICIES: list[dict[str, Any]] = [
    {
        "capability": "text",
        "priority": "P0",
        "shared_control_plane": "enabled",
        "shared_execution": "enabled",
        "execution_mode": "provider_route",
        "default_location": "explicit_route",
        "remote_candidate_requires": ["reviewed_preset", "profile", "route", "consent_v2", "budget", "timeout"],
    },
    {
        "capability": "vision",
        "priority": "P0",
        "shared_control_plane": "enabled",
        "shared_execution": "enabled",
        "execution_mode": "provider_route",
        "default_location": "explicit_route",
        "remote_candidate_requires": [
            "reviewed_preset",
            "profile",
            "route",
            "consent_v2",
            "artifact_manifest",
            "budget",
            "timeout",
        ],
    },
    {
        "capability": "embedding",
        "priority": "P1",
        "shared_control_plane": "enabled",
        "shared_execution": "blocked",
        "execution_mode": "owner_adapter",
        "default_location": "local_first",
        "blocked_reason": "remote Provider execution remains blocked; shared owner-adapter contract is enabled",
        "remote_candidate_requires": ["audited_preset", "explicit_route", "consent_v2", "budget", "timeout"],
    },
    {
        "capability": "asr",
        "priority": "P1",
        "shared_control_plane": "enabled",
        "shared_execution": "blocked",
        "execution_mode": "owner_adapter",
        "default_location": "local_first",
        "blocked_reason": (
            "remote Provider execution remains blocked; VKP owner execution is normalized by the shared contract"
        ),
        "remote_candidate_requires": [
            "audited_preset",
            "explicit_route",
            "consent_v2",
            "audio_manifest",
            "budget",
            "timeout",
        ],
    },
    {
        "capability": "ocr",
        "priority": "P2",
        "shared_control_plane": "enabled",
        "shared_execution": "blocked",
        "execution_mode": "owner_adapter",
        "default_location": "local_first",
        "blocked_reason": (
            "remote Provider execution remains blocked; VKP/ebook owner execution is normalized by the shared contract"
        ),
        "remote_candidate_requires": [
            "audited_preset",
            "explicit_route",
            "consent_v2",
            "document_manifest",
            "budget",
            "timeout",
        ],
    },
    {
        "capability": "tts",
        "priority": "P2",
        "shared_control_plane": "enabled",
        "shared_execution": "blocked",
        "execution_mode": "owner_adapter",
        "default_location": "local_first",
        "blocked_reason": (
            "remote Provider execution remains blocked; MVP owner execution is normalized by the shared contract"
        ),
        "remote_candidate_requires": [
            "audited_preset",
            "explicit_route",
            "consent_v2",
            "voice_authorization",
            "budget",
            "timeout",
        ],
    },
]


def _capability(
    status: str,
    location: str,
    *,
    task: str | None = None,
    owner_adapter: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if status not in INTEGRATION_STATUSES:
        raise ValueError(f"unsupported coverage status: {status}")
    result: dict[str, Any] = {"status": status, "execution_location": location}
    if task:
        result["gateway_task"] = task
    if owner_adapter:
        result["owner_adapter"] = owner_adapter
    if reason:
        result["reason"] = reason
    return result


def _source_ref(
    *,
    observed_commit: str,
    adapter_path: str,
    adapter_sha256: str,
    test_path: str,
    test_sha256: str,
    tracked_files_state: str,
    verification_status: str,
    verification_result: str,
    receipt_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "working_tree_snapshot",
        "observed_commit": observed_commit,
        "adapter_path": adapter_path,
        "adapter_sha256": adapter_sha256,
        "test_path": test_path,
        "test_sha256": test_sha256,
        "tracked_files_state": tracked_files_state,
        "commit_is_compatibility_gate": False,
        "verification_status": verification_status,
        "verification_result": verification_result,
        "receipt_refs": list(receipt_refs or []),
    }


def _interface_contract(
    transport: str,
    *,
    http_api: bool,
    owner_gate: str,
) -> dict[str, Any]:
    return {
        "transport": transport,
        "http_api_schema": _SEMANTIC_BASELINE["http_api_schema"] if http_api else None,
        "adapter_schema": _SEMANTIC_BASELINE["adapter_schema"],
        "adapter_request_schema": _SEMANTIC_BASELINE["adapter_request_schema"],
        "execution_request_schema": _SEMANTIC_BASELINE["execution_request_schema"],
        "execution_result_schema": _SEMANTIC_BASELINE["execution_result_schema"],
        "consent_schema": _SEMANTIC_BASELINE["consent_schema"],
        "error_fields": list(_SEMANTIC_BASELINE["error_fields"]),
        "owner_gate": owner_gate,
        "silent_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }


def _compatibility_assessment(
    *,
    asserted: bool = True,
    schema_matches: bool = True,
    capability_matches: bool = True,
    safety_matches: bool = True,
    exact_release_gate: bool = False,
    provenance_current: bool = True,
    owner_blocked: bool = False,
    blocking_reasons: list[str] | None = None,
) -> dict[str, Any]:
    facts = {
        "asserted": asserted,
        "schema_matches": schema_matches,
        "capability_matches": capability_matches,
        "safety_matches": safety_matches,
        "exact_release_gate": exact_release_gate,
        "provenance_current": provenance_current,
        "owner_blocked": owner_blocked,
    }
    return {
        "status": classify_consumer_compatibility(**facts),
        "facts": facts,
        "blocking_reasons": list(blocking_reasons or []),
        "basis": "semantic_contract_plus_current_source_snapshot",
    }


_CONSUMERS: dict[str, dict[str, Any]] = {
    "translation_desk": {
        "consumer": "Translation Desk",
        "repository": "local-fork:PDFMathTranslate-next/PDFMathTranslate-next",
        "observed_commit": "f8dffcf4c3a33b254391d43514439b975ce8d966",
        "integration_status": "fixture_only",
        "adapter_module": "pdf2zh_next.desk.cloud",
        "evidence": [
            "pdf2zh_next/desk/cloud.py",
            "tests/desk/test_workflow.py",
        ],
        "source_ref": _source_ref(
            observed_commit="f8dffcf4c3a33b254391d43514439b975ce8d966",
            adapter_path="pdf2zh_next/desk/cloud.py",
            adapter_sha256="c803ca7167996601917621cf6416163257987d4eb7526b0b9f7273a5954a8bd9",
            test_path="tests/desk/test_workflow.py",
            test_sha256="4ef7ba63a880b694048c04d2cc15d15f1ca0187ac42ebe6847e72f9de00418d5",
            tracked_files_state="modified",
            verification_status="passed",
            verification_result="exact consent, input binding and drift rejection tested with synthetic transport; no live cloud call",
        ),
        "interface_contract": _interface_contract(
            "direct_python", http_api=False, owner_gate="shared_consent"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            "text": _capability("fixture_only", "explicit_route", task="translate_text", reason="cloud transport simulated; real local LM Studio is outside shared execution"),
            **{name: _capability("not_applicable", "unavailable", reason="document parsing/OCR remains with existing local tools")
               for name in ("vision", "embedding", "asr", "ocr", "tts")},
        },
        "proposal": {
            "source_module": "model_provider_gateway.execution",
            "target_landing": "pdf2zh_next/desk/cloud.py",
            "verification_command": "python -m pytest tests/desk/test_workflow.py -q",
            "implementation_status": "no_code_change_required",
            "required_changes": [],
        },
    },
    "vkp": {
        "consumer": "Video Knowledge Pipeline",
        "repository": "lightcoloror/video-knowledge-pipeline",
        "observed_commit": "f5d4b3177dc4a613626a50bb41d2eb1aee4aaa50",
        "integration_status": "real_adapter",
        "adapter_module": "video_knowledge_pipeline.model_provider_gateway_adapter",
        "evidence": [
            "src/video_knowledge_pipeline/model_provider_gateway_adapter.py",
            "tests/test_model_provider_gateway_adapter.py",
        ],
        "source_ref": _source_ref(
            observed_commit="f5d4b3177dc4a613626a50bb41d2eb1aee4aaa50",
            adapter_path="src/video_knowledge_pipeline/model_provider_gateway_adapter.py",
            adapter_sha256="b7232136083f4f5348a49bb7b64943d2dec906616030db3376dd4203eb0af26c",
            test_path="tests/test_model_provider_gateway_adapter.py",
            test_sha256="03ab282b2ab7c5dc4480e4a620f990a8986d4cf440e639ec85af3c89948022c4",
            tracked_files_state="clean",
            verification_status="passed",
            verification_result="10 passed in the current focused offline probe",
        ),
        "interface_contract": _interface_contract(
            "direct_python_projection", http_api=False, owner_gate="vkp_broker"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            "text": _capability("real_adapter", "explicit_local_or_remote", task="text"),
            "vision": _capability("real_adapter", "explicit_local_or_remote", task="visual"),
            "embedding": _capability(
                "planned",
                "local_first",
                owner_adapter="video_knowledge_pipeline.video_structure",
                reason="shot embeddings can be consumed, but no canonical producer adapter is registered",
            ),
            "asr": _capability(
                "real_adapter",
                "local_first",
                owner_adapter="video_knowledge_pipeline.asr_runner",
                reason="shared execution blocked",
            ),
            "ocr": _capability(
                "real_adapter",
                "local_first",
                owner_adapter="video_knowledge_pipeline.ocr_adapter",
                reason="shared execution blocked",
            ),
            "tts": _capability("not_applicable", "unavailable", reason="VKP does not own media narration generation"),
        },
        "proposal": {
            "source_module": "model_provider_gateway.adapters",
            "target_landing": "src/video_knowledge_pipeline/model_provider_gateway_adapter.py",
            "verification_command": "python -m pytest -q tests/test_model_provider_gateway_adapter.py",
            "implementation_status": "no_code_change_required",
            "required_changes": [],
        },
    },
    "mvp_video_pipeline": {
        "consumer": "MVP Video Pipeline",
        "repository": "lightcoloror/mvp-video-pipeline",
        "observed_commit": "7ec7a22b02586e5c447ee37160f18cbde0503e01",
        "integration_status": "real_adapter",
        "adapter_module": "video_creation_pipeline.gateway_consumer",
        "evidence": ["src/video_creation_pipeline/gateway_consumer.py", "tests/test_gateway_consumer.py"],
        "source_ref": _source_ref(
            observed_commit="7ec7a22b02586e5c447ee37160f18cbde0503e01",
            adapter_path="src/video_creation_pipeline/gateway_consumer.py",
            adapter_sha256="42dccdc856f2a7fa2abdce8779650026fd2d9a3efc811169e78b15c35b89c1cc",
            test_path="tests/test_gateway_consumer.py",
            test_sha256="1573591ac17d2ebc9728c92da65659836e5d7dabbe31a504e607c88dea882480",
            tracked_files_state="clean",
            verification_status="passed",
            verification_result=(
                "9 passed and 11 subtests passed in the current focused probe; "
                "owner receipt records 128/128 and local commit 7ec7a22"
            ),
            receipt_refs=[
                "repository:CHANGE-RATIONALE.md#MVP-GATEWAY-SEMCOMPAT-20260812-01",
                "dispatch:MVP-GATEWAY-SEMCOMPAT-20260812-01/receipt:RC-233b90a373454b4389f711f93b5b7826",
            ],
        ),
        "interface_contract": _interface_contract(
            "direct_python_loopback", http_api=False, owner_gate="shared_consent"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            "text": _capability("real_adapter", "explicit_local_or_remote", task="script"),
            "vision": _capability("real_adapter", "explicit_local_or_remote", task="visual"),
            "embedding": _capability(
                "fixture_only",
                "local_first",
                owner_adapter="video_creation_pipeline.material_index",
                reason="material index fixtures exist; shared execution blocked",
            ),
            "asr": _capability(
                "not_applicable",
                "unavailable",
                reason="MVP consumes speech receipts and must not run a second ASR",
            ),
            "ocr": _capability("not_applicable", "unavailable", reason="MVP consumes VKP visual evidence"),
            "tts": _capability(
                "fixture_only",
                "local_first",
                owner_adapter="video_creation_pipeline.tts_pipeline",
                reason="local TTS job contracts exist; shared execution remains blocked",
            ),
        },
        "proposal": {
            "source_module": "model_provider_gateway.adapters",
            "target_landing": "src/video_creation_pipeline/gateway_consumer.py",
            "verification_command": "python -m pytest -q tests/test_gateway_consumer.py",
            "implementation_status": "no_code_change_required",
            "required_changes": [],
        },
    },
    "content_studio": {
        "consumer": "Content Studio",
        "repository": "content-studio-poc",
        "observed_commit": "a19c1b96aacec744164438b4414de8b2f55f191f",
        "integration_status": "real_adapter",
        "adapter_module": "scripts.model_provider_gateway_adapter",
        "evidence": [
            "scripts/model_provider_gateway_adapter.py",
            "schemas/model-provider-gateway-content-assist-receipt.v1.schema.json",
            "tests/test_model_provider_gateway_adapter.py",
            "docs/model-provider-gateway-adapter/VERIFICATION-RECEIPT.md",
        ],
        "source_ref": _source_ref(
            observed_commit="a19c1b96aacec744164438b4414de8b2f55f191f",
            adapter_path="scripts/model_provider_gateway_adapter.py",
            adapter_sha256="719ef501d6b9dbaf64ab315993fb0e0ea80b961235275bead43eeaf00570da70",
            test_path="tests/test_model_provider_gateway_adapter.py",
            test_sha256="07c31d95d2a1eee12beaa43319d07f6065ab3e06dac91cf1340e6fbe39771b7e",
            tracked_files_state="modified",
            verification_status="passed",
            verification_result="15 passed in the current focused offline probe",
            receipt_refs=[
                "repository:docs/model-provider-gateway-adapter/CONTENT-GATEWAY-SEMCOMPAT-20260811-01-completion-receipt.json",
            ],
        ),
        "interface_contract": _interface_contract(
            "http_v1_semantic_front_door", http_api=True, owner_gate="shared_consent"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            "text": _capability("real_adapter", "explicit_route", task="draft_only"),
            "vision": _capability("real_adapter", "explicit_route", task="vision_candidate"),
            "embedding": _capability("not_applicable", "unavailable"),
            "asr": _capability("not_applicable", "unavailable"),
            "ocr": _capability("not_applicable", "unavailable"),
            "tts": _capability("not_applicable", "unavailable"),
        },
        "proposal": {
            "source_module": "model_provider_gateway.execution",
            "target_landing": "scripts/model_provider_gateway_adapter.py",
            "verification_command": (
                "python -B -m unittest discover -s tests -p test_model_provider_gateway_adapter.py -v"
            ),
            "implementation_status": "owner_review_pending",
            "required_changes": [
                "review and commit the existing HTTP v1 semantic compatibility worktree changes",
            ],
        },
    },
    "research_content_production": {
        "consumer": "Research Content Production",
        "repository": "research-content-production",
        "observed_commit": "8de87e69fa2267e6df455bc057842ded431604d6",
        "integration_status": "real_adapter",
        "adapter_module": "research_content_production",
        "evidence": [
            "src/research_content_production.py:bind_model_gateway_receipt_set",
            "tests/test_model_gateway_receipt_binding.py",
            "evidence/AUTONOMOUS2-20260809-CONTENT-GATEWAY-01-completion-receipt.json",
        ],
        "source_ref": _source_ref(
            observed_commit="8de87e69fa2267e6df455bc057842ded431604d6",
            adapter_path="src/research_content_production.py",
            adapter_sha256="130814af16f27ced19c4e52f77a774f9eae95c67aeba055ef16a674de30885d3",
            test_path="tests/test_model_gateway_receipt_binding.py",
            test_sha256="494ce75b498de02af32f8a3a501012b8d6a41f7d685161c23a709fb890b41023",
            tracked_files_state="modified",
            verification_status="passed",
            verification_result="66 passed in the current full offline suite with documented RCP environment roots",
            receipt_refs=[
                "repository:evidence/AUTONOMOUS2-20260809-CONTENT-GATEWAY-01-completion-receipt.json",
            ],
        ),
        "interface_contract": _interface_contract(
            "receipt_orchestrator_http_v1", http_api=True, owner_gate="consumer_receipts_only"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            name: _capability(
                "not_applicable",
                "consumer_receipts_only",
                reason="orchestrator may bind profile/route/consent/result receipts but cannot execute a Provider",
            )
            for name in ("text", "vision", "embedding", "asr", "ocr", "tts")
        },
        "proposal": {
            "source_module": "model_provider_gateway.adapters",
            "target_landing": "src/research_content_production.py",
            "verification_command": (
                "python -B -m unittest discover -s tests -p test_model_gateway_receipt_binding.py -v"
            ),
            "implementation_status": "owner_review_pending",
            "required_changes": [
                "review and commit the existing semantic receipt compatibility worktree changes",
            ],
        },
    },
    "qrp": {
        "consumer": "Question Research POC",
        "repository": "question-research-poc",
        "observed_commit": "41eded3a56b826c198288c6a064d6030df1a96cb",
        "integration_status": "real_adapter",
        "adapter_module": "question_research_poc.model_gateway_bridge",
        "evidence": ["src/question_research_poc/model_gateway_bridge.py", "tests/test_model_gateway_bridge.py"],
        "source_ref": _source_ref(
            observed_commit="41eded3a56b826c198288c6a064d6030df1a96cb",
            adapter_path="src/question_research_poc/model_gateway_bridge.py",
            adapter_sha256="e4db33d8391d689b56c9ac6204c588a6d716b0a5f2809b11d1e6576730b0b455",
            test_path="tests/test_model_gateway_bridge.py",
            test_sha256="d6e633c1caf24d7933f7b006a323a29dc968209df7032eba06559d7be93c31f2",
            tracked_files_state="modified",
            verification_status="passed",
            verification_result=(
                "40 passed in the current bridge and capability-closure focused offline probe"
            ),
            receipt_refs=[
                "workspace-output:RESEARCH-GATEWAY-SEMCOMPAT-20260812-01/completion-receipt.json",
            ],
        ),
        "interface_contract": _interface_contract(
            "delegated_python_via_arw", http_api=False, owner_gate="shared_consent"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            "text": _capability("real_adapter", "explicit_route", task="research_query"),
            "vision": _capability("real_adapter", "explicit_route", task="visual_claim_assist"),
            "embedding": _capability(
                "fixture_only",
                "local_first",
                owner_adapter="sentence-transformers-local",
                reason="owner receipt contract is reviewed; runtime readiness remains doctor-controlled",
            ),
            "asr": _capability("not_applicable", "unavailable"),
            "ocr": _capability("not_applicable", "unavailable"),
            "tts": _capability("not_applicable", "unavailable"),
        },
        "proposal": {
            "source_module": "model_provider_gateway.execution",
            "target_landing": "src/question_research_poc/model_gateway_bridge.py",
            "verification_command": "python -m pytest -q tests/test_model_gateway_bridge.py",
            "implementation_status": "owner_review_pending",
            "required_changes": [
                "review and commit the existing semantic receipt and task compatibility worktree changes",
            ],
        },
    },
    "arw": {
        "consumer": "AI Research Workflow",
        "repository": "ai-research-workflow",
        "observed_commit": "7aad29b1e9b8284d92d7421be504d6f224ee5ecc",
        "integration_status": "real_adapter",
        "adapter_module": "research_workflow.model_gateway",
        "evidence": ["src/research_workflow/model_gateway.py", "tests/test_model_gateway.py"],
        "source_ref": _source_ref(
            observed_commit="7aad29b1e9b8284d92d7421be504d6f224ee5ecc",
            adapter_path="src/research_workflow/model_gateway.py",
            adapter_sha256="a4e078ebd0829aa4d51ffea51670ffbd0763c5a43a2a4494ae072459972c463b",
            test_path="tests/test_model_gateway.py",
            test_sha256="56b8e134d2451e9a3f0e16620a7ffb2345d6cbb982cad8170a640341b7fd283f",
            tracked_files_state="modified",
            verification_status="passed",
            verification_result=(
                "38 passed and 1 explicit local-runtime skip in the current model and embedding focused probe"
            ),
            receipt_refs=[
                "workspace-output:RESEARCH-GATEWAY-SEMCOMPAT-20260812-01/completion-receipt.json",
            ],
        ),
        "interface_contract": _interface_contract(
            "direct_python", http_api=False, owner_gate="shared_consent"
        ),
        "compatibility_assessment": _compatibility_assessment(),
        "capabilities": {
            "text": _capability("real_adapter", "explicit_route", task="research_query"),
            "vision": _capability("real_adapter", "explicit_route", task="visual_claim_assist"),
            "embedding": _capability(
                "fixture_only",
                "local_first",
                owner_adapter="sentence-transformers-local",
                reason="owner receipt contract is reviewed; runtime readiness remains doctor-controlled",
            ),
            "asr": _capability("not_applicable", "unavailable"),
            "ocr": _capability("not_applicable", "unavailable"),
            "tts": _capability("not_applicable", "unavailable"),
        },
        "proposal": {
            "source_module": "model_provider_gateway.execution",
            "target_landing": "src/research_workflow/model_gateway.py",
            "verification_command": "python -m pytest -q tests/test_model_gateway.py",
            "implementation_status": "owner_review_pending",
            "required_changes": [
                "review and commit the existing semantic compatibility worktree changes",
            ],
        },
    },
}


def capability_policies() -> list[dict[str, Any]]:
    return deepcopy(_CAPABILITY_POLICIES)


def consumer_ids() -> list[str]:
    return list(_CONSUMERS)


def compatibility_summary(*, consumers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = consumers or [
        {"consumer_id": key, **deepcopy(value)} for key, value in _CONSUMERS.items()
    ]
    counts = {status: 0 for status in sorted(COMPATIBILITY_STATUSES)}
    blocking_consumers = []
    for row in rows:
        status = row["compatibility_assessment"]["status"]
        if status not in COMPATIBILITY_STATUSES:
            raise ValueError(f"unsupported compatibility status: {status}")
        counts[status] += 1
        if status in {"real_incompatible", "blocked_by_owner", "unasserted"}:
            blocking_consumers.append(row["consumer_id"])
    return {
        "classification_basis": "semantic_contract_not_git_identity",
        "counts": counts,
        "blocking_consumers": blocking_consumers,
        "all_consumers_asserted": counts["unasserted"] == 0,
        "commit_or_hash_is_runtime_gate": False,
    }


def mock_task_for(consumer_id: str, capability: str) -> str:
    contract = adapter_contract(consumer_id)
    for task, task_contract in contract["tasks"].items():
        if task_contract.get("execution_status") == "enabled" and capability in task_contract.get("capabilities", []):
            return task
    raise ValueError(f"consumer has no executable {capability} task: {consumer_id}")


def build_consumer_capability_matrix(*, consumer_id: str | None = None) -> dict[str, Any]:
    if consumer_id and consumer_id not in _CONSUMERS:
        raise ValueError(f"unknown coverage consumer: {consumer_id}")
    selected = [consumer_id] if consumer_id else consumer_ids()
    consumers = []
    for key in selected:
        row = {"consumer_id": key, **deepcopy(_CONSUMERS[key])}
        row["gateway_contract"] = adapter_contract(key)
        consumers.append(row)
    matrix: dict[str, Any] = {
        "schema": COVERAGE_SCHEMA,
        "gateway_version": "0.3.0",
        "canonical_gateway_commit": "c5f3ec49644453e0cddb56350e3b243b49e0f7da",
        "canonical_gateway_commit_role": "historical_provenance_evidence_only",
        "semantic_baseline": semantic_baseline(),
        "capability_policies": capability_policies(),
        "consumers": consumers,
        "compatibility_summary": compatibility_summary(consumers=consumers),
        "invariants": {
            "provider_protocol_adapter": "litellm_only",
            "arbitrary_urls_allowed": False,
            "silent_fallback_allowed": False,
            "automatic_retry_allowed": False,
            "saving_profile_is_consent": False,
            "real_provider_called": False,
            "file_upload_requires_exact_manifest_and_user_approval": True,
            "owner_adapter_execution_requires_native_gate": True,
            "normalized_owner_receipt_grants_execution_authority": False,
        },
    }
    matrix["matrix_revision"] = sha256_json(without_keys(matrix, "matrix_revision"))
    return matrix
