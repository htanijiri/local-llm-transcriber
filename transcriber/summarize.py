"""フェーズ③: テキスト → 議事録Markdown（Ollama / Qwen）。

長文対策は単一コードパス:
  - chunk_chars に収まる → 単発要約（summarize_single）
  - 超える          → 分割 → 部分要約(map) → 統合(reduce)
短文も長文も同じ経路を通る（短ければチャンクが1個になるだけ、で分岐を最小化）。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, load_prompt
from .ollama_client import generate


def _split_chunks(text: str, chunk_chars: int, overlap: int) -> list[str]:
    """文字数ベースでオーバーラップ付き分割。境界での文脈断絶を緩和する。"""
    text = text.strip()
    if len(text) <= chunk_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    step = max(1, chunk_chars - overlap)
    while start < len(text):
        chunks.append(text[start : start + chunk_chars])
        start += step
    return chunks


def summarize_text(text: str, cfg: Config) -> str:
    """文字起こしテキストを議事録Markdownに要約して返す。"""
    s = cfg.summarize

    def _gen(prompt: str) -> str:
        return generate(
            prompt,
            model=s.model,
            host=s.host,
            temperature=s.temperature,
            num_ctx=s.num_ctx,
        )

    glossary = cfg.glossary.block  # ②A: 用語集をプロンプトへ注入
    chunks = _split_chunks(text, s.chunk_chars, s.chunk_overlap)

    # 短文: 単発要約（1チャンク）
    if len(chunks) == 1:
        prompt = load_prompt("summarize_single").format(transcript=chunks[0], glossary=glossary)
        return _gen(prompt)

    # 長文: map（部分要約）→ reduce（統合）
    map_tmpl = load_prompt("summarize_map")
    partials: list[str] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        prompt = map_tmpl.format(index=i, total=total, chunk=chunk, glossary=glossary)
        partials.append(f"## チャンク {i}/{total}\n{_gen(prompt)}")

    reduce_prompt = load_prompt("summarize_reduce").format(
        partials="\n\n".join(partials), glossary=glossary
    )
    return _gen(reduce_prompt)


def summarize_file(transcript_path: Path, cfg: Config) -> Path:
    """文字起こしテキストファイルを要約し、Markdownを summary_dir に保存してパスを返す。"""
    transcript_path = Path(transcript_path).expanduser().resolve()
    if not transcript_path.exists():
        raise FileNotFoundError(f"テキストが見つかりません: {transcript_path}")

    text = transcript_path.read_text(encoding="utf-8")
    markdown = summarize_text(text, cfg)

    cfg.summary_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.summary_dir / f"{transcript_path.stem}.md"
    out_path.write_text(markdown, encoding="utf-8")
    return out_path
