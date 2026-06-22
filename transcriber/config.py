"""config.toml を読み込み、各フェーズへ設定を渡す。

設定を1か所（config.toml）に集約する。パスは可変なので、Googleドライブ運用時は
config.toml の [paths] をマウントパスに差し替えるだけでコード変更は不要。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# プロジェクトルート（このファイルの2つ上 = transcriber/ の親）
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.toml"
PROMPTS_DIR = ROOT / "prompts"


@dataclass
class Paths:
    watch_dir: Path
    transcript_dir: Path
    summary_dir: Path


@dataclass
class TranscribeCfg:
    model: str
    language: str
    condition_on_previous_text: bool = False
    initial_prompt: str = ""
    trim_leading_silence: bool = True
    silence_threshold_db: int = -40


@dataclass
class SummarizeCfg:
    model: str
    host: str
    chunk_chars: int
    chunk_overlap: int
    temperature: float
    num_ctx: int = 16384


@dataclass
class WatchCfg:
    stable_seconds: int
    extensions: list[str]


@dataclass
class GlossaryCfg:
    # 正しい表記の用語（人名・固有名詞・専門用語・薬名など）。要約での誤変換補正の基準に使う（②A）。
    terms: list[str] = field(default_factory=list)

    @property
    def block(self) -> str:
        """プロンプトへ差し込む用語集ブロック文字列。"""
        if not self.terms:
            return "（用語集の指定なし）"
        return (
            "次の語は正しい表記です。音声認識の誤変換と思われる箇所はこれらに合わせて補正してください：\n"
            + "、".join(self.terms)
        )


@dataclass
class Config:
    paths: Paths
    transcribe: TranscribeCfg
    summarize: SummarizeCfg
    watch: WatchCfg
    glossary: GlossaryCfg = field(default_factory=GlossaryCfg)

    def _resolve(self, p: str) -> Path:
        """相対パスはプロジェクトルート基準で解決。絶対パス（Driveマウント等）はそのまま。"""
        path = Path(p).expanduser()
        return path if path.is_absolute() else (ROOT / path)

    @property
    def watch_dir(self) -> Path:
        return self._resolve(str(self.paths.watch_dir))

    @property
    def transcript_dir(self) -> Path:
        return self._resolve(str(self.paths.transcript_dir))

    @property
    def summary_dir(self) -> Path:
        return self._resolve(str(self.paths.summary_dir))


def load_config(path: Path | None = None) -> Config:
    cfg_path = path or DEFAULT_CONFIG_PATH
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)

    return Config(
        paths=Paths(**data["paths"]),
        transcribe=TranscribeCfg(**data["transcribe"]),
        summarize=SummarizeCfg(**data["summarize"]),
        watch=WatchCfg(**data["watch"]),
        glossary=GlossaryCfg(**data.get("glossary", {})),  # 無くても動くよう任意扱い
    )


def load_prompt(name: str) -> str:
    """prompts/<name>.txt を読み込む。"""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
