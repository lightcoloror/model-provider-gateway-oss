# Artifact lifecycle (optional public core)

This page documents the Gateway-owned, source-checkout integration with the
separate **Artifact Lifecycle Core** public distribution. Gateway orchestrates
the Core through a thin shim; it does **not** vendor, install, download or
search for the Core, and it does not add an installed Gateway console entry for
this feature.

## Fixed public Core reference

- Repository: <https://github.com/lightcoloror/artifact-lifecycle-core-oss>
- Public commit declared for this integration: `fab239085724458c2a6b9452d0a1ba4f4012593e`
- License: Apache-2.0 (separate first-party public edition, not vendored here)
- Gateway license for this integration: AGPL-3.0-only

The Core's own `agent-tool-manifest.v1.json` records its reviewed private-source
provenance commit `acb148f9d1fd0e45225288349ca9975a41e668ee`; that manifest is
part of the Core checkout and is not modified by Gateway. Gateway's
`LICENSE`, `pyproject.toml` and `THIRD_PARTY_NOTICES.md` are unchanged by this
integration.

## Select the Core

The shim at `scripts/artifact_lifecycle_adapter.py` resolves the Core in one of
two ways, and never falls back to a sibling directory:

1. **Explicit source checkout** (recommended for a clean checkout):

   ```sh
   export ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss
   ```

   The directory must contain `project_artifact_adapter.py`.

2. **Ordinary module resolution**: install the separate
   `artifact-lifecycle-core` distribution in the active environment and leave
   `ARTIFACT_LIFECYCLE_CORE_ROOT` unset. The shim uses
   `importlib.util.find_spec` and forwards to the installed module; it does not
   infer installed-wheel proof from a mock.

If neither selection resolves, the shim prints
`ARTIFACT_LIFECYCLE_CORE_UNAVAILABLE` to stderr and exits `2`. No automatic
install, download, sibling search or fallback source selection is performed.

The Core has no runtime dependencies for this flow. Gateway's optional
`.[execution]`, `.[proxy]` and `.[ui]` extras are unrelated and are not needed.
The read-only plan gate uses the Core's own
`git -C <fixture-root> status --porcelain=v1` probe, so **Git must be on
`PATH`** for `plan`/`verify-plan`; no Git write/init/clone/fetch/config/push
operation is performed by Gateway or by the demo.

## New-workspace synthetic demo

The demo refuses to touch an existing path: it exits nonzero with
`DEMO_WORKSPACE_EXISTS` before any overwrite. Supply a new, empty destination.

```sh
ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python examples/artifact_lifecycle_demo.py \
  --workspace "/path/to/a brand-new synthetic workspace" \
  --core-root /path/to/artifact-lifecycle-core-oss \
  --run-id "synthetic closure 01"
```

The demo creates only clearly marked synthetic fixture text. Rule filenames such
as `build/candidate-wheel.whl` and `dist/candidate-sdist.tar.gz` are materialized
as obviously synthetic placeholders and are never claimed to be genuine
wheel/sdist output. It keeps the scan checkpoint, every captured JSON report and
an **empty** quarantine directory inside the new workspace, drives the actual
shim CLI sequence

    doctor -> scan -> explain -> plan -> verify-plan

and records each stdout JSON payload and exit code under
`<workspace>/reports/`. It also demonstrates bounded negative controls:
nonconfigured-root rejection, missing-Core reporting and changed-fixture drift
(the original plan must fail verification against the rescanned manifest).

`GIT_CEILING_DIRECTORIES` is bound to the demo's own new workspace for the
duration of the planning loop and the previous environment is restored
afterwards, so the Core's Git probe cannot discover a parent user repository. A
`not_repository` result is the expected synthetic non-repository state; it is
not proof of clean Git state.

## Source CLI commands

The demo is the complete inspect/plan loop. To drive the shim directly from a
source checkout (using the workspace the demo created), run:

```sh
ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python scripts/artifact_lifecycle_adapter.py doctor \
  --config "/path/to/workspace/artifact-lifecycle/model-provider-gateway-artifacts.v1.json" \
  --root "/path/to/workspace/fixtures/artifact-lifecycle"

ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python scripts/artifact_lifecycle_adapter.py scan \
  --config "/path/to/workspace/artifact-lifecycle/model-provider-gateway-artifacts.v1.json" \
  --root "/path/to/workspace/fixtures/artifact-lifecycle" \
  --closure-run-id "synthetic closure 01" \
  --checkpoint "/path/to/workspace/checkpoints/scan.sqlite3" \
  --output "/path/to/workspace/reports/scan-manifest-1.json"

ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python scripts/artifact_lifecycle_adapter.py explain \
  --manifest "/path/to/workspace/reports/scan-manifest-1.json" \
  --artifact-id "<artifact-id from the manifest>"

ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python scripts/artifact_lifecycle_adapter.py plan \
  --config "/path/to/workspace/artifact-lifecycle/model-provider-gateway-artifacts.v1.json" \
  --root "/path/to/workspace/fixtures/artifact-lifecycle" \
  --manifest "/path/to/workspace/reports/scan-manifest-1.json" \
  --quarantine-root "/path/to/workspace/quarantine" \
  --output "/path/to/workspace/reports/quarantine-plan.json"

ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python scripts/artifact_lifecycle_adapter.py verify-plan \
  --config "/path/to/workspace/artifact-lifecycle/model-provider-gateway-artifacts.v1.json" \
  --root "/path/to/workspace/fixtures/artifact-lifecycle" \
  --manifest "/path/to/workspace/reports/scan-manifest-1.json" \
  --quarantine-root "/path/to/workspace/quarantine" \
  --plan "/path/to/workspace/reports/quarantine-plan.json"
```

## Stdlib checks

The new CLI checks use only the standard library `unittest` runner and the real
shim/Core in-process. They load the real modules, validate that the loaded
origins are under the supplied Core, capture stdout/`SystemExit`, and never
import the Gateway runtime or any provider/model route.

```sh
ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python tests/test_artifact_lifecycle_cli.py

# or
ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python -m unittest discover -s tests -p "test_artifact_lifecycle_cli.py"
```

These checks are not a pytest or `.[dev]` compatibility claim. The existing
source-checkout `python -m pytest -q` invocation remains optional: it needs a
compatible dev environment (`pytest>=8.3,<9`) and the five baseline pytest cases
are not rerun or claimed by this integration.

## Plan-only and evidence boundaries

- The plan is metadata only: `authorization_state` stays `not_authorized`,
  `cleanup_mode` stays `plan_only` and `automatic_purge` stays `false`.
- Owner, process and dispatch gates remain `unknown`. The demo does not
  fabricate owner-idle/process-idle clearance; those fields are retained as
  unknown and keep their blockers.
- Protected roles such as `secret_or_account_state` and
  `evidence_receipt_manifest` stay pinned and blocked in explain/plan output.
- The demo never calls `synthetic-quarantine`, `synthetic-restore`, apply,
  purge or real cleanup, never mutates the fixture bytes during the read-only
  planning sequence, and leaves the quarantine directory empty.
- A successful local synthetic demo is not proof of installed-wheel behavior,
  current online provider state, real usage, or authority to operate on real
  artifacts.
- Source-checkout support only: this edition adds no installed
  `model-provider-gateway` console entry for the lifecycle adapter.
