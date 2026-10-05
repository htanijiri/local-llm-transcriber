"""フェーズ②: mlx-whisper で音声 → テキスト。

whisper.cpp への切替（保険）は、この関数のなかだけ差し替えれば済むように隔離している。
前段に ffmpeg による前処理（先頭無音トリム）を挟み、無音区間でのWhisper幻聴を防ぐ。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Config

# クラウドストレージ(Google Drive for desktop 等)のマウント先
_CLOUD_ROOT = Path.home() / "Library" / "CloudStorage"


def _localize(audio_path: Path) -> tuple[Path, bool]:
    """クラウドマウント上のファイルは、ローカルの一時ファイルにコピーしてから使う。

    Googleドライブのファイルは、アップ直後などまだローカルに実体化(ダウンロード)されて
    いないことがあり、その状態で ffmpeg/whisper が直接開くと失敗する（Resource deadlock 等）。
    先にローカルへコピーすることで実体化を強制し、読み取りを安定させる。処理後に必ず削除する。

    ローカルのパスならコピーせずそのまま使う。
    返り値: (実際に使うパス, それが一時コピーか)
    """
    if _CLOUD_ROOT not in audio_path.parents:
        return audio_path, False
    tmp = Path(tempfile.gettempdir()) / f"lt_src_{os.getpid()}_{audio_path.name}"
    shutil.copy2(audio_path, tmp)  # read()経由でコピー＝Drive側のダウンロードを誘発
    return tmp, True


def _preprocess_audio(audio_path: Path, cfg: Config) -> tuple[Path, bool]:
    """先頭の無音をトリムした一時wav(16kHz mono)を作る。

    Whisperは無音・極小音量の区間で「ご視聴ありがとうございました」等を捏造する。
    実音声では録画開始〜会議開始まで3分超の無音があり、そこが幻聴で埋まっていた。
    先頭無音を物理的に削ることで根本から防ぐ。

    ffmpegが無い/失敗した/トリム結果が空に近い場合は、元ファイルをそのまま使う（安全側）。
    返り値: (文字起こしに使うパス, それが一時ファイルか)
    """
    if not cfg.transcribe.trim_leading_silence or shutil.which("ffmpeg") is None:
        return audio_path, False

    tmp = Path(tempfile.gettempdir()) / f"lt_{audio_path.stem}_trim.wav"
    thr = cfg.transcribe.silence_threshold_db
    # start_periods=1: 先頭の無音のみ除去（会議中の間は残す）。
    filt = f"silenceremove=start_periods=1:start_duration=0.5:start_threshold={thr}dB"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-af",
                filt,
                str(tmp),
            ],
            check=True,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001 - ffmpeg の失敗理由は問わず、元ファイルで続行する（安全側）
        return audio_path, False

    # 全編が閾値以下等でトリム結果が空に近い場合は元ファイルに退避
    if not tmp.exists() or tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        return audio_path, False
    return tmp, True


def transcribe_file(audio_path: Path, cfg: Config) -> Path:
    """音声ファイルを文字起こしし、テキストを transcript_dir に保存してパスを返す。"""
    import mlx_whisper  # 重い import は呼び出し時に遅延

    audio_path = Path(audio_path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_path}")

    # クラウド上のファイルは一旦ローカルへコピー（未DL/placeholder起因の読み取り失敗を回避）
    local_src, src_is_tmp = _localize(audio_path)
    try:
        source, prep_is_tmp = _preprocess_audio(local_src, cfg)
        try:
            result = mlx_whisper.transcribe(
                str(source),
                path_or_hf_repo=cfg.transcribe.model,
                language=cfg.transcribe.language,
                # 幻聴ループ対策（実音声で「ご視聴ありがとうございました」連呼が発生したため）:
                #  - condition_on_previous_text=False: 直前の（幻聴混じりの）出力に引きずられて
                #    ループするのを防ぐ。低品質・無音区間の多い音声で特に効く。
                #  - temperature フォールバック: あるtemperatureで失敗(高圧縮比/低尤度)した区間を
                #    温度を上げて再デコードし、リピート地獄から脱出させる。
                condition_on_previous_text=cfg.transcribe.condition_on_previous_text,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                compression_ratio_threshold=2.4,
                logprob_threshold=-1.0,
                no_speech_threshold=0.6,
                initial_prompt=cfg.transcribe.initial_prompt or None,
            )
        finally:
            if prep_is_tmp:
                source.unlink(missing_ok=True)
    finally:
        if src_is_tmp:
            local_src.unlink(missing_ok=True)  # ローカルコピーは必ず削除

    text = result["text"].strip()

    cfg.transcript_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.transcript_dir / f"{audio_path.stem}.txt"
    out_path.write_text(text, encoding="utf-8")
    return out_path
