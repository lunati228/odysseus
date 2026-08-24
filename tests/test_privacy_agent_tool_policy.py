"""PRV-003/PRV-006: Privacy Workspace exposes a tiny tool allowlist.

Search and fetch are the only model-directed external operations. ``ask_user``
and ``update_plan`` are control messages with no host, account, file, or child-
process authority. Every other current or future tool is hidden and rejected
again at the dispatcher before imports or handlers run.
"""
from __future__ import annotations

import asyncio
import builtins

import pytest

import src.privacy_mode as privacy_mode
import src.tool_execution as tool_execution
from src.agent_tools import ToolBlock
from src.privacy_policy import (
    MAX_TOOL_CALLS_PER_TURN,
    PRIVACY_AGENT_ALLOWED_TOOLS,
    privacy_tool_call_limit,
    require_privacy_agent_tool,
)
from src.tool_policy import build_effective_tool_policy


@pytest.fixture
def privacy_profile(monkeypatch):
    monkeypatch.setattr(privacy_mode, "PROFILE", "privacy")
    assert privacy_mode.is_privacy_mode()


def test_privacy_agent_allowlist_is_exact_and_fail_closed(privacy_profile):
    assert PRIVACY_AGENT_ALLOWED_TOOLS == frozenset(
        {"web_search", "web_fetch", "ask_user", "update_plan"}
    )
    for tool in PRIVACY_AGENT_ALLOWED_TOOLS:
        require_privacy_agent_tool(tool)
    for tool in (
        "bash",
        "python",
        "read_file",
        "write_file",
        "send_email",
        "manage_calendar",
        "manage_webhooks",
        "mcp__anything__new_tool",
        "future_upstream_tool",
    ):
        with pytest.raises(Exception) as excinfo:
            require_privacy_agent_tool(tool)
        assert tool not in str(excinfo.value)


def test_privacy_policy_hides_every_known_tool_except_the_allowlist(privacy_profile):
    policy = build_effective_tool_policy(last_user_message="research this")

    assert policy.mode == "privacy"
    assert policy.disable_mcp is False
    assert policy.block_all_tool_calls is False
    assert not any(policy.blocks(tool) for tool in PRIVACY_AGENT_ALLOWED_TOOLS)
    for tool in ("bash", "python", "read_file", "send_email", "manage_mcp"):
        assert policy.blocks(tool)
        assert tool in policy.hidden_tools


def test_privacy_allows_only_builtin_browser_mcp_tools(privacy_profile):
    from src.privacy_policy import is_privacy_allowed_agent_tool

    for tool in (
        "mcp__builtin_browser__navigate",
        "mcp__builtin_browser__screenshot",
        "mcp__builtin_browser__click",
    ):
        assert is_privacy_allowed_agent_tool(tool)
        require_privacy_agent_tool(tool)

    for tool in (
        "mcp__email__read_email",
        "mcp__anything__run",
        "mcp__filesystem__read_file",
    ):
        assert not is_privacy_allowed_agent_tool(tool)
        with pytest.raises(Exception):
            require_privacy_agent_tool(tool)


@pytest.mark.parametrize("configured", [0, -1, 12, 50, None, "invalid"])
def test_privacy_agent_call_budget_is_always_hard_capped(
    privacy_profile, configured
):
    assert privacy_tool_call_limit(configured) == MAX_TOOL_CALLS_PER_TURN


def test_standard_agent_call_budget_is_unchanged(monkeypatch):
    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    assert privacy_tool_call_limit(0) == 0
    assert privacy_tool_call_limit(50) == 50


@pytest.mark.parametrize(
    "tool",
    ["bash", "write_file", "send_email", "mcp__hostile__run", "future_tool"],
)
def test_dispatcher_rejects_disallowed_tool_before_any_handler(
    privacy_profile, monkeypatch, tool
):
    async def fail_dispatch(*_args, **_kwargs):
        pytest.fail("a disallowed privacy tool reached a handler")

    monkeypatch.setattr(tool_execution, "_direct_fallback", fail_dispatch)
    desc, result = asyncio.run(
        tool_execution.execute_tool_block(
            ToolBlock(tool, "PRIVATE_CANARY"),
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        )
    )

    assert desc == f"{tool}: BLOCKED"
    assert result["exit_code"] == 1
    assert result["blocked"] is True
    assert "PRIVATE_CANARY" not in str(result)
    assert tool not in result["error"]


def test_dispatcher_rejects_before_tool_implementation_imports(
    privacy_profile, monkeypatch
):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"src.tool_implementations", "src.agent_tools"}:
            pytest.fail(f"privacy denial happened after importing {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    desc, result = asyncio.run(
        tool_execution.execute_tool_block(
            ToolBlock("bash", "PRIVATE_CANARY"),
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        )
    )

    assert desc == "bash: BLOCKED"
    assert result["blocked"] is True
