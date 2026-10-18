"""``setup auth`` — one front door for every provider login Code Puppy has.

OAuth logins are *delegated* to the owning plugin's existing interactive
command in a full Code Puppy subprocess (``code-puppy -p "/x-auth"``):
those flows print through the messaging system and may open browsers or
localhost callbacks, none of which exist yet in the ``handle_cli_args``
context this CLI runs in. Status/logout reads go through the plugins'
own loaders with structured results. API keys are collected directly
(hidden prompt, stdin, or environment) into the shared credential
store and never echoed.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from typing import Optional

from . import providers
from .output import Out, SetupError


def _delegate_slash_command(command: str) -> int:
    exe = shutil.which("code-puppy") or sys.argv[0]
    print(f'launching: code-puppy -p "/{command}" (interactive provider flow)')
    return subprocess.call([exe, "-p", f"/{command}"])


def cmd_auth_list(out: Out) -> int:
    status = providers.all_provider_status()
    human = []
    for entry in status.oauth:
        mark = "authenticated" if entry["authenticated"] else "not authenticated"
        human.append(f"{entry['id']}: {mark} [{entry['sandbox_transport']}]")
    for entry in status.api_keys:
        if entry["authenticated"]:
            human.append(f"{entry['env_var']}: set")
    out.result(
        {"oauth": status.oauth, "api_keys": status.api_keys},
        human=human or ["(nothing authenticated)"],
    )
    return 0


def cmd_auth_status(out: Out, provider_id: str) -> int:
    provider = providers.get_oauth_provider(provider_id)
    if provider:
        entry = providers.oauth_status(provider)
        out.result(
            entry,
            human=f"{provider.id}: "
            + ("authenticated" if entry["authenticated"] else "not authenticated")
            + f" (source: {entry['source']})",
        )
        return 0 if entry["authenticated"] else 1
    env_var = provider_id.strip().upper()
    if providers.is_known_api_key_env(env_var):
        present = providers.credential_present(env_var)
        out.result(
            {"env_var": env_var, "authenticated": present, "kind": "api-key"},
            human=f"{env_var}: {'set' if present else 'not set'}",
        )
        return 0 if present else 1
    raise SetupError(
        f"unknown provider '{provider_id}' (see `setup auth list`)", exit_code=2
    )


def _collect_api_key(env_var: str, from_env: Optional[str], stdin: bool) -> str:
    if stdin:
        value = sys.stdin.read().strip()
    elif from_env:
        value = os.environ.get(from_env, "").strip()
    else:
        value = getpass.getpass(f"Enter {env_var}: ").strip()
    if not value:
        raise SetupError(f"no value provided for {env_var}")
    return value


def cmd_auth_login(
    out: Out, provider_id: str, from_env: Optional[str], stdin: bool
) -> int:
    provider = providers.get_oauth_provider(provider_id)
    if provider and provider.login_kind == "slash" and provider.auth_command:
        return _delegate_slash_command(provider.auth_command)
    env_var = (
        provider.env_var
        if provider and provider.env_var
        else provider_id.strip().upper()
    )
    if not env_var:
        raise SetupError(f"provider '{provider_id}' has no API-key login", exit_code=2)
    value = _collect_api_key(env_var, from_env, stdin)
    from code_puppy.provider_credentials import save_credential

    save_credential(env_var, value)
    out.result(
        {"env_var": env_var, "saved": True}, human=f"saved credential for {env_var}"
    )
    return 0


def cmd_auth_logout(out: Out, provider_id: str) -> int:
    provider = providers.get_oauth_provider(provider_id)
    if provider and provider.logout_command:
        return _delegate_slash_command(provider.logout_command)
    env_var = (
        provider.env_var
        if provider and provider.env_var
        else provider_id.strip().upper()
    )
    from pathlib import Path

    from code_puppy import config, secret_store, shared_credentials
    from code_puppy.atomic_io import path_lock
    from code_puppy.config_file import mutate_config

    # Core has no delete-credential API (save() rejects empty values), so
    # mirror shared_credentials.save() in reverse under the same lock:
    # drop the shared-store secret and any legacy puppy.cfg value.
    def _remove_legacy(parser):
        return (
            parser.remove_option(config.DEFAULT_SECTION, env_var)
            if parser.has_section(config.DEFAULT_SECTION)
            else False
        )

    try:
        with path_lock(str(Path(config.CONFIG_DIR) / "provider_credentials")):
            secret_store.delete_secret(shared_credentials._name(env_var))
            mutate_config(str(config.CONFIG_FILE), _remove_legacy)
    except Exception as e:
        raise SetupError(f"failed to clear stored credential for {env_var}: {e}") from e
    os.environ.pop(env_var, None)
    out.result(
        {"env_var": env_var, "cleared": True},
        human=f"cleared stored credential for {env_var} "
        "(a shell environment variable of the same name still wins if set)",
    )
    return 0
