#!/bin/bash
# Stop hook：このターンで Claude が .py を編集していたら pytest を実行し、
# 失敗していれば一度だけ終了をブロックして Claude に直させる。
#
# - 「.py を編集した」印は ruff-post-edit.sh（PostToolUse）が残す。印が無ければ何もしない。
# - 通れば印を消す。差し戻した後もまだ失敗していれば、ループさせずに印を消して終了を許す。
# - ジャーナルの確認（journal-stop.sh）とは独立して動く。

PROJECT="${CLAUDE_PROJECT_DIR:-$(pwd)}"

INPUT="$(cat)"
jq -e . >/dev/null 2>&1 <<<"$INPUT" || exit 0
SESSION_ID="$(jq -r '.session_id // "unknown"' <<<"$INPUT" 2>/dev/null)"

STATE_DIR="${TMPDIR:-/tmp}/local-llm-transcriber-hooks"
EDITED="$STATE_DIR/$SESSION_ID.py-edited"
BLOCKED="$STATE_DIR/$SESSION_ID.pytest-blocked"
[ -f "$EDITED" ] || exit 0

PYTEST="$PROJECT/.venv/bin/pytest"
if [ ! -x "$PYTEST" ]; then
  rm -f "$EDITED" "$BLOCKED"
  exit 0
fi

cd "$PROJECT" || exit 0
if OUTPUT="$("$PYTEST" -q 2>&1)"; then
  rm -f "$EDITED" "$BLOCKED"
  exit 0
fi

# 差し戻し後もまだ失敗している → 2回目は止めない
if [ -f "$BLOCKED" ]; then
  rm -f "$EDITED" "$BLOCKED"
  exit 0
fi

: >"$BLOCKED"
REASON="このターンで .py を変更しましたが、pytest が失敗しています。原因を直してから終了してください（直せない場合は、失敗している内容をユーザーに報告すること）。

$(tail -n 40 <<<"$OUTPUT")"
jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
exit 0
