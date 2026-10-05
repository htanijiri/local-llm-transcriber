"""CLIエントリ。各フェーズを独立サブコマンドとして手動実行できる。

uv run lt transcribe <音声ファイル>
uv run lt summarize  <文字起こしテキスト>
uv run lt run        <音声ファイル>   # ②→③を通しで実行
uv run lt watch                       # 監視フォルダを見張って自動処理（①②③）
"""

from __future__ import annotations

from pathlib import Path

import typer

from .config import Config, load_config

app = typer.Typer(add_completion=False, help="ローカルLLM 文字起こし・要約パイプライン")


@app.command()
def transcribe(audio: Path = typer.Argument(..., help="音声ファイルのパス")) -> None:
    """フェーズ②: 音声 → 文字起こしテキスト。"""
    from .transcribe import transcribe_file

    cfg = load_config()
    typer.echo(f"[transcribe] {audio} を文字起こし中 (model={cfg.transcribe.model}) ...")
    out = transcribe_file(audio, cfg)
    typer.echo(f"[transcribe] 完了 → {out}")


def _summarize_or_exit(transcript: Path, cfg: Config, tag: str) -> Path:
    """要約する。生成が完了しなかった場合は、理由を表示して終了コード 1 で終わる。"""
    from .ollama_client import GenerationIncompleteError
    from .summarize import summarize_file

    try:
        return summarize_file(transcript, cfg, typer.echo)
    except GenerationIncompleteError as e:
        typer.echo(f"[{tag}] 失敗: 議事録は保存していません。{e}", err=True)
        raise typer.Exit(1) from e


@app.command()
def summarize(transcript: Path = typer.Argument(..., help="文字起こしテキストのパス")) -> None:
    """フェーズ③: テキスト → 議事録Markdown。"""
    cfg = load_config()
    typer.echo(f"[summarize] {transcript} を要約中 (model={cfg.summarize.model}) ...")
    out = _summarize_or_exit(transcript, cfg, "summarize")
    typer.echo(f"[summarize] 完了 → {out}")


@app.command()
def run(audio: Path = typer.Argument(..., help="音声ファイルのパス")) -> None:
    """②→③を通しで実行（音声 → 文字起こし → 議事録）。"""
    from .transcribe import transcribe_file

    cfg = load_config()
    typer.echo(f"[run] 文字起こし: {audio} ...")
    transcript = transcribe_file(audio, cfg)
    typer.echo(f"[run] → {transcript}")
    typer.echo("[run] 要約 ...")
    summary = _summarize_or_exit(transcript, cfg, "run")
    typer.echo(f"[run] 完了 → {summary}")


@app.command()
def watch(
    poll: float = typer.Option(2.0, help="ポーリング間隔（秒）"),
    process_existing: bool = typer.Option(True, help="起動時に既にある未処理ファイルも対象にする"),
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
