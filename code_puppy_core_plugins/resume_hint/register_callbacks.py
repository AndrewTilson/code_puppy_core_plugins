"""Leave a breadcrumb on exit: ``Resume this session with: pup --resume <name>``.

Hooks ``shutdown``, which ``cli_runner.main`` fires *after* the persistent UI
and message renderers are torn down. That makes it the last thing on screen,
but it also means the message bus is gone, so we write straight to stdout.

The hint only appears when the session file actually exists on disk, so we
never advertise a resume target that ``--resume`` would lazily create empty
(autosave disabled, nothing said yet, or a fresh ``/clear``).
"""

from __future__ import annotations

import logging
import sys
from importlib.resources import files
from pathlib import Path

from code_puppy.callbacks import register_callback
from code_puppy.config import (
    AUTOSAVE_DIR,
    get_current_session_name,
    get_headless_mode,
)
from code_puppy.i18n import register_plugin_catalog, t
from code_puppy.session_storage import build_session_paths

register_plugin_catalog(files(__package__).joinpath("locales"))

logger = logging.getLogger(__name__)

# Console scripts from code-puppy's pyproject; echo back whichever one was typed.
_ENTRYPOINTS = frozenset({"code-puppy", "pup"})
_DEFAULT_ENTRYPOINT = "code-puppy"


def _entrypoint_name() -> str:
    invoked = Path(sys.argv[0]).stem if sys.argv else ""
    return invoked if invoked in _ENTRYPOINTS else _DEFAULT_ENTRYPOINT


def _saved_session_name() -> str | None:
    """Return the current session name if it has been persisted, else None."""
    name = get_current_session_name()
    paths = build_session_paths(Path(AUTOSAVE_DIR), name)
    if paths.json_path.is_file() or paths.pickle_path.is_file():
        return name
    return None


def _format_resume_hint(session_name: str) -> str:
    return f"\n{t('plugin.resume-hint.header')}\n  {_entrypoint_name()} --resume {session_name}\n"


def _on_shutdown() -> None:
    # Headless ``-p`` stdout is often machine-parsed; don't pollute it.
    if get_headless_mode():
        return
    try:
        session_name = _saved_session_name()
        if session_name:
            print(_format_resume_hint(session_name), flush=True)
    except Exception:
        # A goodbye nicety must never turn a clean exit into a traceback.
        logger.debug("resume_hint: unable to print resume hint", exc_info=True)


register_callback("shutdown", _on_shutdown)
