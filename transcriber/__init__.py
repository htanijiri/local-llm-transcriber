"""ローカルLLM 文字起こし・要約パイプライン。

フェーズ:
- transcribe: mlx-whisper で音声 → テキスト
- summarize:  Ollama(Qwen) でテキスト → 議事録Markdown
- watch:      (今後) フォルダ監視で自動処理
"""

__version__ = "0.1.0"
