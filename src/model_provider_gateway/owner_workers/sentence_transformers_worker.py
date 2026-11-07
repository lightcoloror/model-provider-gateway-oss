"""Local-files-only Sentence Transformers worker.

The worker deliberately lives behind a subprocess boundary so a compatible
``tokenizers``/``transformers`` set can be installed in an isolated runtime
without changing the gateway or VKP Python environments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

INPUT_SCHEMA = "model_provider_gateway.embedding_worker_input.v1"
OUTPUT_SCHEMA = "model_provider_gateway.embedding_worker_output.v2"


def measure_tokens(model: Any, texts: list[str]) -> dict[str, Any]:
    """Use the loaded Transformer tokenizer AND actual ST tokenize path.

    No approximate byte/token conversion. Unknown preprocessors fail closed.
    Counts include special tokens, exclude padding. Encode uses an empty prompt.
    """
    module = model._first_module()
    limit = model.max_seq_length
    if type(limit) is not int or not 1 <= limit <= 1_000_000 or module.tokenizer is not model.tokenizer:
        raise ValueError("token_coverage_unproven: unsupported tokenizer or sequence limit")
    normalized = [text.strip().lower() if getattr(module, "do_lower_case", False) else text.strip() for text in texts]
    raw = model.tokenizer(normalized, padding=False, truncation=False, add_special_tokens=True)["input_ids"]
    actual = model.tokenize(texts)
    ids, masks = actual["input_ids"].tolist(), actual["attention_mask"].tolist()
    if len(raw) != len(texts) or len(ids) != len(texts) or len(masks) != len(texts):
        raise ValueError("token_coverage_unproven: tokenizer item count")
    rows = []
    for index, (text, original, encoded, mask) in enumerate(zip(texts, raw, ids, masks, strict=True)):
        if len(encoded) != len(mask) or any(v not in (0, 1) for v in mask):
            raise ValueError("token_coverage_unproven: invalid attention mask")
        effective = [token for token, include in zip(encoded, mask, strict=True) if include]
        if not original or not effective or len(effective) > limit:
            raise ValueError("token_coverage_unproven: invalid token length")
        truncated = len(original) > len(effective)
        if not truncated and original != effective:
            raise ValueError("token_coverage_unproven: preprocessing mismatch")
        rows.append(
            {
                "index": index,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "raw_token_count": len(original),
                "effective_token_count": len(effective),
                "truncated": truncated,
            }
        )
    return {
        "schema": "model_provider_gateway.embedding_token_attestation.v1",
        "coverage_status": "truncated_rejected" if any(row["truncated"] for row in rows) else "complete",
        "max_seq_length": limit,
        "prompt_policy": "explicit_empty",
        "special_tokens_included": True,
        "items": rows,
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in ("sentence-transformers", "transformers", "tokenizers", "huggingface-hub", "torch"):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--model-revision", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    model_path = Path(args.model).expanduser().resolve()
    if not model_path.is_dir() or not (model_path / "modules.json").is_file():
        _emit({"ok": False, "error": {"category": "model_missing", "message": "local model directory is incomplete"}})
        return 2
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # noqa: BLE001
        _emit(
            {
                "ok": False,
                "error": {
                    "category": "dependency_missing",
                    "message": f"sentence-transformers import failed: {type(exc).__name__}: {exc}",
                },
                "versions": _versions(),
            }
        )
        return 2
    if args.probe:
        _emit({"ok": True, "status": "runtime_import_ready", "versions": _versions(), "model_present": True})
        return 0
    if not args.input or not args.output:
        _emit({"ok": False, "error": {"category": "invalid_request", "message": "input and output are required"}})
        return 2
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if payload.get("schema") != INPUT_SCHEMA:
            raise ValueError("embedding worker input schema mismatch")
        texts = payload.get("texts")
        valid_texts = (
            isinstance(texts, list) and bool(texts) and all(isinstance(value, str) and value.strip() for value in texts)
        )
        if not valid_texts:
            raise ValueError("embedding worker requires non-empty text items")
        if len(texts) > 64 or any(len(t.encode("utf-8")) > 16384 for t in texts):
            raise ValueError("embedding worker input exceeds bounded batch limits")
        if len(args.model_revision) != 64 or any(c not in "0123456789abcdef" for c in args.model_revision):
            raise ValueError("model_hash_mismatch: model revision required")
        config_hash = hashlib.sha256((model_path / "modules.json").read_bytes()).hexdigest()
        model = SentenceTransformer(str(model_path), local_files_only=True, device=args.device)
        attestation = measure_tokens(model, texts)
        identity = {
            "model_class": f"{type(model).__module__}.{type(model).__qualname__}",
            "model_actual": "local-modules-sha256:" + config_hash,
            "identity_scope": "local_modules_config_not_full_weights",
            "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "versions": _versions(),
        }
        if attestation["coverage_status"] != "complete":
            Path(args.output).write_text(
                json.dumps(
                    {
                        "schema": OUTPUT_SCHEMA,
                        "status": "blocked",
                        "model_revision": args.model_revision,
                        "token_attestation": attestation,
                        "execution_identity": identity,
                    }
                ),
                encoding="utf-8",
            )
            _emit({"ok": False, "error": {"category": "token_input_too_long", "message": "rechunk explicitly"}})
            return 2
        vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, prompt="")
        if hashlib.sha256((model_path / "modules.json").read_bytes()).hexdigest() != config_hash:
            raise ValueError("model_hash_mismatch: configuration changed during encode")
        result = {
            "schema": OUTPUT_SCHEMA,
            "item_count": int(vectors.shape[0]),
            "dimensions": int(vectors.shape[1]),
            "normalized": True,
            "model_revision": args.model_revision,
            "vectors": vectors.tolist(),
            "token_attestation": attestation,
            "execution_identity": identity,
        }
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
            newline="\n",
        )
        _emit(
            {
                "ok": True,
                "status": "completed",
                "item_count": result["item_count"],
                "dimensions": result["dimensions"],
                "device": args.device,
                "versions": _versions(),
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        _emit(
            {
                "ok": False,
                "error": {"category": "owner_execution_failed", "message": f"{type(exc).__name__}: {exc}"},
            }
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
