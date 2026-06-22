"""Ollama 呼び出しの薄いラッパ。

`ollama run`（CLI）はTTYのスピナー/エスケープ文字が混ざるため、HTTP API を使う公式
`ollama` パッケージ経由で叩く。エラー時は分かりやすいメッセージに変換する。
"""

from __future__ import annotations

from ollama import Client


class OllamaError(RuntimeError):
    pass


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
    return resp["response"].strip()
