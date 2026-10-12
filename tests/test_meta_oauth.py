"""Tests for the Meta Muse OAuth key mint and plan reporting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from code_puppy_core_plugins.meta_oauth import plan, utils


def _response(payload: dict[str, object], status: int = 200) -> MagicMock:
    response = MagicMock()
    response.ok = 200 <= status < 300
    response.status_code = status
    response.json.return_value = payload
    return response


def _fake_t(key: str, **kwargs: object) -> str:
    args = ",".join(f"{name}={value}" for name, value in sorted(kwargs.items()))
    return f"{key}[{args}]" if args else key


class TestMintApiCredentials:
    def test_sends_json_body(self) -> None:
        """Regression: Meta rejects a body-less mint POST ("Request body is required")."""
        minted = {"api_key": "sk-muse", "base_url": "https://api.meta.ai/v1"}
        with patch.object(
            utils.requests, "post", return_value=_response(minted)
        ) as post:
            credentials = utils.mint_api_credentials("access-token")

        assert post.call_args.kwargs["json"] == {}
        assert (
            post.call_args.kwargs["headers"]["Authorization"] == "Bearer access-token"
        )
        assert credentials["api_key"] == "sk-muse"
        assert credentials["api_base_url"] == "https://api.meta.ai/v1"
        assert "base_url" not in credentials

    def test_payment_required_surfaces_detail_and_action_url(self) -> None:
        refusal = {
            "require_payment": True,
            "detail": "Muse Code requires a subscription",
            "action_url": "https://accountscenter.meta.com/muse_code/",
        }
        with patch.object(utils.requests, "post", return_value=_response(refusal)):
            with pytest.raises(RuntimeError) as excinfo:
                utils.mint_api_credentials("access-token")

        message = str(excinfo.value)
        assert "Muse Code requires a subscription" in message
        assert "https://accountscenter.meta.com/muse_code/" in message

    def test_http_error_uses_problem_detail(self) -> None:
        problem = {"title": "Bad Request", "detail": "nope"}
        with patch.object(utils.requests, "post", return_value=_response(problem, 400)):
            with pytest.raises(RuntimeError, match="nope"):
                utils.mint_api_credentials("access-token")


class TestDescribePlan:
    def test_full_plan(self) -> None:
        credentials = {
            "subs_tier_name": "Pro",
            "is_subs_active": True,
            "is_subs_upgrade_available": True,
            "subs_usage": {
                "window": {
                    "used_percent": 12.5,
                    "window_duration_mins": 300,
                    "resets_at": "2026-10-12T20:00:00Z",
                },
                "weekly": {"used_percent": 40},
            },
        }
        with patch.object(plan, "t", _fake_t):
            lines = plan.describe_plan(credentials)

        assert lines == [
            "plugin.meta-oauth.plan.tier[tier=Pro]",
            "plugin.meta-oauth.plan.active",
            "plugin.meta-oauth.plan.usage_reset[reset=2026-10-12T20:00:00Z,used=12.5,"
            "window=plugin.meta-oauth.plan.window_hours[hours=5]]",
            "plugin.meta-oauth.plan.usage[used=40,window=plugin.meta-oauth.plan.weekly]",
            "plugin.meta-oauth.plan.upgrade",
        ]

    def test_muse_cli_subs_active_key(self) -> None:
        with patch.object(plan, "t", _fake_t):
            assert plan.describe_plan({"subs_active": False}) == [
                "plugin.meta-oauth.plan.inactive"
            ]

    def test_no_plan_data(self) -> None:
        with patch.object(plan, "t", _fake_t):
            assert plan.describe_plan({"api_key": "sk"}) == []

    def test_ignores_malformed_usage(self) -> None:
        credentials = {
            "subs_usage": {"window": "junk", "weekly": {"used_percent": "x"}}
        }
        with patch.object(plan, "t", _fake_t):
            assert plan.describe_plan(credentials) == []
