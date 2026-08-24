"""Tor-only HTTP transport for the Privacy Workspace profile.

This module is deliberately standalone: standard library plus ``httpx``, and
``src.privacy_mode`` for the one shared rule about which SOCKS authority is
allowed.  It does **not** import ``services.search.content``, because that
module binds the standard data directory and the on-disk web cache at import
time (``src.constants``, ``.cache``); pulling either into the private process
would defeat the storage isolation the privacy profile exists to provide.

The IP-range and hostname checks below therefore duplicate logic that also
exists in the standard fetcher.  That duplication is intentional: the two
paths have opposite DNS requirements and must be auditable in isolation.

Why the standard fetcher cannot be reused
-----------------------------------------
``services.search.content`` resolves the target hostname locally and pins the
TCP connection to that IP.  That is a sound SSRF defense for direct network
access, and it stays exactly as it is.  It is also fundamentally incompatible
with Tor: resolving the target locally leaks the destination to the local
resolver and the ISP, which is the precise thing this transport prevents.

What replaces local DNS pinning here
------------------------------------
1. strict pre-flight validation of the URL and every redirect ``Location``;
2. rejection of private, reserved, metadata and alternate-numeric literals
   before any connection is opened;
3. remote DNS inside the SOCKS5 request, so the exit relay resolves the name;
4. Tor's own ``ClientDNSRejectInternalAddresses`` /
   ``ClientRejectInternalAddresses`` settings;
5. fail-closed behavior with no direct-client fallback on any error.

Note on ``socks5h``
-------------------
httpx sends the same SOCKS5 DOMAINNAME address type for ``socks5://`` and
``socks5h://``, so the spelling alone proves nothing.  It is required here for
explicitness; the actual guarantee is asserted by the fake-SOCKS address-type
test in ``tests/test_privacy_tor_transport.py``.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

from src.constants import WEB_FETCH_USER_AGENT
from src.privacy_mode import PrivacyConfigurationError, parse_tor_socks_endpoint


# ---------------------------------------------------------------------------
# errors — every failure mode is a subclass, so a caller cannot accidentally
# treat a Tor failure as a retryable condition and reach for a direct client
# ---------------------------------------------------------------------------


class PrivacyTransportError(RuntimeError):
    """Base class for every privacy-transport refusal."""


class TorUnavailable(PrivacyTransportError):
    """Tor is unconfigured, unreachable, or stopped being the transport."""


class UrlNotAllowed(PrivacyTransportError):
    """The URL or a redirect target is not an allowed public HTTPS target."""


class ResponseTooLarge(PrivacyTransportError):
    """The server declared a body over the hard ceiling."""


class ContentTypeNotAllowed(PrivacyTransportError):
    """The response media type is outside the research allowlist."""


class TooManyRedirects(PrivacyTransportError):
    """The redirect chain exceeded the configured limit."""


# ---------------------------------------------------------------------------
# limits
# ---------------------------------------------------------------------------

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_HARD_MAX_BYTES = 5_000_000
DEFAULT_MAX_REDIRECTS = 3

DEFAULT_TIMEOUT = httpx.Timeout(connect=20.0, read=30.0, write=20.0, pool=10.0)

DEFAULT_ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/json",
    "application/pdf",
)

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

# Deliberately the *same* common desktop UA as the standard fetcher.
#
# A profile-specific UA sounds more private and is in fact worse: it would make
# privacy-profile traffic uniquely identifiable as "Odysseus privacy mode",
# turning a shared string into a fingerprint. Tor Browser's design principle is
# the opposite -- make every client look identical. The two profiles never
# appear to a site from the same address anyway (standard is direct, privacy
# exits from Tor), so sharing a very common UA costs nothing and blends in.
#
# src.constants is safe to import here: it only computes path strings, writes
# nothing at import, and in the privacy process ODYSSEUS_DATA_DIR already
# points inside the vault. It is services.search.content that must stay out --
# that module pulls in the on-disk web cache (PRV-005).
DEFAULT_USER_AGENT = WEB_FETCH_USER_AGENT


# ---------------------------------------------------------------------------
# host and URL validation
# ---------------------------------------------------------------------------

_BLOCKED_HOST_NAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

_BLOCKED_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".intranet",
    ".home.arpa",
    ".in-addr.arpa",
    ".ip6.arpa",
    ".onion",  # arbitrary onion services stay refused; see PINNED_ONION_HOSTS
)

#: Onion services this transport may contact, pinned by exact address.
#:
#: Arbitrary ``.onion`` browsing stays refused above. A pinned address is
#: different in kind: a v3 onion address *is* the service's public key, so the
#: address self-authenticates and cannot be spoofed by a hostile relay or a
#: mis-issued certificate.
#:
#: Reaching DuckDuckGo this way is both more reliable and more private than
#: the clearnet host: an onion circuit never leaves the Tor network, so there
#: is no exit relay to block the request (observed live: a clearnet request
#: was refused with HTTP 403 "There appears to be an issue with the Tor Exit
#: Node you are currently using") and no exit relay that can see which site
#: was contacted.
#:
#: Provenance: this address was served by duckduckgo.com itself over
#: certificate-validated HTTPS, on its own Tor-block notice page.
PINNED_ONION_HOSTS: frozenset[str] = frozenset(
    {
        "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion",
    }
)

_EXTRA_PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),   # benchmarking
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

# One DNS label: alphanumeric, inner hyphens allowed, 1-63 characters.
_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

# A public TLD is alphabetic, or punycode. This is what rejects every
# alternate numeric IPv4 form ("2130706433", "0x7f000001", "0177.0.0.1",
# "127.1"): those either have no dot or end in a non-alphabetic label, and
# the OS resolver would otherwise happily read them as 127.0.0.1.
_PUBLIC_TLD = re.compile(r"^(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{2,59})$")


def _is_private_address(addr: ipaddress._BaseAddress) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        or any(addr in net for net in _EXTRA_PRIVATE_NETWORKS)
    )


def _assert_public_hostname(host: str, label: str) -> None:
    """Reject anything that is not an unambiguous public DNS name."""
    lowered = host.lower()

    if lowered in _BLOCKED_HOST_NAMES:
        raise UrlNotAllowed(f"{label} names a local host: {host!r}")
    if any(lowered.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise UrlNotAllowed(f"{label} names a non-public domain: {host!r}")
    if len(lowered) > 253:
        raise UrlNotAllowed(f"{label} host is too long")
    if "_" in lowered:
        raise UrlNotAllowed(f"{label} host contains an underscore: {host!r}")

    labels = lowered.split(".")
    if len(labels) < 2:
        raise UrlNotAllowed(f"{label} host is not a fully qualified name: {host!r}")
    if not all(_LABEL.match(part) for part in labels):
        raise UrlNotAllowed(f"{label} host has an invalid DNS label: {host!r}")
    if not _PUBLIC_TLD.match(labels[-1]):
        # Catches every bare-numeric and alternate-numeric IPv4 spelling.
        raise UrlNotAllowed(
            f"{label} host does not end in a public TLD (numeric or "
            f"ambiguous host syntax is refused): {host!r}"
        )


def validate_public_https_url(raw: object, *, label: str = "URL") -> str:
    """Return the URL unchanged, or raise :class:`UrlNotAllowed`.

    Applied to the caller's URL and, unchanged, to every redirect ``Location``
    before the next connection is opened.
    """
    value = "" if raw is None else str(raw).strip()
    if not value:
        raise UrlNotAllowed(f"{label} is empty")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise UrlNotAllowed(f"{label} is malformed: {exc}") from exc

    if parsed.scheme.lower() != "https":
        raise UrlNotAllowed(
            f"{label} must use https (got {parsed.scheme or 'no scheme'!r})"
        )
    if parsed.username is not None or parsed.password is not None:
        raise UrlNotAllowed(f"{label} must not embed credentials")
    if parsed.fragment:
        raise UrlNotAllowed(f"{label} must not carry a fragment")

    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UrlNotAllowed(f"{label} has an invalid authority: {exc}") from exc

    if not host:
        raise UrlNotAllowed(f"{label} has no host")
    if port is not None and port != 443:
        raise UrlNotAllowed(f"{label} must use the default https port")

    host = host.rstrip(".")
    if not host:
        raise UrlNotAllowed(f"{label} has no host")

    # Exact-match pinned onion services only. Checked before the suffix and
    # TLD rules below, which refuse .onion in general.
    if host.lower() in PINNED_ONION_HOSTS:
        return value

    # Bracketed IPv6 arrives from ``hostname`` already unwrapped.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        _assert_public_hostname(host, label)
        return value

    if _is_private_address(literal):
        raise UrlNotAllowed(f"{label} names a private or reserved address: {host!r}")
    # A bare public IP literal defeats remote DNS and TLS name validation
    # for research targets, so it is refused as well.
    raise UrlNotAllowed(f"{label} must name a host, not an IP literal: {host!r}")


# ---------------------------------------------------------------------------
# proxy configuration
# ---------------------------------------------------------------------------


def resolve_tor_proxy_url(
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the only SOCKS authority this transport may use.

    Delegates the authority rule to ``src.privacy_mode`` so the manager, the
    readiness probe, and this transport cannot drift apart.
    """
    env = os.environ if environment is None else environment
    raw = env.get("ODYSSEUS_TOR_SOCKS_URL", "")
    try:
        host, port = parse_tor_socks_endpoint(raw)
    except PrivacyConfigurationError as exc:
        raise TorUnavailable(f"Tor SOCKS endpoint is not usable: {exc}") from exc
    return f"socks5h://{host}:{port}"


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TorFetchResult:
    """A bounded response retrieved through Tor.

    ``content`` is untrusted evidence.  Callers must never treat it as
    instructions; see the privacy tool policy.
    """

    url: str
    status_code: int
    content: bytes
    content_type: str
    truncated: bool
    encoding: Optional[str] = None
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class PrivacyTorClient:
    """Synchronous HTTPS client that can only reach the network through Tor.

    Every request builds and closes its own ``httpx.Client``.  That costs a
    TCP/TLS handshake per call and is deliberate: it guarantees the client is
    released on every success and error path, and it avoids reusing one
    connection pool across unrelated research queries.
    """

    def __init__(
        self,
        *,
        proxy_url: Optional[str] = None,
        environment: Optional[Mapping[str, str]] = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        hard_max_bytes: Optional[int] = None,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        timeout: Optional[httpx.Timeout] = None,
        allowed_content_types: Optional[Sequence[str]] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        client_factory: Optional[Callable[..., httpx.Client]] = None,
    ):
        if proxy_url is None:
            self._proxy_url = resolve_tor_proxy_url(environment=environment)
        else:
            # An explicit authority is held to the same rule as the env var.
            self._proxy_url = resolve_tor_proxy_url(
                environment={"ODYSSEUS_TOR_SOCKS_URL": proxy_url}
            )

        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")

        self._max_bytes = int(max_bytes)
        self._hard_max_bytes = max(
            int(hard_max_bytes) if hard_max_bytes is not None else DEFAULT_HARD_MAX_BYTES,
            self._max_bytes,
        )
        self._max_redirects = int(max_redirects)
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._allowed_content_types = frozenset(
            item.strip().lower()
            for item in (allowed_content_types or DEFAULT_ALLOWED_CONTENT_TYPES)
        )
        self._user_agent = user_agent
        self._client_factory = client_factory or self._build_client

    # -- lifecycle ---------------------------------------------------------

    @property
    def proxy_url(self) -> str:
        return self._proxy_url

    def _build_client(self, **_kwargs: object) -> httpx.Client:
        return httpx.Client(
            proxy=self._proxy_url,
            # trust_env=False is load-bearing: it stops inherited HTTP_PROXY,
            # HTTPS_PROXY, ALL_PROXY and NO_PROXY from selecting any other
            # route, and stops netrc credentials being attached.
            trust_env=False,
            follow_redirects=False,
            timeout=self._timeout,
            headers={
                "User-Agent": self._user_agent,
                # The standard browser Accept string. A bespoke one narrows
                # the anonymity set and, observed live, some endpoints answer
                # 406 Not Acceptable to unusual values.
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                # Identity keeps the streamed byte cap a real memory bound.
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )

    def close(self) -> None:
        """Present for symmetry; per-request clients are already closed."""
        return None

    def __enter__(self) -> "PrivacyTorClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- request -----------------------------------------------------------

    def get(self, url: str) -> TorFetchResult:
        """Fetch ``url`` through Tor, or raise a :class:`PrivacyTransportError`.

        There is no direct-network fallback on any path.
        """
        current = validate_public_https_url(url)

        for _hop in range(self._max_redirects + 1):
            client = self._client_factory()
            try:
                with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_STATUS:
                        location = response.headers.get("location")
                        if not location:
                            return self._empty_result(response, current)
                        # Resolve relative targets against the *current* hop,
                        # then re-apply the full rule set before connecting.
                        candidate = urljoin(current, location)
                        current = validate_public_https_url(
                            candidate, label="redirect target"
                        )
                        continue
                    return self._bounded_result(response, current)
            except PrivacyTransportError:
                raise
            except httpx.HTTPError as exc:
                # Includes ProxyError when Tor refuses or is absent. Fail
                # closed: surface it as a transport error, never retry direct.
                raise TorUnavailable(
                    f"Tor transport failed for {current}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            finally:
                client.close()

        raise TooManyRedirects(
            f"exceeded {self._max_redirects} redirects starting at {url}"
        )

    # -- response handling -------------------------------------------------

    @staticmethod
    def _empty_result(response: httpx.Response, url: str) -> TorFetchResult:
        return TorFetchResult(
            url=url,
            status_code=response.status_code,
            content=b"",
            content_type="",
            truncated=False,
            encoding=response.encoding,
            headers=dict(response.headers),
        )

    def _bounded_result(self, response: httpx.Response, url: str) -> TorFetchResult:
        encoding = (response.headers.get("content-encoding") or "").strip().lower()
        if encoding and encoding != "identity":
            raise PrivacyTransportError(
                f"refusing a {encoding!r}-encoded body after requesting identity: "
                "the decoded size cannot be bounded"
            )

        raw_type = response.headers.get("content-type") or ""
        media_type = raw_type.split(";", 1)[0].strip().lower()
        if media_type not in self._allowed_content_types:
            raise ContentTypeNotAllowed(
                f"content type {media_type or 'missing'!r} is not in the "
                "privacy research allowlist"
            )

        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._hard_max_bytes:
            raise ResponseTooLarge(
                f"{url} declared {int(declared):,} bytes, over the "
                f"{self._hard_max_bytes:,}-byte hard ceiling"
            )

        chunks: list[bytes] = []
        read = 0
        truncated = False
        for chunk in response.iter_bytes():
            read += len(chunk)
            if read > self._max_bytes:
                keep = self._max_bytes - (read - len(chunk))
                if keep > 0:
                    chunks.append(chunk[:keep])
                truncated = True
                break
            chunks.append(chunk)

        return TorFetchResult(
            url=url,
            status_code=response.status_code,
            content=b"".join(chunks),
            content_type=media_type,
            truncated=truncated,
            encoding=response.encoding,
            headers=dict(response.headers),
        )
