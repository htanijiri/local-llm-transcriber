#!/bin/bash
# スモークテスト：実モデル（mlx-whisper・Ollama）で、文字起こし→要約を最小の音声で通す。
#
#   scripts/smoke.sh
#
# - 音声は macOS の `say` でその場で作る（実データに依存しない）。
# - 設定は config.example.toml の [paths] だけを一時ディレクトリに差し替えたものを使う。
#   ローカルの config.toml と、その出力先（実運用のフォルダ）には触れない。
# - 文字起こしと議事録のファイルができれば成功（exit 0）。中身の良し悪しは見ない。
# - 前提：Ollama が起動済みで、config.example.toml のモデルを取得済み。数分かかる。
set -euo pipefail

cd "$(dirname "$0")/.."

WORK="$(mktemp -d "${TMPDIR:-/tmp}/lt-smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/inbox" "$WORK/transcripts" "$WORK/summaries"

# 日本語の音声を選ぶ（Kyoko が無い環境では、最初に見つかった日本語の音声を使う）
VOICE="$(say -v '?' | awk '$2 == "ja_JP" { print $1 }' | grep -x Kyoko || true)"
[ -n "$VOICE" ] || VOICE="$(say -v '?' | awk '$2 == "ja_JP" { print $1; exit }')"
if [ -z "$VOICE" ]; then
  echo "[smoke] 日本語の音声が見つかりません（システム設定 > アクセシビリティ > 読み上げコンテンツ で追加）" >&2
  exit 1
fi

AUDIO="$WORK/inbox/smoke.aiff"
say -v "$VOICE" -o "$AUDIO" \
  "それでは定例会議を始めます。今日の議題は二つです。一つ目は、来月のリリース日を十五日に決めることです。二つ目は、テストの担当者を決めることです。リリース日は十五日で合意しました。テストの担当は田中さんにお願いします。以上で会議を終わります。"

CONFIG="$WORK/config.toml"
sed -E \
  -e "s|^watch_dir[[:space:]]*=.*|watch_dir = \"$WORK/inbox\"|" \
  -e "s|^transcript_dir[[:space:]]*=.*|transcript_dir = \"$WORK/transcripts\"|" \
  -e "s|^summary_dir[[:space:]]*=.*|summary_dir = \"$WORK/summaries\"|" \
  config.example.toml >"$CONFIG"

echo "[smoke] 音声: $AUDIO（$VOICE）"
LT_CONFIG="$CONFIG" uv run lt run "$AUDIO"

TRANSCRIPT="$WORK/transcripts/smoke.txt"
SUMMARY="$WORK/summaries/smoke.md"
for f in "$TRANSCRIPT" "$SUMMARY"; do
  if [ ! -s "$f" ]; then
    echo "[smoke] 失敗：出力がありません: $f" >&2
    exit 1
  fi
done

echo "[smoke] 文字起こし（$(wc -c <"$TRANSCRIPT" | tr -d ' ') バイト）:"
cat "$TRANSCRIPT"
echo
echo "[smoke] 議事録（$(wc -c <"$SUMMARY" | tr -d ' ') バイト）の先頭:"
head -n 5 "$SUMMARY"
echo "[smoke] 成功"
