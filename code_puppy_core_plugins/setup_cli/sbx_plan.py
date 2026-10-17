"""``setup sbx plan`` — derive sandbox needs from the configured catalog.

The Docker sandbox kits must ask only for credentials the user
actually uses. Hand-enumerating providers in kit YAML both nags about
unused keys and misses long-tail setups (ZAI Coding Plan, any
``custom_openai`` endpoint added via models.dev). This module walks
the merged model catalog and derives, per configured model:

- the credential env var and the domain/header the proxy must inject,
- the egress domains the sandbox needs,
- or the OAuth transport when no static key exists.

Values are never read or emitted — only presence on the host.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from . import providers
from .output import Out

# Static-key model types: type -> (service, env var, domain, header, format)
_STATIC_TYPES: dict[str, tuple[str, str, str, str, str]] = {
    "meta_oauth": ("meta", "META_API_KEY", "api.meta.ai", "Authorization", "Bearer %s"),
    "anthropic": (
        "anthropic",
        "ANTHROPIC_API_KEY",
        "api.anthropic.com",
        "x-api-key",
        "%s",
    ),
    "openai": (
        "openai",
        "OPENAI_API_KEY",
        "api.openai.com",
        "Authorization",
        "Bearer %s",
    ),
    "gemini": (
        "gemini",
        "GEMINI_API_KEY",
        "generativelanguage.googleapis.com",
        "x-goog-api-key",
        "%s",
    ),
    "openrouter": (
        "openrouter",
        "OPENROUTER_API_KEY",
        "openrouter.ai",
        "Authorization",
        "Bearer %s",
    ),
    "zai_coding": ("zai", "ZAI_API_KEY", "api.z.ai", "Authorization", "Bearer %s"),
    "zai_api": ("zai", "ZAI_API_KEY", "api.z.ai", "Authorization", "Bearer %s"),
    "cerebras": (
        "cerebras",
        "CEREBRAS_API_KEY",
        "api.cerebras.ai",
        "Authorization",
        "Bearer %s",
    ),
}

_CUSTOM_TYPES = {
    "custom_openai": ("Authorization", "Bearer %s"),
    "custom_openai_responses": ("Authorization", "Bearer %s"),
    "custom_anthropic": ("x-api-key", "%s"),
    "custom_gemini": ("x-goog-api-key", "%s"),
}

# OAuth model types: type -> (providers.py id, egress domains)
_OAUTH_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "claude_code": (
        "claude-code",
        ("api.anthropic.com", "claude.ai", "console.anthropic.com"),
    ),
    "chatgpt_oauth": ("codex", ("chatgpt.com", "auth.openai.com")),
    "copilot": ("copilot", ("api.githubcopilot.com", "api.github.com", "github.com")),
    "grok_oauth": ("grok", ("api.x.ai", "auth.x.ai", "accounts.x.ai")),
}

_UNSUPPORTED_TYPES = {
    "azure_openai": "per-deployment host; add its domain and AZURE_OPENAI_API_KEY handling manually",
    "azure_foundry_openai": "per-deployment host; configure manually",
    "round_robin": "meta-type over other models; covered via its members if configured",
}


def _service_from_env(env_var: str) -> str:
    name = env_var.lower()
    for suffix in ("_api_key", "_key", "_token"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return re.sub(r"[^a-z0-9-]", "-", name) or "custom"


def _custom_endpoint_credential(
    model_key: str, cfg: dict[str, Any], header: str, fmt: str
) -> Optional[dict[str, Any]]:
    endpoint = cfg.get("custom_endpoint")
    if not isinstance(endpoint, dict):
        return None
    host = urlparse(str(endpoint.get("url") or "")).hostname
    raw_key = str(endpoint.get("api_key") or "")
    env_var = raw_key[1:] if raw_key.startswith("$") else ""
    if not host or not env_var:
        return None
    return {
        "service": _service_from_env(env_var),
        "env_var": env_var,
        "domain": host,
        "header": header,
        "format": fmt,
        "models": [model_key],
    }


def build_plan(catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Derive the sandbox credential/egress plan from a model catalog."""
    if catalog is None:
        from code_puppy.model_factory import ModelFactory

        catalog = ModelFactory.load_config()

    credentials: dict[tuple[str, str, str], dict[str, Any]] = {}
    oauth: dict[str, dict[str, Any]] = {}
    unsupported: list[dict[str, Any]] = []
    egress: set[str] = set()

    def add_credential(entry: dict[str, Any]) -> None:
        key = (entry["service"], entry["env_var"], entry["domain"])
        if key in credentials:
            credentials[key]["models"].extend(entry["models"])
        else:
            credentials[key] = entry
        egress.add(entry["domain"])

    for model_key in sorted(catalog):
        cfg = catalog[model_key]
        if not isinstance(cfg, dict):
            continue
        model_type = str(cfg.get("type") or "")

        if model_type in _OAUTH_TYPES:
            provider_id, domains = _OAUTH_TYPES[model_type]
            provider = providers.get_oauth_provider(provider_id)
            oauth.setdefault(
                provider_id,
                {
                    "id": provider_id,
                    "transport": provider.sandbox_transport
                    if provider
                    else "token-file-sync",
                    "domains": list(domains),
                    "models": [],
                },
            )["models"].append(model_key)
            egress.update(domains)
            continue

        if model_type in _CUSTOM_TYPES or (
            model_type == "cerebras" and isinstance(cfg.get("custom_endpoint"), dict)
        ):
            header, fmt = _CUSTOM_TYPES.get(model_type, ("Authorization", "Bearer %s"))
            entry = _custom_endpoint_credential(model_key, cfg, header, fmt)
            if entry:
                add_credential(entry)
            else:
                unsupported.append(
                    {
                        "model": model_key,
                        "type": model_type,
                        "reason": "custom endpoint without a $ENV key reference or host",
                    }
                )
            continue

        if model_type in _STATIC_TYPES:
            service, env_var, domain, header, fmt = _STATIC_TYPES[model_type]
            # A custom_endpoint on a static type (e.g. a proxy) wins.
            custom = _custom_endpoint_credential(model_key, cfg, header, fmt)
            add_credential(
                custom
                or {
                    "service": service,
                    "env_var": env_var,
                    "domain": domain,
                    "header": header,
                    "format": fmt,
                    "models": [model_key],
                }
            )
            continue

        unsupported.append(
            {
                "model": model_key,
                "type": model_type or "(missing)",
                "reason": _UNSUPPORTED_TYPES.get(
                    model_type, "no sandbox credential derivation for this type"
                ),
            }
        )

    cred_list = []
    for entry in credentials.values():
        entry = {**entry, "models": sorted(set(entry["models"]))}
        present = providers.credential_present(entry["env_var"])
        if not present and entry["env_var"] == "META_API_KEY":
            meta = providers.get_oauth_provider("meta")
            present = bool(meta and providers.oauth_status(meta)["authenticated"])
        entry["host_credential_present"] = present
        entry["transport"] = "proxy-managed-env"
        cred_list.append(entry)
    cred_list.sort(key=lambda e: (e["service"], e["domain"]))

    from code_puppy.config import get_value

    return {
        "credentials": cred_list,
        "oauth": sorted(oauth.values(), key=lambda e: e["id"]),
        "egress": sorted(egress),
        "unsupported": unsupported,
        "default_model": (get_value("model") or "").strip() or None,
    }


def render_human(plan: dict[str, Any]) -> list[str]:
    lines = []
    for cred in plan["credentials"]:
        state = "host key present" if cred["host_credential_present"] else "no host key"
        lines.append(
            f"credential {cred['service']}: {cred['env_var']} -> {cred['domain']} "
            f"({state}) for {len(cred['models'])} model(s)"
        )
    for entry in plan["oauth"]:
        lines.append(
            f"oauth {entry['id']}: [{entry['transport']}] egress "
            f"{', '.join(entry['domains'])} for {len(entry['models'])} model(s)"
        )
    for entry in plan["unsupported"]:
        lines.append(
            f"unsupported {entry['model']} [{entry['type']}]: {entry['reason']}"
        )
    if not plan["credentials"] and not plan["oauth"]:
        lines.append("(no sandbox-usable models configured)")
    return lines


def cmd_sbx_plan(out: Out) -> int:
    plan = build_plan()
    out.result(plan, human=render_human(plan))
    return 0
