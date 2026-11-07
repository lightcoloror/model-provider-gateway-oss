"""Offline-only sherpa-onnx VITS worker.

Intent: reuse ``sherpa_onnx.OfflineTts`` behind the gateway owner gate.
Decision: keep model loading and PCM generation in a bounded subprocess.
Reason: missing packages, malformed bundles and native failures must not affect
the gateway process or trigger a cloud fallback.
Evidence: k2-fsa/sherpa-onnx ``python-api-examples/offline-tts.py`` at commit
75e1fc31e747194c546787ec7b40a7e0b390dc4b.
Effective scope: local VITS bundles and local WAV output only.
Rollback: remove this worker and the additive ``sherpa-onnx-tts`` adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import time
import wave
from importlib import metadata
from pathlib import Path
from typing import Any

INPUT_SCHEMA = "model_provider_gateway.sherpa_onnx_tts_worker_input.v1"
OUTPUT_SCHEMA = "model_provider_gateway.sherpa_onnx_tts_worker_output.v1"
REQUIRED_MODEL_FILES = ("model.onnx", "tokens.txt", "lexicon.txt")


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _version() -> str | None:
    try:
        return metadata.version("sherpa-onnx")
    except metadata.PackageNotFoundError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-runtime", action="store_true")
    parser.add_argument("--probe-model", action="store_true")
    parser.add_argument("--model-dir")
    parser.add_argument("--input")
    parser.add_argument("--output-wav")
    parser.add_argument("--output-json")
    parser.add_argument("--provider", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--num-threads", type=int, default=2)
    parser.add_argument("--speaker", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--model-revision", default="")
    return parser


def _fail(category: str, message: str, *, code: int = 2) -> int:
    _emit(
        {
            "ok": False,
            "error": {"category": category, "message": message},
            "external_io_performed": False,
            "automatic_fallback_performed": False,
        }
    )
    return code


def _model_files(model_dir: Path) -> dict[str, Path]:
    rows = {name: model_dir / name for name in REQUIRED_MODEL_FILES}
    missing = [name for name, path in rows.items() if not path.is_file()]
    if missing:
        category = "lexicon_missing" if missing == ["lexicon.txt"] else "model_missing"
        raise ValueError(f"{category}: missing required model files: {', '.join(missing)}")
    return rows


def _load_runtime() -> Any:
    try:
        import sherpa_onnx  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"dependency_missing: sherpa-onnx import failed: {type(exc).__name__}: {exc}") from exc
    return sherpa_onnx


def _make_tts(sherpa_onnx: Any, model_dir: Path, provider: str, num_threads: int) -> Any:
    files = _model_files(model_dir)
    rule_fsts = [
        str(model_dir / name)
        for name in ("phone.fst", "date.fst", "number.fst", "new_heteronym.fst")
        if (model_dir / name).is_file()
    ]
    rule_fars = [str(model_dir / "rule.far")] if (model_dir / "rule.far").is_file() else []
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(files["model.onnx"]),
                lexicon=str(files["lexicon.txt"]),
                tokens=str(files["tokens.txt"]),
            ),
            provider=provider,
            num_threads=num_threads,
            debug=False,
        ),
        rule_fsts=",".join(rule_fsts),
        rule_fars=",".join(rule_fars),
        max_num_sentences=1,
    )
    if not config.validate():
        raise ValueError("model_missing: sherpa-onnx rejected the local VITS model configuration")
    return sherpa_onnx.OfflineTts(config)


def _write_pcm16(path: Path, samples: Any, sample_rate: int) -> dict[str, Any]:
    values = [float(value) for value in samples]
    if not values or sample_rate <= 0:
        raise ValueError("empty_audio: sherpa-onnx returned no audio samples")
    pcm = bytearray()
    energy = 0.0
    non_silent = 0
    peak = 0.0
    for value in values:
        clipped = max(-1.0, min(1.0, value))
        peak = max(peak, abs(clipped))
        energy += clipped * clipped
        if abs(clipped) >= 1e-4:
            non_silent += 1
        pcm.extend(struct.pack("<h", int(round(clipped * 32767.0))))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(pcm))
    return {
        "sample_rate": sample_rate,
        "sample_count": len(values),
        "duration_seconds": round(len(values) / sample_rate, 6),
        "peak_amplitude": round(peak, 8),
        "rms_amplitude": round(math.sqrt(energy / len(values)), 8),
        "non_silent_ratio": round(non_silent / len(values), 8),
        "wav_sha256": _sha256(path),
        "wav_bytes": path.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["NO_PROXY"] = "*"
    if args.probe_runtime:
        try:
            _load_runtime()
        except RuntimeError as exc:
            return _fail("dependency_missing", str(exc).split(": ", 1)[-1])
        _emit({"ok": True, "status": "runtime_import_ready", "version": _version(), "external_io_performed": False})
        return 0
    if not args.model_dir:
        return _fail("model_missing", "model-dir is required")
    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        return _fail("model_missing", f"local model directory not found: {model_dir}")
    if not 1 <= args.num_threads <= 8:
        return _fail("invalid_request", "num-threads must be between 1 and 8")
    try:
        runtime = _load_runtime()
        tts = _make_tts(runtime, model_dir, args.provider, args.num_threads)
        num_speakers = int(tts.num_speakers)
        sample_rate = int(tts.sample_rate)
        if not 0 <= args.speaker < num_speakers:
            return _fail("invalid_speaker", f"speaker must be in [0, {num_speakers - 1}]")
        if args.probe_model:
            _emit(
                {
                    "ok": True,
                    "status": "model_load_ready",
                    "version": _version(),
                    "provider": args.provider,
                    "sample_rate": sample_rate,
                    "num_speakers": num_speakers,
                    "external_io_performed": False,
                }
            )
            return 0
        if not args.input or not args.output_wav or not args.output_json:
            return _fail("invalid_request", "input, output-wav and output-json are required")
        payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if payload.get("schema") != INPUT_SCHEMA:
            return _fail("invalid_request", "TTS worker input schema mismatch")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return _fail("invalid_request", "TTS input text must be non-empty")
        if not 0.5 <= args.speed <= 2.0:
            return _fail("invalid_request", "speed must be between 0.5 and 2.0")
        generation = runtime.GenerationConfig()
        generation.sid = args.speaker
        generation.speed = args.speed
        generation.silence_scale = 0.2
        started = time.monotonic()
        audio = tts.generate(text.strip(), generation)
        synthesis_seconds = time.monotonic() - started
        metrics = _write_pcm16(Path(args.output_wav).resolve(), audio.samples, int(audio.sample_rate))
        result = {
            "schema": OUTPUT_SCHEMA,
            "model_revision": args.model_revision,
            "runtime_version": _version(),
            "provider": args.provider,
            "speaker": args.speaker,
            "speed": args.speed,
            "num_threads": args.num_threads,
            **metrics,
            "real_time_factor": round(synthesis_seconds / metrics["duration_seconds"], 6),
            "quality_status": "technical_smoke_only_human_listening_not_performed",
            "provider_called": False,
            "external_io_performed": False,
        }
        output_json = Path(args.output_json).resolve()
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
            newline="\n",
        )
        _emit({"ok": True, "status": "completed", **result})
        return 0
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        category = message.split(":", 1)[0] if ":" in message else "owner_execution_failed"
        if category not in {"dependency_missing", "model_missing", "lexicon_missing", "empty_audio"}:
            category = "owner_execution_failed"
        return _fail(category, message)


if __name__ == "__main__":
    sys.exit(main())
