"""Source-checkout synthetic demo for the Gateway artifact lifecycle shim.

The demo drives the real ``scripts/artifact_lifecycle_adapter.py`` CLI, which
forwards its exact argv to the separate public ``project_artifact_adapter``
core. It creates a brand-new synthetic workspace, runs the read-only
inspect/plan loop (doctor -> scan -> explain -> plan -> verify-plan), captures
every JSON payload and exit code, and then runs bounded negative controls
(nonconfigured root, missing core selection, a clearly synthetic injected CLI
failure, and changed-fixture drift).

It never applies, quarantines, restores, purges or cleans up artifacts, never
downloads or installs anything, and never imports the Gateway runtime. The only
child process is the public core's own read-only
``git -C <fixture-root> status --porcelain=v1`` probe; the model itself must not
run Git. ``GIT_CEILING_DIRECTORIES`` is bound to this demo's own new workspace
for the duration of the loop and the previous environment is restored.

Workspace rule: the requested path is refused **lexically** (``os.path.lexists``)
before any ``resolve`` and before any write, so an existing file, directory or
dangling link is never followed and its target is never created.

Failure rule: every CLI invocation is written to a receipt report **before** its
exit code or payload is validated. The first failing payload is therefore
preserved with its exact stdout/stderr/exception instead of being lost.

The state of a source checkout is reported honestly: this script is not an
installed Gateway entry point, and a successful run is not proof of an installed
wheel, live provider state, real usage or authority to mutate real artifacts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "scripts" / "artifact_lifecycle_adapter.py"
RULES = ROOT / "artifact-lifecycle" / "model-provider-gateway-artifacts.v1.json"

CORE_ROOT_ENV = "ARTIFACT_LIFECYCLE_CORE_ROOT"
CORE_MODULE = "project_artifact_adapter"
CORE_ENTRY_NAME = "project_artifact_adapter.py"
UNAVAILABLE = "ARTIFACT_LIFECYCLE_CORE_UNAVAILABLE"
SCHEMA_VERSION = "model-provider-gateway-artifact-lifecycle-demo.v1"
DRIFT_RELATIVE_PATH = "runtime/route-plan.json"
INJECTED_MISSING_ARTIFACT_ID = "synthetic-missing-artifact-for-failure-capture"

# Every value is an obvious synthetic placeholder. Some names mirror the rule
# file, but no byte is claimed to be a genuine wheel, sdist, cache, credential,
# license record or runtime/provider record.
FIXTURE_BYTES = {
    "build/candidate-wheel.whl": (
        b"SYNTHETIC placeholder for build/candidate-wheel.whl; "
        b"not a real wheel and not a build output\n"
    ),
    "dist/candidate-sdist.tar.gz": (
        b"SYNTHETIC placeholder for dist/candidate-sdist.tar.gz; "
        b"not a real sdist archive\n"
    ),
    "test-cache/pytest.cache": (
        b"SYNTHETIC placeholder for test-cache/pytest.cache; "
        b"not a real test cache\n"
    ),
    "runtime/provider-profile.ref": (
        b"SYNTHETIC secret reference placeholder; contains no credential material\n"
    ),
    "runtime/route-plan.json": (
        b'{"synthetic": true, "not_real": true, "fixture": "runtime/route-plan.json"}\n'
    ),
    "runtime/consent.json": (
        b'{"synthetic": true, "not_real": true, "fixture": "runtime/consent.json"}\n'
    ),
    "runtime/execution-ledger.json": (
        b'{"synthetic": true, "not_real": true, "fixture": "runtime/execution-ledger.json"}\n'
    ),
    "runtime/capability-receipt.json": (
        b'{"synthetic": true, "not_real": true, "fixture": "runtime/capability-receipt.json"}\n'
    ),
    "runtime/license-evidence.txt": (
        b"SYNTHETIC license evidence placeholder; no real license material\n"
    ),
}

PROTECTED_FIXTURES = {
    "runtime/provider-profile.ref": "secret_or_account_state",
    "runtime/execution-ledger.json": "evidence_receipt_manifest",
}


class _Blocked(RuntimeError):
    """A first, concrete demo blocker. Execution stops here without an alternate route."""

    def __init__(self, code: str, detail: str, receipt: str | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.receipt = receipt


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _lexical_absolute(path_value: str | Path) -> Path:
    """Return an absolute path using lexical normalisation only (no resolve)."""
    raw = Path(path_value).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    return Path(os.path.abspath(str(raw)))


def _lexically_exists(path_value: str | Path) -> bool:
    """Existence check that is true for dangling links and never follows them."""
    return os.path.lexists(str(path_value))


def _emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _load_shim():
    if not SHIM.is_file():
        raise _Blocked("DEMO_SHIM_MISSING", f"expected {SHIM}")
    spec = importlib.util.spec_from_file_location("gateway_artifact_lifecycle_demo_shim", SHIM)
    if spec is None or spec.loader is None:
        raise _Blocked("DEMO_SHIM_UNLOADABLE", f"cannot load {SHIM}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_core_dir(raw: str) -> Path:
    root = Path(raw).expanduser().resolve()
    if not root.is_dir() or not (root / CORE_ENTRY_NAME).is_file():
        raise _Blocked(
            "DEMO_CORE_UNAVAILABLE",
            f"{CORE_ROOT_ENV} must name a source checkout containing "
            f"{CORE_ENTRY_NAME}: {root}",
        )
    return root


def _select_core(core_root_argument: str | None) -> tuple[Path, str, str]:
    """Return (core root, selection mode, how it was selected)."""
    if core_root_argument:
        return _resolve_core_dir(core_root_argument), "explicit_env", "--core-root"
    raw = os.environ.get(CORE_ROOT_ENV)
    if raw:
        return _resolve_core_dir(raw), "explicit_env", "environment"
    try:
        spec = importlib.util.find_spec(CORE_MODULE)
    except (ImportError, ValueError):
        spec = None
    if spec is None or not spec.origin:
        raise _Blocked(
            "DEMO_CORE_UNAVAILABLE",
            f"select the separate public artifact lifecycle core with "
            f"{CORE_ROOT_ENV}=<checkout directory> or install the public "
            "artifact-lifecycle-core distribution. No automatic install, "
            "download or sibling search is performed.",
        )
    origin = Path(spec.origin).resolve()
    return origin.parent, "module_resolution", "module_resolution"


def _invoke(shim, argv: list[str]) -> dict:
    """Run one real shim CLI invocation in-process and capture JSON plus exit code."""
    argv = [str(item) for item in argv]
    out, err = io.StringIO(), io.StringIO()
    exit_code = None
    exception = None
    value = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            value = shim.main(list(argv))
    except SystemExit as exc:
        code = exc.code
        exit_code = 0 if code is None else int(code)
    except BaseException as exc:  # preserve the first unexpected failure
        exception = f"{type(exc).__name__}: {exc}"
    else:
        if isinstance(value, int):
            exit_code = int(value)
    text = out.getvalue()
    payload = None
    if text.strip():
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
    return {
        "argv": argv,
        "exit_code": exit_code,
        "stdout_json": payload,
        "stdout_text": text,
        "stderr_text": err.getvalue(),
        "exception": exception,
    }


def _expect_ok(record: dict, label: str, receipt: str) -> None:
    """Validate a receipt that was already persisted, preserving full detail."""
    if record["exception"]:
        raise _Blocked(
            "DEMO_CLI_EXCEPTION",
            f"{label}: {record['exception']}; raw receipt={receipt}",
            receipt=receipt,
        )
    if record["exit_code"] != 0:
        raise _Blocked(
            "DEMO_CLI_BLOCKED",
            f"{label} exited {record['exit_code']}; raw receipt={receipt}; "
            f"stdout_json={json.dumps(record['stdout_json'], ensure_ascii=False)}; "
            f"stdout_text={record['stdout_text']!r}; stderr_text={record['stderr_text']!r}",
            receipt=receipt,
        )
    if record["stdout_json"] is None:
        raise _Blocked(
            "DEMO_CLI_JSON_MISSING",
            f"{label} produced no JSON; raw receipt={receipt}; "
            f"stdout_text={record['stdout_text']!r}; stderr_text={record['stderr_text']!r}",
            receipt=receipt,
        )


def _problem(code: str, detail: str, receipt: str | None = None) -> None:
    raise _Blocked(code, detail, receipt=receipt)


def _snapshot(fixture_root: Path) -> dict[str, str]:
    return {
        relative: _sha256_file(fixture_root / relative)
        for relative in FIXTURE_BYTES
    }


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _build_workspace(workspace: Path) -> tuple[Path, Path, Path, Path, Path]:
    if not RULES.is_file():
        raise _Blocked("DEMO_RULES_MISSING", f"expected rule file {RULES}")
    workspace.mkdir(parents=True, exist_ok=False)
    config_dir = workspace / "artifact-lifecycle"
    config_dir.mkdir()
    config_path = config_dir / RULES.name
    config_path.write_bytes(RULES.read_bytes())
    if config_path.read_bytes() != RULES.read_bytes():
        raise _Blocked("DEMO_RULES_COPY_MISMATCH", str(config_path))

    fixture_root = workspace / "fixtures" / "artifact-lifecycle"
    fixture_root.mkdir(parents=True)
    for relative, payload in FIXTURE_BYTES.items():
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    quarantine_root = workspace / "quarantine"
    quarantine_root.mkdir()
    reports_dir = workspace / "reports"
    reports_dir.mkdir()
    checkpoints_dir = workspace / "checkpoints"
    checkpoints_dir.mkdir()
    return config_path, fixture_root, quarantine_root, reports_dir, checkpoints_dir


def _run_sequence(
    shim,
    summary: dict,
    config_path: Path,
    fixture_root: Path,
    quarantine_root: Path,
    reports_dir: Path,
    checkpoints_dir: Path,
    run_id: str,
) -> None:
    written: list[str] = []

    def record(name: str, invocation: dict) -> str:
        """Persist the raw invocation BEFORE any exit-code/payload validation."""
        filename = f"{len(written) + 1:02d}-{name}.json"
        _write_json(reports_dir / filename, {"step": name, **invocation})
        relative = f"reports/{filename}"
        written.append(relative)
        summary["reports_written"].append(relative)
        return relative

    def checked(name: str, argv: list[str], ok: bool = True) -> tuple[dict, str]:
        invocation = _invoke(shim, argv)
        receipt = record(name, invocation)
        if ok:
            _expect_ok(invocation, name, receipt)
        return invocation, receipt

    config = str(config_path)
    fixture = str(fixture_root)
    quarantine = str(quarantine_root)
    before_snapshot = _snapshot(fixture_root)

    sequence: list[dict] = []

    doctor, doctor_receipt = checked(
        "doctor", ["doctor", "--config", config, "--root", fixture]
    )
    if doctor["stdout_json"].get("status") != "ready":
        _problem(
            "DEMO_DOCTOR_NOT_READY",
            f"status={doctor['stdout_json'].get('status')!r}; raw receipt={doctor_receipt}",
            receipt=doctor_receipt,
        )
    sequence.append({
        "step": "doctor",
        "exit_code": doctor["exit_code"],
        "report": doctor_receipt,
        "stdout_json": doctor["stdout_json"],
    })

    core_module = sys.modules.get("artifact_lifecycle_core")
    core_origin = None
    if core_module is not None and getattr(core_module, "__file__", None):
        core_origin = str(Path(core_module.__file__).resolve())
    core_root = Path(summary["core_selection"]["core_root"])
    if core_origin is None or not _is_within(Path(core_origin), core_root):
        _problem(
            "DEMO_CORE_ORIGIN_MISMATCH",
            f"loaded core origin {core_origin!r} is not under supplied core root {core_root}",
        )
    summary["core_selection"]["artifact_lifecycle_core_origin"] = core_origin
    summary["core_selection"]["origin_under_core_root"] = True

    manifest_one = reports_dir / "scan-manifest-1.json"
    manifest_two = reports_dir / "scan-manifest-2.json"
    checkpoints = str(checkpoints_dir / "scan.sqlite3")
    scan_one, scan_one_receipt = checked(
        "scan",
        [
            "scan", "--config", config, "--root", fixture,
            "--closure-run-id", run_id, "--checkpoint", checkpoints,
            "--output", str(manifest_one), "--max-files", "128",
        ],
    )
    sequence.append({
        "step": "scan",
        "exit_code": scan_one["exit_code"],
        "report": scan_one_receipt,
        "stdout_json": scan_one["stdout_json"],
    })

    scan_two, scan_two_receipt = checked(
        "scan-repeat",
        [
            "scan", "--config", config, "--root", fixture,
            "--closure-run-id", run_id, "--checkpoint", checkpoints,
            "--output", str(manifest_two), "--max-files", "128",
        ],
    )
    sequence.append({
        "step": "scan-repeat",
        "exit_code": scan_two["exit_code"],
        "report": scan_two_receipt,
        "stdout_json": scan_two["stdout_json"],
    })

    first_manifest = json.loads(manifest_one.read_text(encoding="utf-8"))
    second_manifest = json.loads(manifest_two.read_text(encoding="utf-8"))
    first_sha = first_manifest["manifest_sha256"]
    second_sha = second_manifest["manifest_sha256"]
    stable = first_sha == second_sha and manifest_one.read_bytes() == manifest_two.read_bytes()
    if not stable:
        _problem(
            "DEMO_SCAN_UNSTABLE",
            f"first={first_sha} second={second_sha}",
            receipt=scan_two_receipt,
        )
    if first_manifest.get("closure_run_id") != run_id:
        _problem(
            "DEMO_SCAN_CLOSURE_MISMATCH",
            f"manifest closure_run_id={first_manifest.get('closure_run_id')!r} run_id={run_id!r}",
            receipt=scan_one_receipt,
        )
    by_path = {item["path"]: item for item in first_manifest.get("artifacts", [])}
    missing = sorted(set(FIXTURE_BYTES) - set(by_path))
    if missing:
        _problem(
            "DEMO_FIXTURE_RULE_MISMATCH",
            f"scanned artifacts missing: {missing}",
            receipt=scan_one_receipt,
        )

    protected_evidence: list[dict] = []
    for relative, expected_role in PROTECTED_FIXTURES.items():
        item = by_path[relative]
        label = "explain-" + relative.rsplit("/", 1)[-1]
        explain, explain_receipt = checked(
            label,
            ["explain", "--manifest", str(manifest_one), "--artifact-id", str(item["artifact_id"])],
        )
        artifact = explain["stdout_json"].get("artifact") or {}
        if artifact.get("artifact_role") != expected_role or artifact.get("pin") is not True:
            _problem(
                "DEMO_PROTECTED_ROLE_MISMATCH",
                f"{relative}: role={artifact.get('artifact_role')!r} pin={artifact.get('pin')!r}; "
                f"raw receipt={explain_receipt}",
                receipt=explain_receipt,
            )
        if explain["stdout_json"].get("authorizes_operation") is not False:
            _problem(
                "DEMO_EXPLAIN_AUTHORIZED",
                f"{relative}: raw receipt={explain_receipt}",
                receipt=explain_receipt,
            )
        protected_evidence.append({
            "path": relative,
            "artifact_id": item["artifact_id"],
            "artifact_role": artifact.get("artifact_role"),
            "pin": artifact.get("pin"),
        })
        sequence.append({
            "step": label,
            "exit_code": explain["exit_code"],
            "report": explain_receipt,
            "stdout_json": explain["stdout_json"],
        })

    plan_path = reports_dir / "quarantine-plan.json"
    plan_invocation, plan_receipt = checked(
        "plan",
        [
            "plan", "--config", config, "--root", fixture,
            "--manifest", str(manifest_one), "--quarantine-root", quarantine,
            "--output", str(plan_path),
        ],
    )
    sequence.append({
        "step": "plan",
        "exit_code": plan_invocation["exit_code"],
        "report": plan_receipt,
        "stdout_json": plan_invocation["stdout_json"],
    })

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("authorization_state") != "not_authorized":
        _problem(
            "DEMO_PLAN_AUTHORIZED",
            f"authorization_state={plan.get('authorization_state')!r}; raw receipt={plan_receipt}",
            receipt=plan_receipt,
        )
    if plan.get("cleanup_mode") != "plan_only" or plan.get("automatic_purge") is not False:
        _problem(
            "DEMO_PLAN_POLICY_UNSAFE",
            f"cleanup_mode={plan.get('cleanup_mode')!r} "
            f"automatic_purge={plan.get('automatic_purge')!r}; raw receipt={plan_receipt}",
            receipt=plan_receipt,
        )
    gates = plan.get("gate_evidence") or {}
    for field in ("owner_state", "process_state", "dispatch_state"):
        if gates.get(field) != "unknown":
            _problem(
                "DEMO_UNKNOWN_GATE_REPLACED",
                f"{field}={gates.get(field)!r}; raw receipt={plan_receipt}",
                receipt=plan_receipt,
            )
    if gates.get("git_state") != "not_repository":
        _problem(
            "DEMO_GIT_STATE_UNEXPECTED",
            f"expected the synthetic non-repository state, got {gates.get('git_state')!r}; "
            f"raw receipt={plan_receipt}",
            receipt=plan_receipt,
        )

    entries = {entry["artifact_id"]: entry for entry in plan.get("entries", [])}
    if len(entries) != len(FIXTURE_BYTES):
        _problem(
            "DEMO_PLAN_ENTRY_COUNT",
            f"expected {len(FIXTURE_BYTES)} plan entries, got {len(entries)}; raw receipt={plan_receipt}",
            receipt=plan_receipt,
        )
    eligible = [entry for entry in entries.values() if entry.get("eligible")]
    if eligible:
        _problem(
            "DEMO_UNEXPECTED_ELIGIBLE_ENTRIES",
            f"{len(eligible)} eligible entries; raw receipt={plan_receipt}",
            receipt=plan_receipt,
        )
    protected_blockers: dict[str, list[str]] = {}
    for evidence in protected_evidence:
        entry = entries.get(evidence["artifact_id"])
        gates_list = list((entry or {}).get("blocking_gates") or [])
        required = {"artifact_pinned", "protected_role:" + str(evidence["artifact_role"])}
        if not required.issubset(set(gates_list)):
            _problem(
                "DEMO_PROTECTED_BLOCKER_MISSING",
                f"{evidence['path']}: {sorted(gates_list)}; raw receipt={plan_receipt}",
                receipt=plan_receipt,
            )
        protected_blockers[evidence["path"]] = gates_list

    verify_invocation, verify_receipt = checked(
        "verify-plan",
        [
            "verify-plan", "--config", config, "--root", fixture,
            "--manifest", str(manifest_one), "--quarantine-root", quarantine,
            "--plan", str(plan_path),
        ],
    )
    verify = verify_invocation["stdout_json"]
    if verify.get("ok") is not True or verify.get("errors"):
        _problem(
            "DEMO_VERIFY_PLAN_FAILED",
            f"ok={verify.get('ok')!r} errors={verify.get('errors')!r}; raw receipt={verify_receipt}",
            receipt=verify_receipt,
        )
    sequence.append({
        "step": "verify-plan",
        "exit_code": verify_invocation["exit_code"],
        "report": verify_receipt,
        "stdout_json": verify,
    })

    after = _snapshot(fixture_root)
    if before_snapshot != after:
        _problem(
            "DEMO_FIXTURE_CHANGED_BY_PLANNING",
            "fixture bytes changed during the read-only loop",
            receipt=verify_receipt,
        )
    quarantine_entries = sorted(path.name for path in quarantine_root.iterdir())
    if quarantine_entries:
        _problem(
            "DEMO_QUARANTINE_NOT_EMPTY",
            str(quarantine_entries),
            receipt=verify_receipt,
        )

    root_identity = (plan.get("root_identity") or {}).get("resolved_path")
    quarantine_identity = (plan.get("quarantine_root_identity") or {}).get("resolved_path")

    summary["manifest"] = {
        "path": "reports/scan-manifest-1.json",
        "sha256": first_sha,
        "artifact_count": len(first_manifest.get("artifacts", [])),
        "closure_run_id": first_manifest.get("closure_run_id"),
        "config_sha256": (first_manifest.get("adapter") or {}).get("config_sha256"),
        "synthetic_only": (first_manifest.get("adapter") or {}).get("synthetic_only"),
        "cleanup_mode": first_manifest.get("cleanup_mode"),
        "automatic_purge": first_manifest.get("automatic_purge"),
    }
    summary["repeated_scan"] = {
        "first_sha256": first_sha,
        "second_sha256": second_sha,
        "stable": stable,
        "manifests_byte_identical": manifest_one.read_bytes() == manifest_two.read_bytes(),
        "closure_run_id": first_manifest.get("closure_run_id"),
    }
    summary["protected_artifacts"] = protected_evidence
    summary["plan"] = {
        "path": "reports/quarantine-plan.json",
        "sha256": plan.get("plan_sha256"),
        "authorization_state": plan.get("authorization_state"),
        "cleanup_mode": plan.get("cleanup_mode"),
        "automatic_purge": plan.get("automatic_purge"),
        "gate_evidence": gates,
        "entry_count": len(entries),
        "eligible_count": len(eligible),
        "blocked_count": len(entries) - len(eligible),
        "protected_blockers": protected_blockers,
        "root_identity": root_identity,
        "quarantine_root_identity": quarantine_identity,
    }
    summary["verify_plan"] = {
        "exit_code": verify_invocation["exit_code"],
        "ok": verify.get("ok"),
        "errors": verify.get("errors"),
        "plan_sha256": verify.get("plan_sha256"),
    }
    summary["fixture_bytes_unchanged_before_after_planning"] = True
    summary["quarantine_empty"] = not quarantine_entries
    summary["planning_sequence"] = sequence
    summary["forwarded_arguments"] = {
        "run_id": run_id,
        "manifest_closure_run_id": first_manifest.get("closure_run_id"),
        "plan_closure_run_id": plan.get("closure_run_id"),
        "workspace": str(fixture_root.parent.parent),
        "fixture_root": str(fixture_root),
        "manifest_path": str(manifest_one),
        "plan_root_identity": root_identity,
        "quarantine_root_identity": quarantine_identity,
    }

    _run_negative_controls(
        shim=shim,
        summary=summary,
        config=config,
        fixture=fixture,
        workspace=fixture_root.parent.parent,
        quarantine=quarantine,
        reports_dir=reports_dir,
        checkpoints_dir=checkpoints_dir,
        manifest_one=manifest_one,
        plan_path=plan_path,
        original_sha=first_sha,
        run_id=run_id,
        record=record,
        checked=checked,
    )


def _run_negative_controls(
    *,
    shim,
    summary: dict,
    config: str,
    fixture: str,
    workspace: Path,
    quarantine: str,
    reports_dir: Path,
    checkpoints_dir: Path,
    manifest_one: Path,
    plan_path: Path,
    original_sha: str,
    run_id: str,
    record,
    checked,
) -> None:
    bad_root = _invoke(
        shim,
        [
            "scan", "--config", config, "--root", str(workspace),
            "--closure-run-id", f"{run_id} bad-root",
            "--checkpoint", str(checkpoints_dir / "bad-root.sqlite3"),
            "--output", str(reports_dir / "bad-root-manifest.json"),
        ],
    )
    bad_root_receipt = record("negative-nonconfigured-root", bad_root)
    if bad_root["exception"] or bad_root["exit_code"] != 2:
        _problem(
            "DEMO_BAD_ROOT_NOT_REJECTED",
            f"exit={bad_root['exit_code']} exception={bad_root['exception']}; "
            f"raw receipt={bad_root_receipt}",
            receipt=bad_root_receipt,
        )
    if (bad_root["stdout_json"] or {}).get("error_code") != "ADAPTER_ROOT_NOT_CONFIGURED":
        _problem(
            "DEMO_BAD_ROOT_ERROR_CODE",
            f"raw receipt={bad_root_receipt}; "
            f"stdout_json={json.dumps(bad_root['stdout_json'], ensure_ascii=False)}",
            receipt=bad_root_receipt,
        )
    summary["controls"] = {
        "nonconfigured_root": {
            "argv": bad_root["argv"],
            "exit_code": bad_root["exit_code"],
            "error_code": (bad_root["stdout_json"] or {}).get("error_code"),
            "report": bad_root_receipt,
        }
    }

    injected = _invoke(
        shim,
        [
            "explain", "--manifest", str(manifest_one),
            "--artifact-id", INJECTED_MISSING_ARTIFACT_ID,
        ],
    )
    injected_receipt = record("negative-injected-cli-failure", injected)
    injected_payload = injected["stdout_json"] or {}
    if (
        injected["exception"]
        or injected["exit_code"] != 2
        or injected_payload.get("status") != "blocked"
        or injected_payload.get("error_code") != "ARTIFACT_NOT_FOUND"
    ):
        _problem(
            "DEMO_INJECTED_FAILURE_NOT_RECEIPTED",
            f"exit={injected['exit_code']} exception={injected['exception']}; "
            f"raw receipt={injected_receipt}; payload={json.dumps(injected_payload, ensure_ascii=False)}",
            receipt=injected_receipt,
        )
    summary["controls"]["injected_cli_failure"] = {
        "synthetic_injected_failure": True,
        "synthetic_artifact_id": INJECTED_MISSING_ARTIFACT_ID,
        "argv": injected["argv"],
        "exit_code": injected["exit_code"],
        "error_code": injected_payload.get("error_code"),
        "report": injected_receipt,
        "raw_failure_payload_saved_before_validation": True,
        "retried": False,
        "alternate_host_used": False,
        "permission_or_security_change_attempted": False,
    }

    saved_path = list(sys.path)
    saved_env = os.environ.pop(CORE_ROOT_ENV, None)
    try:
        sys.path[:] = []
        missing_core = _invoke(
            shim,
            ["doctor", "--config", config, "--root", fixture],
        )
    finally:
        sys.path[:] = saved_path
        _restore_env(CORE_ROOT_ENV, saved_env)
    missing_core_receipt = record("negative-missing-core", missing_core)
    unavailable = UNAVAILABLE in missing_core["stderr_text"]
    if missing_core["exception"] or missing_core["exit_code"] != 2 or not unavailable:
        _problem(
            "DEMO_MISSING_CORE_NOT_REPORTED",
            f"exit={missing_core['exit_code']} exception={missing_core['exception']} "
            f"stderr={missing_core['stderr_text']!r}; raw receipt={missing_core_receipt}",
            receipt=missing_core_receipt,
        )
    summary["controls"]["missing_core"] = {
        "argv": missing_core["argv"],
        "exit_code": missing_core["exit_code"],
        "stderr_contains_unavailable": unavailable,
        "method": "empty_sys_path_and_unset_core_env_control",
        "report": missing_core_receipt,
    }

    original_payload = json.loads(manifest_one.read_text(encoding="utf-8"))
    original_by_path = {item["path"]: item for item in original_payload["artifacts"]}
    drift_original = original_by_path[DRIFT_RELATIVE_PATH]
    original_digest = drift_original["digest"]["value"]
    drift_file = Path(fixture) / DRIFT_RELATIVE_PATH
    drift_file.write_bytes(
        drift_file.read_bytes()
        + b"\nSYNTHETIC drift control: fixture changed on purpose; not a real artifact\n"
    )
    drifted_digest = _sha256_file(drift_file)
    if drifted_digest == original_digest:
        _problem(
            "DEMO_DRIFT_DIGEST_UNCHANGED",
            f"{DRIFT_RELATIVE_PATH}: {original_digest}",
        )

    drift_manifest = reports_dir / "scan-manifest-drift.json"
    drift_scan = _invoke(
        shim,
        [
            "scan", "--config", config, "--root", fixture,
            "--closure-run-id", run_id,
            "--checkpoint", str(checkpoints_dir / "scan.sqlite3"),
            "--output", str(drift_manifest), "--max-files", "128",
        ],
    )
    drift_scan_receipt = record("negative-changed-fixture-drift-scan", drift_scan)
    _expect_ok(drift_scan, "drift-scan", drift_scan_receipt)
    drift_payload = json.loads(drift_manifest.read_text(encoding="utf-8"))
    drift_sha = drift_payload.get("manifest_sha256")
    if drift_payload.get("closure_run_id") != original_payload.get("closure_run_id"):
        _problem(
            "DEMO_DRIFT_CLOSURE_CHANGED",
            f"original={original_payload.get('closure_run_id')!r} "
            f"drift={drift_payload.get('closure_run_id')!r}; receipt={drift_scan_receipt}",
            receipt=drift_scan_receipt,
        )
    drifted_by_path = {item["path"]: item for item in drift_payload["artifacts"]}
    drift_item = drifted_by_path[DRIFT_RELATIVE_PATH]
    if drift_item["digest"]["value"] != drifted_digest:
        _problem(
            "DEMO_DRIFT_DIGEST_MISMATCH",
            f"file={drifted_digest} manifest={drift_item['digest']['value']}; "
            f"receipt={drift_scan_receipt}",
            receipt=drift_scan_receipt,
        )
    if drift_item["digest"]["value"] == original_digest:
        _problem(
            "DEMO_DRIFT_MANIFEST_DIGEST_UNCHANGED",
            f"digest={original_digest}; receipt={drift_scan_receipt}",
            receipt=drift_scan_receipt,
        )
    if drift_sha == original_sha:
        _problem(
            "DEMO_DRIFT_SCAN_UNCHANGED",
            f"manifest_sha256={drift_sha}; receipt={drift_scan_receipt}",
            receipt=drift_scan_receipt,
        )

    drift_verify = _invoke(
        shim,
        [
            "verify-plan", "--config", config, "--root", fixture,
            "--manifest", str(drift_manifest), "--quarantine-root", quarantine,
            "--plan", str(plan_path),
        ],
    )
    drift_verify_receipt = record("negative-changed-fixture-drift-verify", drift_verify)
    drift_errors = (drift_verify["stdout_json"] or {}).get("errors") or []
    if (
        drift_verify["exception"]
        or drift_verify["exit_code"] != 2
        or (drift_verify["stdout_json"] or {}).get("ok") is not False
        or "manifest_hash_drift" not in drift_errors
    ):
        _problem(
            "DEMO_DRIFT_NOT_REPORTED",
            f"exit={drift_verify['exit_code']} errors={drift_errors} "
            f"exception={drift_verify['exception']}; "
            f"payload={json.dumps(drift_verify['stdout_json'], ensure_ascii=False)}; "
            f"raw receipt={drift_verify_receipt}",
            receipt=drift_verify_receipt,
        )
    summary["controls"]["changed_fixture_drift"] = {
        "changed_path": DRIFT_RELATIVE_PATH,
        "closure_run_id": drift_payload.get("closure_run_id"),
        "closure_fixed": drift_payload.get("closure_run_id") == original_payload.get("closure_run_id"),
        "original_digest": original_digest,
        "drifted_digest": drift_item["digest"]["value"],
        "digest_changed": drift_item["digest"]["value"] != original_digest,
        "rescan_sha256": drift_sha,
        "original_manifest_sha256": original_sha,
        "verify_exit_code": drift_verify["exit_code"],
        "errors": drift_errors,
        "scan_report": drift_scan_receipt,
        "verify_report": drift_verify_receipt,
        "report": drift_verify_receipt,
        "cleanup_or_apply_called": False,
        "live_clearance_fabricated": False,
    }


def run_demo(workspace_argument: str | Path, core_root_argument: str | None, run_id: str) -> int:
    lexical_workspace = _lexical_absolute(workspace_argument)
    summary: dict = {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "generated_at": _utc_now(),
        "run_id": run_id,
        "requested_lexical_workspace": str(lexical_workspace),
        "workspace": str(lexical_workspace),
        "demo_entry": str(Path(__file__).resolve()),
        "shim_entry": str(SHIM),
        "rules_file": str(RULES),
        "reports_written": [],
        "boundaries": {
            "synthetic_fixtures_only": True,
            "quarantine_restore_purge_cleanup_calls": 0,
            "installs_downloads_network_calls": 0,
            "provider_or_model_imports": 0,
            "gateway_runtime_imports": 0,
            "external_processes": (
                "only the public core's read-only "
                "git -C <fixture-root> status --porcelain=v1 probe"
            ),
        },
    }

    # Refuse an existing lexical path BEFORE any resolve or write. os.path.lexists
    # is true for dangling links, so their targets are never created here.
    if _lexically_exists(lexical_workspace):
        summary["error_code"] = "DEMO_WORKSPACE_EXISTS"
        summary["detail"] = (
            "lexical workspace path already exists (file, directory or dangling link); "
            f"no resolve and no write was attempted: {lexical_workspace}"
        )
        _emit(summary)
        return 2
    workspace = lexical_workspace.resolve(strict=False)
    summary["workspace"] = str(workspace)
    if _lexically_exists(workspace):
        summary["error_code"] = "DEMO_WORKSPACE_EXISTS"
        summary["detail"] = (
            "resolved workspace path already exists after lexical check; "
            f"no write was attempted: {workspace}"
        )
        _emit(summary)
        return 2

    previous_core_env = os.environ.get(CORE_ROOT_ENV)
    previous_git_ceiling = os.environ.get("GIT_CEILING_DIRECTORIES")
    created = False
    try:
        shim = _load_shim()
        core_root, selection_mode, selection_source = _select_core(core_root_argument)
        os.environ[CORE_ROOT_ENV] = str(core_root)
        if shutil.which("git") is None:
            _problem(
                "DEMO_GIT_UNAVAILABLE",
                "the public core's plan stage needs a read-only `git status` probe "
                "on PATH; no Git write/init/clone operation is performed or needed",
            )
        summary["core_selection"] = {
            "mode": selection_mode,
            "selected_via": selection_source,
            "core_root": str(core_root),
            "core_entry": str(core_root / CORE_ENTRY_NAME),
            "artifact_lifecycle_core_origin": None,
            "origin_under_core_root": False,
        }

        config_path, fixture_root, quarantine_root, reports_dir, checkpoints_dir = _build_workspace(workspace)
        created = True
        summary["layout"] = {
            "config": config_path.relative_to(workspace).as_posix(),
            "fixture_root": fixture_root.relative_to(workspace).as_posix(),
            "quarantine_root": quarantine_root.relative_to(workspace).as_posix(),
            "reports": reports_dir.relative_to(workspace).as_posix(),
            "checkpoints": checkpoints_dir.relative_to(workspace).as_posix(),
        }

        os.environ["GIT_CEILING_DIRECTORIES"] = str(workspace)
        summary["git_ceiling"] = {
            "bound_to_workspace": str(workspace),
            "previous": previous_git_ceiling,
            "restored": False,
        }

        _run_sequence(
            shim=shim,
            summary=summary,
            config_path=config_path,
            fixture_root=fixture_root,
            quarantine_root=quarantine_root,
            reports_dir=reports_dir,
            checkpoints_dir=checkpoints_dir,
            run_id=run_id,
        )
        summary["status"] = "ok"
    except _Blocked as exc:
        summary["error_code"] = exc.code
        summary["detail"] = exc.detail
        if exc.receipt:
            summary["failure_receipt"] = exc.receipt
    except OSError as exc:
        summary["error_code"] = "DEMO_FILESYSTEM_ERROR"
        summary["detail"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # last-resort honest reporting; workspace evidence is retained
        summary["error_code"] = "DEMO_UNEXPECTED_ERROR"
        summary["detail"] = f"{type(exc).__name__}: {exc}"
    finally:
        _restore_env(CORE_ROOT_ENV, previous_core_env)
        _restore_env("GIT_CEILING_DIRECTORIES", previous_git_ceiling)
        summary["environment_restored"] = {
            CORE_ROOT_ENV: os.environ.get(CORE_ROOT_ENV) == previous_core_env,
            "GIT_CEILING_DIRECTORIES": os.environ.get("GIT_CEILING_DIRECTORIES") == previous_git_ceiling,
        }
        if "git_ceiling" in summary:
            summary["git_ceiling"]["restored"] = (
                os.environ.get("GIT_CEILING_DIRECTORIES") == previous_git_ceiling
            )

    if created and (workspace / "reports").is_dir():
        _write_json(workspace / "reports" / "demo-summary.json", summary)
    _emit(summary)
    return 0 if summary.get("status") == "ok" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the source-checkout synthetic inspect/plan loop through the "
            "Gateway artifact lifecycle shim and public core."
        )
    )
    parser.add_argument("--workspace", required=True, help="new workspace directory to create")
    parser.add_argument("--core-root", help=f"explicit {CORE_ROOT_ENV} source checkout")
    parser.add_argument("--run-id", default="synthetic demo closure 01")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_demo(args.workspace, args.core_root, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
