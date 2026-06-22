# local-llm-transcriber

Mac mini M4 上で**完全ローカル**に動く、音声の文字起こし＋要約パイプライン。
会議音声 → mlx-whisper で文字起こし → Ollama(Qwen) で議事録ドラフト生成。

## 必要環境
- Apple Silicon Mac（M4で動作確認）/ macOS
- [uv](https://docs.astral.sh/uv/)（Python 3.12 環境を自動管理）
- [Ollama](https://ollama.com/) + `qwen2.5:14b`（`brew services start ollama && ollama pull qwen2.5:14b`）
- ffmpeg

## セットアップ
```bash
uv sync                 # 依存をインストール（初回のみ）
```

## 使い方
```bash
# 音声 → 文字起こし → 議事録 を通しで実行
uv run lt run path/to/meeting.m4a

# 個別に実行することも可能（疎結合）
uv run lt transcribe path/to/meeting.m4a           # → data/transcripts/meeting.txt
uv run lt summarize  data/transcripts/meeting.txt  # → data/summaries/meeting.md
```

出力先・モデル・チャンクサイズ等は [config.toml](config.toml) に集約。

## Googleドライブ連携
Google Drive for desktop を導入し、対象フォルダを「オフラインで使用可能」にしたうえで、
`config.toml` の `[paths]` をそのマウントフォルダ（`~/Library/CloudStorage/GoogleDrive-.../...`）に
向けるだけ。コピー/アップロードの自前処理は不要（同期はDriveアプリが担当）。

## 構成
```
transcriber/   本体（cli / config / transcribe / summarize / ollama_client）
prompts/       要約プロンプト（single / map / reduce）
config.toml    設定の集約
data/          ローカル検証用の入出力（inbox / transcripts / summaries）
```

## 今後
- `watch`：フォルダ監視で自動処理（ファイル安定検知付き）
- 要約プロンプトのチューニング（前置き抑制・見出し最終形）
- whisper.cpp 保険経路 / 日本語精度の検討（SenseVoice 等）
