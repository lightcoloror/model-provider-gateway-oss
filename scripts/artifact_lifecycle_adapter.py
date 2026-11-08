"""Gateway-owned thin entry to the optional public artifact lifecycle core.

The Gateway does not vendor the artifact lifecycle core. Operators select the
separate Apache-2.0 public core explicitly, either by pointing
``ARTIFACT_LIFECYCLE_CORE_ROOT`` at a source checkout that contains
``project_artifact_adapter.py`` or by installing that public distribution so
the entry resolves as an ordinary module.

The shim forwards its own argv to the selected core entry without splitting or
requoting it and propagates the core exit status. It never installs, downloads,
searches parent or sibling directories, or routes or executes a provider/model.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import runpy
import sys
from pathlib import Path

CORE_ROOT_ENV = "ARTIFACT_LIFECYCLE_CORE_ROOT"
CORE_MODULE = "project_artifact_adapter"
CORE_ENTRY_NAME = "project_artifact_adapter.py"
UNAVAILABLE = "ARTIFACT_LIFECYCLE_CORE_UNAVAILABLE"


def _explicit_core_root() -> Path | None:
    raw = os.environ.get(CORE_ROOT_ENV)
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    entry = root / CORE_ENTRY_NAME
    if not root.is_dir() or not entry.is_file():
        print(
            f"{UNAVAILABLE}: {CORE_ROOT_ENV} must name a directory containing "
            f"{CORE_ENTRY_NAME}: {root}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return root


def _installed_core_spec() -> importlib.machinery.ModuleSpec | None:
    try:
        return importlib.util.find_spec(CORE_MODULE)
    except (ImportError, ValueError):
        return None


def _forward(argv: list[str], runner) -> None:
    previous_argv = sys.argv
    try:
        # One element per caller argument: no shell-style splitting or requoting.
        sys.argv = [previous_argv[0] if previous_argv else "", *argv]
        runner()
    finally:
        sys.argv = previous_argv


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    root = _explicit_core_root()
    if root is not None:
        sys.path.insert(0, str(root))
        entry = root / CORE_ENTRY_NAME
        _forward(args, lambda: runpy.run_path(str(entry), run_name="__main__"))
        return
    if _installed_core_spec() is None:
        print(
            f"{UNAVAILABLE}: select the separate public artifact lifecycle core "
            f"with {CORE_ROOT_ENV}=<checkout directory> or install the public "
            "artifact-lifecycle-core distribution. No automatic install, download, "
            "sibling search or fallback source selection is performed.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    _forward(args, lambda: runpy.run_module(CORE_MODULE, run_name="__main__", alter_sys=True))


if __name__ == "__main__":
    main()
