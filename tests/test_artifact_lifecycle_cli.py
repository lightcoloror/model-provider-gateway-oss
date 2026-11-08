"""Stdlib unittest CLI checks for the source-checkout artifact lifecycle port.

These checks load the real Gateway shim and the real public core in-process
through the demo/CLI entry points, never a mock lifecycle engine. The public
core is supplied explicitly with ``ARTIFACT_LIFECYCLE_CORE_ROOT``; without that
selection the module is skipped so the candidate can still be inspected without
claiming runtime proof.

The checks assert actual module origins are under the supplied Core checkout,
capture stdout/SystemExit, and exercise: the full doctor -> scan -> explain ->
plan -> verify-plan loop, repeated-scan hash stability, unchanged synthetic
fixture bytes around the read-only planning sequence, protected
provider-profile/ledger roles, nonconfigured-root rejection, missing-core
reporting, a clearly synthetic injected CLI failure receipt, changed-fixture
drift with a fixed closure id, existing-workspace/file rejection without any
write, and exact spaces/CJK argument forwarding.

Core CLI facts exercised here: only ``scan`` takes ``--closure-run-id``. The
``plan`` command has no such flag and reads the closure id from the scan
manifest, so the checks verify scan argv forwarding, plan ``--manifest``
forwarding, and the closure values echoed back from the manifest/plan outputs.

These are stdlib ``unittest`` checks only. They are not a pytest/dev-compat
claim, do not import the Gateway runtime or any provider/model route, and never
apply, quarantine, restore, purge or clean up artifacts. The only child process
is the public core's own read-only ``git -C <fixture-root> status
--porcelain=v1`` probe.

Run from a clean source checkout with an explicit public Core:

    ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
      python tests/test_artifact_lifecycle_cli.py

or with unittest discovery:

    ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
      python -m unittest discover -s tests -p "test_artifact_lifecycle_cli.py"
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "scripts" / "artifact_lifecycle_adapter.py"
DEMO = ROOT / "examples" / "artifact_lifecycle_demo.py"
RULES = ROOT / "artifact-lifecycle" / "model-provider-gateway-artifacts.v1.json"

CORE_MODULE = "project_artifact_adapter"
CORE_ENTRY_NAME = "project_artifact_adapter.py"
CORE_ROOT_ENV = "ARTIFACT_LIFECYCLE_CORE_ROOT"
UNAVAILABLE = "ARTIFACT_LIFECYCLE_CORE_UNAVAILABLE"
RUN_ID = "synthetic 状态 closure 01"
EXPECTED_FIXTURE_PATHS = {
    "build/candidate-wheel.whl",
    "dist/candidate-sdist.tar.gz",
    "test-cache/pytest.cache",
    "runtime/provider-profile.ref",
    "runtime/route-plan.json",
    "runtime/consent.json",
    "runtime/execution-ledger.json",
    "runtime/capability-receipt.json",
    "runtime/license-evidence.txt",
}

_STATE: dict = {}


def _supplied_core_root() -> Path:
    raw = os.environ.get(CORE_ROOT_ENV)
    if not raw:
        raise unittest.SkipTest(
            f"{CORE_ROOT_ENV} is required: point it at the separate public "
            "artifact-lifecycle-core source checkout"
        )
    root = Path(raw).expanduser().resolve()
    if not (root / CORE_ENTRY_NAME).is_file():
        raise AssertionError(f"{CORE_ROOT_ENV} must contain {CORE_ENTRY_NAME}: {root}")
    return root


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(module, argv: list[str]) -> tuple[int | None, dict | None, str, str]:
    out, err = io.StringIO(), io.StringIO()
    exit_code = None
    value = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            value = module.main(list(argv))
    except SystemExit as exc:
        code = exc.code
        exit_code = 0 if code is None else int(code)
    else:
        if isinstance(value, int):
            exit_code = int(value)
    stdout = out.getvalue()
    payload = None
    if stdout.strip():
        payload = json.loads(stdout)
    return exit_code, payload, stdout, err.getvalue()


def _tree(workspace: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in workspace.rglob("*")
        if path.is_file()
    }


def _step_invocation(workspace: Path, step: dict) -> dict:
    return json.loads((workspace / step["report"]).read_text(encoding="utf-8"))


def setUpModule() -> None:
    core_root = _supplied_core_root()
    shim = _load_module("gateway_shim_under_test", SHIM)
    demo = _load_module("gateway_demo_under_test", DEMO)

    temporary = tempfile.TemporaryDirectory(prefix="gateway-artifact-lifecycle-")
    unittest.addModuleCleanup(temporary.cleanup)
    workspace = Path(temporary.name) / "synthetic demo 状态 with space"

    previous_core_env = os.environ.get(CORE_ROOT_ENV)
    os.environ[CORE_ROOT_ENV] = str(core_root)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exit_code = demo.main(
                [
                    "--workspace", str(workspace),
                    "--core-root", str(core_root),
                    "--run-id", RUN_ID,
                ]
            )
    finally:
        if previous_core_env is None:
            os.environ.pop(CORE_ROOT_ENV, None)
        else:
            os.environ[CORE_ROOT_ENV] = previous_core_env

    summary_path = workspace / "reports" / "demo-summary.json"
    summary = None
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    _STATE.update(
        core_root=core_root,
        shim=shim,
        demo=demo,
        temporary=temporary,
        workspace=workspace,
        exit_code=exit_code,
        stdout=out.getvalue(),
        stderr=err.getvalue(),
        summary=summary,
    )


class ArtifactLifecycleCliTests(unittest.TestCase):
    maxDiff = None

    def _summary(self) -> dict:
        summary = _STATE.get("summary")
        self.assertEqual(
            _STATE.get("exit_code"),
            0,
            msg=(
                f"demo exit={_STATE.get('exit_code')}\n"
                f"stderr={_STATE.get('stderr', '')[:2000]}\n"
                f"summary={json.dumps(summary, ensure_ascii=False, sort_keys=True)[:4000]}"
            ),
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary.get("status"), "ok", msg=json.dumps(summary, ensure_ascii=False)[:4000])
        return summary

    def test_demo_captures_actual_cli_json_and_stdout(self) -> None:
        summary = self._summary()
        self.assertEqual(summary["schema_version"], "model-provider-gateway-artifact-lifecycle-demo.v1")
        self.assertEqual(json.loads(_STATE["stdout"]), summary)
        self.assertNotIn("Traceback", _STATE["stderr"])

        steps = [item["step"] for item in summary["planning_sequence"]]
        self.assertEqual(
            steps,
            [
                "doctor",
                "scan",
                "scan-repeat",
                "explain-provider-profile.ref",
                "explain-execution-ledger.json",
                "plan",
                "verify-plan",
            ],
        )
        workspace = _STATE["workspace"]
        for item in summary["planning_sequence"]:
            self.assertEqual(item["exit_code"], 0, msg=item["step"])
            self.assertIsNotNone(item["stdout_json"], msg=item["step"])
            invocation = _step_invocation(workspace, item)
            for key in ("argv", "exit_code", "stdout_json", "stdout_text", "stderr_text", "exception"):
                self.assertIn(key, invocation, msg=f"{item['step']}:{key}")
            self.assertIsNone(invocation["exception"], msg=item["step"])

        sequence = {item["step"]: item for item in summary["planning_sequence"]}
        self.assertEqual(sequence["doctor"]["stdout_json"]["status"], "ready")
        self.assertEqual(sequence["scan"]["stdout_json"]["status"], "ok")
        self.assertEqual(sequence["scan"]["stdout_json"]["artifact_count"], len(EXPECTED_FIXTURE_PATHS))
        self.assertEqual(sequence["plan"]["stdout_json"]["status"], "ok")
        self.assertIs(sequence["verify-plan"]["stdout_json"]["ok"], True)
        self.assertEqual(sequence["verify-plan"]["stdout_json"]["errors"], [])
        self.assertEqual(summary["manifest"]["sha256"], sequence["scan"]["stdout_json"]["manifest_sha256"])

    def test_repeated_scan_stable_and_planning_sequence_read_only(self) -> None:
        summary = self._summary()
        repeated = summary["repeated_scan"]
        self.assertTrue(repeated["stable"])
        self.assertEqual(repeated["first_sha256"], repeated["second_sha256"])
        self.assertTrue(repeated["manifests_byte_identical"])
        self.assertEqual(repeated["closure_run_id"], RUN_ID)

        self.assertTrue(summary["fixture_bytes_unchanged_before_after_planning"])
        self.assertTrue(summary["quarantine_empty"])
        self.assertEqual(summary["manifest"]["artifact_count"], len(EXPECTED_FIXTURE_PATHS))
        self.assertEqual(summary["manifest"]["cleanup_mode"], "plan_only")
        self.assertIs(summary["manifest"]["automatic_purge"], False)

        plan = summary["plan"]
        self.assertEqual(plan["authorization_state"], "not_authorized")
        self.assertEqual(plan["cleanup_mode"], "plan_only")
        self.assertIs(plan["automatic_purge"], False)
        gate = plan["gate_evidence"]
        self.assertEqual(gate["owner_state"], "unknown")
        self.assertEqual(gate["process_state"], "unknown")
        self.assertEqual(gate["dispatch_state"], "unknown")
        self.assertEqual(gate["git_state"], "not_repository")
        self.assertEqual(plan["entry_count"], len(EXPECTED_FIXTURE_PATHS))
        self.assertEqual(plan["eligible_count"], 0)
        self.assertEqual(plan["blocked_count"], len(EXPECTED_FIXTURE_PATHS))

    def test_protected_roles_are_explained_and_blocked(self) -> None:
        summary = self._summary()
        evidence = {item["path"]: item for item in summary["protected_artifacts"]}
        self.assertEqual(evidence["runtime/provider-profile.ref"]["artifact_role"], "secret_or_account_state")
        self.assertIs(evidence["runtime/provider-profile.ref"]["pin"], True)
        self.assertEqual(evidence["runtime/execution-ledger.json"]["artifact_role"], "evidence_receipt_manifest")
        self.assertIs(evidence["runtime/execution-ledger.json"]["pin"], True)

        blockers = summary["plan"]["protected_blockers"]
        self.assertIn("artifact_pinned", blockers["runtime/provider-profile.ref"])
        self.assertIn("protected_role:secret_or_account_state", blockers["runtime/provider-profile.ref"])
        self.assertIn("artifact_pinned", blockers["runtime/execution-ledger.json"])
        self.assertIn("protected_role:evidence_receipt_manifest", blockers["runtime/execution-ledger.json"])

        for item in summary["planning_sequence"]:
            if item["step"].startswith("explain-"):
                self.assertIs(item["stdout_json"]["authorizes_operation"], False)

    def test_negative_controls_are_real_cli_failures(self) -> None:
        summary = self._summary()
        workspace = _STATE["workspace"]
        controls = summary["controls"]

        nonconfigured = controls["nonconfigured_root"]
        self.assertEqual(nonconfigured["exit_code"], 2)
        self.assertEqual(nonconfigured["error_code"], "ADAPTER_ROOT_NOT_CONFIGURED")
        self.assertTrue((workspace / nonconfigured["report"]).is_file())

        injected = controls["injected_cli_failure"]
        self.assertTrue(injected["synthetic_injected_failure"])
        self.assertEqual(injected["exit_code"], 2)
        self.assertEqual(injected["error_code"], "ARTIFACT_NOT_FOUND")
        self.assertTrue(injected["raw_failure_payload_saved_before_validation"])
        self.assertFalse(injected["retried"])
        self.assertFalse(injected["alternate_host_used"])
        self.assertFalse(injected["permission_or_security_change_attempted"])
        injected_report = json.loads((workspace / injected["report"]).read_text(encoding="utf-8"))
        self.assertEqual(injected_report["stdout_json"]["status"], "blocked")
        self.assertEqual(injected_report["stdout_json"]["error_code"], "ARTIFACT_NOT_FOUND")

        missing_core = controls["missing_core"]
        self.assertEqual(missing_core["exit_code"], 2)
        self.assertTrue(missing_core["stderr_contains_unavailable"])
        self.assertIn("empty_sys_path", missing_core["method"])

        drift = controls["changed_fixture_drift"]
        self.assertEqual(drift["changed_path"], "runtime/route-plan.json")
        self.assertEqual(drift["closure_run_id"], RUN_ID)
        self.assertTrue(drift["closure_fixed"])
        self.assertTrue(drift["digest_changed"])
        self.assertNotEqual(drift["original_digest"], drift["drifted_digest"])
        self.assertNotEqual(drift["rescan_sha256"], drift["original_manifest_sha256"])
        self.assertEqual(drift["verify_exit_code"], 2)
        self.assertIn("manifest_hash_drift", drift["errors"])
        self.assertFalse(drift["cleanup_or_apply_called"])
        self.assertFalse(drift["live_clearance_fabricated"])

    def test_demo_refuses_existing_workspace_without_writing(self) -> None:
        self._summary()
        workspace = _STATE["workspace"]
        summary_path = workspace / "reports" / "demo-summary.json"
        before_summary = summary_path.read_bytes()
        before_tree = _tree(workspace)

        exit_code, payload, _, _ = _invoke(
            _STATE["demo"],
            ["--workspace", str(workspace), "--core-root", str(_STATE["core_root"])],
        )
        self.assertEqual(exit_code, 2)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["error_code"], "DEMO_WORKSPACE_EXISTS")
        self.assertEqual(summary_path.read_bytes(), before_summary)
        self.assertEqual(_tree(workspace), before_tree)

        base = Path(_STATE["temporary"].name)
        existing_file = base / "existing-workspace-file"
        existing_file.write_bytes(b"SYNTHETIC placeholder; must never be overwritten\n")
        before_parent = sorted(path.name for path in base.iterdir())
        before_bytes = existing_file.read_bytes()
        exit_code, payload, _, _ = _invoke(
            _STATE["demo"],
            ["--workspace", str(existing_file), "--core-root", str(_STATE["core_root"])],
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["error_code"], "DEMO_WORKSPACE_EXISTS")
        self.assertEqual(existing_file.read_bytes(), before_bytes)
        self.assertEqual(sorted(path.name for path in base.iterdir()), before_parent)

        nonexistent = base / "not-created-yet"
        self.assertTrue(_STATE["demo"]._lexically_exists(existing_file))
        self.assertFalse(_STATE["demo"]._lexically_exists(nonexistent))
        self.assertTrue(_STATE["demo"]._lexical_absolute("lexical/../probe").is_absolute())
        self.assertEqual(
            _STATE["demo"]._lexical_absolute("lexical/../probe"),
            Path.cwd() / "probe",
        )

    def test_core_origin_and_argument_forwarding(self) -> None:
        summary = self._summary()
        core_root = _STATE["core_root"]
        if str(core_root) not in sys.path:
            sys.path.insert(0, str(core_root))
        spec = importlib.util.find_spec(CORE_MODULE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.origin)
        self.assertTrue(
            Path(spec.origin).resolve().is_relative_to(core_root),
            msg=f"adapter origin {spec.origin} is not under supplied core {core_root}",
        )
        core_module = sys.modules.get("artifact_lifecycle_core")
        if core_module is None:
            import artifact_lifecycle_core as core_module  # noqa: F401
        self.assertTrue(
            Path(core_module.__file__).resolve().is_relative_to(core_root),
            msg=f"core origin {core_module.__file__} is not under supplied core {core_root}",
        )
        self.assertTrue(summary["core_selection"]["origin_under_core_root"])
        self.assertTrue(summary["core_selection"]["artifact_lifecycle_core_origin"])

        workspace_text = summary["workspace"]
        self.assertIn(" ", workspace_text)
        self.assertTrue(any(ord(character) > 127 for character in workspace_text))
        workspace = Path(workspace_text)
        fixture_root = workspace / "fixtures" / "artifact-lifecycle"
        self.assertEqual(summary["plan"]["root_identity"], str(fixture_root.resolve()))

        sequence = {item["step"]: item for item in summary["planning_sequence"]}
        scan_argv = _step_invocation(workspace, sequence["scan"])["argv"]
        self.assertIn("--closure-run-id", scan_argv)
        self.assertEqual(scan_argv[scan_argv.index("--closure-run-id") + 1], RUN_ID)
        self.assertIn(str(fixture_root.resolve()), scan_argv)

        manifest_path = (workspace / summary["manifest"]["path"]).resolve()
        plan_argv = _step_invocation(workspace, sequence["plan"])["argv"]
        self.assertNotIn("--closure-run-id", plan_argv)
        self.assertIn("--manifest", plan_argv)
        self.assertEqual(plan_argv[plan_argv.index("--manifest") + 1], str(manifest_path))
        self.assertIn(str(fixture_root.resolve()), plan_argv)
        self.assertIn(str((workspace / "quarantine").resolve()), plan_argv)
        self.assertEqual(summary["forwarded_arguments"]["manifest_path"], str(manifest_path))

        self.assertEqual(summary["forwarded_arguments"]["manifest_closure_run_id"], RUN_ID)
        self.assertEqual(summary["forwarded_arguments"]["plan_closure_run_id"], RUN_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
