"""watch：処理済み判定・安定検知・失敗時の再試行。1回分の走査（scan_once）を時刻を進めながら呼ぶ。"""

from __future__ import annotations

from pathlib import Path

import pytest

from transcriber import watch as watch_module
from transcriber.config import Config
from transcriber.watch import _MAX_ATTEMPTS, _RETRY_COOLDOWN, WatchState, scan_once, watch_loop

from .conftest import FakeClock, FakeOllama, FakeWhisper


class Recorder:
    """process_one の差し替え。呼ばれたファイル名を記録し、指定された回数だけ失敗する。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_times: dict[str, int] = {}

    def __call__(self, audio_path: Path, cfg: Config, log, state=None) -> Path:
        self.calls.append(audio_path.name)
        if self.fail_times.get(audio_path.name, 0) > 0:
            self.fail_times[audio_path.name] -= 1
            raise RuntimeError("処理に失敗")
        return cfg.summary_dir / f"{audio_path.stem}.md"


@pytest.fixture
def process(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(watch_module, "process_one", recorder)
    return recorder


@pytest.fixture
def logs() -> list[str]:
    return []


def add_audio(cfg: Config, name: str = "会議.m4a", size: int = 10) -> Path:
    path = cfg.watch_dir / name
    path.write_bytes(b"\0" * size)
    return path


def scan_until_stable(cfg: Config, state: WatchState, logs: list[str], clock: FakeClock) -> None:
    """初観測 → 安定待ち → 処理、までを進める。"""
    scan_once(cfg, state, logs.append, clock=clock)
    clock.advance(cfg.watch.stable_seconds)
    scan_once(cfg, state, logs.append, clock=clock)


def test_file_with_existing_summary_is_skipped(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    add_audio(cfg)
    (cfg.summary_dir / "会議.md").write_text("済み", encoding="utf-8")
    state = WatchState()

    scan_until_stable(cfg, state, logs, clock)

    assert process.calls == []
    assert state.processed == {"会議.m4a"}


def test_file_is_processed_only_after_it_is_stable(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    add_audio(cfg)
    state = WatchState()

    scan_once(cfg, state, logs.append, clock=clock)  # 初観測
    clock.advance(cfg.watch.stable_seconds - 1)
    scan_once(cfg, state, logs.append, clock=clock)  # まだ安定していない
    assert process.calls == []

    clock.advance(1)
    scan_once(cfg, state, logs.append, clock=clock)
    assert process.calls == ["会議.m4a"]
    assert state.processed == {"会議.m4a"}

    scan_once(cfg, state, logs.append, clock=clock)  # 処理済みは二度処理しない
    assert process.calls == ["会議.m4a"]


def test_size_change_restarts_the_wait(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    """同期・書き込みの途中でサイズが変わったら、そこから安定待ちをやり直す。"""
    state = WatchState()
    add_audio(cfg, size=10)
    scan_once(cfg, state, logs.append, clock=clock)

    clock.advance(cfg.watch.stable_seconds)
    add_audio(cfg, size=20)
    scan_once(cfg, state, logs.append, clock=clock)
    assert process.calls == []

    clock.advance(cfg.watch.stable_seconds)
    scan_once(cfg, state, logs.append, clock=clock)
    assert process.calls == ["会議.m4a"]


def test_failed_file_is_retried_after_cooldown(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    add_audio(cfg)
    process.fail_times["会議.m4a"] = 1
    state = WatchState()

    scan_until_stable(cfg, state, logs, clock)
    assert process.calls == ["会議.m4a"]
    assert "会議.m4a" not in state.processed
    assert f"試行1/{_MAX_ATTEMPTS}" in logs[-1]

    clock.advance(_RETRY_COOLDOWN - 1)
    scan_once(cfg, state, logs.append, clock=clock)  # クールダウン中は試さない
    assert process.calls == ["会議.m4a"]

    clock.advance(1)
    scan_once(cfg, state, logs.append, clock=clock)
    assert process.calls == ["会議.m4a", "会議.m4a"]
    assert state.processed == {"会議.m4a"}
    assert state.failures == {}


def test_file_is_given_up_after_max_attempts(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    add_audio(cfg)
    process.fail_times["会議.m4a"] = _MAX_ATTEMPTS + 5
    state = WatchState()

    scan_until_stable(cfg, state, logs, clock)
    for _ in range(_MAX_ATTEMPTS + 2):
        clock.advance(_RETRY_COOLDOWN)
        scan_once(cfg, state, logs.append, clock=clock)

    assert len(process.calls) == _MAX_ATTEMPTS
    assert state.processed == {"会議.m4a"}
    assert any("諦め" in line for line in logs)


def test_one_failure_does_not_stop_other_files(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    add_audio(cfg, "a.m4a")
    add_audio(cfg, "b.m4a")
    process.fail_times["a.m4a"] = 1
    state = WatchState()

    scan_until_stable(cfg, state, logs, clock)

    assert process.calls == ["a.m4a", "b.m4a"]
    assert state.processed == {"b.m4a"}


def test_hidden_files_and_other_extensions_are_ignored(
    cfg: Config, process: Recorder, logs: list[str], clock: FakeClock
) -> None:
    add_audio(cfg, ".同期中.m4a")
    add_audio(cfg, "メモ.txt")
    add_audio(cfg, "大文字.M4A")
    (cfg.watch_dir / "フォルダ.m4a").mkdir()
    state = WatchState()

    scan_until_stable(cfg, state, logs, clock)

    assert process.calls == ["大文字.M4A"]


def test_process_one_runs_transcribe_then_summarize(
    cfg: Config, whisper: FakeWhisper, ollama: FakeOllama, logs: list[str], clock: FakeClock
) -> None:
    """差し替えなしの process_one で、文字起こし → 要約 → 処理済み判定までつながる。"""
    add_audio(cfg)
    state = WatchState()

    scan_until_stable(cfg, state, logs, clock)

    assert (cfg.transcript_dir / "会議.txt").read_text(encoding="utf-8") == "文字起こしの結果です。"
    assert (cfg.summary_dir / "会議.md").read_text(encoding="utf-8") == "応答1"
    assert state.processed == {"会議.m4a"}
    assert len(whisper.calls) == 1
    assert len(ollama.calls) == 1


class StopLoop(Exception):
    pass


def run_loop_once(cfg: Config, monkeypatch: pytest.MonkeyPatch, **kwargs) -> list[str]:
    """watch_loop を、最初の sleep で止めて1周だけ回す。"""
    logs: list[str] = []

    def stop_at_sleep(seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(watch_module.time, "sleep", stop_at_sleep)
    with pytest.raises(StopLoop):
        watch_loop(cfg, log=logs.append, **kwargs)
    return logs


def test_watch_loop_creates_folders_and_logs_start(
    cfg: Config, process: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg.summary_dir.rmdir()

    logs = run_loop_once(cfg, monkeypatch)

    assert cfg.summary_dir.is_dir()
    assert "監視開始" in logs[0]


def test_watch_loop_can_ignore_existing_files(
    cfg: Config, process: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """process_existing=False のとき、起動時点であったファイルは処理しない。"""
    add_audio(cfg)
    monkeypatch.setattr(cfg.watch, "stable_seconds", 0)
    seen: list[WatchState] = []
    original = watch_module.scan_once

    def scan_twice(cfg, state, log, **kwargs):
        seen.append(state)
        original(cfg, state, log, **kwargs)
        original(cfg, state, log, **kwargs)

    monkeypatch.setattr(watch_module, "scan_once", scan_twice)
    run_loop_once(cfg, monkeypatch, process_existing=False)

    assert process.calls == []
    assert seen[0].processed == {"会議.m4a"}
