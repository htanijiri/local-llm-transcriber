#!/bin/bash
# launchd から起動される watch のラッパー。
# Googleドライブの監視フォルダがマウントされるのを待ってから `lt watch` を起動する。
# （ログイン直後はまだDriveがマウントされていないことがあるため）
set -u

PROJECT="$HOME/Developer/local-llm-transcriber"
UV="/opt/homebrew/bin/uv"

cd "$PROJECT" || exit 1

# config から監視フォルダのパスを取得
WATCH_DIR="$("$UV" run python -c 'from transcriber.config import load_config; print(load_config().watch_dir)' 2>/dev/null)"

# 監視フォルダ（＝Driveのマウント）が現れるまで最大5分待つ
if [ -n "$WATCH_DIR" ]; then
  for _ in $(seq 1 60); do
    [ -d "$WATCH_DIR" ] && break
    sleep 5
  done
  # それでも無ければ、まだDriveが用意できていないので一旦終了。
  # launchd の KeepAlive が少し後にまた起動してくれる（＝ローカルに空フォルダを作ってしまうのを防ぐ）
  if [ ! -d "$WATCH_DIR" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') watch_dir not mounted yet: $WATCH_DIR" >&2
    exit 1
  fi
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') starting watch on: $WATCH_DIR"
exec "$UV" run lt watch
