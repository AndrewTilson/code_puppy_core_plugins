"""Tests for the setup_cli plugin (scriptable configuration)."""

from __future__ import annotations

import argparse
import json

from code_puppy_core_plugins.setup_cli import cli, register_callbacks


def test_register_cli_args_adds_setup_remainder():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="*")
    register_callbacks._register_cli_args(parser)
    args = parser.parse_args(["--setup", "doctor", "--json"])
    assert args.setup == ["doctor", "--json"]
    plain = parser.parse_args([])
    assert plain.setup is None


def test_handle_cli_args_passes_through_without_setup():
    args = argparse.Namespace(setup=None)
    assert register_callbacks._handle_cli_args(args) is None


def test_handle_cli_args_handles_setup(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "code_puppy_core_plugins.setup_cli.cli.main",
        lambda tokens: seen.update(tokens=tokens) or 7,
    )
    result = register_callbacks._handle_cli_args(argparse.Namespace(setup=["doctor"]))
    assert result == {"handled": True, "exit_code": 7}
    assert seen["tokens"] == ["doctor"]


def test_doctor_json_shape(capsys):
    code = cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert set(report) >= {"ok", "checks", "providers"}
    assert code in (0, 1)
    assert any(c["check"] == "identity" for c in report["checks"])


def test_config_set_get_roundtrip(capsys):
    assert cli.main(["config", "set", "puppy_name", "Testy"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "get", "puppy_name", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["value"] == "Testy"
    assert data["set"] is True


def test_config_get_masks_secrets(capsys, monkeypatch):
    from code_puppy import config as cp_config

    monkeypatch.setattr(cp_config, "get_value", lambda k: "sk-real-secret-value")
    assert cli.main(["config", "get", "OPENAI_API_KEY", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "real-secret" not in json.dumps(data)
    assert data["value"] == "(set, hidden)"


def test_model_add_and_default(capsys, monkeypatch):
    from code_puppy_core_plugins.setup_cli import model_cmds

    from code_puppy.models_dev_parser import ModelInfo, ProviderInfo

    def FakeModel():
        return ModelInfo(
            provider_id="openai",
            model_id="acme-large",
            name="Acme Large",
            tool_call=True,
            temperature=True,
            context_length=200000,
        )

    def FakeProvider():
        return ProviderInfo(
            id="openai",
            name="OpenAI",
            env=["OPENAI_API_KEY"],
            api="https://api.openai.com/v1",
        )

    class FakeRegistry:
        data_source = "test"

        def get_providers(self):
            return {"openai": FakeProvider()}

        def get_models(self, provider_id=None):
            return [FakeModel()]

    monkeypatch.setattr(model_cmds, "_get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(model_cmds, "_catalog", lambda: {})
    assert cli.main(["model", "add", "openai/acme-large", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["key"] == "openai-acme-large"
    assert data["added"] is True

    monkeypatch.setattr(model_cmds, "_catalog", lambda: {"openai-acme-large": {}})
    assert cli.main(["model", "default", "openai-acme-large", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["default"] == "openai-acme-large"

    assert cli.main(["model", "add", "no-separator"]) == 1


def test_mcp_add_list_remove(capsys):
    assert (
        cli.main(
            ["mcp", "add", "echoer", "--command", "uvx", "--arg", "mcp-echo", "--json"]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["mcp", "list", "--json"]) == 0
    servers = json.loads(capsys.readouterr().out)["servers"]
    assert any(s["name"] == "echoer" and s["transport"] == "stdio" for s in servers)
    assert cli.main(["mcp", "bind", "code-puppy", "echoer"]) == 0
    assert cli.main(["mcp", "unbind", "code-puppy", "echoer"]) == 0
    assert cli.main(["mcp", "remove", "echoer"]) == 0


def test_export_excludes_credentials(tmp_path, capsys):
    from code_puppy import config as cp_config

    cfg = tmp_path / "puppy.cfg"
    cfg.write_text(
        "[puppy]\npuppy_name = Testy\nOPENAI_API_KEY = sk-should-not-leak\n",
        encoding="utf-8",
    )
    from code_puppy_core_plugins.setup_cli import export_cmds

    dropped = export_cmds._sanitized_puppy_cfg(cfg, tmp_path / "out.cfg")
    text = (tmp_path / "out.cfg").read_text(encoding="utf-8")
    assert "sk-should-not-leak" not in text
    assert "Testy" in text
    assert dropped  # at least the key was reported
    _ = cp_config  # keep import for fixture parity with conftest isolation


def test_auth_list_json(capsys):
    assert cli.main(["auth", "list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    ids = {entry["id"] for entry in data["oauth"]}
    assert {"meta", "claude-code", "codex", "copilot", "grok"} <= ids


def test_unknown_provider_errors(capsys):
    code = cli.main(["auth", "status", "nope-not-real", "--json"])
    assert code == 2
