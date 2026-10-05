"""要約：単一パスと map-reduce の切り替え、Ollama に渡す設定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from transcriber.config import Config
from transcriber.summarize import _split_chunks, summarize_file, summarize_text

from .conftest import FakeOllama, stop

SHORT = "短い文字起こし。"
LONG = "あ" * 250  # chunk_chars=100, overlap=10 → 90字ずつ進んで3チャンク


def test_split_short_text_is_one_chunk() -> None:
    assert _split_chunks("  本文  ", chunk_chars=100, overlap=10) == ["本文"]


def test_split_long_text_overlaps() -> None:
    text = "".join(str(i % 10) for i in range(250))
    chunks = _split_chunks(text, chunk_chars=100, overlap=10)
    assert [len(c) for c in chunks] == [100, 100, 70]
    assert chunks[0][-10:] == chunks[1][:10]  # 境界の10字が重なる


def test_short_text_uses_single_pass(cfg: Config, ollama: FakeOllama) -> None:
    ollama.script = [stop("# 議事録")]
    assert summarize_text(SHORT, cfg) == "# 議事録"
    assert len(ollama.calls) == 1
    assert SHORT in ollama.prompts[0]
    assert "用語A、用語B" in ollama.prompts[0]  # 用語集がプロンプトに入る


def test_long_text_uses_map_reduce(cfg: Config, ollama: FakeOllama) -> None:
    ollama.script = [stop("メモ1"), stop("メモ2"), stop("メモ3"), stop("# 統合した議事録")]
    assert summarize_text(LONG, cfg) == "# 統合した議事録"
    assert len(ollama.calls) == 4  # map 3回 + reduce 1回
    assert "チャンク 1/3" in ollama.prompts[0]
    assert "チャンク 3/3" in ollama.prompts[2]
    # reduce には、map の結果が順番どおりに入る
    reduce_prompt = ollama.prompts[3]
    assert (
        reduce_prompt.index("メモ1") < reduce_prompt.index("メモ2") < reduce_prompt.index("メモ3")
    )


def test_text_at_chunk_limit_stays_single_pass(cfg: Config, ollama: FakeOllama) -> None:
    """chunk_chars ちょうどまでは単一パス（map-reduce より単一パスを優先する）。"""
    summarize_text("あ" * 100, cfg)
    assert len(ollama.calls) == 1


def test_generate_options_are_passed(cfg: Config, ollama: FakeOllama) -> None:
    """消してはいけない設定（num_ctx・num_predict）が Ollama に渡る。"""
    summarize_text(SHORT, cfg)
    call = ollama.calls[0]
    assert call["model"] == "dummy-llm"
    assert call["stream"] is False
    assert call["options"]["temperature"] == 0.3
    assert call["options"]["num_ctx"] == 16384
    assert call["options"]["num_predict"] == 4096
    assert ollama.hosts == ["http://localhost:0"]


def test_summarize_file_writes_markdown(cfg: Config, ollama: FakeOllama) -> None:
    transcript = cfg.transcript_dir / "会議.txt"
    transcript.write_text(SHORT, encoding="utf-8")
    ollama.script = [stop("# 議事録")]

    out = summarize_file(transcript, cfg)

    assert out == cfg.summary_dir / "会議.md"
    assert out.read_text(encoding="utf-8") == "# 議事録"


def test_summarize_file_missing_transcript(cfg: Config, ollama: FakeOllama, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        summarize_file(tmp_path / "ない.txt", cfg)
    assert ollama.calls == []
