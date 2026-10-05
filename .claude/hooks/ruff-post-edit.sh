#!/bin/bash
# PostToolUse hook（Edit / Write）：Claude が .py を編集したら ruff で整形・自動修正する。
#
#   1. `ruff format` と `ruff check --fix` を、編集されたファイルにかける。
#   2. 自動で直せない指摘が残ったら、exit 2 で Claude に伝える（stderr が Claude に渡る）。
#   3. このターンで .py を触った印を残す（Stop hook の pytest-stop.sh が見る）。

PROJECT="${CLAUDE_PROJECT_DIR:-$(pwd)}"

INPUT="$(cat)"
jq -e . >/dev/null 2>&1 <<<"$INPUT" || exit 0
FILE="$(jq -r '.tool_input.file_path // ""' <<<"$INPUT" 2>/dev/null)"
SESSION_ID="$(jq -r '.session_id // "unknown"' <<<"$INPUT" 2>/dev/null)"

case "$FILE" in
  "$PROJECT"/*.py) ;;
  *) exit 0 ;;
esac
# ワークツリー（リポジトリの複製）と仮想環境は対象外
case "$FILE" in
  "$PROJECT"/.claude/* | "$PROJECT"/.venv/*) exit 0 ;;
esac
[ -f "$FILE" ] || exit 0

STATE_DIR="${TMPDIR:-/tmp}/local-llm-transcriber-hooks"
mkdir -p "$STATE_DIR" 2>/dev/null && : >"$STATE_DIR/$SESSION_ID.py-edited"

RUFF="$PROJECT/.venv/bin/ruff"
[ -x "$RUFF" ] || exit 0  # dev 依存が未インストール（uv sync 前）なら何もしない

cd "$PROJECT" || exit 0
"$RUFF" format --quiet "$FILE" >/dev/null 2>&1
if ! OUTPUT="$("$RUFF" check --fix --quiet "$FILE" 2>&1)"; then
  {
    echo "ruff check に、自動で直せない指摘が残っています（$FILE）。修正してください。"
    echo "$OUTPUT"
  } >&2
  exit 2
fi
exit 0
