from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_provider_gateway.owner_workers import sherpa_onnx_tts_worker as worker


def _model(path: Path, *, lexicon: bool = True) -> Path:
    path.mkdir()
    (path / "model.onnx").write_bytes(b"model")
    (path / "tokens.txt").write_text("_ 0\n", encoding="utf-8")
    if lexicon:
        (path / "lexicon.txt").write_text("测 c e\n", encoding="utf-8")
    return path


def test_worker_rejects_missing_model_and_lexicon(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model_missing"):
        worker._model_files(tmp_path / "missing")
    model = _model(tmp_path / "model", lexicon=False)
    with pytest.raises(ValueError, match="lexicon_missing"):
        worker._model_files(model)


def test_worker_rejects_invalid_speaker_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = _model(tmp_path / "model")
    monkeypatch.setattr(worker, "_load_runtime", lambda: object())
    monkeypatch.setattr(
        worker,
        "_make_tts",
        lambda *_args, **_kwargs: SimpleNamespace(num_speakers=1, sample_rate=16000),
    )
    code = worker.main(["--probe-model", "--model-dir", str(model), "--speaker", "2"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["error"]["category"] == "invalid_speaker"
    assert payload["automatic_fallback_performed"] is False
    assert payload["external_io_performed"] is False


def test_worker_rejects_empty_audio(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty_audio"):
        worker._write_pcm16(tmp_path / "empty.wav", [], 16000)
