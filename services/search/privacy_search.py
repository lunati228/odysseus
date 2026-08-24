"""Tor-routed search and page retrieval for the Privacy Workspace profile.

Design note -- why this module is small
---------------------------------------
The privacy profile does not need its own research pipeline.  It needs the
existing one to receive Tor-routed data.  So this module implements only the
two leaf operations that actually touch the network:

* :func:`privacy_call_provider`      -- replaces ``core._call_provider``
* :func:`privacy_fetch_webpage_content` -- replaces ``content.fetch_webpage_content``

Everything above them -- query planning, domain filters, ranking, dedup,
threading, context assembly, citations -- is upstream code that runs unchanged.
That keeps the fork's diff against upstream to two short guards, which is the
whole point: this fork is meant to be rebased onto new upstream releases.

Storage policy (PRV-005)
------------------------
Nothing here writes a disk cache entry, a search-analytics row, or a log line
containing the query, the URL, or fetched text.  The standard path does all
three; the privacy path deliberately trades that performance for the absence
of plaintext residue in the vault.

Untrusted content (PRV-006)
---------------------------
``content`` is returned already wrapped by
``privacy_policy.frame_untrusted_evidence``.  Framing at the fetch boundary
rather than at the prompt boundary is deliberate: it is the fail-safe order.
If a future caller forgets to frame, the text is already framed; the opposite
mistake would silently hand raw hostile page text to the model.
"""
from __future__ import annotations

import logging
from typing import List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

from bs4 import BeautifulSoup

from src.privacy_policy import (
    CapabilityDenied,
    bound_generated_query,
    frame_untrusted_evidence,
    require_capability,
)
from .privacy_transport import (
    PrivacyTorClient,
    PrivacyTransportError,
    validate_public_https_url,
)

logger = logging.getLogger(__name__)

# DuckDuckGo's no-JavaScript endpoints. Chosen because they need no API key:
# a key would bind an account identity to a query that was routed over Tor
# specifically to avoid that linkage.
#
# The onion service is tried first, and not only for reliability. An onion
# circuit never leaves the Tor network, so there is no exit relay to see the
# destination and none that can refuse the request. Observed live, the
# clearnet host answered HTTP 403 with "There appears to be an issue with the
# Tor Exit Node you are currently using" -- the exact PRV-010 failure mode.
#
# The clearnet host remains as a fallback. Falling back between these two is
# permitted because both are Tor-routed; what the threat model forbids is
# falling back to a *direct* client, which never happens on this path.
_DDG_ENDPOINTS = (
    "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/?q={query}",
    "https://html.duckduckgo.com/html/?q={query}",
)

_TIME_FILTER_SUFFIX = {
    "day": "&df=d",
    "week": "&df=w",
    "month": "&df=m",
    "year": "&df=y",
}


def _client() -> PrivacyTorClient:
    """Build the Tor client, or fail closed."""
    require_capability("tor-fetch")
    return PrivacyTorClient()


def privacy_call_provider(
    provider_name: str,
    query: str,
    count: int,
    time_filter: Optional[str] = None,
) -> List[dict]:
    """Tor-routed replacement for ``core._call_provider``.

    ``provider_name`` is accepted for signature compatibility and then
    ignored: in the privacy profile the provider is not the caller's choice,
    because most of the configured chain is key-bearing or direct.  Returning
    an empty list on failure matches the upstream contract, so the caller's
    existing fallback logic still works -- it just has nothing unsafe to fall
    back to.
    """
    require_capability("tor-search")
    try:
        safe_query = bound_generated_query(query)
    except CapabilityDenied:
        # Includes an over-long query, which is the channel a hostile page
        # would use to smuggle local context out through a search provider.
        logger.warning("privacy search refused a query that failed policy bounds")
        return []

    encoded = quote_plus(safe_query)
    suffix = _TIME_FILTER_SUFFIX.get(time_filter or "", "")

    for index, template in enumerate(_DDG_ENDPOINTS):
        url = template.format(query=encoded) + suffix
        try:
            with _client() as client:
                response = client.get(url)
        except PrivacyTransportError as exc:
            # Fail closed for this endpoint and try the next Tor-routed one.
            # Never retry through a direct provider: that is exactly the
            # fallback the threat model forbids.
            logger.warning(
                "privacy search endpoint %d failed closed: %s",
                index,
                type(exc).__name__,
            )
            continue

        if response.status_code != 200:
            # A blocked exit relay answers 403 here. Try the next endpoint.
            logger.warning(
                "privacy search endpoint %d returned HTTP %d",
                index,
                response.status_code,
            )
            continue

        rows = _parse_ddg_html(response.text, count)
        if rows:
            return rows
        logger.warning("privacy search endpoint %d returned no parseable rows", index)

    logger.warning("privacy search exhausted every Tor-routed endpoint")
    return []


def _resolve_ddg_href(href: str) -> str:
    """Unwrap a DuckDuckGo result link into the real target URL.

    Both endpoints wrap results in a redirector, but spell it differently:
    the clearnet host emits ``//duckduckgo.com/l/?uddg=<encoded>`` while the
    onion service emits a site-relative ``/l/?uddg=<encoded>``. Rather than
    resolve either against a base, the encoded target is read straight out of
    the query string, which works for both and never constructs a URL that
    points back at the redirector.
    """
    if not href:
        return ""
    query = urlsplit(href).query
    target = parse_qs(query).get("uddg", [""])[0]
    if target:
        return unquote(target)
    # Some rows link straight out, with no redirector.
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def _parse_ddg_html(html: str, count: int) -> List[dict]:
    """Extract result rows, dropping anything that is not a public https URL."""
    soup = BeautifulSoup(html or "", "html.parser")
    results: List[dict] = []

    for node in soup.select("div.result, div.web-result"):
        anchor = node.select_one("a.result__a")
        if anchor is None:
            continue
        resolved = _resolve_ddg_href(anchor.get("href") or "")
        if not resolved:
            continue

        # The result list is attacker-influenced, so every URL is validated
        # here rather than trusted until fetch time.
        try:
            resolved = validate_public_https_url(resolved, label="search result")
        except PrivacyTransportError:
            continue

        snippet_node = node.select_one("a.result__snippet, div.result__snippet")
        results.append(
            {
                "title": anchor.get_text(strip=True),
                "url": resolved,
                "snippet": snippet_node.get_text(strip=True) if snippet_node else "",
            }
        )
        if len(results) >= count:
            break

    return results


def privacy_fetch_webpage_content(
    url: str,
    timeout: int = 5,
    retry_attempt: int = 0,
    max_bytes: Optional[int] = None,
) -> dict:
    """Tor-routed replacement for ``content.fetch_webpage_content``.

    Returns the same dict shape as the standard fetcher so every existing
    caller and the UI keep working, with two differences: nothing is cached to
    disk, and ``content`` arrives pre-framed as untrusted evidence.
    """
    from .content import (
        _empty_result,
        _extract_code_blocks,
        _extract_lists,
        _extract_tables,
    )

    del retry_attempt  # no retry loop: a Tor failure must not be hammered

    try:
        target = validate_public_https_url(url)
    except PrivacyTransportError as exc:
        return _empty_result(url, f"BlockedByPrivacyPolicy: {exc}")

    try:
        with PrivacyTorClient(max_bytes=max_bytes) if max_bytes else _client() as client:
            response = client.get(target)
    except PrivacyTransportError as exc:
        # The message names the failure class, never the query or page text.
        return _empty_result(url, f"{type(exc).__name__}: {exc}")

    if response.status_code >= 400:
        return _empty_result(url, f"HTTP {response.status_code}")

    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        return _empty_result(url, f"ParseError: {exc}")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title_node = soup.find("title")
    title_text = title_node.get_text(strip=True) if title_node else ""

    body = soup.find("main") or soup.find("article") or soup.body or soup
    raw_text = body.get_text("\n", strip=True) if body else ""

    meta_description = ""
    description_node = soup.find("meta", attrs={"name": "description"})
    if description_node and description_node.get("content"):
        meta_description = description_node["content"]

    return {
        "url": url,
        "title": title_text,
        # Framed here so no downstream caller can forget to.
        "content": frame_untrusted_evidence(target, raw_text),
        "lists": _extract_lists(soup),
        "tables": _extract_tables(soup),
        "code_blocks": _extract_code_blocks(soup),
        "meta_description": meta_description,
        "meta_keywords": "",
        "js_rendered": False,
        "js_message": "",
        "success": bool(raw_text),
        "error": "" if raw_text else "EmptyDocument",
        "truncated": response.truncated,
        "fetched_bytes": len(response.content),
    }
