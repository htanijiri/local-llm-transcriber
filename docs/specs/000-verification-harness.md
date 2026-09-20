# 000 検証の土台（テスト・lint・スモークテスト）

- 状態：ドラフト
- 依存：—

## 背景・課題
- 自動テストも lint もなく、変更が壊していないかを確認する手段が「実音声で手動実行」しかない。
- 文字起こし（mlx-whisper）と要約（Ollama）は重く遅いため、変更のたびに実行するのは現実的でない。
- Claude に実装を任せるうえで、Claude 自身が「完了したか」を機械的に確かめられる仕組み（センサー）が必要。
  以降の仕様書の「検証方法」はすべてこの土台の上に書く。

## ゴール
- 数秒で終わる自動テストで、パイプラインのロジック（前処理の判断、チャンク分割、watch の処理判定など）を検証できる。
- lint／フォーマットを統一し、Claude の編集時に自動で適用される。
- 実モデルを使った最小の通し確認（スモークテスト）をコマンド1つで実行できる。
- Claude が Python を変更したターンは、テストが通らない限り完了にならない。

## やらないこと
- 要約品質の評価（eval）。LLM 出力の良し悪しはテストでは測れないため、別の仕様書で扱う。
- CI（GitHub Actions）。Apple Silicon と Ollama が必要で、クラウドの CI では実モデルを動かせないため当面は見送る。
- 型チェッカー（pyright/mypy）の導入。必要になったら追加する。

## 受け入れ条件
- [ ] AC1：`uv run pytest` が、mlx-whisper と Ollama を起動・ロードせずに完走する（ネットワーク・モデル不要）。
- [ ] AC2：テスト全体が 10 秒以内に終わる。
- [ ] AC3：少なくとも次をテストでカバーする：設定の読み込みと相対パス解決／要約の単一パスと map-reduce の切り替え／watch の「処理済み判定・安定検知・失敗時の再試行」。
- [ ] AC4：`uv run ruff check` と `uv run ruff format --check` がエラーなく通る。
- [ ] AC5：`scripts/smoke.sh` が、macOS の `say` で生成した短い日本語音声を実モデルで文字起こし→要約し、出力ファイルができれば成功（exit 0）とする。実データ（`samples/` 等）に依存しない。
- [ ] AC6：Claude が `.py` を編集すると、hook で `ruff format` と `ruff check --fix` が自動で走る。
- [ ] AC7：Claude がそのターンで `.py` を変更していて `pytest` が失敗している場合、Stop hook が終了を差し戻す（1回まで）。

## 設計方針
- 開発用依存は `pyproject.toml` の `[dependency-groups] dev` に `pytest` と `ruff` を追加する。
- 重い外部依存はテストで差し替える：
  - `mlx_whisper`：`transcribe_file` 内で遅延 import しているので、`sys.modules` にダミーモジュールを差し込む。
  - Ollama：`ollama_client` の呼び出しを monkeypatch する。
  - ffmpeg：前処理はテストでは無効化、または `subprocess.run` を差し替える。
- `watch_loop` は無限ループなので、1回分の走査を関数（例：`scan_once`）に切り出してテスト可能にする。挙動は変えない。
- テストで使う設定は `tmp_path` 上に作り、`config.toml`（ローカルの実設定）を読まない。
- ruff の設定は `pyproject.toml` に置く。行長などは既存コードに合わせ、初回の整形で差分が大きくなる場合はコミットを分ける。
- hook：
  - `PostToolUse`（Edit/Write で `*.py`）：`ruff format` と `ruff check --fix` を実行。
  - `Stop`：そのターンで `.py` に変更があれば `pytest -q` を実行し、失敗なら差し戻す。既存のジャーナル確認 hook とは別スクリプトにする。
- スモークテストは実モデルを使うため hook では実行しない（数分かかる）。仕様書の検証方法で必要なときに明示的に実行する。

## 検討した案と不採用の理由
| 案 | 採用 | 理由 |
|---|---|---|
| 実モデルを使うテストだけにする | × | 1回数分かかり、Claude の編集ごとに回せない |
| 重い依存をダミーに差し替えた高速テスト＋実モデルのスモークテスト | ○ | 普段は高速テスト、節目でスモークテストと使い分けられる |
| スモークテストに `samples/` の実音声を使う | × | 公開リポジトリで再現できず、実データへの依存も生む |

## 検証方法
- 自動テスト：`uv run pytest`（AC1〜3）、`uv run ruff check && uv run ruff format --check`（AC4）
- スモークテスト：`scripts/smoke.sh`（AC5）
- hook：意図的に lint 違反と失敗するテストを入れて、自動整形と差し戻しが起きることを確認する（AC6〜7）

## 未決事項
- なし（ドラフト段階でユーザー確認待ち）

## タスク
- [ ] dev 依存（pytest, ruff）と ruff 設定を追加
- [ ] `watch_loop` から 1 回分の走査を切り出す
- [ ] テストを追加（config / summarize / watch / transcribe の前処理判断）
- [ ] `scripts/smoke.sh` を作成
- [ ] PostToolUse（ruff）と Stop（pytest）の hook を追加
- [ ] CLAUDE.md のコマンド欄と「テストはない」の記述を更新
