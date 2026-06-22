"""フェーズ①: 監視フォルダ（config の watch_dir）を見張り、新規音声を自動で処理する。

Googleドライブのマウントフォルダを対象にするため、FSEvents(watchdog) ではなく
ポーリング方式にしている（クラウド同期フォルダでの取りこぼし/誤発火が少なく、依存も増えない）。

要点:
- ファイル安定検知: サイズが stable_seconds 秒変化しなくなってから処理（同期途中/書き込み途中を掴まない）。
- 冪等性: 既に議事録(summary_dir/<stem>.md)があるファイルは再処理しない。
- 1ファイルの失敗でループを止めない。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .config import Config
from .summarize import summarize_file
from .transcribe import transcribe_file


def _audio_files(watch_dir: Path, exts: set[str]) -> Iterator[Path]:
    for p in sorted(watch_dir.iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts:
            yield p


def _already_done(p: Path, cfg: Config) -> bool:
    return (cfg.summary_dir / f"{p.stem}.md").exists()


def process_one(audio_path: Path, cfg: Config, log: Callable[[str], None]) -> Path:
    """1ファイルを 文字起こし → 要約 まで通す。"""
    log(f"[watch] 検知: {audio_path.name} → 文字起こし...")
    transcript = transcribe_file(audio_path, cfg)
    log(f"[watch] → {transcript.name} を要約...")
    summary = summarize_file(transcript, cfg)
    log(f"[watch] 完了: {summary}")
    return summary


def watch_loop(
    cfg: Config,
    *,
    poll_interval: float = 2.0,
    process_existing: bool = True,
    log: Callable[[str], None] = print,
) -> None:
    """監視ループ。Ctrl-C で停止。"""
    exts = {e.lower() for e in cfg.watch.extensions}
    stable_seconds = cfg.watch.stable_seconds
    watch_dir = cfg.watch_dir
    watch_dir.mkdir(parents=True, exist_ok=True)
    cfg.transcript_dir.mkdir(parents=True, exist_ok=True)
    cfg.summary_dir.mkdir(parents=True, exist_ok=True)

    processed: set[str] = set()
    # name -> (size, この size を最初に観測した monotonic 時刻)
    pending: dict[str, tuple[int, float]] = {}

    if not process_existing:
        # 起動時点で既にあるファイルは「新規でない」として無視
        for p in _audio_files(watch_dir, exts):
            processed.add(p.name)

    log(f"[watch] 監視開始: {watch_dir}")
    log(f"[watch] 安定検知 {stable_seconds}s / ポーリング {poll_interval}s / 対象 {sorted(exts)}")

    while True:
        for p in _audio_files(watch_dir, exts):
            if p.name in processed:
                continue
            if _already_done(p, cfg):
                processed.add(p.name)
                continue

            try:
                size = p.stat().st_size
            except FileNotFoundError:
                continue  # 走査中に消えた

            prev = pending.get(p.name)
            now = time.monotonic()
            if prev is None or prev[0] != size:
                # 初観測 or サイズ変化中 → 安定待ち
                pending[p.name] = (size, now)
                continue
            if now - prev[1] < stable_seconds:
                continue  # まだ安定していない

            # 安定 → 処理（成否に関わらず再処理対象から外す）
            processed.add(p.name)
            pending.pop(p.name, None)
            try:
                process_one(p, cfg, log)
            except Exception as e:  # noqa: BLE001 - 1件の失敗でループを止めない
                log(f"[watch] エラー ({p.name}): {e}")

        time.sleep(poll_interval)
