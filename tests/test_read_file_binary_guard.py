"""Focused binary-safety test without importing every built-in agent tool."""

import asyncio
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _filesystem_tools_module():
    path = ROOT / "src" / "agent_tools" / "filesystem_tools.py"
    spec = importlib.util.spec_from_file_location("_isolated_filesystem_tools", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_file_rejects_binary_without_putting_bytes_in_prompt(
    tmp_path, monkeypatch
):
    path = tmp_path / "attached.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\xff" * 256)
    monkeypatch.setattr("src.tool_execution._resolve_tool_path", lambda raw: raw)

    module = _filesystem_tools_module()
    result = asyncio.run(module.ReadFileTool().execute(str(path), {}))

    assert result["exit_code"] == 1
    assert "binary file" in result["error"].lower()
    assert "use the image attachment" in result["error"].lower()
    assert "output" not in result
