"""Atomic, content-free execution reservation ledger."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canonical import sha256_json

LEDGER_SCHEMA = "model_provider_gateway.execution_ledger.v1"


class ReservationError(ValueError):
    """Fail-closed quota or replay error."""


def _empty() -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "reservations": []}


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty()
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schema") != LEDGER_SCHEMA or not isinstance(value.get("reservations"), list):
        raise ReservationError("execution ledger is invalid")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _lock(path: Path, timeout_seconds: float = 5.0):
    lock_path = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock_path.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ReservationError("execution ledger lock timeout") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with suppress(FileNotFoundError):
            lock_path.rmdir()


def reserve_execution(
    ledger_path: str | Path,
    *,
    request_id: str,
    consent_hash: str,
    max_calls: int,
    max_cost_usd: float,
    estimated_cost_usd: float,
) -> dict[str, Any]:
    path = Path(ledger_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock(path):
        ledger = _read(path)
        if any(row["request_id"] == request_id for row in ledger["reservations"]):
            raise ReservationError("execution request replay is forbidden")
        matching = [row for row in ledger["reservations"] if row["consent_hash"] == consent_hash]
        reserved_calls = len(matching)
        reserved_cost = sum(float(row["reserved_cost_usd"]) for row in matching)
        if reserved_calls + 1 > max_calls:
            raise ReservationError("consent call reservation limit exceeded")
        if reserved_cost + estimated_cost_usd > max_cost_usd + 1e-12:
            raise ReservationError("consent cost reservation limit exceeded")
        reservation = {
            "reservation_id": uuid.uuid4().hex,
            "request_id": request_id,
            "consent_hash": consent_hash,
            "reserved_calls": 1,
            "reserved_cost_usd": estimated_cost_usd,
            "status": "reserved",
            "reserved_at": datetime.now(UTC).isoformat(),
            "result_sha256": None,
        }
        ledger["reservations"].append(reservation)
        _write(path, ledger)
        return dict(reservation)


def finalize_execution(
    ledger_path: str | Path,
    *,
    request_id: str,
    status: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    path = Path(ledger_path).resolve()
    with _lock(path):
        ledger = _read(path)
        matches = [row for row in ledger["reservations"] if row["request_id"] == request_id]
        if len(matches) != 1:
            raise ReservationError("execution reservation was not found")
        row = matches[0]
        if row["status"] != "reserved":
            raise ReservationError("execution reservation is already finalized")
        row["status"] = status
        row["finalized_at"] = datetime.now(UTC).isoformat()
        row["result_sha256"] = sha256_json(result)
        _write(path, ledger)
        return dict(row)


def ledger_status(ledger_path: str | Path) -> dict[str, Any]:
    return _read(Path(ledger_path).resolve())
