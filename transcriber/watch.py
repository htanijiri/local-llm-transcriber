"""フェーズ①: 監視フォルダ（config の watch_dir）を見張り、新規音声を自動で処理する。

Googleドライブのマウントフォルダを対象にするため、FSEvents(watchdog) ではなく
ポーリング方式にしている（クラウド同期フォルダでの取りこぼし/誤発火が少なく、依存も増えない）。

要点:
- ファイル安定検知: サイズが stable_seconds 秒変化しなくなってから処理（同期途中/書き込み途中を掴まない）。
- 冪等性: 既に議事録(summary_dir/<stem>.md)があるファイルは再処理しない。
- 1ファイルの失敗でループを止めない。失敗したファイルは即あきらめず、クールダウンを置いて
  上限回数まで再試行する（Driveのダウンロード遅延など一時的な失敗を拾い直すため）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .ollama_client import GenerationIncompleteError
from .summarize import summarize_file
from .transcribe import transcribe_file

# 失敗時の再試行ポリシー
_MAX_ATTEMPTS = 10  # これを超えたら諦めて処理済み扱い
_RETRY_COOLDOWN = 60.0  # 失敗後、次に試すまでの最短秒数


def _audio_files(watch_dir: Path, exts: set[str]) -> Iterator[Path]:
    for p in sorted(watch_dir.iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts:
            yield p


def _already_done(p: Path, cfg: Config) -> bool:
    return (cfg.summary_dir / f"{p.stem}.md").exists()


@dataclass
class WatchState:
    """走査をまたいで持ち越す状態。"""

    processed: set[str] = field(default_factory=set)
    # name -> (size, この size を最初に観測した monotonic 時刻)
    pending: dict[str, tuple[int, float]] = field(default_factory=dict)
    # name -> (失敗回数, 最後に失敗した monotonic 時刻)
    failures: dict[str, tuple[int, float]] = field(default_factory=dict)
    # name -> (文字起こしした時点の size, 文字起こしのパス)。要約だけ失敗したときの再試行で使う。
    # プロセス内だけで覚える（再起動後は文字起こしからやり直す）。
    transcribed: dict[str, tuple[int, Path]] = field(default_factory=dict)

    def forget(self, name: str) -> None:
        """処理が終わった（成功した／諦めた）ファイルの途中経過を捨てる。"""
        self.pending.pop(name, None)
        self.failures.pop(name, None)
        self.transcribed.pop(name, None)


def process_one(
    audio_path: Path,
    cfg: Config,
    log: Callable[[str], None],
    state: WatchState | None = None,
) -> Path:
    """1ファイルを 文字起こし → 要約 まで通す。

    state を渡すと、文字起こしが済んだことを覚えておき、要約だけが失敗した場合の再試行では
    文字起こし（数分かかる）をやり直さない。音声のサイズが変わっていたら文字起こしからやり直す。
    """
    size = audio_path.stat().st_size
    done = state.transcribed.get(audio_path.name) if state is not None else None
    if done is not None and done[0] == size and done[1].exists():
        transcript = done[1]
        log(f"[watch] 再試行: {audio_path.name} → 文字起こし済みのため要約から...")
    else:
        log(f"[watch] 検知: {audio_path.name} → 文字起こし...")
        transcript = transcribe_file(audio_path, cfg)
        if state is not None:
            state.transcribed[audio_path.name] = (size, transcript)
    log(f"[watch] → {transcript.name} を要約...")
    summary = summarize_file(transcript, cfg, log)
    log(f"[watch] 完了: {summary}")
    return summary


def scan_once(
    cfg: Config,
    state: WatchState,
    log: Callable[[str], None],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """監視フォルダを1回走査し、安定したファイルを処理する（watch_loop の1周分）。

    clock はテストで時刻を進めるための差し替え口。
    """
    exts = {e.lower() for e in cfg.watch.extensions}
    for p in _audio_files(cfg.watch_dir, exts):
        if p.name in state.processed:
            continue
        if _already_done(p, cfg):
            state.processed.add(p.name)
            continue

        try:
            size = p.stat().st_size
        except FileNotFoundError:
            continue  # 走査中に消えた

        prev = state.pending.get(p.name)
        now = clock()
        if prev is None or prev[0] != size:
            # 初観測 or サイズ変化中 → 安定待ち
            state.pending[p.name] = (size, now)
            continue
        if now - prev[1] < cfg.watch.stable_seconds:
            continue  # まだ安定していない

        # 直近に失敗した場合はクールダウン中はスキップ
        fail = state.failures.get(p.name)
        if fail is not None and (now - fail[1]) < _RETRY_COOLDOWN:
            continue

        # 安定 → 処理。成功したら処理済み、失敗したら再試行対象として残す。
        try:
            process_one(p, cfg, log, state)
        except Exception as e:  # noqa: BLE001 - 1件の失敗でループを止めない
            attempts = (fail[0] if fail else 0) + 1
            if isinstance(e, GenerationIncompleteError) and not e.retryable:
                # 出力の上限到達など、同じ設定で繰り返しても結果が変わらない失敗は1回で諦める。
                # 設定を直して watch を再起動すれば、未処理のファイルとして再び処理される。
                state.processed.add(p.name)
                state.forget(p.name)
                log(f"[watch] 諦め ({p.name}): 再試行しても結果が変わらないため。{e}")
            elif attempts >= _MAX_ATTEMPTS:
                state.processed.add(p.name)  # これ以上は諦める
                state.forget(p.name)
                log(f"[watch] 諦め ({p.name}): {attempts}回失敗。{e}")
            else:
                state.failures[p.name] = (attempts, clock())
                log(f"[watch] エラー ({p.name}) 試行{attempts}/{_MAX_ATTEMPTS}、後で再試行: {e}")
        else:
            state.processed.add(p.name)
            state.forget(p.name)


def watch_loop(
    cfg: Config,
    *,
    poll_interval: float = 2.0,
    process_existing: bool = True,
    log: Callable[[str], None] = print,
) -> None:
    """監視ループ。Ctrl-C で停止。"""
    exts = {e.lower() for e in cfg.watch.extensions}
    watch_dir = cfg.watch_dir
    watch_dir.mkdir(parents=True, exist_ok=True)
    cfg.transcript_dir.mkdir(parents=True, exist_ok=True)
    cfg.summary_dir.mkdir(parents=True, exist_ok=True)

    state = WatchState()
    if not process_existing:
        # 起動時点で既にあるファイルは「新規でない」として無視
        for p in _audio_files(watch_dir, exts):
            state.processed.add(p.name)

    log(f"[watch] 監視開始: {watch_dir}")
    log(
        f"[watch] 安定検知 {cfg.watch.stable_seconds}s / ポーリング {poll_interval}s"
        f" / 対象 {sorted(exts)}"
    )

    while True:
        scan_once(cfg, state, log)
        time.sleep(poll_interval)
