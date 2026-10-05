"""文字起こし：前処理の判断と、mlx-whisper に渡す設定。ffmpeg と mlx-whisper は呼ばない。"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from transcriber import transcribe as transcribe_module
from transcriber.config import Config
from transcriber.transcribe import _localize, _preprocess_audio, transcribe_file

from .conftest import FakeWhisper


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "会議.m4a"
    path.write_bytes(b"\0" * 64)
    return path


@pytest.fixture
def trim_cfg(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """先頭無音トリムを有効にした設定。一時ファイルは tmp_path の下に作らせる。"""
    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(transcribe_module.tempfile, "gettempdir", lambda: str(work))
    monkeypatch.setattr(transcribe_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    return dataclasses.replace(
        cfg, transcribe=dataclasses.replace(cfg.transcribe, trim_leading_silence=True)
    )


def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, *, output_bytes: int) -> list[list[str]]:
    """subprocess.run を差し替え、ffmpeg が output_bytes の wav を書いたことにする。"""
    commands: list[list[str]] = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"\0" * output_bytes)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(transcribe_module.subprocess, "run", run)
    return commands


def test_preprocess_skipped_when_disabled(cfg: Config, audio: Path) -> None:
    assert _preprocess_audio(audio, cfg) == (audio, False)


def test_preprocess_skipped_without_ffmpeg(
    trim_cfg: Config, audio: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcribe_module.shutil, "which", lambda name: None)
    assert _preprocess_audio(audio, trim_cfg) == (audio, False)


def test_preprocess_trims_leading_silence(
    trim_cfg: Config, audio: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = fake_ffmpeg(monkeypatch, output_bytes=4096)

    source, is_tmp = _preprocess_audio(audio, trim_cfg)

    assert is_tmp is True
    assert source.name == "lt_会議_trim.wav"
    assert source.exists()
    # 先頭の無音だけを除く指定（会議中の間は残す）と、設定の閾値が ffmpeg に渡る
    assert "silenceremove=start_periods=1:start_duration=0.5:start_threshold=-40dB" in commands[0]


def test_preprocess_falls_back_when_ffmpeg_fails(
    trim_cfg: Config, audio: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(transcribe_module.subprocess, "run", run)
    assert _preprocess_audio(audio, trim_cfg) == (audio, False)


def test_preprocess_falls_back_when_result_is_nearly_empty(
    trim_cfg: Config, audio: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """全編が無音扱いになった場合は、元のファイルを使い、空の一時ファイルは残さない。"""
    fake_ffmpeg(monkeypatch, output_bytes=100)
    assert _preprocess_audio(audio, trim_cfg) == (audio, False)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_localize_keeps_local_path(audio: Path) -> None:
    assert _localize(audio) == (audio, False)


def test_localize_copies_cloud_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trim_cfg: Config
) -> None:
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    src = cloud / "会議.m4a"
    src.write_bytes(b"abc")
    monkeypatch.setattr(transcribe_module, "_CLOUD_ROOT", cloud)

    local, is_tmp = _localize(src)

    assert is_tmp is True
    assert local != src
    assert local.read_bytes() == b"abc"


def test_transcribe_file_writes_text(cfg: Config, audio: Path, whisper: FakeWhisper) -> None:
    out = transcribe_file(audio, cfg)

    assert out == cfg.transcript_dir / "会議.txt"
    assert out.read_text(encoding="utf-8") == "文字起こしの結果です。"  # 前後の空白は除く


def test_transcribe_file_passes_hallucination_guards(
    cfg: Config, audio: Path, whisper: FakeWhisper
) -> None:
    """消してはいけない設定（幻聴対策）が mlx-whisper に渡る。"""
    transcribe_file(audio, cfg)

    path, kwargs = whisper.calls[0]
    assert path == str(audio.resolve())
    assert kwargs["path_or_hf_repo"] == "dummy/whisper"
    assert kwargs["language"] == "ja"
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["no_speech_threshold"] == 0.6
    assert kwargs["temperature"] == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    assert kwargs["initial_prompt"] is None  # 空文字は None にして渡す


def test_transcribe_file_removes_trimmed_tmp(
    trim_cfg: Config, audio: Path, whisper: FakeWhisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ffmpeg(monkeypatch, output_bytes=4096)

    transcribe_file(audio, trim_cfg)

    used = Path(whisper.calls[0][0])
    assert used.name == "lt_会議_trim.wav"  # トリム後のファイルを文字起こしした
    assert not used.exists()  # 終わったら消す


def test_transcribe_file_missing_audio(cfg: Config, whisper: FakeWhisper, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        transcribe_file(tmp_path / "ない.m4a", cfg)
    assert whisper.calls == []
