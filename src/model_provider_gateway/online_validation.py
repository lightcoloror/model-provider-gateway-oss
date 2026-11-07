"""Prepare non-executable, secretless online Provider approval packages.

This module only composes existing Catalog/Profile/Route/Consent primitives.
It never resolves credentials, creates an executable consent record, or calls a
Provider.  A later, explicit operator approval is required to materialize one
candidate with the canonical consent and execution CLIs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema

from .artifacts import build_artifact_manifest, input_manifest_hash, public_artifact_refs
from .audit import assert_secretless
from .canonical import sha256_file, sha256_json, without_keys
from .catalog import find_model_preset
from .profile import build_profile, validate_profile
from .route import build_route_plan, validate_route_plan

PACKAGE_SCHEMA = "model_provider_gateway.online_provider_validation_package.v1"
CONSENT_DRAFT_SCHEMA = "model_provider_gateway.consent_approval_draft.v1"
EXECUTION_DRAFT_SCHEMA = "model_provider_gateway.execution_request_draft.v1"
DEFAULT_CANDIDATES = (
    "ark-deepseek-v4-flash",
    "groq-qwen3-6-27b",
    "modelscope-glm-5-2",
    "siliconflow-qwen3-5-4b",
)
DEFAULT_PURPOSE = (
    "Compress the supplied public synthetic test text to no more than 15 Chinese characters. "
    "The output is transport acceptance evidence only and is not factual evidence."
)
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_CALLS = 1
DEFAULT_MAX_COST_USD = 0.01
DEFAULT_TTL_SECONDS = 3600


def _schema() -> dict[str, Any]:
    path = Path(__file__).with_name("schemas") / "online-provider-validation-package.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, _json_text(value))


def _draft_hash(value: dict[str, Any]) -> str:
    return sha256_json(without_keys(value, "draft_sha256"))


def _package_file(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("online validation package path must be relative")
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("online validation package path escapes its root")
    if not resolved.is_file():
        raise ValueError("online validation package file is missing")
    return resolved


def _candidate_command_text(
    *,
    package_root: Path,
    candidate_id: str,
    purpose: str,
    request_id: str,
    max_cost_usd: float,
) -> str:
    root = str(package_root.resolve()).replace("'", "''")
    purpose_escaped = purpose.replace("'", "''")
    return "\n".join(
        [
            "# STOP: this file is preparation material, not authorization.",
            "# Run only after explicit approval of this candidate, fixture, purpose, and cost ceiling.",
            f"$PackageRoot = '{root}'",
            f"$Candidate = '{candidate_id}'",
            '$ProfilePath = Join-Path $PackageRoot "profiles\\$Candidate.json"',
            '$RoutePath = Join-Path $PackageRoot "routes\\$Candidate.json"',
            '$FixturePath = Join-Path $PackageRoot "fixture\\public-summary-input.txt"',
            '$ApprovedDir = Join-Path $PackageRoot "approved"',
            '$ConsentPath = Join-Path $ApprovedDir "$Candidate.consent.json"',
            '$RequestPath = Join-Path $ApprovedDir "$Candidate.request.json"',
            '$LedgerPath = Join-Path $ApprovedDir "$Candidate.ledger.json"',
            '$ResultPath = Join-Path $ApprovedDir "$Candidate.result.json"',
            "New-Item -ItemType Directory -Force -Path $ApprovedDir | Out-Null",
            "",
            "python -m model_provider_gateway doctor $ProfilePath",
            "python -m model_provider_gateway profile validate $ProfilePath",
            "python -m model_provider_gateway route validate $RoutePath",
            "python -m model_provider_gateway consent plan `",
            "  $RoutePath $FixturePath --capability text `",
            f"  --purpose '{purpose_escaped}' --data-type public_synthetic_text `",
            f"  --ttl-seconds {DEFAULT_TTL_SECONDS} --max-calls {DEFAULT_MAX_CALLS} `",
            f"  --max-cost-usd {max_cost_usd:.2f} | Out-File -LiteralPath $ConsentPath -Encoding utf8",
            "python -m model_provider_gateway consent validate $ConsentPath $RoutePath",
            "python -m model_provider_gateway execution plan `",
            "  $ProfilePath $RoutePath $ConsentPath `",
            "  --consumer-id content_studio --task draft_only `",
            f"  --max-estimated-cost-usd {max_cost_usd:.2f} --request-id {request_id} `",
            "  | Out-File -LiteralPath $RequestPath -Encoding utf8",
            "",
            "# The next command is the only real Provider call. It is forbidden under the current dispatch.",
            "python -m model_provider_gateway execution run `",
            "  $ProfilePath $RoutePath $ConsentPath $RequestPath `",
            "  --ledger $LedgerPath | Tee-Object -FilePath $ResultPath",
            "",
        ]
    )


def _approval_markdown(package: dict[str, Any]) -> str:
    fixture = package["fixture"]
    lines = [
        "# 受控在线 Provider 验收人工审批包",
        "",
        f"更新时间：{package['generated_at']}",
        "执行者：Codex / GPT-5.6 Sol",
        "",
        "> 当前状态：仅完成准备与离线验证。尚未选择 Provider，未读取 Key，未上传资料，未产生费用。",
        "",
        "## 固定公开样例",
        "",
        f"- 内容预览：`{fixture['preview']}`",
        f"- SHA-256：`{fixture['sha256']}`",
        f"- MIME：`{fixture['mime_type']}`",
        f"- 字节数：`{fixture['bytes']}`",
        f"- 用途：{package['purpose']}",
        "",
        "## 候选路线（未选择）",
        "",
        "| candidate_id | Provider | model | destination | max_calls | max_cost_usd | timeout | fallback |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for candidate in package["candidates"]:
        lines.append(
            "| {candidate_id} | {provider_id} | {model} | {destination} | {max_calls} | {max_cost_usd:.2f} | "
            "{timeout_seconds}s | disabled |".format(**candidate)
        )
    lines.extend(
        [
            "",
            "每个候选均为单成员、零重试、无 fallback 路线。`credential_status=not_checked`，不代表本机已配置或可用。",
            "用户必须明确选择其中一个 candidate；准备程序不会自动推荐、排序执行或跨 Provider 降级。",
            "",
            "## 批准后执行入口",
            "",
            "每个候选都有一份精确命令文本，位于 `commands/<candidate_id>-after-explicit-approval.txt`。",
            "命令会先重新运行 doctor/profile/route/consent 校验，再生成正式 consent v2 与 execution request。",
            "文件末行才是唯一真实 Provider 调用；当前派发明确禁止执行该末行。",
            "",
            "## 预期产物",
            "",
            "- `approved/<candidate_id>.consent.json`：批准后即时生成、有效期一小时的 consent v2。",
            "- `approved/<candidate_id>.request.json`：绑定 consent、route revision 与输入清单的执行请求。",
            "- `approved/<candidate_id>.ledger.json`：原子调用/费用 reservation 审计。",
            "- `approved/<candidate_id>.result.json`：统一、脱敏的执行结果信封。",
            "",
            "## 停止与回滚条件",
            "",
            (
                "- 未明确选择 candidate、Key reference 未就绪、route/consent/input hash 漂移、"
                "费用或有效期不匹配时立即停止。"
            ),
            "- 429、5xx 或 timeout 只记录一次失败；不重试、不 fallback、不改用其他 Provider。",
            "- 回滚只需不执行末行或删除尚未使用的 `approved/` 草案；既有 profile、业务数据和 Provider 账号均不改变。",
            "",
            "## Change rationale",
            "",
            "- 意图：在真实调用前提供可逐项核对的最小公开样例和精确路由材料。",
            "- 决策：复用现有 Catalog/Profile/Route/Consent/Execution；准备包只含不可执行草案。",
            "- 理由：避免因本机已有 Key 而把准备动作误变成外部请求，也避免隐式替用户选 Provider。",
            "- 证据：LiteLLM loopback、doctor、Schema、漂移、429/5xx/timeout 和无 fallback 离线回归。",
            "- 生效范围：仅本次公开合成文本的在线验收准备，不授予其他数据、能力、目的地或调用额度。",
            "- 回滚：删除准备目录；不会修改 Provider 配置、Key、业务数据或既有 ledger。",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_online_validation_package(
    fixture_path: str | Path,
    output_dir: str | Path,
    *,
    generated_at: str,
    candidate_preset_ids: tuple[str, ...] = DEFAULT_CANDIDATES,
    purpose: str = DEFAULT_PURPOSE,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Build a non-executable package. This function never resolves a credential."""
    datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if max_cost_usd <= 0 or max_cost_usd > 0.10:
        raise ValueError("validation preparation cost ceiling must be positive and at most USD 0.10 per candidate")
    if timeout_seconds < 1 or timeout_seconds > 120:
        raise ValueError("validation preparation timeout must be between 1 and 120 seconds")
    if not candidate_preset_ids or len(set(candidate_preset_ids)) != len(candidate_preset_ids):
        raise ValueError("candidate preset ids must be non-empty and unique")

    source_fixture = Path(fixture_path).resolve()
    output = Path(output_dir).resolve()
    copied_fixture = output / "fixture" / "public-summary-input.txt"
    copied_fixture.parent.mkdir(parents=True, exist_ok=True)
    copied_fixture.write_bytes(source_fixture.read_bytes())
    artifact = build_artifact_manifest(
        [copied_fixture], data_type="public_synthetic_text", purpose=purpose
    )[0]
    if artifact["mime_type"] != "text/plain":
        raise ValueError("online validation fixture must be UTF-8 plain text")
    preview = copied_fixture.read_text(encoding="utf-8-sig").strip()
    if not preview or len(preview) > 500:
        raise ValueError("online validation fixture must be a non-empty short text")
    public_artifact = {**public_artifact_refs([artifact])[0], "relative_path": "fixture/public-summary-input.txt"}

    candidates: list[dict[str, Any]] = []
    for preset_id in candidate_preset_ids:
        provider_id, provider, model = find_model_preset(preset_id)
        if "text" not in model["capabilities"]:
            raise ValueError(f"candidate preset is not text capable: {preset_id}")
        profile = build_profile(
            preset_id,
            f"env:{provider['auth_env']}",
            profile_id=f"online-validation-{preset_id}",
            capabilities=["text"],
            timeout_seconds=timeout_seconds,
            max_attempts=1,
            max_calls=DEFAULT_MAX_CALLS,
            max_cost_usd=max_cost_usd,
            last_verified=generated_at,
        )
        route = build_route_plan(
            f"online-validation-{preset_id}",
            "text",
            [profile],
            fallback_enabled=False,
            max_calls=DEFAULT_MAX_CALLS,
            max_cost_usd=max_cost_usd,
        )
        request_id = f"controlled-online-validation-{preset_id}"
        consent_draft = {
            "schema": CONSENT_DRAFT_SCHEMA,
            "status": "awaiting_explicit_user_approval",
            "executable": False,
            "candidate_id": preset_id,
            "route_id": route["route_id"],
            "route_revision": route["route_revision"],
            "capability": "text",
            "purpose": purpose,
            "artifacts": [public_artifact],
            "input_policy": {
                "instruction_sha256": sha256_json({"instruction": purpose}),
                "input_manifest_sha256": input_manifest_hash(purpose, [artifact]),
            },
            "authorized_deployments": [
                {
                    "profile_id": profile["profile_id"],
                    "profile_revision": profile["route_revision"],
                    "provider_id": provider_id,
                    "destination": provider["destination"],
                    "model": model["model"],
                }
            ],
            "ttl_seconds_after_approval": DEFAULT_TTL_SECONDS,
            "limits": {"max_calls": DEFAULT_MAX_CALLS, "max_cost_usd": max_cost_usd},
            "fallback_authorized": False,
        }
        consent_draft["draft_sha256"] = _draft_hash(consent_draft)
        request_draft = {
            "schema": EXECUTION_DRAFT_SCHEMA,
            "status": "awaiting_materialized_consent_v2",
            "executable": False,
            "candidate_id": preset_id,
            "request_id": request_id,
            "consumer_id": "content_studio",
            "task": "draft_only",
            "capability": "text",
            "route_id": route["route_id"],
            "route_revision": route["route_revision"],
            "profile_id": profile["profile_id"],
            "profile_revision": profile["route_revision"],
            "provider_id": provider_id,
            "model": model["model"],
            "instruction": purpose,
            "artifacts": [public_artifact],
            "input_manifest_hash": consent_draft["input_policy"]["input_manifest_sha256"],
            "consent_draft_sha256": consent_draft["draft_sha256"],
            "max_estimated_cost_usd": max_cost_usd,
            "owner_gate_receipt_hash": None,
            "automatic_retry": False,
            "automatic_fallback": False,
        }
        request_draft["draft_sha256"] = _draft_hash(request_draft)

        relative = {
            "profile_path": f"profiles/{preset_id}.json",
            "route_path": f"routes/{preset_id}.json",
            "consent_draft_path": f"consent-drafts/{preset_id}.json",
            "execution_request_draft_path": f"execution-request-drafts/{preset_id}.json",
            "command_path": f"commands/{preset_id}-after-explicit-approval.txt",
        }
        _write_json(output / relative["profile_path"], profile)
        _write_json(output / relative["route_path"], route)
        _write_json(output / relative["consent_draft_path"], consent_draft)
        _write_json(output / relative["execution_request_draft_path"], request_draft)
        command_text = _candidate_command_text(
            package_root=output,
            candidate_id=preset_id,
            purpose=purpose,
            request_id=request_id,
            max_cost_usd=max_cost_usd,
        )
        _write_text(output / relative["command_path"], command_text)
        candidates.append(
            {
                "candidate_id": preset_id,
                "selection_status": "candidate_unselected",
                "credential_status": "not_checked",
                "provider_id": provider_id,
                "model": model["model"],
                "endpoint": provider["base_url"],
                "destination": provider["destination"],
                "timeout_seconds": timeout_seconds,
                "max_calls": DEFAULT_MAX_CALLS,
                "max_cost_usd": max_cost_usd,
                "fallback_enabled": False,
                "command_sha256": sha256_file(output / relative["command_path"]),
                **relative,
            }
        )

    package = {
        "schema": PACKAGE_SCHEMA,
        "dispatch_id": "CLOSURE-20260810-ONLINE-PROVIDER-VALIDATION-01",
        "status": "awaiting_explicit_provider_selection",
        "generated_at": generated_at,
        "purpose": purpose,
        "fixture": {
            "relative_path": "fixture/public-summary-input.txt",
            "preview": preview,
            "sha256": sha256_file(copied_fixture),
            "mime_type": artifact["mime_type"],
            "bytes": artifact["bytes"],
            "data_type": artifact["data_type"],
        },
        "candidate_count": len(candidates),
        "selected_candidate_id": None,
        "candidates": candidates,
        "safety": {
            "real_provider_call_performed": False,
            "upload_performed": False,
            "cost_incurred_usd": 0.0,
            "credential_read_performed": False,
            "active_consent_created": False,
            "automatic_retry_enabled": False,
            "automatic_fallback_enabled": False,
        },
    }
    approval_path = output / "APPROVAL-PACKAGE.md"
    _write_text(approval_path, _approval_markdown(package))
    package["approval_document"] = {
        "relative_path": "APPROVAL-PACKAGE.md",
        "sha256": sha256_file(approval_path),
        "bytes": approval_path.stat().st_size,
    }
    package["package_sha256"] = sha256_json(without_keys(package, "package_sha256"))
    jsonschema.validate(package, _schema())
    assert_secretless(package)
    _write_json(output / "validation-package.json", package)
    return package


def validate_online_validation_package(package_path: str | Path) -> dict[str, Any]:
    path = Path(package_path).resolve()
    root = path.parent
    package = json.loads(path.read_text(encoding="utf-8-sig"))
    jsonschema.validate(package, _schema())
    if package["package_sha256"] != sha256_json(without_keys(package, "package_sha256")):
        raise ValueError("online validation package hash drift detected")
    if package["candidate_count"] != len(package["candidates"]):
        raise ValueError("online validation candidate count drift detected")
    if len({item["candidate_id"] for item in package["candidates"]}) != package["candidate_count"]:
        raise ValueError("online validation candidate ids must be unique")
    if package["selected_candidate_id"] is not None:
        raise ValueError("preparation package must not select a Provider")
    if any(package["safety"].values()) or package["safety"]["cost_incurred_usd"] != 0.0:
        raise ValueError("preparation package safety assertions are invalid")
    fixture = package["fixture"]
    fixture_path = _package_file(root, fixture["relative_path"])
    manifest = build_artifact_manifest(
        [fixture_path], data_type=fixture["data_type"], purpose=package["purpose"]
    )[0]
    for key in ("sha256", "mime_type", "bytes", "data_type"):
        if fixture[key] != manifest[key]:
            raise ValueError("online validation fixture drift detected")

    for candidate in package["candidates"]:
        if candidate["selection_status"] != "candidate_unselected" or candidate["credential_status"] != "not_checked":
            raise ValueError("candidate selection or credential status was expanded")
        if candidate["fallback_enabled"] or candidate["max_calls"] != 1:
            raise ValueError("candidate retry/fallback/call scope is invalid")
        profile = json.loads(_package_file(root, candidate["profile_path"]).read_text(encoding="utf-8-sig"))
        route = json.loads(_package_file(root, candidate["route_path"]).read_text(encoding="utf-8-sig"))
        consent_draft = json.loads(
            _package_file(root, candidate["consent_draft_path"]).read_text(encoding="utf-8-sig")
        )
        request_draft = json.loads(
            _package_file(root, candidate["execution_request_draft_path"]).read_text(encoding="utf-8-sig")
        )
        command_path = _package_file(root, candidate["command_path"])
        if sha256_file(command_path) != candidate["command_sha256"]:
            raise ValueError("approved execution command drift detected")
        validate_profile(profile)
        validate_route_plan(route)
        if route["fallback_policy"]["enabled"] or len(route["members"]) != 1:
            raise ValueError("online validation route must be single-member with fallback disabled")
        if consent_draft.get("schema") != CONSENT_DRAFT_SCHEMA or consent_draft.get("executable") is not False:
            raise ValueError("consent draft must remain non-executable")
        if consent_draft.get("draft_sha256") != _draft_hash(consent_draft):
            raise ValueError("consent draft drift detected")
        if request_draft.get("schema") != EXECUTION_DRAFT_SCHEMA or request_draft.get("executable") is not False:
            raise ValueError("execution request draft must remain non-executable")
        if request_draft.get("draft_sha256") != _draft_hash(request_draft):
            raise ValueError("execution request draft drift detected")
        expected = {
            "candidate_id": candidate["candidate_id"],
            "provider_id": candidate["provider_id"],
            "model": candidate["model"],
            "route_id": route["route_id"],
            "route_revision": route["route_revision"],
        }
        for key, value in expected.items():
            if consent_draft.get(key, value) != value or request_draft.get(key) != value:
                raise ValueError(f"online validation candidate {key} drift detected")
        expected_deployment = {
            "profile_id": profile["profile_id"],
            "profile_revision": profile["route_revision"],
            "provider_id": candidate["provider_id"],
            "destination": candidate["destination"],
            "model": candidate["model"],
        }
        if consent_draft["authorized_deployments"] != [expected_deployment]:
            raise ValueError("online validation deployment draft drift detected")
        if consent_draft["artifacts"] != request_draft["artifacts"]:
            raise ValueError("online validation artifact draft drift detected")
        assert_secretless(profile)
        assert_secretless(route)
        assert_secretless(consent_draft)
        assert_secretless(request_draft)
        assert_secretless(command_path.read_text(encoding="utf-8-sig"))
    approval = package["approval_document"]
    approval_path = _package_file(root, approval["relative_path"])
    if sha256_file(approval_path) != approval["sha256"] or approval_path.stat().st_size != approval["bytes"]:
        raise ValueError("online validation approval document drift detected")
    assert_secretless(package)
    return {
        "ok": True,
        "status": "ready_for_explicit_user_selection",
        "schema": package["schema"],
        "package_sha256": package["package_sha256"],
        "candidate_count": package["candidate_count"],
        "real_provider_call_performed": False,
        "upload_performed": False,
        "cost_incurred_usd": 0.0,
    }
