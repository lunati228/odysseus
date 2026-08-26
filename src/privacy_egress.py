"""Process-wide egress chokepoint for the Privacy Workspace profile (PRV-003).

Why a chokepoint instead of 128 guarded call sites
--------------------------------------------------
``tests/test_privacy_policy.py`` enumerates **128 direct HTTP/socket call sites
across 48 files**.  Editing all of them would be a very large upstream diff, it
would still miss every third-party library in the process (``httpx``,
``urllib3``, ``huggingface_hub``, ``chromadb``, ``openai`` ...), and it would
regress the moment upstream adds site 129.  A per-call-site guard is a
*review* artifact, not a containment guarantee.

This module is the containment guarantee.  In the privacy profile it replaces
the small number of standard-library primitives that every network egress in a
CPython process must pass through, and denies by default:

* ``socket.socket.connect`` / ``connect_ex``   -- blocking sockets
* ``socket.create_connection``                 -- httpx/httpcore sync backend
* ``socket.getaddrinfo`` / ``gethostbyname``   -- DNS, including leak-only DNS
* ``socket.socket.sendto`` / ``sendmsg``       -- connectionless UDP
* ``BaseSelectorEventLoop.sock_connect``       -- asyncio on POSIX
* ``BaseProactorEventLoop.sock_connect``       -- asyncio on Windows

The two ``sock_connect`` patches are load-bearing and easy to miss.  On Windows
the proactor loop connects through the overlapped ``ConnectEx`` API, which does
**not** go through ``socket.socket.connect``; and ``BaseEventLoop`` short-cuts
``getaddrinfo`` entirely when the host is already a numeric literal
(``base_events._ipaddr_info``).  Without those two patches an async client
could reach a hard-coded public IP with both of the other guards installed.

The rule
--------
1. **Non-loopback destinations are denied unconditionally.**  There is no
   configuration that re-enables them.  This is what makes the privacy claim
   ("nothing leaves this machine except through Tor") true by construction
   rather than by inspection: Tor's SOCKS port is on ``127.0.0.1``, so the
   approved research path is unaffected, and every other destination -- present,
   future, first-party or vendored -- is refused before a packet is sent.
2. **Resolving a non-loopback hostname is denied**, so a denied destination
   never even reaches the local resolver.  A DNS query is itself a disclosure.
3. **The Standard Workspace authority is denied even though it is loopback.**
   It does not leave the machine, but it crosses the profile boundary, which is
   the other thing the privacy vault exists to prevent.
4. Every other loopback destination is allowed.  It cannot leave the machine,
   and a strict loopback *port* allowlist is not viable: ``asyncio`` builds its
   own self-pipe with ``socket.socketpair()``, which on Windows connects to an
   ephemeral loopback port chosen by the OS.

Residual risk, stated plainly
-----------------------------
* A C extension that calls ``connect(2)`` without going through the ``socket``
  module is not intercepted.  Nothing in the pinned dependency set does this;
  ``curl``-based wheels would.
* A child process is not intercepted.  The privacy profile denies
  ``shell-automation`` and disables the background/scheduler services, but
  process isolation is an OS-level control this module does not provide.
* A locally listening proxy that itself re-exports to the internet would be
  reachable, because it is loopback.  Only the Standard Workspace authority is
  singled out; a general local-proxy defence needs an OS firewall rule.

Error type
----------
:class:`EgressDenied` derives from :class:`OSError` on purpose.  Every one of
the 128 call sites already handles a network failure; raising an ``OSError``
means a denied call degrades exactly like an unreachable host (``httpx`` turns
it into ``ConnectError``) instead of escaping as an unhandled ``RuntimeError``
through a route that only catches ``httpx.HTTPError``.  Fail closed *and* stay
inside each caller's existing error path.

What the message may say
------------------------
For a non-loopback denial the message names the port and address family but
**not** the host.  A denied destination is frequently a research target, and
these messages reach the privacy log, which ``README-PRIVACY-WORKSPACE-FORK.md`` requires to stay
free of query and page content.  The full destination is kept only in the
bounded in-memory :func:`egress_journal`, which is never written to disk and is
what the live-capture evidence reads.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from src.privacy_mode import is_privacy_mode

logger = logging.getLogger(__name__)


class EgressDenied(OSError):
    """A network destination the privacy profile refuses to contact.

    Subclasses :class:`OSError` so existing call sites treat it as an ordinary
    connection failure rather than letting it escape as a ``RuntimeError``.
    """

    def __init__(self, message: str, *, capability: str = "direct-http"):
        self.capability = capability
        super().__init__(message)


# ---------------------------------------------------------------------------
# the decision, as a pure function
# ---------------------------------------------------------------------------

#: Hostnames that resolve without a network DNS query.  ``localhost`` is
#: answered from the OS hosts file on both supported platforms, and whatever it
#: resolves to is still re-checked by the connect guard.
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

_DEFAULT_STANDARD_URL = "http://127.0.0.1:7000"

#: Denials retained for the live-capture evidence.  Bounded so a hostile page
#: cannot grow the process's memory by triggering refusals in a loop.
_JOURNAL_LIMIT = 256

_lock = threading.Lock()
_journal: list[dict[str, object]] = []
_installed = False
_originals: dict[str, Any] = {}
_denied_authorities: frozenset[tuple[str, int]] = frozenset()


def _as_text(host: object) -> str:
    if isinstance(host, (bytes, bytearray)):
        return bytes(host).decode("ascii", "replace")
    return "" if host is None else str(host)


def _ip_or_none(host: str) -> Optional[ipaddress._BaseAddress]:
    """Parse ``host`` as an IP literal, tolerating brackets and IPv6 zone ids."""
    value = host.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if "%" in value:
        value = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def is_loopback_host(host: object) -> bool:
    """Return whether ``host`` is a loopback literal or a local alias name."""
    text = _as_text(host).strip()
    if not text:
        # None/"" means "this machine" for bind() and for AI_PASSIVE lookups.
        return True
    address = _ip_or_none(text)
    if address is not None:
        return bool(address.is_loopback)
    return text.lower().rstrip(".") in _LOCAL_NAMES


def _standard_authorities(
    environment: Optional[Mapping[str, str]] = None,
) -> frozenset[tuple[str, int]]:
    """Loopback authorities the privacy process must never contact.

    Derived from ``ODYSSEUS_STANDARD_URL`` so a redeployment on other ports
    stays covered.  A malformed value falls back to the documented default
    rather than to "deny nothing".
    """
    import os

    env = os.environ if environment is None else environment
    raw = str(env.get("ODYSSEUS_STANDARD_URL") or _DEFAULT_STANDARD_URL).strip()
    for candidate in (raw, _DEFAULT_STANDARD_URL):
        try:
            parsed = urlsplit(candidate)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            continue
        if host and port:
            return frozenset({(host, int(port))})
    return frozenset()


def classify_destination(host: object, port: object) -> str:
    """Return ``"allow"``, ``"deny-remote"`` or ``"deny-cross-profile"``.

    Pure and side-effect free, so the policy can be tested without patching the
    interpreter.
    """
    text = _as_text(host).strip()
    if not is_loopback_host(text):
        return "deny-remote"
    try:
        number = int(port)
    except (TypeError, ValueError):
        return "allow"
    address = _ip_or_none(text)
    normalized = str(address) if address is not None else text.lower().rstrip(".")
    for denied_host, denied_port in _denied_authorities:
        if number != denied_port:
            continue
        denied_address = _ip_or_none(denied_host)
        if denied_address is not None and address is not None:
            if denied_address == address:
                return "deny-cross-profile"
        elif normalized == denied_host.lower():
            return "deny-cross-profile"
    return "allow"


def _record(verdict: str, host: object, port: object, primitive: str) -> None:
    entry = {
        "verdict": verdict,
        "primitive": primitive,
        "host": _as_text(host),
        "port": port,
    }
    with _lock:
        _journal.append(entry)
        if len(_journal) > _JOURNAL_LIMIT:
            del _journal[: len(_journal) - _JOURNAL_LIMIT]


def _deny(verdict: str, host: object, port: object, primitive: str) -> EgressDenied:
    _record(verdict, host, port, primitive)
    if verdict == "deny-cross-profile":
        # Naming a loopback authority discloses nothing: it is the paired
        # workspace, never a research target.
        message = (
            f"privacy profile refuses to contact the Standard Workspace "
            f"authority {_as_text(host)}:{port} via {primitive}"
        )
        capability = "local-storage"
    else:
        # Deliberately host-free -- see the module docstring.
        message = (
            f"privacy profile refuses direct network egress to a non-loopback "
            f"destination on port {port!r} via {primitive}; "
            "the Tor transport is the only approved route"
        )
        capability = "direct-http"
    logger.warning("%s", message)
    return EgressDenied(message, capability=capability)


def _check(host: object, port: object, primitive: str) -> None:
    verdict = classify_destination(host, port)
    if verdict != "allow":
        raise _deny(verdict, host, port, primitive)


def _split_address(address: object) -> tuple[object, object]:
    """Pull ``(host, port)`` out of any of the address shapes ``socket`` uses."""
    if isinstance(address, (tuple, list)) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else None
        return host, port
    # AF_UNIX (str/bytes path) and AF_* families with no network reach.
    return "", None


def egress_journal() -> tuple[dict[str, object], ...]:
    """Return the bounded in-memory record of allowed-check denials."""
    with _lock:
        return tuple(dict(entry) for entry in _journal)


def clear_egress_journal() -> None:
    with _lock:
        _journal.clear()


def is_installed() -> bool:
    return _installed


# ---------------------------------------------------------------------------
# installation
# ---------------------------------------------------------------------------


def _wrap_socket_method(name: str) -> Callable[..., Any]:
    original = getattr(socket.socket, name)

    def guarded(self: socket.socket, address: object, *args: Any, **kwargs: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host, port = _split_address(address)
            _check(host, port, f"socket.{name}")
        return original(self, address, *args, **kwargs)

    guarded.__name__ = name
    guarded.__qualname__ = f"socket.socket.{name}"
    _originals[f"socket.socket.{name}"] = original
    return guarded


def _wrap_sendto() -> Callable[..., Any]:
    original = socket.socket.sendto

    def guarded(self: socket.socket, *args: Any, **kwargs: Any) -> Any:
        # sendto(data, address) or sendto(data, flags, address)
        address = args[-1] if args else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(
            address, (tuple, list)
        ):
            host, port = _split_address(address)
            _check(host, port, "socket.sendto")
        return original(self, *args, **kwargs)

    guarded.__name__ = "sendto"
    guarded.__qualname__ = "socket.socket.sendto"
    _originals["socket.socket.sendto"] = original
    return guarded


def _wrap_create_connection() -> Callable[..., Any]:
    original = socket.create_connection

    def guarded(address: object, *args: Any, **kwargs: Any) -> Any:
        host, port = _split_address(address)
        _check(host, port, "socket.create_connection")
        return original(address, *args, **kwargs)

    guarded.__name__ = "create_connection"
    _originals["socket.create_connection"] = original
    return guarded


def _wrap_getaddrinfo() -> Callable[..., Any]:
    original = socket.getaddrinfo

    def guarded(host: object, port: object = None, *args: Any, **kwargs: Any) -> Any:
        if not is_loopback_host(host):
            raise _deny("deny-remote", host, port, "socket.getaddrinfo")
        return original(host, port, *args, **kwargs)

    guarded.__name__ = "getaddrinfo"
    _originals["socket.getaddrinfo"] = original
    return guarded


def _wrap_name_resolver(name: str) -> Callable[..., Any]:
    original = getattr(socket, name)

    def guarded(host: object, *args: Any, **kwargs: Any) -> Any:
        if not is_loopback_host(host):
            raise _deny("deny-remote", host, None, f"socket.{name}")
        return original(host, *args, **kwargs)

    guarded.__name__ = name
    _originals[f"socket.{name}"] = original
    return guarded


def _wrap_sock_connect(owner: type, label: str) -> Optional[Callable[..., Any]]:
    original = getattr(owner, "sock_connect", None)
    if original is None:
        return None

    async def guarded(self: Any, sock: socket.socket, address: object) -> Any:
        if getattr(sock, "family", None) in (socket.AF_INET, socket.AF_INET6):
            host, port = _split_address(address)
            _check(host, port, label)
        return await original(self, sock, address)

    guarded.__name__ = "sock_connect"
    guarded.__qualname__ = f"{owner.__name__}.sock_connect"
    _originals[label] = original
    return guarded


def install_privacy_egress_guard(
    *,
    profile: Optional[str] = None,
    environment: Optional[Mapping[str, str]] = None,
    force: bool = False,
) -> bool:
    """Install the chokepoint.  No-op outside the privacy profile.

    Idempotent: a second call while installed does nothing, so importing the
    application twice in one interpreter cannot double-wrap the primitives.
    """
    global _installed, _denied_authorities

    if _installed:
        return False
    if not force and not is_privacy_mode(profile):
        return False

    _denied_authorities = _standard_authorities(environment)

    socket.socket.connect = _wrap_socket_method("connect")  # type: ignore[method-assign]
    socket.socket.connect_ex = _wrap_socket_method("connect_ex")  # type: ignore[method-assign]
    socket.socket.sendto = _wrap_sendto()  # type: ignore[method-assign]
    socket.create_connection = _wrap_create_connection()  # type: ignore[assignment]
    socket.getaddrinfo = _wrap_getaddrinfo()  # type: ignore[assignment]
    for resolver in ("gethostbyname", "gethostbyname_ex"):
        if hasattr(socket, resolver):
            setattr(socket, resolver, _wrap_name_resolver(resolver))

    # asyncio: both loop implementations, because neither one routes through
    # socket.socket.connect and the Windows proactor also skips getaddrinfo for
    # numeric hosts.
    try:
        from asyncio.selector_events import BaseSelectorEventLoop

        guarded = _wrap_sock_connect(
            BaseSelectorEventLoop, "asyncio.selector.sock_connect"
        )
        if guarded is not None:
            BaseSelectorEventLoop.sock_connect = guarded  # type: ignore[method-assign]
    except ImportError:  # pragma: no cover - selector loop is always present
        pass
    try:
        from asyncio.proactor_events import BaseProactorEventLoop

        guarded = _wrap_sock_connect(
            BaseProactorEventLoop, "asyncio.proactor.sock_connect"
        )
        if guarded is not None:
            BaseProactorEventLoop.sock_connect = guarded  # type: ignore[method-assign]
    except ImportError:  # pragma: no cover - POSIX has no proactor loop
        pass

    _installed = True
    logger.info(
        "privacy egress guard installed: non-loopback destinations are denied "
        "(%d cross-profile authority/authorities also denied)",
        len(_denied_authorities),
    )
    return True


def uninstall_privacy_egress_guard() -> bool:
    """Restore the original primitives.  Used by tests, never by the app."""
    global _installed, _denied_authorities

    if not _installed:
        return False

    for key, original in list(_originals.items()):
        if key.startswith("socket.socket."):
            setattr(socket.socket, key.rsplit(".", 1)[1], original)
        elif key.startswith("socket."):
            setattr(socket, key.rsplit(".", 1)[1], original)
        elif key == "asyncio.selector.sock_connect":
            from asyncio.selector_events import BaseSelectorEventLoop

            BaseSelectorEventLoop.sock_connect = original  # type: ignore[method-assign]
        elif key == "asyncio.proactor.sock_connect":
            from asyncio.proactor_events import BaseProactorEventLoop

            BaseProactorEventLoop.sock_connect = original  # type: ignore[method-assign]

    _originals.clear()
    _denied_authorities = frozenset()
    _installed = False
    return True
