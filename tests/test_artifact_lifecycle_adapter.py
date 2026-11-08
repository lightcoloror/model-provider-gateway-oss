"""Synthetic-only contract checks for the Gateway artifact lifecycle port.

The optional public artifact lifecycle core is not vendored. These tests use
only the runner-provided ``ARTIFACT_LIFECYCLE_CORE_ROOT``; without that
selection the module is skipped so the baseline Gateway test suite still runs.
Fixtures are synthetic bytes written into test-owned temporary roots and are
never committed. No provider, model, network, install or real cleanup is
exercised.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE_MODULE = "project_artifact_adapter"
CORE_ENTRY_NAME = "project_artifact_adapter.py"
RULES = ROOT / "artifact-lifecycle" / "model-provider-gateway-artifacts.v1.json"
SHIM = ROOT / "scripts" / "artifact_lifecycle_adapter.py"


def _selected_core_root() -> Path | None:
    raw = os.environ.get("ARTIFACT_LIFECYCLE_CORE_ROOT")
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    if not (root / CORE_ENTRY_NAME).is_file():
        raise RuntimeError(
            "ARTIFACT_LIFECYCLE_CORE_ROOT must contain the public core entry "
            f"{CORE_ENTRY_NAME}: {root}"
        )
    return root


CORE_ROOT = _selected_core_root()
if CORE_ROOT is None:
    pytest.skip(
        "optional public artifact-lifecycle core not selected; set "
        "ARTIFACT_LIFECYCLE_CORE_ROOT to its source checkout",
        allow_module_level=True,
    )
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

import project_artifact_adapter as adapter  # noqa: E402

FIXTURE_BYTES = {
    "build/candidate-wheel.whl": b"SYNTHETIC wheel placeholder; not a real wheel\n",
    "dist/candidate-sdist.tar.gz": b"SYNTHETIC sdist placeholder; not a real archive\n",
    "test-cache/pytest.cache": b"SYNTHETIC cache placeholder\n",
    "runtime/provider-profile.ref": b"SYNTHETIC secret reference; no credential material\n",
    "runtime/route-plan.json": b'{"synthetic": true, "route": "offline"}\n',
    "runtime/consent.json": b'{"synthetic": true, "consent": "fixture-only"}\n',
    "runtime/execution-ledger.json": b'{"synthetic": true, "executions": []}\n',
    "runtime/capability-receipt.json": b'{"synthetic": true, "receipt": "fixture-only"}\n',
    "runtime/license-evidence.txt": b"SYNTHETIC license evidence for fixture-only tests\n",
}


def _synthetic_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a repository-shaped fixture in a test-owned temporary root."""
    repo = tmp_path / "repo"
    config = repo / "artifact-lifecycle" / RULES.name
    config.parent.mkdir(parents=True)
    config.write_bytes(RULES.read_bytes())
    fixture_root = repo / "fixtures" / "artifact-lifecycle"
    for relative, payload in FIXTURE_BYTES.items():
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return config, fixture_root


def _load_shim():
    spec = importlib.util.spec_from_file_location(
        "gateway_artifact_lifecycle_adapter", SHIM
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_core(directory: Path) -> Path:
    """Write a test-owned core entry that records argv and exits with a code."""
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / CORE_ENTRY_NAME
    entry.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['SHIM_STUB_MARKER']).write_text(\n"
        "    json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n', encoding='utf-8')\n"
        "raise SystemExit(int(os.environ.get('SHIM_STUB_EXIT', '0')))\n",
        encoding="utf-8",
    )
    return entry


def test_gateway_artifact_adapter_is_fixture_only_and_idempotent(tmp_path):
    config, fixture_root = _synthetic_repo(tmp_path)
    checkpoint = tmp_path / "checkpoint.sqlite3"
    first = adapter.build_manifest(config, fixture_root, "closure-gateway-fixture", checkpoint)
    second = adapter.build_manifest(config, fixture_root, "closure-gateway-fixture", checkpoint)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert isinstance(first["manifest_sha256"], str) and len(first["manifest_sha256"]) == 64
    items = {item["path"]: item for item in first["artifacts"]}
    assert set(items) == set(FIXTURE_BYTES)
    assert not items["build/candidate-wheel.whl"]["pin"]
    assert items["runtime/provider-profile.ref"]["artifact_role"] == "secret_or_account_state"
    assert items["runtime/execution-ledger.json"]["pin"]
    assert first["cleanup_mode"] == "plan_only"
    assert first["automatic_purge"] is False
    assert first["adapter"]["synthetic_only"] is True
    assert first["adapter"]["real_mutation_count"] == 0


def test_gateway_doctor_separates_large_build_disk_gate_from_online_truth(tmp_path):
    config, fixture_root = _synthetic_repo(tmp_path)
    report = adapter.doctor(config, fixture_root)
    assert report["current_online"] is False
    assert report["execution_authority"] == "none_metadata_only"
    assert report["cleanup_mode"] == "plan_only"
    assert report["filesystem_mutation_performed"] is False
    assert report["real_mutation_count"] == 0


def test_gateway_adapter_shim_forwards_argv_and_exit_status(tmp_path, monkeypatch):
    shim = _load_shim()
    core_root = tmp_path / "core root with space"
    _stub_core(core_root)
    marker = tmp_path / "forwarded-argv.json"
    monkeypatch.setenv("ARTIFACT_LIFECYCLE_CORE_ROOT", str(core_root))
    monkeypatch.setenv("SHIM_STUB_MARKER", str(marker))
    monkeypatch.setenv("SHIM_STUB_EXIT", "7")
    argv = [
        "doctor",
        "--config",
        "synthetic dir/config.json",
        "--root",
        "fixtures/artifact-lifecycle",
        "--owner-state",
        "space and CJK 状态",
    ]
    with pytest.raises(SystemExit) as excinfo:
        shim.main(argv)
    assert excinfo.value.code == 7
    assert json.loads(marker.read_text(encoding="utf-8")) == argv


def test_gateway_adapter_shim_uses_installed_module_resolution(tmp_path, monkeypatch):
    shim = _load_shim()
    entry = _stub_core(tmp_path / "installed core")
    marker = tmp_path / "installed-argv.json"
    spec = importlib.util.spec_from_file_location(CORE_MODULE, entry)
    monkeypatch.delenv("ARTIFACT_LIFECYCLE_CORE_ROOT", raising=False)
    monkeypatch.setenv("SHIM_STUB_MARKER", str(marker))
    monkeypatch.setenv("SHIM_STUB_EXIT", "0")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs: spec)
    with pytest.raises(SystemExit) as excinfo:
        shim.main(["--config", "x y"])
    assert excinfo.value.code == 0
    assert json.loads(marker.read_text(encoding="utf-8")) == ["--config", "x y"]


def test_gateway_adapter_shim_reports_missing_core_clearly(tmp_path, monkeypatch, capsys):
    shim = _load_shim()
    monkeypatch.delenv("ARTIFACT_LIFECYCLE_CORE_ROOT", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs: None)
    with pytest.raises(SystemExit) as excinfo:
        shim.main(["doctor"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "ARTIFACT_LIFECYCLE_CORE_UNAVAILABLE" in captured.err
