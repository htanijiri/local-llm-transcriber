# CLAUDE.md

Mac mini M4 上で完全ローカルに動く、会議音声の文字起こし（mlx-whisper）＋要約（Ollama / qwen2.5:14b）パイプライン。
使い方は [README.md](README.md)、技術的な経緯とハマりどころは [TECH_NOTES.md](TECH_NOTES.md) を参照。

## 絶対に守ること
- **文字起こし・要約は完全ローカルが前提**。音声やテキストをクラウドの AI/LLM サービスに送る処理を追加しない／提案しない。
  - 例外は Web 画面（`web/`、仕様書 003/004）だけ。Cloudflare Pages が配信するのは静的ファイルのみで、データはブラウザと Google Drive API の間で直接やりとりする。自前のサーバーや、データが Cloudflare を経由する仕組み（Workers 等）は作らない。
- **Apple Silicon 専用**（mlx-whisper）。他プラットフォーム対応のための抽象化は不要。
- **公開リポジトリ**。以下は個人情報・社内情報を含むため gitignore 済み。コミットしない、中身を README / TECH_NOTES / コード / コミットメッセージに転記しない。
  - `config.toml`（実パス・用語集）、`data/`、`samples/`、`logs/`、`articles/`、`books/`、`slides/`、`journal/`
  - `PROJECT_BRIEF.md` / `REQUIREMENTS.md` / `SETUP_LOG.md` / `BUILD_RECORD.md` / `TODO.md`
- 設定キーを追加・変更したら `config.example.toml`（サンプル値・コメント）と `transcriber/config.py` の既定値も揃える。

## 設計方針
- 文字起こしと要約は疎結合。`lt transcribe` / `lt summarize` を単独で実行できる状態を保つ。
- Googleドライブ連携は Drive for desktop に任せる。同期・コピー・アップロード処理を自前で書かない。
- 設定値は `config.toml`、プロンプトは `prompts/` に置く。コードに直書きしない。

## 機能追加・変更は仕様書から
- 相談した結果は `docs/specs/NNN-機能名.md` にまとめる。運用ルールと一覧は [docs/specs/README.md](docs/specs/README.md)。
- **状態が `合意済み` の仕様書がない実装には着手しない。** 状態を `合意済み` にするのはユーザー。
- 実装中に仕様とずれたら、先に仕様書を直す。完了の判断は、仕様書の受け入れ条件と検証方法に従う。
- 仕様書は公開される。機能は汎用的に書き、録音の内容・社内の固有名詞・実際のパスは書かない。

## 消してはいけない設定（実際に失敗して入れたもの）
一見冗長でも「整理」で削らない。理由は TECH_NOTES の 4章・5章・8章。
- Ollama: `num_ctx`（既定2048だと長文が切り詰められる）、`num_predict`（議事録が途中で切れる）
- Ollama: 応答の `done_reason` が `"stop"` であることの確認（`ollama_client.generate`）。生成がサーバー側で打ち切られても HTTP 200 で途中までの本文が返るため、確認を外すと途中で切れた議事録が黙って保存される。中断は再試行（`max_attempts`）、上限到達（`"length"`）は再試行せず失敗にする
- Whisper: `condition_on_previous_text=false`、`no_speech_threshold`、先頭無音トリム（幻聴対策）
- 長文は map-reduce より単一パスを優先（`chunk_chars` を超えた場合のみ map-reduce）

## コマンド
```bash
uv sync
uv run lt run <音声ファイル>          # 文字起こし→要約を通しで実行
uv run lt transcribe <音声ファイル>
uv run lt summarize <文字起こし.txt>
uv run lt watch                       # フォルダ監視

uv run pytest                         # 自動テスト（数秒。実モデル・config.toml を使わない）
uv run ruff check && uv run ruff format --check
scripts/smoke.sh                      # 実モデルでの最小の通し確認（数分）
```
- 前提：Ollama が起動済みで `qwen2.5:14b` を pull 済み、ffmpeg がインストール済み。
- 設定ファイルは環境変数 `LT_CONFIG` で差し替えられる（未指定なら `config.toml`）。実運用のフォルダに触れずに試すときに使う。

## 検証（仕様書 000）
- **変更したら `uv run pytest` を通す。** mlx-whisper・Ollama・ffmpeg はダミーに差し替えてあり、設定は `tmp_path` 上に作る（`tests/conftest.py`）。ロジックを足したらテストも足す。
- 実モデルを通す確認は `scripts/smoke.sh`。`say` で作った音声と一時ディレクトリの設定を使うので、実データと実運用のフォルダには触れない。hook では実行されないので、文字起こし・要約の経路を変えたときに明示的に実行する。
- 要約の品質（内容の良し悪し）はテストでは測れない。必要なら `samples/` の音声で実際に通して読む。
- hook（`.claude/hooks/`）：`.py` を Edit/Write すると `ruff format` と `ruff check --fix` が走る（`ruff-post-edit.sh`）。そのターンで `.py` を変更していて pytest が失敗していると、Stop が1回だけ差し戻す（`pytest-stop.sh`）。

## watch の常駐運用
- launchd（`~/Library/LaunchAgents/com.tanijiri.local-llm-transcriber.watch.plist`）から `scripts/watch-daemon.sh` 経由で `lt watch` が常駐している。
- watch / transcribe / summarize のコードや `config.toml` を変えたら、常駐プロセスを再起動しないと反映されない：
  `launchctl kickstart -k gui/$(id -u)/com.tanijiri.local-llm-transcriber.watch`
- ログは `logs/watch.out.log` / `logs/watch.err.log`。

## 書き方
- コード中のコメント・コミットメッセージは日本語。
- Zenn記事（`articles/`）を書く・直すときは、先に `articles/STYLE.md` を読んで文体を合わせる。

## 作業ジャーナル（必須）
ユーザーの指示と、Claudeが実施した内容を `journal/YYYY-MM-DD.md` に記録する。
目的は、あとで Zenn 記事や発表資料を作るときの一次資料にすること。TECH_NOTES より細かい「何を指示して、何が起きたか」の記録。

**ルール**
- ユーザーから指示・質問を受けて応答するたびに、その応答の最後に追記する（作業を伴わない質問への回答も記録する）。
- ファイルは日単位。日付は `date '+%Y-%m-%d'` のローカル日付で決める。その日のファイルがなければ作る。
- **ユーザーの指示は要約・言い換え・誤字修正をせず、原文のまま全文を残す。**
- 追記のみ。過去のエントリは書き換えない（訂正は新しいエントリで書く）。
- 失敗したこと、うまくいかなかったこと、途中で方針を変えたことも隠さず書く（記事ではここが一番役に立つ）。
- 実行したコマンド、実測値（処理時間など）、エラーメッセージは、記事で使えるように具体的に残す。
- `journal/` は gitignore 済み。社内情報や人名が混ざる可能性があるので、公開ドキュメントへ転記するときは内容を確認する。

**hook による自動記録（`.claude/settings.json`、`.claude/hooks/`）**
- `UserPromptSubmit`（`journal-prompt.sh`）：指示の原文を `journal/raw/YYYY-MM-DD.md` に記録する。
- `Stop`（`journal-stop.sh`）：最終応答の本文を同じファイルに追記する。さらに、このターンで `journal/YYYY-MM-DD.md` に下記フォーマットの見出し（`### 指示（原文）`／`### 実施内容`／`### 結果・気づき`）が追記されたかを確認し、なければ1回だけ終了を差し戻す。
- `journal/raw/` は機械的な原文ログなので、Claude は編集しない。見出し名を変えると Stop の確認が通らなくなるので、変えるときはスクリプトも直す。

**フォーマット**
````markdown
## HH:MM 作業のタイトル（短く）

### 指示（原文）
> ユーザーの発言をそのまま。
> 複数行ならすべての行に `>` を付ける。

### 実施内容
- 何を調べ、何を判断し、何をしたか（判断の理由も）
- 実行したコマンドと結果

### 変更ファイル
- `path/to/file` — 変更の概要

### 結果・気づき
- どうなったか、残課題
- 記事ネタになりそうな点／TECH_NOTES に追記すべき知見があれば明記
````
