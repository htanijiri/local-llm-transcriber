"""CLIエントリ。各フェーズを独立サブコマンドとして手動実行できる。

  uv run lt transcribe <音声ファイル>
  uv run lt summarize  <文字起こしテキスト> [--model <Ollamaモデル名>]
  uv run lt run        <音声ファイル>   # ②→③を通しで実行
  uv run lt watch                       # 監視フォルダを見張って自動処理（①②③）
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from .config import load_config

app = typer.Typer(add_completion=False, help="ローカルLLM 文字起こし・要約パイプライン")


@app.command()
def transcribe(audio: Path = typer.Argument(..., help="音声ファイルのパス")) -> None:
    """フェーズ②: 音声 → 文字起こしテキスト。"""
    from .transcribe import transcribe_file

    cfg = load_config()
    typer.echo(f"[transcribe] {audio} を文字起こし中 (model={cfg.transcribe.model}) ...")
    out = transcribe_file(audio, cfg)
    typer.echo(f"[transcribe] 完了 → {out}")


@app.command()
def summarize(
    transcript: Path = typer.Argument(..., help="文字起こしテキストのパス"),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="要約モデルを一時的に差し替える（モデル比較用）。出力名にモデル名が付き、既存の議事録を上書きしない",
    ),
) -> None:
    """フェーズ③: テキスト → 議事録Markdown。"""
    import time

    from .summarize import summarize_file

    cfg = load_config()
    suffix = ""
    if model:
        cfg.summarize.model = model
        # 比較用: 既定モデルの議事録を上書きしないよう、出力名にモデル名を付ける
        suffix = "." + re.sub(r"[^0-9A-Za-z._-]+", "_", model)
    typer.echo(f"[summarize] {transcript} を要約中 (model={cfg.summarize.model}) ...")
    t0 = time.monotonic()
    out = summarize_file(transcript, cfg, suffix=suffix)
    typer.echo(f"[summarize] 完了 → {out}（{time.monotonic() - t0:.1f}秒）")


@app.command()
def run(audio: Path = typer.Argument(..., help="音声ファイルのパス")) -> None:
    """②→③を通しで実行（音声 → 文字起こし → 議事録）。"""
    from .summarize import summarize_file
    from .transcribe import transcribe_file

    cfg = load_config()
    typer.echo(f"[run] 文字起こし: {audio} ...")
    transcript = transcribe_file(audio, cfg)
    typer.echo(f"[run] → {transcript}")
    typer.echo("[run] 要約 ...")
    summary = summarize_file(transcript, cfg)
    typer.echo(f"[run] 完了 → {summary}")


@app.command()
def watch(
    poll: float = typer.Option(2.0, help="ポーリング間隔（秒）"),
    process_existing: bool = typer.Option(
        True, help="起動時に既にある未処理ファイルも対象にする"
    ),
) -> None:
    """フェーズ①: 監視フォルダを見張り、新規音声を自動で 文字起こし→要約。Ctrl-C で停止。"""
    from .watch import watch_loop

    cfg = load_config()
    try:
        watch_loop(
            cfg,
            poll_interval=poll,
            process_existing=process_existing,
            log=typer.echo,
        )
    except KeyboardInterrupt:
        typer.echo("\n[watch] 停止しました。")


if __name__ == "__main__":
    app()
