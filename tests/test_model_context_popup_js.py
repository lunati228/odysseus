"""Focused contracts for the model context tooltip."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_context_formatter_uses_binary_k_units():
    match = re.search(r"function _fmtCtx\(n\) \{[\s\S]*?\n\}", SOURCE)
    assert match, "_fmtCtx helper is missing"
    script = (
        match.group(0)
        + "\nconsole.log(JSON.stringify(["
        + "_fmtCtx(184320), _fmtCtx(131072), _fmtCtx(1048576)]));"
    )
    proc = subprocess.run(
        ["node", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ["180K", "128K", "1M"]


def test_context_tooltip_refreshes_live_value_even_when_cached():
    block = SOURCE[SOURCE.index("// Fetch real context from server async"):]
    block = block[:block.index("// Show configured max tokens")]

    assert "if (!_realCtx && window.sessionModule)" not in block
    assert "if (window.sessionModule)" in block
    assert 'id="_ctx-val"' in SOURCE
