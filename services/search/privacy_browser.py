"""Fail-closed Brave/Windscribe fallback for Privacy Workspace research.

The primary Privacy Workspace transport is always Tor.  This module is only
called after that path fails.  It delegates one atomic navigation to the
already-managed Playwright MCP browser, whose launch configuration is checked
again here before every fallback call.  There is deliberately no HTTP client
or direct-network fallback in this module.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urlsplit

from src.builtin_mcp import (
    _validated_browser_mcp_config,
    _validated_browser_proxy,
)
from src.privacy_mode import is_privacy_mode
from src.privacy_policy import (
    MAX_EVIDENCE_CHARS,
    bound_generated_query,
    frame_untrusted_evidence,
    require_capability,
)
from src.tool_utils import get_mcp_manager

from .privacy_search import _resolve_ddg_href
from .privacy_transport import PrivacyTransportError, validate_public_https_url


_BROWSER_TOOL = "mcp__builtin_browser__browser_navigate"
_DDG_SEARCH = "https://html.duckduckgo.com/html/?q={query}"
_DDG_HOSTS = frozenset({"duckduckgo.com", "html.duckduckgo.com"})
_MAX_BROWSER_OUTPUT_CHARS = 100_000
_LINK_RE = re.compile(r'^\s*-\s+link\s+"(?P<title>.*?)"(?:\s|\[|:|$)')
_URL_RE = re.compile(r"^\s*-?\s*/url:\s*(?P<url>\S+)\s*$")
_PAGE_URL_RE = re.compile(r"^\s*-\s+Page URL:\s*(?P<url>\S+)\s*$", re.MULTILINE)
_PAGE_TITLE_RE = re.compile(r"^\s*-\s+Page Title:\s*(?P<title>.*)\s*$", re.MULTILINE)
_SNAPSHOT_MARKER_RE = re.compile(r"^\s*-\s+Page Snapshot:\s*$", re.MULTILINE)
_BROWSER_CALL_LOCK = asyncio.Lock()


class BrowserFallbackUnavailable(RuntimeError):
    """The managed VPN browser is unavailable or failed closed."""


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _assert_managed_browser_environment() -> None:
    """Re-check the exact managed-browser boundary before dispatch."""
    if not is_privacy_mode():
        raise BrowserFallbackUnavailable("managed privacy browser is not active")
    require_capability("vpn-browser")
    if os.environ.get("ODYSSEUS_BROWSER_ROLE", "").strip() != "windscribe-fallback":
        raise BrowserFallbackUnavailable("managed privacy browser role is not active")
    if not _enabled("ODYSSEUS_BROWSER_REQUIRE_PROXY"):
        raise BrowserFallbackUnavailable("managed browser proxy is not mandatory")
    if not _enabled("ODYSSEUS_BROWSER_ISOLATED"):
        raise BrowserFallbackUnavailable("managed browser isolation is not active")

    try:
        proxy, username, password = _validated_browser_proxy()
        if not proxy:
            raise ValueError("missing proxy")
        _validated_browser_mcp_config(
            os.environ.get("ODYSSEUS_BROWSER_MCP_CONFIG", "").strip(),
            proxy,
            username,
            password,
        )
    except (OSError, ValueError) as exc:
        raise BrowserFallbackUnavailable(
            "managed browser configuration failed validation"
        ) from exc


def _page_url(output: str) -> str:
    match = _PAGE_URL_RE.search(output or "")
    if not match:
        raise BrowserFallbackUnavailable("browser did not report its final URL")
    try:
        return validate_public_https_url(match.group("url"), label="browser final URL")
    except PrivacyTransportError as exc:
        raise BrowserFallbackUnavailable("browser final URL failed validation") from exc


def _page_title(output: str) -> str:
    match = _PAGE_TITLE_RE.search(output or "")
    return match.group("title").strip()[:300] if match else ""


def _snapshot_text(output: str) -> str:
    match = _SNAPSHOT_MARKER_RE.search(output or "")
    if not match:
        raise BrowserFallbackUnavailable("browser returned no page snapshot")
    return output[match.end():].strip()


def _parse_search_snapshot(output: str, count: int) -> List[Dict]:
    """Parse Playwright's accessibility snapshot into validated result rows."""
    rows: List[Dict] = []
    seen: set[str] = set()
    pending_title: Optional[str] = None

    for line in _snapshot_text(output).splitlines():
        link = _LINK_RE.match(line)
        if link:
            pending_title = link.group("title").replace(r'\"', '"').strip()[:300]
            continue

        target = _URL_RE.match(line)
        if target is None or pending_title is None:
            continue
        raw_url = _resolve_ddg_href(target.group("url"))
        title = pending_title
        pending_title = None
        if not raw_url:
            continue
        try:
            url = validate_public_https_url(raw_url, label="browser search result")
        except PrivacyTransportError:
            continue
        if (urlsplit(url).hostname or "").lower() in _DDG_HOSTS or url in seen:
            continue
        seen.add(url)
        rows.append({"title": title or url, "url": url, "snippet": ""})
        if len(rows) >= count:
            break
    return rows


class PrivacyBrowserFallback:
    """One-call search/fetch adapter around the managed browser MCP."""

    async def _navigate(self, url: str) -> str:
        _assert_managed_browser_environment()
        manager = get_mcp_manager()
        if manager is None:
            raise BrowserFallbackUnavailable("managed browser is not connected")

        try:
            async with _BROWSER_CALL_LOCK:
                result = await manager.call_tool(_BROWSER_TOOL, {"url": url})
        except Exception as exc:
            raise BrowserFallbackUnavailable("managed browser call failed") from exc

        if not isinstance(result, dict) or result.get("exit_code", 1) != 0:
            raise BrowserFallbackUnavailable("managed browser call failed")
        output = result.get("stdout", "")
        if not isinstance(output, str) or not output.strip():
            raise BrowserFallbackUnavailable("managed browser returned no content")
        return output[:_MAX_BROWSER_OUTPUT_CHARS]

    async def search(self, query: str, count: int = 10) -> List[Dict]:
        safe_query = bound_generated_query(query)
        try:
            limit = min(10, max(1, int(count)))
        except (TypeError, ValueError):
            limit = 10
        search_url = _DDG_SEARCH.format(query=quote_plus(safe_query))
        output = await self._navigate(search_url)
        final_url = _page_url(output)
        if (urlsplit(final_url).hostname or "").lower() not in _DDG_HOSTS:
            raise BrowserFallbackUnavailable("browser search left the fixed provider")
        return _parse_search_snapshot(output, limit)

    async def fetch(self, url: str, *, max_content_chars: int = MAX_EVIDENCE_CHARS) -> Dict:
        try:
            target = validate_public_https_url(url)
        except PrivacyTransportError as exc:
            raise BrowserFallbackUnavailable("browser target failed validation") from exc

        output = await self._navigate(target)
        final_url = _page_url(output)
        snapshot = _snapshot_text(output)
        try:
            limit = min(MAX_EVIDENCE_CHARS, max(1, int(max_content_chars)))
        except (TypeError, ValueError):
            limit = MAX_EVIDENCE_CHARS
        snapshot = snapshot[:limit]
        if not snapshot:
            raise BrowserFallbackUnavailable("browser returned an empty page snapshot")

        return {
            "url": final_url,
            "title": _page_title(output),
            "content": frame_untrusted_evidence(final_url, snapshot, max_chars=limit),
            "lists": [],
            "tables": [],
            "code_blocks": [],
            "meta_description": "",
            "meta_keywords": "",
            "og_image": "",
            "js_rendered": True,
            "js_message": "Rendered by isolated Brave through the managed VPN fallback.",
            "success": True,
            "error": "",
            "truncated": len(_snapshot_text(output)) > limit,
            "fetched_bytes": 0,
        }
