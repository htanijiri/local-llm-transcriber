"""要約の途中切れの検知と再試行（仕様書 006 の受け入れ条件 AC1〜AC19）。

Ollama は呼ばず、応答（本文・done_reason）を1回ごとに指定する。
「中断応答」は done_reason が無い応答、「上限応答」は done_reason が "length" の応答。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from ollama import GenerateResponse
from typer.testing import CliRunner

from transcriber import config as config_module
from transcriber.cli import app
from transcriber.config import ROOT, Config, load_config
from transcriber.ollama_client import (
    GenerationIncompleteError,
    GenerationLengthLimitError,
    OllamaError,
    generate,
)
from transcriber.summarize import summarize_file, summarize_text
from transcriber.watch import _RETRY_COOLDOWN, WatchState, scan_once

from .conftest import FakeClock, FakeOllama, FakeWhisper, aborted, length_limit, stop

SHORT = "短い文字起こし。"
LONG = "あ" * 250  # chunk_chars=100, overlap=10 → 3チャンク
PARTIAL = "途中までの議事録の本文"


def call_generate(num_predict: int = 4096) -> str:
    return generate(
        "プロンプト",
        model="dummy-llm",
        host="http://localhost:0",
        temperature=0.3,
        num_predict=num_predict,
    )


def write_transcript(cfg: Config, text: str = SHORT) -> Path:
    path = cfg.transcript_dir / "会議.txt"
    path.write_text(text, encoding="utf-8")
    return path


def summary_path(cfg: Config) -> Path:
    return cfg.summary_dir / "会議.md"


# --- 完了の判定（ollama_client.generate）---


def test_ac1_stop_returns_stripped_text(ollama: FakeOllama) -> None:
    ollama.script = [stop("  # 議事録\n")]
    assert call_generate() == "# 議事録"


def test_ac2_aborted_response_raises_without_leaking_text(ollama: FakeOllama) -> None:
    ollama.script = [aborted(f"  {PARTIAL}  ")]

    with pytest.raises(GenerationIncompleteError) as excinfo:
        call_generate()

    error = excinfo.value
    assert isinstance(error, OllamaError)
    assert error.done_reason is None
    assert error.partial_chars == len(PARTIAL)
    assert error.retryable is True
    assert PARTIAL not in str(error)  # 途中までの本文（会議の内容）はメッセージに出さない
    assert "完了しませんでした" in str(error)


def test_ac3_length_limit_is_a_distinct_error(ollama: FakeOllama) -> None:
    ollama.script = [length_limit(PARTIAL)]

    with pytest.raises(GenerationLengthLimitError) as excinfo:
        call_generate(num_predict=1234)

    error = excinfo.value
    assert isinstance(error, GenerationIncompleteError)
    assert error.done_reason == "length"
    assert error.retryable is False
    assert "num_predict=1234" in str(error)
    assert "上限に達し" in str(error)
    assert PARTIAL not in str(error)


def test_ac3_aborted_is_not_a_length_limit_error(ollama: FakeOllama) -> None:
    ollama.script = [aborted()]
    with pytest.raises(GenerationIncompleteError) as excinfo:
        call_generate()
    assert not isinstance(excinfo.value, GenerationLengthLimitError)


@pytest.mark.parametrize("done_reason", ["unload", "load", "STOP", ""])
def test_ac4_any_other_done_reason_is_incomplete(ollama: FakeOllama, done_reason: str) -> None:
    ollama.script = [GenerateResponse(response="本文", done=True, done_reason=done_reason)]

    with pytest.raises(GenerationIncompleteError) as excinfo:
        call_generate()

    assert excinfo.value.done_reason == done_reason
    assert not isinstance(excinfo.value, GenerationLengthLimitError)


# --- 再試行（summarize_text / summarize_file）---


def test_ac5_single_pass_retries_and_saves_the_completed_one(
    cfg: Config, ollama: FakeOllama
) -> None:
    transcript = write_transcript(cfg)
    ollama.script = [aborted(PARTIAL), stop("# 完了した議事録")]

    out = summarize_file(transcript, cfg, log=lambda line: None)

    assert out.read_text(encoding="utf-8") == "# 完了した議事録"
    assert len(ollama.calls) == 2
    assert ollama.prompts[0] == ollama.prompts[1]  # 同じプロンプトでやり直す


def test_ac6_single_pass_gives_up_after_max_attempts(cfg: Config, ollama: FakeOllama) -> None:
    transcript = write_transcript(cfg)
    ollama.script = [aborted(), aborted(), aborted(), stop("呼ばれないはず")]

    with pytest.raises(GenerationIncompleteError):
        summarize_file(transcript, cfg, log=lambda line: None)

    assert len(ollama.calls) == cfg.summarize.max_attempts == 3
    assert not summary_path(cfg).exists()


def test_ac7_existing_summary_is_left_untouched_on_failure(cfg: Config, ollama: FakeOllama) -> None:
    transcript = write_transcript(cfg)
    summary_path(cfg).write_text("前回の議事録", encoding="utf-8")
    ollama.script = [aborted(), aborted(), aborted()]

    with pytest.raises(GenerationIncompleteError):
        summarize_file(transcript, cfg, log=lambda line: None)

    assert summary_path(cfg).read_text(encoding="utf-8") == "前回の議事録"


def test_ac8_length_limit_is_not_retried(cfg: Config, ollama: FakeOllama) -> None:
    transcript = write_transcript(cfg)
    ollama.script = [length_limit(), stop("呼ばれないはず")]
    logs: list[str] = []

    with pytest.raises(GenerationLengthLimitError):
        summarize_file(transcript, cfg, log=logs.append)

    assert len(ollama.calls) == 1
    assert logs == []
    assert not summary_path(cfg).exists()


def test_ac9_connection_failure_is_not_retried(cfg: Config, ollama: FakeOllama) -> None:
    transcript = write_transcript(cfg)
    ollama.script = [ConnectionError("connection refused"), stop("呼ばれないはず")]

    with pytest.raises(OllamaError) as excinfo:
        summarize_file(transcript, cfg, log=lambda line: None)

    assert not isinstance(excinfo.value, GenerationIncompleteError)
    assert len(ollama.calls) == 1
    assert not summary_path(cfg).exists()


def test_ac10_map_reduce_retries_only_the_aborted_chunk(cfg: Config, ollama: FakeOllama) -> None:
    ollama.script = [stop("メモ1"), aborted(), stop("メモ2"), stop("メモ3"), stop("# 統合")]

    assert summarize_text(LONG, cfg, log=lambda line: None) == "# 統合"

    assert len(ollama.calls) == 5  # map 3回 + やり直し1回 + reduce 1回
    labels = ["チャンク 1/3", "チャンク 2/3", "チャンク 2/3", "チャンク 3/3"]
    for prompt, label in zip(ollama.prompts[:4], labels, strict=True):
        assert label in prompt  # 完了済みの map（1/3）はやり直さない
    assert all(memo in ollama.prompts[4] for memo in ("メモ1", "メモ2", "メモ3"))


def test_ac11_map_failure_stops_the_whole_summary(cfg: Config, ollama: FakeOllama) -> None:
    transcript = write_transcript(cfg, LONG)
    ollama.script = [stop("メモ1"), aborted(), aborted(), aborted(), stop("呼ばれないはず")]

    with pytest.raises(GenerationIncompleteError):
        summarize_file(transcript, cfg, log=lambda line: None)

    assert len(ollama.calls) == 4  # map1 + map2 を3回。map3 と reduce は呼ばない
    assert not any("チャンク 3/3" in prompt for prompt in ollama.prompts)
    assert not summary_path(cfg).exists()


def test_ac12_reduce_failure_does_not_redo_maps(cfg: Config, ollama: FakeOllama) -> None:
    transcript = write_transcript(cfg, LONG)
    ollama.script = [stop("メモ1"), stop("メモ2"), stop("メモ3"), aborted(), aborted(), aborted()]

    with pytest.raises(GenerationIncompleteError):
        summarize_file(transcript, cfg, log=lambda line: None)

    assert len(ollama.calls) == 6  # map 3回 + reduce を3回
    # map のプロンプトは各チャンク1回ずつ（reduce のやり直しで map をやり直さない）
    map_prompts = [p for p in ollama.prompts if "# 文字起こし（チャンク" in p]
    assert map_prompts == ollama.prompts[:3]
    assert ollama.prompts[3] == ollama.prompts[4] == ollama.prompts[5]
    assert not summary_path(cfg).exists()


def test_ac13_each_retry_logs_one_line_without_the_text(cfg: Config, ollama: FakeOllama) -> None:
    ollama.script = [aborted(PARTIAL), aborted(PARTIAL + "の続き"), stop("# 議事録")]
    logs: list[str] = []

    summarize_text(SHORT, cfg, log=logs.append)

    assert len(logs) == 2
    assert "2/3" in logs[0] and "3/3" in logs[1]  # 何回目の試行か
    assert all("中断" in line for line in logs)  # 種類
    assert f"{len(PARTIAL)} 文字" in logs[0]  # 途中までの出力の文字数
    assert f"{len(PARTIAL) + 3} 文字" in logs[1]
    assert not any(PARTIAL in line for line in logs)  # 本文は出さない


# --- CLI・設定 ---


def write_cli_config(tmp_path: Path, extra_summarize: str = "") -> Path:
    for name in ("inbox", "transcripts", "summaries"):
        (tmp_path / name).mkdir(exist_ok=True)
    path = tmp_path / "config.toml"
    path.write_text(
        f"""
[paths]
watch_dir      = "{tmp_path}/inbox"
transcript_dir = "{tmp_path}/transcripts"
summary_dir    = "{tmp_path}/summaries"

[transcribe]
model    = "dummy/whisper"
language = "ja"

[summarize]
model         = "dummy-llm"
host          = "http://localhost:0"
chunk_chars   = 100
chunk_overlap = 10
temperature   = 0.3
{extra_summarize}

[watch]
stable_seconds = 5
extensions     = [".m4a"]
""",
        encoding="utf-8",
    )
    return path


def run_cli_summarize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(config_module.CONFIG_ENV_VAR, str(write_cli_config(tmp_path)))
    transcript = tmp_path / "transcripts" / "会議.txt"
    transcript.write_text(SHORT, encoding="utf-8")
    return CliRunner().invoke(app, ["summarize", str(transcript)])


def test_ac14_cli_fails_when_generation_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama
) -> None:
    ollama.script = [aborted(PARTIAL), aborted(PARTIAL), aborted(PARTIAL)]

    result = run_cli_summarize(tmp_path, monkeypatch)

    assert result.exit_code != 0
    assert "完了しませんでした" in result.output
    assert "保存していません" in result.output
    assert PARTIAL not in result.output
    assert not (tmp_path / "summaries" / "会議.md").exists()


def test_ac14_cli_fails_on_length_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama
) -> None:
    ollama.script = [length_limit(PARTIAL)]

    result = run_cli_summarize(tmp_path, monkeypatch)

    assert result.exit_code != 0
    assert "上限に達し" in result.output
    assert "num_predict=4096" in result.output
    assert not (tmp_path / "summaries" / "会議.md").exists()


def test_ac14_cli_succeeds_and_shows_the_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama
) -> None:
    ollama.script = [aborted(PARTIAL), stop("# 議事録")]

    result = run_cli_summarize(tmp_path, monkeypatch)

    assert result.exit_code == 0
    assert "再試行 2/3" in result.output
    assert (tmp_path / "summaries" / "会議.md").read_text(encoding="utf-8") == "# 議事録"


def test_ac15_keys_have_defaults(tmp_path: Path) -> None:
    cfg = load_config(write_cli_config(tmp_path))
    assert cfg.summarize.max_attempts == 3
    assert cfg.summarize.num_predict == 4096


def test_ac15_keys_can_be_set_in_config(tmp_path: Path) -> None:
    cfg = load_config(write_cli_config(tmp_path, "max_attempts = 5\nnum_predict = 8192"))
    assert cfg.summarize.max_attempts == 5
    assert cfg.summarize.num_predict == 8192


def test_ac15_example_config_documents_both_keys() -> None:
    lines = (ROOT / "config.example.toml").read_text(encoding="utf-8").splitlines()
    for key in ("max_attempts", "num_predict"):
        (line,) = [ln for ln in lines if ln.startswith(key)]
        assert "#" in line  # コメント付き
    cfg = load_config(ROOT / "config.example.toml")
    assert (cfg.summarize.max_attempts, cfg.summarize.num_predict) == (3, 4096)


def test_ac15_num_predict_is_passed_to_ollama(cfg: Config, ollama: FakeOllama) -> None:
    cfg = dataclasses.replace(cfg, summarize=dataclasses.replace(cfg.summarize, num_predict=8192))
    summarize_text(SHORT, cfg)
    assert ollama.calls[0]["options"]["num_predict"] == 8192


def test_ac15_max_attempts_one_disables_retry(cfg: Config, ollama: FakeOllama) -> None:
    cfg = dataclasses.replace(cfg, summarize=dataclasses.replace(cfg.summarize, max_attempts=1))
    ollama.script = [aborted(), stop("呼ばれないはず")]

    with pytest.raises(GenerationIncompleteError):
        summarize_text(SHORT, cfg, log=lambda line: None)

    assert len(ollama.calls) == 1


# --- watch ---


def add_audio(cfg: Config, size: int = 10) -> Path:
    path = cfg.watch_dir / "会議.m4a"
    path.write_bytes(b"\0" * size)
    return path


def scan_until_processed(cfg: Config, state: WatchState, logs: list[str], clock: FakeClock) -> None:
    """初観測 → 安定待ち → 処理、までを進める。"""
    scan_once(cfg, state, logs.append, clock=clock)
    clock.advance(cfg.watch.stable_seconds)
    scan_once(cfg, state, logs.append, clock=clock)


def test_ac16_ac17_summary_failure_is_retried_without_transcribing_again(
    cfg: Config, whisper: FakeWhisper, ollama: FakeOllama, clock: FakeClock
) -> None:
    add_audio(cfg)
    ollama.script = [aborted(), aborted(), aborted()]  # 1回目の処理は要約が完了しない
    state, logs = WatchState(), []

    scan_until_processed(cfg, state, logs, clock)

    assert not summary_path(cfg).exists()
    assert "会議.m4a" not in state.processed  # 処理済みにしない
    assert any("後で再試行" in line and "完了しませんでした" in line for line in logs)

    clock.advance(_RETRY_COOLDOWN - 1)
    scan_once(cfg, state, logs.append, clock=clock)  # クールダウン中は試さない
    assert len(ollama.calls) == 3

    clock.advance(1)
    scan_once(cfg, state, logs.append, clock=clock)  # 今度は要約が完了する

    assert summary_path(cfg).read_text(encoding="utf-8") == "応答4"
    assert state.processed == {"会議.m4a"}
    assert state.transcribed == {}
    assert len(whisper.calls) == 1  # AC17：文字起こしは最初の1回だけ
    assert any("文字起こし済みのため要約から" in line for line in logs)


def test_ac18_transcription_failure_is_retried_from_transcription(
    cfg: Config, whisper: FakeWhisper, ollama: FakeOllama, clock: FakeClock
) -> None:
    add_audio(cfg)
    whisper.error = RuntimeError("読み取りに失敗")
    state, logs = WatchState(), []

    scan_until_processed(cfg, state, logs, clock)
    assert len(whisper.calls) == 1
    assert ollama.calls == []

    whisper.error = None
    clock.advance(_RETRY_COOLDOWN)
    scan_once(cfg, state, logs.append, clock=clock)

    assert len(whisper.calls) == 2  # 文字起こしからやり直す
    assert state.processed == {"会議.m4a"}
    assert summary_path(cfg).exists()


def test_ac19_length_limit_is_given_up_at_once(
    cfg: Config, whisper: FakeWhisper, ollama: FakeOllama, clock: FakeClock
) -> None:
    add_audio(cfg)
    ollama.script = [length_limit()]
    state, logs = WatchState(), []

    scan_until_processed(cfg, state, logs, clock)

    assert state.processed == {"会議.m4a"}  # 1回で「諦め」として処理済みにする
    assert not summary_path(cfg).exists()
    (line,) = [ln for ln in logs if "諦め" in ln]
    assert "num_predict=4096" in line and "上限に達し" in line  # 理由がログに出る

    clock.advance(_RETRY_COOLDOWN)
    scan_once(cfg, state, logs.append, clock=clock)
    assert len(ollama.calls) == 1  # 繰り返さない


def test_changed_audio_is_transcribed_again_on_retry(
    cfg: Config, whisper: FakeWhisper, ollama: FakeOllama, clock: FakeClock
) -> None:
    """要約の失敗後に音声のサイズが変わっていたら、覚えていた文字起こしは使わない。"""
    add_audio(cfg, size=10)
    ollama.script = [aborted(), aborted(), aborted()]
    state, logs = WatchState(), []
    scan_until_processed(cfg, state, logs, clock)
    assert len(whisper.calls) == 1

    add_audio(cfg, size=20)
    clock.advance(_RETRY_COOLDOWN)
    scan_until_processed(cfg, state, logs, clock)

    assert len(whisper.calls) == 2
    assert state.processed == {"会議.m4a"}
