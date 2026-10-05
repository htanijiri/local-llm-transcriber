"""設定の読み込みとパス解決。"""

from __future__ import annotations

from pathlib import Path

import pytest

from transcriber import config as config_module
from transcriber.config import ROOT, load_config

MINIMAL_TOML = """
[paths]
watch_dir      = "data/inbox"
transcript_dir = "{abs_dir}/transcripts"
summary_dir    = "~/lt-test-summaries"

[transcribe]
model    = "dummy/whisper"
language = "ja"

[summarize]
model         = "dummy-llm"
host          = "http://localhost:11434"
chunk_chars   = 8000
chunk_overlap = 400
temperature   = 0.3

[watch]
stable_seconds = 5
extensions     = [".m4a"]
"""


def write_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL_TOML.format(abs_dir=tmp_path) + extra, encoding="utf-8")
    return path


def test_relative_path_is_resolved_from_project_root(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    assert cfg.watch_dir == ROOT / "data" / "inbox"


def test_absolute_path_is_kept(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    assert cfg.transcript_dir == tmp_path / "transcripts"


def test_home_is_expanded(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    assert cfg.summary_dir == Path.home() / "lt-test-summaries"


def test_defaults_for_omitted_keys(tmp_path: Path) -> None:
    """消してはいけない設定の既定値（幻聴対策・コンテキスト長）が、省略時にも効く。"""
    cfg = load_config(write_config(tmp_path))
    assert cfg.transcribe.condition_on_previous_text is False
    assert cfg.transcribe.trim_leading_silence is True
    assert cfg.summarize.num_ctx == 16384


def test_glossary_is_optional(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    assert cfg.glossary.terms == []
    assert cfg.glossary.block == "（用語集の指定なし）"


def test_glossary_block_lists_terms(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, '\n[glossary]\nterms = ["用語A", "用語B"]\n'))
    assert "用語A、用語B" in cfg.glossary.block


def test_env_var_selects_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_module.CONFIG_ENV_VAR, str(write_config(tmp_path)))
    assert load_config().summarize.model == "dummy-llm"


def test_argument_wins_over_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_module.CONFIG_ENV_VAR, str(tmp_path / "missing.toml"))
    assert load_config(write_config(tmp_path)).summarize.model == "dummy-llm"


def test_example_config_is_loadable() -> None:
    """config.example.toml が、今の設定クラスでそのまま読める（キーの追加漏れを検知する）。"""
    cfg = load_config(ROOT / "config.example.toml")
    assert cfg.summarize.model
    assert cfg.watch.extensions
