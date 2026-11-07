"""JSON-first command line front door."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters import adapter_contract
from .capability_control import (
    OWNER_CAPABILITIES,
    build_owner_capability_plan,
    build_synthetic_owner_receipt,
    owner_adapter_registry,
    validate_owner_capability_receipt,
)
from .catalog import catalog_list, catalog_show
from .consent import build_consent_plan
from .coverage import build_consumer_capability_matrix, consumer_ids, mock_task_for
from .doctor import doctor
from .execution import LiteLLMProxyTransport, LiteLLMTransport, build_execution_request, execute_model_request
from .litellm_config import render_litellm_config
from .mock_server import mock_server, mock_smoke
from .onboarding import ProfileStore
from .online_validation import prepare_online_validation_package, validate_online_validation_package
from .owner_execution import execute_owner_capability, owner_runtime_doctor, write_owner_runtime_doctor
from .profile import build_profile, validate_profile
from .route import build_route_plan, validate_route_plan
from .runtime import DEFAULT_PROXY_HOST, DEFAULT_PROXY_PORT, DEFAULT_UI_HOST, DEFAULT_UI_PORT, proxy_plan, run_proxy
from .secrets import resolve_secret_ref
from .web import serve_ui

_CONSUMERS = consumer_ids()
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-provider-gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog = sub.add_parser("catalog")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_sub.add_parser("list")
    show = catalog_sub.add_parser("show")
    show.add_argument("provider_id")

    profile = sub.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    build = profile_sub.add_parser("build")
    build.add_argument("preset_id")
    build.add_argument("--auth-ref", required=True)
    build.add_argument("--profile-id")
    validate = profile_sub.add_parser("validate")
    validate.add_argument("path")

    route = sub.add_parser("route")
    route_sub = route.add_subparsers(dest="route_command", required=True)
    plan = route_sub.add_parser("plan")
    plan.add_argument("route_id")
    plan.add_argument("capability")
    plan.add_argument("profiles", nargs="+")
    plan.add_argument("--fallback", action="store_true")
    plan.add_argument("--max-calls", type=int)
    plan.add_argument("--max-cost-usd", type=float)
    route_validate = route_sub.add_parser("validate")
    route_validate.add_argument("path")

    consent = sub.add_parser("consent")
    consent_sub = consent.add_subparsers(dest="consent_command", required=True)
    consent_plan = consent_sub.add_parser("plan")
    consent_plan.add_argument("route")
    consent_plan.add_argument("artifacts", nargs="+")
    consent_plan.add_argument("--capability", required=True)
    consent_plan.add_argument("--purpose", required=True)
    consent_plan.add_argument("--data-type", required=True)
    consent_plan.add_argument("--ttl-seconds", type=int, default=3600)
    consent_plan.add_argument("--max-calls", type=int)
    consent_plan.add_argument("--max-cost-usd", type=float)
    consent_validate = consent_sub.add_parser("validate")
    consent_validate.add_argument("consent")
    consent_validate.add_argument("route")

    execution = sub.add_parser("execution")
    execution_sub = execution.add_subparsers(dest="execution_command", required=True)
    execution_plan = execution_sub.add_parser("plan")
    execution_plan.add_argument("profile")
    execution_plan.add_argument("route")
    execution_plan.add_argument("consent")
    execution_plan.add_argument("--consumer-id", choices=_CONSUMERS, required=True)
    execution_plan.add_argument("--task", required=True)
    execution_plan.add_argument("--max-estimated-cost-usd", type=float, required=True)
    execution_plan.add_argument("--request-id")
    execution_plan.add_argument("--owner-gate-receipt-hash")
    execution_run = execution_sub.add_parser("run")
    execution_run.add_argument("profile")
    execution_run.add_argument("route")
    execution_run.add_argument("consent")
    execution_run.add_argument("request")
    execution_run.add_argument("--ledger", required=True)
    execution_run.add_argument("--proxy-url")
    execution_run.add_argument("--proxy-master-ref", default="keyring:local-proxy/master-key")
    execution_mock = execution_sub.add_parser("mock-smoke")
    execution_mock.add_argument("--capability", choices=["text", "vision"], default="text")
    execution_mock.add_argument("--consumer-id", choices=_CONSUMERS, default="content_studio")
    execution_mock.add_argument("--task", default="")
    execution_mock.add_argument("--trigger", choices=["", "429", "400", "500", "timeout"], default="")

    render = sub.add_parser("render-config")
    render.add_argument("route")
    render.add_argument("profiles", nargs="+")
    smoke = sub.add_parser("mock-smoke")
    smoke.add_argument("--trigger", choices=["", "429", "500", "timeout"], default="")
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("profiles", nargs="*")
    doctor_parser.add_argument("--check-secrets", action="store_true")
    doctor_parser.add_argument("--owner-receipt-root")
    adapter = sub.add_parser("adapter")
    adapter.add_argument("consumer_id", choices=_CONSUMERS)
    capability = sub.add_parser("capability")
    capability_sub = capability.add_subparsers(dest="capability_command", required=True)
    matrix = capability_sub.add_parser("matrix")
    matrix.add_argument("--consumer-id", choices=_CONSUMERS)
    owners = capability_sub.add_parser("owners")
    owners.add_argument("--capability", choices=OWNER_CAPABILITIES)
    owner_plan = capability_sub.add_parser("plan")
    owner_plan.add_argument("capability", choices=OWNER_CAPABILITIES)
    owner_plan.add_argument("adapter_id")
    owner_plan.add_argument("input_manifest_hash")
    owner_plan.add_argument("--consumer-id", required=True)
    owner_plan.add_argument("--plan-id")
    owner_validate = capability_sub.add_parser("validate-receipt")
    owner_validate.add_argument("plan")
    owner_validate.add_argument("receipt")
    owner_mock = capability_sub.add_parser("mock-smoke")
    owner_mock.add_argument("--capability", choices=OWNER_CAPABILITIES, required=True)
    owner_mock.add_argument("--adapter-id")
    owner_run = capability_sub.add_parser(
        "run-local", help="Execute a reviewed local owner adapter with exact file binding"
    )
    owner_run.add_argument("capability", choices=OWNER_CAPABILITIES)
    owner_run.add_argument("adapter_id")
    owner_run.add_argument("artifacts", nargs="+")
    owner_run.add_argument("--output-dir", required=True)
    owner_run.add_argument("--consumer-id", required=True)
    owner_run.add_argument("--purpose", required=True)
    owner_run.add_argument(
        "--data-classification",
        choices=["synthetic_fixture", "public_fixture", "user_authorized_local"],
        required=True,
    )
    owner_run.add_argument("--timeout-seconds", type=int, default=600)
    owner_run.add_argument("--local-authorization-path")
    owner_run.add_argument("--local-authorization-revision")
    owner_run.add_argument("--vkp-root", default="")
    owner_run.add_argument("--ebook-root", default="")
    owner_run.add_argument("--embedding-python", default="")
    owner_run.add_argument("--embedding-model", default="")
    owner_run.add_argument("--embedding-device", choices=["cpu", "cuda"], default="cpu")
    owner_run.add_argument("--model-revision", default="")
    owner_run.add_argument("--sherpa-python", default="")
    owner_run.add_argument("--sherpa-model-dir", default="")
    owner_run.add_argument("--sherpa-provider", choices=["cpu", "cuda"], default="cpu")
    owner_run.add_argument("--sherpa-speaker", type=int, default=0)
    owner_run.add_argument("--sherpa-speed", type=float, default=1.0)
    owner_run.add_argument("--sherpa-num-threads", type=int, default=2)
    owner_doctor = capability_sub.add_parser("doctor", help="Show five-dimensional owner capability runtime truth")
    owner_doctor.add_argument("--receipt-root")
    owner_doctor.add_argument("--output")
    owner_doctor.add_argument("--sherpa-python")
    owner_doctor.add_argument("--sherpa-model-dir")
    owner_doctor.add_argument("--sherpa-provider", choices=["cpu", "cuda"], default="cpu")
    owner_doctor.add_argument("--sherpa-speaker", type=int, default=0)
    online_validation = sub.add_parser("online-validation")
    online_validation_sub = online_validation.add_subparsers(dest="online_validation_command", required=True)
    online_prepare = online_validation_sub.add_parser("prepare")
    online_prepare.add_argument("fixture")
    online_prepare.add_argument("output_dir")
    online_prepare.add_argument("--generated-at", default="")
    online_prepare.add_argument("--candidate", action="append", dest="candidates")
    online_validate = online_validation_sub.add_parser("validate")
    online_validate.add_argument("package")
    ui = sub.add_parser("ui")
    ui.add_argument("--host", default=DEFAULT_UI_HOST)
    ui.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    ui.add_argument("--state-dir")
    proxy = sub.add_parser("proxy")
    proxy_sub = proxy.add_subparsers(dest="proxy_command", required=True)
    proxy_plan_parser = proxy_sub.add_parser("plan")
    proxy_plan_parser.add_argument("--host", default=DEFAULT_PROXY_HOST)
    proxy_plan_parser.add_argument("--port", type=int, default=DEFAULT_PROXY_PORT)
    proxy_plan_parser.add_argument("--state-dir")
    proxy_serve_parser = proxy_sub.add_parser("serve")
    proxy_serve_parser.add_argument("--host", default=DEFAULT_PROXY_HOST)
    proxy_serve_parser.add_argument("--port", type=int, default=DEFAULT_PROXY_PORT)
    proxy_serve_parser.add_argument("--state-dir")
    return parser


def _execution_mock_smoke(
    capability: str,
    trigger: str,
    *,
    consumer_id: str = "content_studio",
    task: str = "",
) -> dict[str, Any]:
    suffix = ".txt" if capability == "text" else ".png"
    artifact_fd, artifact_name = tempfile.mkstemp(prefix="mpg-execution-mock-", suffix=suffix)
    os.close(artifact_fd)
    ledger_fd, ledger_name = tempfile.mkstemp(prefix="mpg-execution-ledger-", suffix=".json")
    os.close(ledger_fd)
    artifact = Path(artifact_name)
    ledger_path = Path(ledger_name)
    ledger_path.unlink(missing_ok=True)
    try:
        if capability == "text":
            artifact.write_text("synthetic fixture", encoding="utf-8")
            preset = "modelscope-glm-5-2"
            default_task = mock_task_for(consumer_id, capability)
        else:
            artifact.write_bytes(_PNG_1X1)
            preset = "siliconflow-glm-4-5v"
            default_task = mock_task_for(consumer_id, capability)
        selected_task = task or default_task
        profile = build_profile(
            preset,
            "env:MPG_SYNTHETIC_ONLY",
            last_verified="2026-08-05T00:00:00+00:00",
            timeout_seconds=1,
            max_calls=1,
            max_cost_usd=1.0,
        )
        route = build_route_plan("execution-mock", capability, [profile], max_calls=1, max_cost_usd=1.0)
        consent = build_consent_plan(
            route,
            [artifact],
            capability=capability,
            purpose="Return a synthetic-only response.",
            data_type="synthetic_fixture",
            max_calls=1,
            max_cost_usd=1.0,
        )
        request = build_execution_request(
            profile,
            route,
            consent,
            consumer_id=consumer_id,
            task=selected_task,
            max_estimated_cost_usd=0.1,
            request_id=f"execution-mock-{capability}",
        )
        status = 200
        delay = 0.0
        if trigger in {"400", "429", "500"}:
            status = int(trigger)
        elif trigger == "timeout":
            delay = 1.3
        with mock_server(status=status, delay_seconds=delay) as base_url:
            return execute_model_request(
                request,
                profile=profile,
                route=route,
                consent=consent,
                ledger_path=ledger_path,
                transport=LiteLLMTransport.loopback_stub(base_url),
                secret_resolver=lambda _ref: "synthetic-loopback-key",
            )
    finally:
        artifact.unlink(missing_ok=True)
        ledger_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "catalog":
            _emit(catalog_list() if args.catalog_command == "list" else catalog_show(args.provider_id))
        elif args.command == "profile" and args.profile_command == "build":
            _emit(build_profile(args.preset_id, args.auth_ref, profile_id=args.profile_id))
        elif args.command == "profile":
            _emit({"ok": True, "profile": validate_profile(_load(args.path))})
        elif args.command == "route" and args.route_command == "plan":
            profiles = [_load(path) for path in args.profiles]
            _emit(
                build_route_plan(
                    args.route_id,
                    args.capability,
                    profiles,
                    fallback_enabled=args.fallback,
                    max_calls=args.max_calls,
                    max_cost_usd=args.max_cost_usd,
                )
            )
        elif args.command == "route":
            _emit({"ok": True, "route": validate_route_plan(_load(args.path))})
        elif args.command == "consent" and args.consent_command == "plan":
            route = validate_route_plan(_load(args.route))
            _emit(
                build_consent_plan(
                    route,
                    args.artifacts,
                    capability=args.capability,
                    purpose=args.purpose,
                    data_type=args.data_type,
                    ttl_seconds=args.ttl_seconds,
                    max_calls=args.max_calls,
                    max_cost_usd=args.max_cost_usd,
                )
            )
        elif args.command == "consent":
            from .consent import validate_consent

            _emit({"ok": True, "consent": validate_consent(_load(args.consent), route=_load(args.route))})
        elif args.command == "execution" and args.execution_command == "plan":
            _emit(
                build_execution_request(
                    _load(args.profile),
                    _load(args.route),
                    _load(args.consent),
                    consumer_id=args.consumer_id,
                    task=args.task,
                    max_estimated_cost_usd=args.max_estimated_cost_usd,
                    request_id=args.request_id,
                    owner_gate_receipt_hash=args.owner_gate_receipt_hash,
                )
            )
        elif args.command == "execution" and args.execution_command == "run":
            transport = None
            if args.proxy_url:
                master_key = resolve_secret_ref(args.proxy_master_ref)
                if not master_key:
                    raise ValueError("local proxy master key reference is not ready")
                transport = LiteLLMProxyTransport(args.proxy_url, master_key)
            result = execute_model_request(
                _load(args.request),
                profile=_load(args.profile),
                route=_load(args.route),
                consent=_load(args.consent),
                ledger_path=args.ledger,
                transport=transport,
            )
            _emit(result)
            return 0 if result["ok"] else 2
        elif args.command == "execution":
            result = _execution_mock_smoke(
                args.capability,
                args.trigger,
                consumer_id=args.consumer_id,
                task=args.task,
            )
            _emit(result)
            return 0 if result["ok"] else 2
        elif args.command == "render-config":
            _emit(render_litellm_config(_load(args.route), [_load(path) for path in args.profiles]))
        elif args.command == "mock-smoke":
            result = mock_smoke(trigger=args.trigger, timeout_seconds=0.05 if args.trigger == "timeout" else 1.0)
            _emit(result)
            return 0 if result["ok"] else 2
        elif args.command == "doctor":
            result = doctor(
                [_load(path) for path in args.profiles],
                check_secrets=args.check_secrets,
                owner_receipt_root=args.owner_receipt_root,
            )
            _emit(result)
            return 0 if result["ok"] else 2
        elif args.command == "adapter":
            _emit(adapter_contract(args.consumer_id))
        elif args.command == "capability" and args.capability_command == "matrix":
            _emit(build_consumer_capability_matrix(consumer_id=args.consumer_id))
        elif args.command == "capability" and args.capability_command == "owners":
            _emit(owner_adapter_registry(capability=args.capability))
        elif args.command == "capability" and args.capability_command == "plan":
            _emit(
                build_owner_capability_plan(
                    args.capability,
                    args.adapter_id,
                    consumer_id=args.consumer_id,
                    input_manifest_hash=args.input_manifest_hash,
                    plan_id=args.plan_id,
                )
            )
        elif args.command == "capability" and args.capability_command == "validate-receipt":
            _emit(
                {
                    "ok": True,
                    "receipt": validate_owner_capability_receipt(
                        _load(args.receipt),
                        plan=_load(args.plan),
                    ),
                }
            )
        elif args.command == "capability" and args.capability_command == "run-local":
            runtime = {
                "vkp_root": args.vkp_root,
                "ebook_root": args.ebook_root,
                "embedding_python": args.embedding_python,
                "embedding_model": args.embedding_model,
                "embedding_device": args.embedding_device,
                "model_revision": args.model_revision,
                "timeout_seconds": str(args.timeout_seconds),
                "sherpa_python": args.sherpa_python,
                "sherpa_model_dir": args.sherpa_model_dir,
                "sherpa_provider": args.sherpa_provider,
                "sherpa_speaker": str(args.sherpa_speaker),
                "sherpa_speed": str(args.sherpa_speed),
                "sherpa_num_threads": str(args.sherpa_num_threads),
            }
            result = execute_owner_capability(
                args.capability,
                args.adapter_id,
                args.artifacts,
                output_dir=args.output_dir,
                consumer_id=args.consumer_id,
                purpose=args.purpose,
                data_classification=args.data_classification,
                runtime=runtime,
                timeout_seconds=args.timeout_seconds,
                local_authorization_path=args.local_authorization_path,
                local_authorization_revision=args.local_authorization_revision,
            )
            _emit(result)
            return 0 if result["report"]["status"] == "completed" else 2
        elif args.command == "capability" and args.capability_command == "doctor":
            result = (
                write_owner_runtime_doctor(
                    args.output,
                    receipt_root=args.receipt_root,
                    sherpa_python=args.sherpa_python,
                    sherpa_model_dir=args.sherpa_model_dir,
                    sherpa_provider=args.sherpa_provider,
                    sherpa_speaker=args.sherpa_speaker,
                )
                if args.output
                else owner_runtime_doctor(
                    receipt_root=args.receipt_root,
                    sherpa_python=args.sherpa_python,
                    sherpa_model_dir=args.sherpa_model_dir,
                    sherpa_provider=args.sherpa_provider,
                    sherpa_speaker=args.sherpa_speaker,
                )
            )
            _emit(result)
        elif args.command == "capability":
            _emit(build_synthetic_owner_receipt(args.capability, adapter_id=args.adapter_id))
        elif args.command == "online-validation" and args.online_validation_command == "prepare":
            generated_at = args.generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
            kwargs = {"candidate_preset_ids": tuple(args.candidates)} if args.candidates else {}
            package = prepare_online_validation_package(
                args.fixture,
                args.output_dir,
                generated_at=generated_at,
                **kwargs,
            )
            _emit(
                {
                    "ok": True,
                    "status": package["status"],
                    "package": str(Path(args.output_dir).resolve() / "validation-package.json"),
                    "package_sha256": package["package_sha256"],
                    **package["safety"],
                }
            )
        elif args.command == "online-validation":
            _emit(validate_online_validation_package(args.package))
        elif args.command == "ui":
            serve_ui(
                host=args.host,
                port=args.port,
                state_dir=Path(args.state_dir).resolve() if args.state_dir else None,
            )
        elif args.command == "proxy" and args.proxy_command == "plan":
            _emit(
                proxy_plan(
                    ProfileStore(Path(args.state_dir).resolve() if args.state_dir else None),
                    host=args.host,
                    port=args.port,
                )
            )
        elif args.command == "proxy":
            return run_proxy(
                ProfileStore(Path(args.state_dir).resolve() if args.state_dir else None),
                host=args.host,
                port=args.port,
            )
        else:
            raise ValueError("unsupported command")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        message = "execution command validation failed" if args.command == "execution" else str(exc)
        _emit({"ok": False, "status": "blocked", "error": message})
        return 2


if __name__ == "__main__":
    sys.exit(main())
