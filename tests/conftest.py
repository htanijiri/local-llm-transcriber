"""テスト共通の土台。

重い外部依存（mlx-whisper、Ollama）と実設定（config.toml）には触れない。
設定は tmp_path 上に作り、Ollama と mlx-whisper はダミーに差し替える。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from ollama import GenerateResponse

from transcriber import config as config_module
from transcriber import ollama_client
from transcriber.config import (
    Config,
    GlossaryCfg,
    Paths,
    SummarizeCfg,
    TranscribeCfg,
    WatchCfg,
)


@pytest.fixture(autouse=True)
def _no_real_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ローカルの config.toml（実パス・用語集）をテストから読めないようにする。"""
    monkeypatch.delenv(config_module.CONFIG_ENV_VAR, raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", tmp_path / "no-such-config.toml")


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """tmp_path 上の入出力フォルダを指す設定。チャンクは小さくして map-reduce を起こしやすくする。"""
    for name in ("inbox", "transcripts", "summaries"):
        (tmp_path / name).mkdir()
    return Config(
        paths=Paths(
            watch_dir=tmp_path / "inbox",
            transcript_dir=tmp_path / "transcripts",
            summary_dir=tmp_path / "summaries",
        ),
        transcribe=TranscribeCfg(
            model="dummy/whisper",
            language="ja",
            trim_leading_silence=False,  # ffmpeg を呼ばない（前処理は個別のテストで確認する）
        ),
        summarize=SummarizeCfg(
            model="dummy-llm",
            host="http://localhost:0",
            chunk_chars=100,
            chunk_overlap=10,
            temperature=0.3,
        ),
        watch=WatchCfg(stable_seconds=5, extensions=[".m4a", ".wav"]),
        glossary=GlossaryCfg(terms=["用語A", "用語B"]),
    )


def stop(text: str = "議事録") -> GenerateResponse:
    """生成が完了した応答。"""
    return GenerateResponse(
        response=text, done=True, done_reason="stop", prompt_eval_count=20, eval_count=10
    )


def aborted(text: str = "途中まで") -> GenerateResponse:
    """中断応答：サーバー側で生成が打ち切られたときの形。

    実際に観測したとおり、done_reason・eval_count・prompt_eval_count が無い（HTTP は 200）。
    """
    return GenerateResponse(response=text)


def length_limit(text: str = "上限まで") -> GenerateResponse:
    """上限応答：出力トークン上限（num_predict）に達して打ち切られたときの形。"""
    return GenerateResponse(
        response=text, done=True, done_reason="length", prompt_eval_count=20, eval_count=4096
    )


class FakeOllama:
    """ollama.Client の差し替え。応答を順番に返し、呼び出しを記録する。

    script に入れたものを generate の呼び出しごとに1つずつ使う（応答はそのまま返し、例外は送出する）。
    script が空なら、完了した応答を返す。
    """

    def __init__(self) -> None:
        self.script: list[GenerateResponse | Exception] = []
        self.calls: list[dict] = []
        self.hosts: list[str | None] = []

    def Client(self, host: str | None = None):  # noqa: N802 - ollama.Client と同じ名前で差し替える
        self.hosts.append(host)
        return self

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else stop(f"応答{len(self.calls)}")
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def prompts(self) -> list[str]:
        return [c["prompt"] for c in self.calls]


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
    fake = FakeOllama()
    monkeypatch.setattr(ollama_client, "Client", fake.Client)
    return fake


class FakeWhisper:
    """mlx_whisper の差し替え。transcribe の呼び出しを記録し、決まったテキストを返す。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.text = " 文字起こしの結果です。 "
        self.error: Exception | None = None

    def transcribe(self, path: str, **kwargs) -> dict:
        self.calls.append((path, kwargs))
        if self.error is not None:
            raise self.error
        return {"text": self.text}


@pytest.fixture
def whisper(monkeypatch: pytest.MonkeyPatch) -> FakeWhisper:
    """transcribe_file は mlx_whisper を遅延 import するので、sys.modules に差し込む。"""
    fake = FakeWhisper()
    module = types.ModuleType("mlx_whisper")
    module.transcribe = fake.transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    return fake


class FakeClock:
    """time.monotonic の差し替え。advance で時刻を進める。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
