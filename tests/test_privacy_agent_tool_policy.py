"""PRV-003/PRV-006: Privacy Workspace exposes an exact tool allowlist.

Tor/VPN browsing and workspace reads are silent. Workspace changes and command
execution are visible but require a sealed, exact user approval. Every other
current or future tool is hidden and rejected again at the dispatcher.
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
    PRIVACY_AGENT_APPROVAL_TOOLS,
    enforce_tool_call_budget,
    privacy_tool_call_limit,
    require_privacy_agent_tool,
)
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_policy import build_effective_tool_policy


@pytest.fixture
def privacy_profile(monkeypatch):
    monkeypatch.setattr(privacy_mode, "PROFILE", "privacy")
    assert privacy_mode.is_privacy_mode()


def test_privacy_agent_allowlist_is_exact_and_fail_closed(privacy_profile):
    assert PRIVACY_AGENT_ALLOWED_TOOLS == frozenset(
        {
            "apply_patch",
            "ask_user",
            "bash",
            "edit_file",
            "get_workspace",
            "glob",
            "grep",
            "ls",
            "manage_bg_jobs",
            "python",
            "read_file",
            "trigger_research",
            "update_plan",
            "web_fetch",
            "web_search",
            "write_file",
        }
    )
    assert PRIVACY_AGENT_APPROVAL_TOOLS == frozenset(
        {"apply_patch", "bash", "edit_file", "manage_bg_jobs", "python", "write_file"}
    )
    for tool in PRIVACY_AGENT_ALLOWED_TOOLS:
        require_privacy_agent_tool(tool)
    for tool in (
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
    for tool in ("send_email", "manage_mcp", "manage_memory"):
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


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(0, 0), (-1, 0), (12, 12), (50, 50), (None, 0), ("invalid", 0)],
)
def test_privacy_agent_call_budget_honors_user_setting(
    privacy_profile, configured, expected
):
    assert privacy_tool_call_limit(configured) == expected


def test_zero_tool_call_budget_is_unlimited(privacy_profile):
    enforce_tool_call_budget(used=10_000, limit=0)


def test_standard_agent_call_budget_is_unchanged(monkeypatch):
    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    assert privacy_tool_call_limit(0) == 0
    assert privacy_tool_call_limit(50) == 50


@pytest.mark.parametrize(
    "tool",
    ["send_email", "manage_memory", "mcp__hostile__run", "future_tool"],
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


@pytest.mark.parametrize("tool", sorted(PRIVACY_AGENT_APPROVAL_TOOLS))
def test_dispatcher_requires_exact_approval_for_privacy_changes(
    privacy_profile, monkeypatch, tmp_path, tool
):
    async def fail_dispatch(*_args, **_kwargs):
        pytest.fail("an unapproved privacy action reached a handler")

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", fail_dispatch)
    desc, result = asyncio.run(
        tool_execution.execute_tool_block(
            ToolBlock(tool, "PRIVATE_CANARY"),
            owner="alice",
            session_id="session-1",
            workspace=str(tmp_path),
            security_context=ToolRunSecurityContext(),
        )
    )

    assert desc == f"{tool}: APPROVAL REQUIRED"
    assert result["approval_required"] is True
    assert result["blocked"] is True
    assert "PRIVATE_CANARY" not in str(result)


def test_dispatcher_accepts_sealed_privacy_command_approval_without_web_taint(
    privacy_profile, monkeypatch, tmp_path
):
    content = "printf exact"
    store = ToolApprovalStore()
    pending = store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content=content,
        workspace=str(tmp_path),
        external_untrusted_context_seen=False,
        capabilities=capabilities_for_action("bash", content),
    )
    approval = store.consume(
        pending.approval_id,
        decision="approve_task",
        owner="alice",
        session_id="session-1",
    )
    assert approval is not None

    async def fake_dispatch(*_args, **_kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", fake_dispatch)
    desc, result = asyncio.run(
        tool_execution.execute_tool_block(
            ToolBlock("bash", content),
            owner="alice",
            session_id="session-1",
            workspace=str(tmp_path),
            security_context=ToolRunSecurityContext(),
            exact_approval=approval,
        )
    )

    assert desc == "bash"
    assert result["exit_code"] == 0


def test_privacy_workspace_read_is_silent_and_confined(
    privacy_profile, monkeypatch, tmp_path
):
    target = tmp_path / "note.txt"
    target.write_text("workspace-only", encoding="utf-8")
    monkeypatch.setattr(
        tool_execution,
        "owner_is_admin_or_single_user",
        lambda _owner: True,
    )

    _, result = asyncio.run(
        tool_execution.execute_tool_block(
            ToolBlock("read_file", "note.txt"),
            owner="alice",
            workspace=str(tmp_path),
            security_context=ToolRunSecurityContext(),
        )
    )

    assert result["exit_code"] == 0
    assert result["output"] == "workspace-only"


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
            ToolBlock("send_email", "PRIVATE_CANARY"),
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        )
    )

    assert desc == "send_email: BLOCKED"
    assert result["blocked"] is True
