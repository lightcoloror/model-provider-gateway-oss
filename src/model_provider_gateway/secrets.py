"""Secret references and secret-store adapters.

Raw secret values are returned only to the caller that explicitly resolves a
reference. They are never serialised by this module.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


@dataclass
class EnvironmentSecretStore:
    def get(self, name: str) -> str | None:
        return os.environ.get(name)

    def set(self, name: str, value: str) -> None:
        raise ValueError("environment secret store is read-only")


@dataclass
class KeyringSecretStore:
    service_name: str = "model-provider-gateway"

    def _module(self):
        try:
            import keyring
        except ImportError as exc:
            raise RuntimeError("keyring support is not installed") from exc
        return keyring

    def get(self, name: str) -> str | None:
        return self._module().get_password(self.service_name, name)

    def set(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("secret value must not be empty")
        self._module().set_password(self.service_name, name, value)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


@dataclass
class WindowsDpapiAdapter:
    """Optional in-memory DPAPI adapter; persistence is delegated to callers."""

    entropy: bytes = b"model-provider-gateway.v1"

    def _require_windows(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI is available only on Windows")

    def protect(self, secret: str) -> str:
        self._require_windows()
        if not secret:
            raise ValueError("secret value must not be empty")
        source, source_buffer = _blob(secret.encode("utf-8"))
        entropy, entropy_buffer = _blob(self.entropy)
        output = _DataBlob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0,
            ctypes.byref(output),
        )
        _ = (source_buffer, entropy_buffer)
        if not ok:
            raise ctypes.WinError()
        try:
            protected = ctypes.string_at(output.pbData, output.cbData)
            return base64.b64encode(protected).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    def unprotect(self, encoded: str) -> str:
        self._require_windows()
        source, source_buffer = _blob(base64.b64decode(encoded, validate=True))
        entropy, entropy_buffer = _blob(self.entropy)
        output = _DataBlob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0,
            ctypes.byref(output),
        )
        _ = (source_buffer, entropy_buffer)
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)


def resolve_secret_ref(auth_ref: str, *, keyring_service: str = "model-provider-gateway") -> str | None:
    kind, separator, name = auth_ref.partition(":")
    if not separator or not name:
        raise ValueError("invalid auth_ref")
    if kind == "env":
        return EnvironmentSecretStore().get(name)
    if kind == "keyring":
        return KeyringSecretStore(keyring_service).get(name)
    if kind == "dpapi":
        raise ValueError("dpapi references require an application-owned encrypted blob adapter")
    raise ValueError(f"unsupported auth_ref kind: {kind}")


def secret_ready(auth_ref: str) -> bool:
    try:
        value = resolve_secret_ref(auth_ref)
    except (RuntimeError, ValueError):
        return False
    return bool(value)
