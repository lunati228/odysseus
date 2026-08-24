"""Tests for the process-wide privacy egress chokepoint (PRV-003).

``src/privacy_egress.py`` is what makes the PRV-003 claim true by
construction rather than by review: the privacy profile cannot open a TCP
connection to, or even resolve, anything outside loopback.  These tests hold
that claim to the two things that could quietly break it -- a bypassable
address check, and an incompletely patched primitive.

Every test that installs the guard must remove it again, because the guard
rewrites interpreter-global attributes and would otherwise leak into the rest
of the suite.  The ``guard`` fixture owns that.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from src.privacy_egress import (
    EgressDenied,
    classify_destination,
    clear_egress_journal,
    egress_journal,
    install_privacy_egress_guard,
    is_installed,
    is_loopback_host,
    uninstall_privacy_egress_guard,
)


@pytest.fixture
def guard():
    """Install the guard for one test and always remove it afterwards."""
    clear_egress_journal()
    installed = install_privacy_egress_guard(
        force=True, environment={"ODYSSEUS_STANDARD_URL": "http://127.0.0.1:7000"}
    )
    assert installed is True
    try:
        yield
    finally:
        uninstall_privacy_egress_guard()
        clear_egress_journal()


# ---------------------------------------------------------------------------
# the standard profile must be untouched
# ---------------------------------------------------------------------------


def test_the_standard_profile_installs_nothing():
    original = socket.create_connection
    assert install_privacy_egress_guard(profile="standard") is False
    assert is_installed() is False
    assert socket.create_connection is original


# ---------------------------------------------------------------------------
# the address decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.53",
        "::1",
        "localhost",
        "LOCALHOST",
        "localhost.",
        b"127.0.0.1",
        "",
        None,
    ],
)
def test_loopback_and_local_aliases_are_recognised(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "8.8.8.8",
        "example.com",
        "duckduckgo.com",
        "169.254.169.254",          # cloud metadata
        "192.168.1.10",             # LAN
        "0.0.0.0",
        "2001:4860:4860::8888",
        "::ffff:8.8.8.8",           # IPv4-mapped remote -- see the test below
        "2130706433",               # decimal 127.0.0.1 -- not a valid literal
        "0177.0.0.1",               # octal -- rejected by ipaddress
        "127.0.0.1.evil.test",
        "localhost.evil.test",
    ],
)
def test_everything_else_is_treated_as_remote(host):
    assert is_loopback_host(host) is False


def test_an_ipv4_mapped_ipv6_literal_is_judged_on_the_embedded_address():
    """The mapping must not be usable as a disguise.

    ``ipaddress`` unwraps ``::ffff:a.b.c.d`` before answering ``is_loopback``,
    so the mapped form inherits the verdict of the embedded IPv4 address
    rather than being classified on the IPv6 form. Asserted in both
    directions, because getting only the permissive half right would be a
    silent hole.
    """
    assert is_loopback_host("::ffff:127.0.0.1") is True
    assert is_loopback_host("::ffff:8.8.8.8") is False
    assert classify_destination("::ffff:8.8.8.8", 443) == "deny-remote"


def test_ipv6_zone_ids_and_brackets_do_not_hide_a_remote_address(guard):
    assert is_loopback_host("[::1]") is True
    assert is_loopback_host("::1%eth0") is True
    assert is_loopback_host("[2001:4860:4860::8888]") is False


# ---------------------------------------------------------------------------
# the verdicts
# ---------------------------------------------------------------------------


def test_a_remote_destination_is_denied(guard):
    assert classify_destination("8.8.8.8", 443) == "deny-remote"


def test_an_ordinary_loopback_destination_is_allowed(guard):
    # The Tor SOCKS authority and the app's own API both live here.
    assert classify_destination("127.0.0.1", 19050) == "allow"
    assert classify_destination("127.0.0.1", 7001) == "allow"
    assert classify_destination("127.0.0.1", 18085) == "allow"


def test_the_standard_workspace_authority_is_denied_even_though_it_is_local(guard):
    """Loopback does not leave the machine, but it does cross the profile."""
    assert classify_destination("127.0.0.1", 7000) == "deny-cross-profile"


def test_a_different_loopback_literal_on_the_standard_port_is_still_allowed(guard):
    # 127.0.0.53:7000 is not the paired workspace; only the exact authority is
    # refused, so the rule stays a statement about the counterpart process.
    assert classify_destination("127.0.0.53", 7000) == "allow"


# ---------------------------------------------------------------------------
# the patched primitives
# ---------------------------------------------------------------------------


def test_create_connection_to_a_remote_host_is_refused_before_any_socket(guard):
    with pytest.raises(EgressDenied):
        socket.create_connection(("example.com", 443), timeout=0.5)


def test_resolving_a_remote_hostname_is_refused(guard):
    """A DNS query is itself a disclosure, so it must not even be attempted."""
    with pytest.raises(EgressDenied):
        socket.getaddrinfo("example.com", 443)


def test_resolving_a_loopback_literal_still_works(guard):
    infos = socket.getaddrinfo("127.0.0.1", 80, socket.AF_INET, socket.SOCK_STREAM)
    assert infos


def test_a_raw_socket_connect_to_a_remote_ip_is_refused(guard):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(EgressDenied):
            sock.connect(("8.8.8.8", 53))
    finally:
        sock.close()


def test_connect_ex_is_guarded_too(guard):
    """connect_ex returns an errno instead of raising, so it is easy to miss."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(EgressDenied):
            sock.connect_ex(("8.8.8.8", 53))
    finally:
        sock.close()


def test_udp_sendto_to_a_remote_address_is_refused(guard):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(EgressDenied):
            sock.sendto(b"probe", ("8.8.8.8", 53))
    finally:
        sock.close()


def test_gethostbyname_is_refused_for_a_remote_name(guard):
    with pytest.raises(EgressDenied):
        socket.gethostbyname("example.com")


def test_both_asyncio_loops_are_patched(guard):
    """The Windows proactor loop does not use socket.socket.connect at all.

    It connects through the overlapped ``ConnectEx`` API, and
    ``BaseEventLoop._ensure_resolved`` skips ``getaddrinfo`` when the host is
    already numeric -- so without this patch an async client could reach a
    hard-coded public IP with both other guards installed.
    """
    from asyncio.proactor_events import BaseProactorEventLoop
    from asyncio.selector_events import BaseSelectorEventLoop

    for owner in (BaseSelectorEventLoop, BaseProactorEventLoop):
        assert owner.sock_connect.__qualname__ == f"{owner.__name__}.sock_connect"
        assert owner.sock_connect.__module__ == "src.privacy_egress"


def test_an_async_connection_to_a_numeric_remote_ip_is_refused(guard):
    """The case that both other guards miss, exercised end to end."""

    async def _attempt():
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await loop.sock_connect(sock, ("8.8.8.8", 53))
        finally:
            sock.close()

    with pytest.raises(EgressDenied):
        asyncio.run(_attempt())


# ---------------------------------------------------------------------------
# behavior of the refusal itself
# ---------------------------------------------------------------------------


def test_a_refusal_is_an_oserror_so_existing_callers_handle_it(guard):
    """Fail closed, but inside each caller's existing error path.

    Every one of the 128 inventoried call sites already handles a network
    failure. A ``RuntimeError`` would escape a handler that only catches
    ``httpx.HTTPError``; an ``OSError`` becomes ``httpx.ConnectError``.
    """
    assert issubclass(EgressDenied, OSError)
    try:
        socket.create_connection(("example.com", 443))
    except OSError as exc:
        assert isinstance(exc, EgressDenied)
    else:  # pragma: no cover - the call above must raise
        pytest.fail("a remote destination was not refused")


def test_httpx_turns_a_refusal_into_an_ordinary_connect_error(guard):
    httpx = pytest.importorskip("httpx")
    with pytest.raises(httpx.HTTPError):
        httpx.get("https://example.com/", timeout=2.0)


def test_a_remote_refusal_message_does_not_name_the_host(guard):
    """Refusal messages reach the privacy log, which must stay content-free.

    A denied destination is often a research target, so the host goes only to
    the in-memory journal.
    """
    with pytest.raises(EgressDenied) as excinfo:
        socket.create_connection(("secret-research-target.example", 443))
    message = str(excinfo.value)
    assert "secret-research-target" not in message
    assert "443" in message


def test_a_cross_profile_refusal_may_name_the_authority(guard):
    """The paired workspace is never a research target, so naming it is safe."""
    with pytest.raises(EgressDenied) as excinfo:
        socket.create_connection(("127.0.0.1", 7000))
    assert "127.0.0.1:7000" in str(excinfo.value)


def test_the_journal_records_denials_for_the_live_capture(guard):
    clear_egress_journal()
    for host, port in (("example.com", 443), ("127.0.0.1", 7000)):
        with pytest.raises(EgressDenied):
            socket.create_connection((host, port))

    entries = egress_journal()
    verdicts = [entry["verdict"] for entry in entries]
    assert "deny-remote" in verdicts
    assert "deny-cross-profile" in verdicts
    assert any(entry["host"] == "example.com" for entry in entries)


def test_the_journal_is_bounded(guard):
    clear_egress_journal()
    for index in range(400):
        with pytest.raises(EgressDenied):
            socket.create_connection((f"host{index}.example", 443))
    assert len(egress_journal()) <= 256


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def test_installing_twice_does_not_double_wrap(guard):
    wrapped = socket.create_connection
    assert install_privacy_egress_guard(force=True) is False
    assert socket.create_connection is wrapped


def test_uninstall_restores_every_primitive():
    before = {
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "gethostbyname": socket.gethostbyname,
    }
    install_privacy_egress_guard(force=True)
    try:
        assert socket.create_connection is not before["create_connection"]
    finally:
        assert uninstall_privacy_egress_guard() is True

    assert socket.create_connection is before["create_connection"]
    assert socket.getaddrinfo is before["getaddrinfo"]
    assert socket.socket.connect is before["connect"]
    assert socket.socket.connect_ex is before["connect_ex"]
    assert socket.socket.sendto is before["sendto"]
    assert socket.gethostbyname is before["gethostbyname"]
    assert is_installed() is False


def test_the_tor_transport_still_reaches_its_proxy_with_the_guard_installed(guard):
    """The one approved path must survive the chokepoint.

    A listener is opened on loopback and connected to through the same
    primitive httpx's sync backend uses, which is the whole reason the SOCKS
    authority is required to be numeric loopback.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        connection = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        connection.close()
    finally:
        listener.close()
