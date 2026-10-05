"""Ollama 呼び出しの薄いラッパ。

`ollama run`（CLI）はTTYのスピナー/エスケープ文字が混ざるため、HTTP API を使う公式
`ollama` パッケージ経由で叩く。エラー時は分かりやすいメッセージに変換する。
"""

from __future__ import annotations

from ollama import Client


class OllamaError(RuntimeError):
    pass


class GenerationIncompleteError(OllamaError):
    """生成が完了しなかった（done_reason が "stop" でない）。

    Ollama は、サーバー側で生成が打ち切られても HTTP 200 で途中までの本文を返すことがある。
    途中までの本文は会議の内容なので、メッセージには含めない（文字数だけ持つ）。
    """

    # 同じ条件でやり直せば完了する見込みがあるか（サンプリングで毎回結果が変わるため、中断は「ある」）
    retryable = True

    def __init__(self, message: str, *, done_reason: str | None, partial_chars: int) -> None:
        super().__init__(message)
        self.done_reason = done_reason
        self.partial_chars = partial_chars


class GenerationLengthLimitError(GenerationIncompleteError):
    """出力トークン上限（num_predict）に達して打ち切られた。設定を変えない限り結果は変わらない。"""

    retryable = False


def generate(
    prompt: str,
    *,
    model: str,
    host: str,
    temperature: float,
    num_ctx: int = 16384,
    num_predict: int = 4096,
) -> str:
    """1回の生成。サーバ未起動やモデル未取得を分かりやすいエラーにする。

    num_ctx: コンテキスト長。Ollama既定(2048)では長い文字起こしが切り詰められるため明示する。
    num_predict: 出力トークン上限。明示しないと議事録が途中で打ち切られることがあるため確保する。

    生成が完了した（done_reason が "stop"）ときだけ本文を返す。それ以外は例外にする。
    """
    client = Client(host=host)
    try:
        resp = client.generate(
            model=model,
            prompt=prompt,
            options={
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
            stream=False,
        )
    except Exception as e:  # noqa: BLE001 - 接続/モデル未取得などをまとめて翻訳
        raise OllamaError(
            f"Ollama 呼び出しに失敗しました（host={host}, model={model}）。\n"
            f"  - サーバ起動: `brew services start ollama`\n"
            f"  - モデル取得: `ollama pull {model}`\n"
            f"  元エラー: {e}"
        ) from e

    # 完了の判定は done_reason だけで行う（中断された応答は done_reason が無い）
    text = (resp.get("response") or "").strip()
    done_reason = resp.get("done_reason")
    if done_reason == "stop":
        return text
    if done_reason == "length":
        raise GenerationLengthLimitError(
            f"出力が上限に達して打ち切られました（num_predict={num_predict}、"
            f"途中までの出力 {len(text)} 文字）。"
            f"config.toml の [summarize] num_predict を増やしてください。",
            done_reason=done_reason,
            partial_chars=len(text),
        )
    raise GenerationIncompleteError(
        f"生成が完了しませんでした（done_reason={done_reason!r}、"
        f"途中までの出力 {len(text)} 文字）。Ollama 側で生成が打ち切られた可能性があります。",
        done_reason=done_reason,
        partial_chars=len(text),
    )
