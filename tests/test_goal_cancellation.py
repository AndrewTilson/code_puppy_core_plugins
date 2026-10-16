"""Exercise the real cancel routes while the implementor is no longer running."""

from __future__ import annotations

import asyncio
import signal
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from code_puppy import keymap
from code_puppy.agents import _key_listeners
from code_puppy.messaging import run_ui
from code_puppy.tools import command_runner
from code_puppy_core_plugins.wiggum import (
    cancellation,
    goal_runs,
    register_callbacks,
    state,
)
from code_puppy_core_plugins.wiggum.judge import GoalJudgement
from code_puppy_core_plugins.wiggum.judge_config import JudgeConfig


@pytest.fixture
def judge_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_runs, "_RUNS_DIR", tmp_path)
    run = goal_runs.create("finish the work")
    state.start(run.prompt, mode="goal", run_id=run.run_id)
    monkeypatch.setattr(register_callbacks, "_display_llm_judge", Mock())
    monkeypatch.setattr(register_callbacks, "emit_info", Mock())
    monkeypatch.setattr(
        register_callbacks,
        "_resolve_judges",
        lambda _: [
            JudgeConfig(name="alpha", model="test"),
            JudgeConfig(name="beta", model="test"),
        ],
    )
    monkeypatch.setattr(run_ui, "_persistent", True)
    monkeypatch.setattr(run_ui, "_run_active", False)
    monkeypatch.setattr(run_ui, "_editor", None)
    monkeypatch.setattr(run_ui, "_ensure_persistent_listener_locked", lambda: None)
    monkeypatch.setattr(run_ui, "_clear_status_row", lambda: None)
    monkeypatch.setattr(run_ui, "_defer_undelivered_steers", lambda: None)
    monkeypatch.setattr(run_ui, "get_bottom_bar", Mock(return_value=Mock()))
    monkeypatch.setattr(_key_listeners, "_cancel_handler", None)
    monkeypatch.setattr(command_runner, "_AGENT_CANCEL_CB", None)
    monkeypatch.setattr(command_runner, "_SIGINT_CANCEL_REQUESTED", False)
    monkeypatch.setattr(command_runner, "_RUNNING_PROCESSES", {})
    monkeypatch.setattr(command_runner, "_tear_down_live_panels", Mock())
    monkeypatch.setattr(command_runner, "kill_all_running_shell_processes", Mock())
    monkeypatch.setattr(command_runner, "emit_warning", Mock())
    handle = SimpleNamespace(thread=Mock(), stop_event=threading.Event(), stop=Mock())
    handle.thread.is_alive.return_value = True
    monkeypatch.setattr(_key_listeners, "_active_handle", handle)
    previous_signal = signal.getsignal(signal.SIGINT)
    restore_signal = signal.signal
    yield SimpleNamespace(run=run, handle=handle)
    restore_signal(signal.SIGINT, previous_signal)
    state.stop()


@pytest.mark.parametrize("gesture", ["raw", "remapped", "sigint", "shell"])
async def test_ctrl_c_stops_all_judges_and_preserves_resume(
    judge_phase, monkeypatch, gesture
):
    started: set[str] = set()
    cancelled: set[str] = set()
    ready = asyncio.Event()
    release = asyncio.Event()
    composing = gesture in {"raw", "sigint"}

    def absorb_composing() -> bool:
        nonlocal composing
        absorbed = composing
        composing = False
        return absorbed

    monkeypatch.setattr(run_ui, "absorb_ctrl_c_if_composing", absorb_composing)
    monkeypatch.setattr(
        keymap,
        "get_cancel_agent_key",
        lambda: "ctrl+k" if gesture == "remapped" else "ctrl+c",
    )

    async def blocked_judge(*, judge_config, **kwargs):
        started.add(judge_config.name)
        if len(started) == 2:
            ready.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.add(judge_config.name)
            raise
        return GoalJudgement(
            judge_name=judge_config.name, complete=True, notes="", raw_response=""
        )

    monkeypatch.setattr(register_callbacks, "judge_goal", blocked_judge)
    task = asyncio.create_task(
        register_callbacks._on_interactive_turn_end(
            SimpleNamespace(get_message_history=lambda: []), judge_phase.run.prompt
        )
    )
    previous_signal = signal.getsignal(signal.SIGINT)

    def cancel() -> None:
        if gesture == "sigint":
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        elif gesture == "shell":
            command_runner._shell_sigint_handler(signal.SIGINT, None)
        else:
            character = "\x0b" if gesture == "remapped" else "\x03"
            _key_listeners._dispatch_key(character, lambda: None, character, None)

    try:
        await asyncio.wait_for(ready.wait(), 2)
        assert run_ui.is_run_active(), "Judges must not leave the REPL in idle mode"
        assert _key_listeners._resolve_cancel_handler(None) is not None
        assert command_runner._AGENT_CANCEL_CB is not None
        if composing:
            cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert not cancelled
        cancel()
        assert await asyncio.wait_for(task, 2) is None
        assert cancelled == {"alpha", "beta"}
        assert not state.is_active()
        saved = goal_runs.load(judge_phase.run.run_id)
        assert saved.status == "interrupted"
        assert saved.loop_count == 1
        assert not run_ui.is_run_active()
        assert _key_listeners._resolve_cancel_handler(None) is None
        assert command_runner._AGENT_CANCEL_CB is None
        assert signal.getsignal(signal.SIGINT) is previous_signal
        judge_phase.handle.stop.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
@pytest.mark.parametrize("listener", ["persistent", "owned", "no-tty"])
async def test_judge_cancel_controls_always_clean_up(
    judge_phase, monkeypatch, outcome, listener
):
    handle = judge_phase.handle if listener != "no-tty" else None
    if listener != "persistent":
        monkeypatch.setattr(
            _key_listeners, "acquire_listener", lambda *a, **k: (handle, True)
        )
    monkeypatch.setattr(cancellation, "sigint_fallback_cancels", lambda: True)
    previous_signal = signal.getsignal(signal.SIGINT)

    async def judge(**kwargs):
        assert run_ui.is_run_active()
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "error":
            raise RuntimeError("judge failure")
        return True, "", []

    monkeypatch.setattr(register_callbacks, "_run_goal_judges", judge)
    expectation = (
        pytest.raises(RuntimeError, match="judge failure")
        if outcome == "error"
        else nullcontext()
    )
    with expectation:
        await register_callbacks._on_interactive_turn_end(
            SimpleNamespace(), judge_phase.run.prompt
        )
    assert not run_ui.is_run_active()
    assert _key_listeners._resolve_cancel_handler(None) is None
    assert command_runner._AGENT_CANCEL_CB is None
    assert signal.getsignal(signal.SIGINT) is previous_signal
    if listener == "owned":
        handle.stop.assert_called_once()
        handle.thread.join.assert_called_once()
    elif listener == "persistent":
        handle.stop.assert_not_called()


@pytest.mark.parametrize("non_main_thread", [False, True])
async def test_judging_preserves_sigint_when_not_cancel_gesture(
    judge_phase, monkeypatch, non_main_thread
):
    monkeypatch.setattr(
        cancellation, "sigint_fallback_cancels", lambda: non_main_thread
    )
    if non_main_thread:
        monkeypatch.setattr(
            cancellation,
            "threading",
            SimpleNamespace(
                Event=threading.Event,
                current_thread=lambda: object(),
                main_thread=threading.main_thread,
            ),
        )
    install_signal = Mock(side_effect=AssertionError("SIGINT must stay untouched"))
    monkeypatch.setattr(
        cancellation,
        "signal",
        SimpleNamespace(signal=install_signal, SIGINT=signal.SIGINT),
    )

    async def judge(**kwargs):
        assert _key_listeners._resolve_cancel_handler(None) is not None
        return True, "", []

    monkeypatch.setattr(register_callbacks, "_run_goal_judges", judge)
    assert (
        await register_callbacks._on_interactive_turn_end(
            SimpleNamespace(), judge_phase.run.prompt
        )
        is None
    )
    install_signal.assert_not_called()


async def test_listener_setup_failure_restores_controls(judge_phase, monkeypatch):
    previous_signal = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(cancellation, "sigint_fallback_cancels", lambda: True)

    def fail_listener(*args, **kwargs):
        raise RuntimeError("listener failure")

    monkeypatch.setattr(_key_listeners, "acquire_listener", fail_listener)
    judge = Mock()

    async def work():
        judge()

    with pytest.raises(RuntimeError, match="listener failure"):
        await cancellation.run_with_cancel_controls(work())
    assert not run_ui.is_run_active()
    assert command_runner._AGENT_CANCEL_CB is None
    assert signal.getsignal(signal.SIGINT) is previous_signal
    judge.assert_not_called()
