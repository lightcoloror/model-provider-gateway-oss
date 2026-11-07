from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def tmp_path() -> Path:
    """Use inherited Windows ACLs instead of pytest's Python 3.13 mode-700 root."""
    configured_root = os.environ.get("MPG_TEST_TMP_ROOT") or os.environ.get("TEST_TMP_ROOT")
    root = Path(configured_root).resolve() if configured_root else ROOT / ".test-tmp-pytest"
    root.mkdir(parents=True, exist_ok=True, mode=0o777)
    case_dir = root / f"case-{uuid.uuid4().hex}"
    case_dir.mkdir(mode=0o777)
    try:
        yield case_dir
    finally:
        shutil.rmtree(case_dir)
