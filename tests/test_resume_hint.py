"""Tests for the resume_hint plugin (exit-time ``--resume`` breadcrumb)."""

from __future__ import annotations

import json

from pathlib import Path

import pytest
from code_puppy.i18n import add_catalog_dir
from code_puppy.i18n.plugin_catalog import canonical_plugin_namespace

from code_puppy_core_plugins.resume_hint import register_callbacks as plugin

SESSION = "auto_session_20261020_132142_000001_4242"
LOCALES = Path(plugin.__file__).parent / "locales"


def test_catalog_keys_use_the_namespace_the_loader_requires():
    # register_plugin_catalog rejects the whole catalog if any key strays.
    namespace = canonical_plugin_namespace("resume_hint")
    for catalog in LOCALES.glob("*.json"):
        for key in json.loads(catalog.read_text(encoding="utf-8")):
            assert key.startswith(namespace), f"{catalog.name}: {key}"


@pytest.fixture
def autosave_dir(tmp_path, monkeypatch):
    # Direct imports bypass the plugin loader that owns register_plugin_catalog.
    add_catalog_dir(str(LOCALES))
    monkeypatch.setattr(plugin, "AUTOSAVE_DIR", str(tmp_path))
    monkeypatch.setattr(plugin, "get_current_session_name", lambda: SESSION)
    monkeypatch.setattr(plugin, "get_headless_mode", lambda: False)
    monkeypatch.setattr(plugin.sys, "argv", ["/some/venv/bin/pup"])
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


def test_lookup_failure_never_raises(autosave_dir, capsys, monkeypatch):
    def boom():
        raise RuntimeError("config exploded")

    monkeypatch.setattr(plugin, "get_current_session_name", boom)

    plugin._on_shutdown()  # must not raise on the exit path

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        ("/venv/bin/pup", "pup"),
        ("/venv/Scripts/pup.exe", "pup"),
        ("/venv/bin/code-puppy", "code-puppy"),
        ("/repo/code_puppy/__main__.py", "code-puppy"),
    ],
)
def test_entrypoint_echoes_what_user_typed(monkeypatch, argv0, expected):
    monkeypatch.setattr(plugin.sys, "argv", [argv0])

    assert plugin._entrypoint_name() == expected


def test_entrypoint_defaults_when_argv_empty(monkeypatch):
    monkeypatch.setattr(plugin.sys, "argv", [])

    assert plugin._entrypoint_name() == "code-puppy"
