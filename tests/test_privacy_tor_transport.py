"""Acceptance tests for the Privacy Workspace Tor-only HTTP transport.

The decisive tests here are the SOCKS-level ones.  A ``socks5h://`` proxy
string is not by itself proof of remote DNS: httpx sends the same SOCKS5
DOMAINNAME address type for both ``socks5://`` and ``socks5h://``.  So the
suite runs a fake SOCKS5 server that records the request address-type byte
and asserts it is 3 (DOMAINNAME), never 1 (IPv4), and separately guards
``socket.getaddrinfo`` so any local resolution of a *target* host fails the
test.
"""
from __future__ import annotations

import ipaddress
import pathlib
import socket
import threading

import httpx
import pytest

from services.search.privacy_transport import (
    PINNED_ONION_HOSTS,
    ContentTypeNotAllowed,
    PrivacyTorClient,
    PrivacyTransportError,
    ResponseTooLarge,
    TooManyRedirects,
    TorUnavailable,
    UrlNotAllowed,
    resolve_tor_proxy_url,
    validate_public_https_url,
)


# ---------------------------------------------------------------------------
# fake SOCKS5 server
# ---------------------------------------------------------------------------


class FakeSocks5Server:
    """Minimal SOCKS5 endpoint that records one CONNECT request.

    ``refuse=True`` answers the CONNECT with a general failure, which is how
    the suite simulates "Tor is not running / Tor was killed".
    """

    def __init__(self, *, refuse: bool = True):
        self.refuse = refuse
        self.atyp: int | None = None
        self.requested_host: str | None = None
        self.requested_port: int | None = None
        self.connections = 0
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def proxy_url(self) -> str:
        return f"socks5h://127.0.0.1:{self.port}"

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            with conn:
                try:
                    self._handshake(conn)
                except OSError:
                    continue

    def _handshake(self, conn: socket.socket) -> None:
        head = conn.recv(2)
        if len(head) < 2:
            return
        conn.recv(head[1])          # method list
        conn.sendall(b"\x05\x00")   # version 5, no authentication

        request = conn.recv(4)
        if len(request) < 4:
            return
        self.atyp = request[3]
        if request[3] == 3:         # DOMAINNAME
            length = conn.recv(1)[0]
            self.requested_host = conn.recv(length).decode("ascii", "replace")
        elif request[3] == 1:       # IPv4
            self.requested_host = socket.inet_ntoa(conn.recv(4))
        elif request[3] == 4:       # IPv6
            self.requested_host = socket.inet_ntop(socket.AF_INET6, conn.recv(16))
        self.requested_port = int.from_bytes(conn.recv(2), "big")

        # 0x01 == general SOCKS server failure
        conn.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def socks_server():
    server = FakeSocks5Server()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def no_target_dns(monkeypatch):
    """Fail the test if local DNS is used for anything but a numeric literal.

    Connecting to the loopback proxy legitimately resolves ``127.0.0.1``, so
    numeric literals stay allowed.  Any hostname lookup is a leak.
    """
    real = socket.getaddrinfo
    attempted: list[str] = []

    def guard(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(str(host).strip("[]"))
        except ValueError:
            attempted.append(str(host))
            raise AssertionError(
                f"local DNS resolution attempted for target host {host!r}; "
                "the Tor path must send hostnames to the SOCKS proxy"
            )
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guard)
    return attempted


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


def test_accepts_a_plain_public_https_url():
    assert validate_public_https_url("https://example.com/a?b=c") == (
        "https://example.com/a?b=c"
    )


def test_rejects_plaintext_http_scheme():
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url("http://example.com/")


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/win.ini",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "data:text/html,<b>x</b>",
        "javascript:alert(1)",
        "ws://example.com/",
    ],
)
def test_rejects_non_web_schemes(url):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(url)


def test_rejects_credentials_embedded_in_the_url():
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url("https://user:pw@example.com/")


def test_rejects_a_url_fragment():
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url("https://example.com/page#section")


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "metadata",
        "metadata.google.internal",
        "box.local",
        "app.localhost",
        "svc.internal",
        "printer.lan",
        "wiki.intranet",
    ],
)
def test_rejects_local_and_metadata_hostnames(host):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(f"https://{host}/")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "172.16.4.4",
        "192.168.1.1",
        "169.254.169.254",   # cloud metadata
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",         # multicast
    ],
)
def test_rejects_private_reserved_and_metadata_ipv4_literals(host):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(f"https://{host}/")


@pytest.mark.parametrize(
    "host",
    [
        "2130706433",        # decimal 127.0.0.1
        "0x7f000001",        # hex 127.0.0.1
        "0177.0.0.1",        # octal 127.0.0.1
        "127.1",             # short form 127.0.0.1
        "0",
    ],
)
def test_rejects_alternate_numeric_ipv4_forms(host):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(f"https://{host}/")


@pytest.mark.parametrize(
    "host",
    [
        "[::1]",
        "[::ffff:127.0.0.1]",   # IPv4-mapped loopback
        "[fe80::1]",
        "[fc00::1]",
        "[::]",
    ],
)
def test_rejects_loopback_mapped_and_link_local_ipv6(host):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(f"https://{host}/")


@pytest.mark.parametrize("url", ["", "   ", "https://", "https:///path", "not a url"])
def test_rejects_malformed_and_hostless_urls(url):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(url)


def test_accepts_only_exactly_pinned_onion_services():
    """A v3 onion address is the service's public key, so it self-authenticates.

    That is why an exact pin is acceptable where arbitrary onion browsing is
    not: a hostile relay cannot impersonate a pinned address.
    """
    assert PINNED_ONION_HOSTS, "at least one search onion must be pinned"

    for host in PINNED_ONION_HOSTS:
        url = f"https://{host}/html/?q=test"
        assert validate_public_https_url(url) == url


@pytest.mark.parametrize(
    "host",
    [
        "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczaa.onion",  # 1 char off
        "evil.onion",
        "3g2upl4pq6kufc4m.onion",
        "notpinned7kjsdfhkjsdhfkjsdhfkjshdfkjhsdkfjhskdjfhksjd.onion",
    ],
)
def test_refuses_any_onion_that_is_not_pinned(host):
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(f"https://{host}/")


def test_a_pinned_onion_is_still_refused_over_plaintext_http():
    host = next(iter(PINNED_ONION_HOSTS))
    with pytest.raises(UrlNotAllowed):
        validate_public_https_url(f"http://{host}/")


# ---------------------------------------------------------------------------
# proxy configuration
# ---------------------------------------------------------------------------


def test_reads_the_configured_tor_socks_authority():
    env = {"ODYSSEUS_TOR_SOCKS_URL": "socks5h://127.0.0.1:19050"}
    assert resolve_tor_proxy_url(environment=env) == "socks5h://127.0.0.1:19050"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "socks5://127.0.0.1:19050",      # wrong scheme, must be socks5h
        "socks5h://localhost:19050",     # must be the numeric literal
        "socks5h://127.0.0.1",           # no explicit port
        "socks5h://10.0.0.1:19050",      # not loopback
        "http://127.0.0.1:19050",
        "socks5h://user:pw@127.0.0.1:19050",
    ],
)
def test_refuses_any_proxy_authority_that_is_not_the_configured_tor_listener(value):
    with pytest.raises(TorUnavailable):
        resolve_tor_proxy_url(environment={"ODYSSEUS_TOR_SOCKS_URL": value})


def test_missing_tor_configuration_fails_closed():
    with pytest.raises(TorUnavailable):
        resolve_tor_proxy_url(environment={})


# ---------------------------------------------------------------------------
# SOCKS-level proof: remote DNS, and no direct fallback
# ---------------------------------------------------------------------------


def test_sends_the_hostname_as_socks5_domainname_not_a_resolved_ipv4(
    socks_server, no_target_dns
):
    with PrivacyTorClient(proxy_url=socks_server.proxy_url) as client:
        with pytest.raises(PrivacyTransportError):
            client.get("https://example.com/")

    assert socks_server.atyp == 3, (
        f"expected SOCKS5 DOMAINNAME (3), got address type {socks_server.atyp}"
    )
    assert socks_server.requested_host == "example.com"
    assert socks_server.requested_port == 443


def test_a_refusing_tor_listener_fails_closed_without_a_direct_attempt(
    socks_server, no_target_dns
):
    with PrivacyTorClient(proxy_url=socks_server.proxy_url) as client:
        with pytest.raises(PrivacyTransportError):
            client.get("https://example.com/")

    # One proxy connection, one refusal, no retry through a direct client.
    assert socks_server.connections == 1
    assert no_target_dns == []


def test_inherited_proxy_environment_cannot_redirect_the_transport(
    socks_server, no_target_dns, monkeypatch
):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")

    with PrivacyTorClient(proxy_url=socks_server.proxy_url) as client:
        with pytest.raises(PrivacyTransportError):
            client.get("https://example.com/")

    # Traffic still reached the Tor listener, not the injected proxy.
    assert socks_server.connections == 1
    assert socks_server.requested_host == "example.com"


def test_client_construction_fails_closed_when_tor_is_not_configured():
    with pytest.raises(TorUnavailable):
        PrivacyTorClient(environment={})


def test_the_socks5_backend_dependency_is_installed_and_pinned():
    """PRV-011: httpx cannot honour a socks5h proxy without socksio present.

    Without this the transport would fail only at first live use, which is
    exactly when a fail-closed error is least diagnosable.
    """
    import socksio

    assert socksio.__version__ == "1.0.0"

    requirements = (
        pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert "socksio==1.0.0" in requirements, (
        "socksio is used on the privacy egress path and must stay pinned"
    )

    # Proves httpx accepts the authority and resolves a SOCKS backend for it.
    client = httpx.Client(proxy="socks5h://127.0.0.1:19050", trust_env=False)
    client.close()


def test_the_production_client_is_configured_to_bound_and_not_follow_redirects():
    """Guards the real client factory, which the mock-transport tests bypass.

    The proxy wiring itself is proven end-to-end by the fake-SOCKS tests; what
    is checked here is the configuration those tests cannot observe.
    """
    client = PrivacyTorClient(proxy_url="socks5h://127.0.0.1:19050")
    built = client._build_client()
    try:
        # Inherited proxy/netrc environment must not be consulted at all.
        assert built.trust_env is False
        # Redirects are revalidated by hand, never followed automatically.
        assert built.follow_redirects is False
        # Identity encoding keeps the streamed byte cap a real memory bound.
        assert built.headers["accept-encoding"] == "identity"
    finally:
        built.close()

    assert client.proxy_url == "socks5h://127.0.0.1:19050"


# ---------------------------------------------------------------------------
# redirect revalidation
# ---------------------------------------------------------------------------


def _mock_client_factory(handler):
    """Build a client factory backed by httpx.MockTransport.

    Used to exercise redirect and cap logic without a live network.  The
    proxy wiring itself is proven by the SOCKS tests above.
    """
    def factory(**_kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)

    return factory


def _redirect_to(location: str):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

    return handler, seen


@pytest.mark.parametrize(
    "location",
    [
        "https://127.0.0.1/admin",
        "https://localhost/admin",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/",
        "https://[::1]/",
        "https://2130706433/",
        "http://example.com/downgraded",   # scheme downgrade
        "file:///C:/Windows/win.ini",
    ],
)
def test_rejects_a_redirect_target_before_making_the_second_request(location):
    handler, seen = _redirect_to(location)
    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        client_factory=_mock_client_factory(handler),
    )
    with client:
        with pytest.raises(UrlNotAllowed):
            client.get("https://example.com/start")

    assert len(seen) == 1, "the rejected redirect target was still requested"


def test_follows_and_revalidates_a_relative_redirect():
    handler, seen = _redirect_to("/next-page")
    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        client_factory=_mock_client_factory(handler),
    )
    with client:
        result = client.get("https://example.com/start")

    assert seen == ["https://example.com/start", "https://example.com/next-page"]
    assert result.status_code == 200


def test_enforces_the_redirect_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        max_redirects=2,
        client_factory=_mock_client_factory(handler),
    )
    with client:
        with pytest.raises(TooManyRedirects):
            client.get("https://example.com/start")


# ---------------------------------------------------------------------------
# response bounds
# ---------------------------------------------------------------------------


def test_rejects_a_declared_content_length_over_the_hard_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "999999999"},
            content=b"x",
        )

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        max_bytes=1024,
        client_factory=_mock_client_factory(handler),
    )
    with client:
        with pytest.raises(ResponseTooLarge):
            client.get("https://example.com/big")


def test_truncates_a_body_that_streams_past_the_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"a" * 5000
        )

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        max_bytes=1000,
        client_factory=_mock_client_factory(handler),
    )
    with client:
        result = client.get("https://example.com/long")

    assert result.truncated is True
    assert len(result.content) == 1000


def test_refuses_a_compressed_body_after_requesting_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            content=b"\x1f\x8b" + b"\x00" * 40,
        )

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        client_factory=_mock_client_factory(handler),
    )
    with client:
        with pytest.raises(PrivacyTransportError):
            client.get("https://example.com/gz")


@pytest.mark.parametrize(
    "content_type",
    ["application/octet-stream", "video/mp4", "application/zip", ""],
)
def test_rejects_a_content_type_outside_the_allowlist(content_type):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": content_type}, content=b"x"
        )

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        client_factory=_mock_client_factory(handler),
    )
    with client:
        with pytest.raises(ContentTypeNotAllowed):
            client.get("https://example.com/blob")


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", "text/plain"])
def test_accepts_allowlisted_content_types(content_type):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": content_type}, content=b"hello"
        )

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        client_factory=_mock_client_factory(handler),
    )
    with client:
        result = client.get("https://example.com/page")

    assert result.text == "hello"
    assert result.truncated is False


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def test_closes_the_underlying_client_on_the_error_path():
    closed: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/zip"}, content=b"x"
        )

    def factory(**_kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
        original_close = client.close

        def tracking_close():
            closed.append(True)
            original_close()

        client.close = tracking_close  # type: ignore[method-assign]
        return client

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050", client_factory=factory
    )
    with pytest.raises(ContentTypeNotAllowed):
        client.get("https://example.com/blob")
    client.close()

    assert closed, "the per-request client was not closed on the error path"


def test_rejects_a_non_get_style_disallowed_target_after_construction():
    """A validated client must still validate every call, not just the first."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"ok"
        )

    client = PrivacyTorClient(
        proxy_url="socks5h://127.0.0.1:19050",
        client_factory=_mock_client_factory(handler),
    )
    with client:
        assert client.get("https://example.com/ok").status_code == 200
        with pytest.raises(UrlNotAllowed):
            client.get("https://127.0.0.1/admin")
