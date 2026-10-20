"""Tests for the resume_hint plugin (exit-time ``--resume`` breadcrumb)."""

from __future__ import annotations

import argparse
import shlex
from importlib.resources import files

import pytest
from code_puppy.i18n import register_plugin_catalog
from code_puppy.plugins import _plugin_loading_context

from code_puppy_core_plugins.resume_hint import register_callbacks as plugin

SESSION = "auto_session_20261020_132142_000001_4242"


def _on_path(*names):
    return lambda name: f"/usr/local/bin/{name}" if name in names else None


@pytest.fixture
def autosave_dir(tmp_path, monkeypatch):
    # Register exactly as the loader does, so namespace rules are enforced.
    with _plugin_loading_context("resume_hint"):
        assert register_plugin_catalog(files(plugin.__package__).joinpath("locales"))
    monkeypatch.setattr(plugin, "AUTOSAVE_DIR", str(tmp_path))
    monkeypatch.setattr(plugin, "get_current_session_name", lambda: SESSION)
    monkeypatch.setattr(plugin, "get_headless_mode", lambda: False)
    monkeypatch.setattr(plugin, "get_auto_save_session", lambda: True)
    monkeypatch.setattr(plugin.sys, "argv", ["/some/venv/bin/pup"])
    monkeypatch.setattr(plugin.shutil, "which", _on_path("pup", "code-puppy"))
    return tmp_path


@pytest.mark.parametrize("suffix", [".json", ".pkl"])
def test_prints_hint_when_session_saved(autosave_dir, capsys, suffix):
    (autosave_dir / f"{SESSION}{suffix}").write_text("{}")

    plugin._on_shutdown()

    out = capsys.readouterr().out
    assert "Resume this session with:" in out
    assert f"  pup --resume {SESSION}" in out


def test_silent_when_session_never_saved(autosave_dir, capsys):
    plugin._on_shutdown()

    assert capsys.readouterr().out == ""


def test_silent_in_headless_mode(autosave_dir, capsys, monkeypatch):
    (autosave_dir / f"{SESSION}.json").write_text("{}")
    monkeypatch.setattr(plugin, "get_headless_mode", lambda: True)

    plugin._on_shutdown()

    assert capsys.readouterr().out == ""


def test_silent_when_autosave_disabled(autosave_dir, capsys, monkeypatch):
    # A file from /compact or an earlier --resume would be a stale snapshot.
    (autosave_dir / f"{SESSION}.json").write_text("{}")
    monkeypatch.setattr(plugin, "get_auto_save_session", lambda: False)

    plugin._on_shutdown()

    assert capsys.readouterr().out == ""


def test_lookup_failure_never_raises(autosave_dir, capsys, monkeypatch):
    def boom():
        raise RuntimeError("config exploded")

    monkeypatch.setattr(plugin, "get_current_session_name", boom)

    plugin._on_shutdown()  # must not raise on the exit path

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("session_name", [SESSION, "-dash-first"])
def test_resume_arg_parses_back_to_the_session_name(session_name):
    # Mirrors cli_runner's --resume definition; argparse eats a bare "-x" value.
    parser = argparse.ArgumentParser(exit_on_error=False)
    parser.add_argument("--resume", "-r", type=str)

    args = parser.parse_args(shlex.split(plugin._resume_arg(session_name)))

    assert args.resume == session_name


@pytest.mark.parametrize(
    ("argv", "on_path", "expected"),
    [
        (["/venv/bin/pup"], ("pup", "code-puppy"), "pup"),
        (["/venv/Scripts/pup.exe"], ("pup",), "pup"),
        (["/venv/bin/code-puppy"], ("code-puppy",), "code-puppy"),
        (["/repo/code_puppy/__main__.py"], ("code-puppy",), "code-puppy"),
        (["/venv/bin/pup"], ("code-puppy",), "code-puppy"),
        (["/uv/archive-v0/abc/bin/code-puppy"], (), "uvx code-puppy"),
        ([], (), "uvx code-puppy"),
    ],
)
def test_launch_command_is_runnable_from_the_shell(
    monkeypatch, argv, on_path, expected
):
    monkeypatch.setattr(plugin.sys, "argv", argv)
    monkeypatch.setattr(plugin.shutil, "which", _on_path(*on_path))

    assert plugin._launch_command() == expected
