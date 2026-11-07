"""Exact local artifact manifests used by consent and execution."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from .canonical import sha256_file, sha256_json

TEXT_MIME_TYPES = {"application/json", "text/markdown", "text/plain"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _sniff_mime(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if b"\x00" not in head:
        try:
            path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            pass
        else:
            guessed = mimetypes.guess_type(path.name)[0]
            return guessed if guessed in TEXT_MIME_TYPES else "text/plain"
    raise ValueError(f"artifact MIME type is unsupported or does not match content: {path.name}")


def build_artifact_manifest(
    artifact_paths: list[str | Path],
    *,
    data_type: str,
    purpose: str,
) -> list[dict[str, Any]]:
    if not purpose.strip():
        raise ValueError("artifact purpose must not be empty")
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in artifact_paths:
        path = Path(item).resolve()
        if not path.is_file():
            raise ValueError(f"consent artifact is not a file: {path}")
        normalized = str(path).casefold()
        if normalized in seen_paths:
            raise ValueError("duplicate artifact path is forbidden")
        seen_paths.add(normalized)
        artifacts.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "mime_type": _sniff_mime(path),
                "data_type": data_type,
                "purpose": purpose,
            }
        )
    if not artifacts:
        raise ValueError("at least one artifact is required")
    return artifacts


def validate_artifact_manifest(
    artifacts: list[dict[str, Any]],
    *,
    capability: str,
    verify_files: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("artifact manifest must contain at least one file")
    allowed = TEXT_MIME_TYPES if capability == "text" else IMAGE_MIME_TYPES if capability == "vision" else set()
    if not allowed:
        raise ValueError(f"artifact execution capability is blocked: {capability}")
    seen: set[str] = set()
    for artifact in artifacts:
        required = {"path", "sha256", "bytes", "mime_type", "data_type", "purpose"}
        if required - set(artifact):
            raise ValueError("artifact manifest entry is incomplete")
        if artifact["mime_type"] not in allowed:
            raise ValueError(f"artifact MIME type is not allowed for {capability}")
        if not str(artifact["purpose"]).strip():
            raise ValueError("artifact purpose must not be empty")
        if int(artifact["bytes"]) < 1:
            raise ValueError("empty artifacts are forbidden")
        path = Path(str(artifact["path"])).resolve()
        key = str(path).casefold()
        if key in seen:
            raise ValueError("duplicate artifact path is forbidden")
        seen.add(key)
        if verify_files:
            drifted = (
                not path.is_file()
                or path.stat().st_size != int(artifact["bytes"])
                or sha256_file(path) != artifact["sha256"]
                or _sniff_mime(path) != artifact["mime_type"]
            )
            if drifted:
                raise ValueError("artifact manifest drift detected")
    return artifacts


def public_artifact_refs(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = ("sha256", "bytes", "mime_type", "data_type", "purpose")
    return [
        {key: item[key] for key in required}
        for item in artifacts
        if isinstance(item, dict) and all(key in item for key in required)
    ]


def input_manifest_hash(instruction: str, artifacts: list[dict[str, Any]]) -> str:
    return sha256_json(
        {
            "instruction_sha256": sha256_json({"instruction": instruction}),
            "artifacts": public_artifact_refs(artifacts),
        }
    )
