"""Cancel ownership for judges, which run after the implementor disarms its UI."""

from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from code_puppy.agents import _key_listeners
from code_puppy.agents._run_signals import make_schedule_cancel, sigint_should_cancel
from code_puppy.keymap import sigint_fallback_cancels
from code_puppy.messaging.run_ui import run_ui
from code_puppy.tools import command_runner

_Result = TypeVar("_Result")


async def run_with_cancel_controls(work: Coroutine[Any, Any, _Result]) -> _Result:
    """Called by: register_callbacks::_on_interactive_turn_end.

    Depends on: core run UI, key listener, run-signals and shell-cancel bridge.
    """
    task = asyncio.create_task(work)
    cancel = make_schedule_cancel(task, asyncio.get_running_loop())
    stop_event = threading.Event()
    handle = None
    spawned = True
    previous_signal = None

    def handle_sigint(_sig: int, _frame: Any) -> None:
        if not command_runner.is_awaiting_user_input() and sigint_should_cancel():
            cancel()

    try:
        with run_ui():
            try:
                command_runner.register_agent_cancel(cancel)
                if (
                    threading.current_thread() is threading.main_thread()
                    and sigint_fallback_cancels()
                ):
                    previous_signal = signal.signal(signal.SIGINT, handle_sigint)
                handle, spawned = _key_listeners.acquire_listener(
                    stop_event, on_escape=lambda: None, on_cancel_agent=cancel
                )
                if not spawned:
                    _key_listeners.set_cancel_handler(cancel)
                return await task
            finally:
                # Disarm before returning the persistent prompt to idle routing.
                command_runner.clear_agent_cancel()
                if not spawned:
                    _key_listeners.set_cancel_handler(None)
                elif handle is not None:
                    _key_listeners.set_active_handle(None)
                    handle.stop()
                    handle.thread.join(timeout=1.0)
                else:
                    stop_event.set()
                if previous_signal is not None:
                    signal.signal(signal.SIGINT, previous_signal)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
