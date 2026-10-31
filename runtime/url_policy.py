"""Parsed URL policy for browser navigation and external endpoint checks (H04, D35).

Prefix checks such as ``url.startswith("http://localhost")`` accept
``http://localhost.attacker.example``. This module parses the URL, normalises the
host, rejects ambiguous encodings, and checks every resolved address.

Rules (defaults; ``UrlPolicy`` fields change them):

* Only ``http`` and ``https``. No userinfo, control characters, whitespace or backslashes.
* ``http`` is allowed only for literal loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``).
* ``https`` to a public host is allowed on the configured ports (443 and 8443 by default).
* A host that resolves to a loopback, private, link-local, CGNAT (Tailscale/Mesh),
  multicast, reserved or cloud metadata address is rejected unless the
  operator lists the network in ``private_networks``. Loopback is allowed only through
  the explicit loopback host forms above, never through a DNS name.
* Numeric IPv4 shorthands (``2130706433``, ``0x7f.1``, ``0177.0.0.1``) are rejected as ambiguous.

DNS can change between this check and the browser's own lookup (rebinding).
Callers therefore re-validate the page URL after navigation and close the session
when it violates policy. That narrows, but cannot eliminate, the window.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]

_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")
_METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200")}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class UrlPolicyError(ValueError):
    """The URL is not permitted. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


@dataclass
class UrlPolicy:
    https_ports: set[int] = field(default_factory=lambda: {443, 8443})
    allow_loopback_http: bool = True
    private_networks: list[str] = field(default_factory=list)
    resolver: Resolver = system_resolver

    def _private_allowed(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        for raw in self.private_networks:
            try:
                if ip in ipaddress.ip_network(raw, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def check(self, url: str) -> dict[str, object]:
        """Return a normalised description of ``url`` or raise ``UrlPolicyError``."""
        if not isinstance(url, str) or not url or len(url) > 4096:
            raise UrlPolicyError("invalid_url", "URL must be a non-empty string of at most 4096 characters")
        if _CONTROL.search(url):
            raise UrlPolicyError("invalid_url", "URL contains whitespace, control characters or backslashes")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise UrlPolicyError("invalid_url", f"URL cannot be parsed ({exc})") from None
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise UrlPolicyError("scheme_denied", "only http and https URLs are permitted")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise UrlPolicyError("userinfo_denied", "URLs with embedded credentials are not permitted")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise UrlPolicyError("invalid_url", "URL has no host")
        try:
            host = host.encode("idna").decode("ascii") if not _is_ip(host) else host
        except UnicodeError:
            raise UrlPolicyError("invalid_host", "host name is not valid IDNA") from None
        if port == 0:
            raise UrlPolicyError("port_denied", "port 0 is not permitted")
        literal = _ip_literal(host)
        if literal is None and _ambiguous_ipv4(host):
            raise UrlPolicyError("ambiguous_host", "numeric IPv4 shorthand hosts are not permitted; use dotted-quad form")

        loopback_host = host == "localhost" or (literal is not None and literal.is_loopback)
        effective_port = port or (443 if scheme == "https" else 80)
        if scheme == "http":
            if not (self.allow_loopback_http and loopback_host):
                raise UrlPolicyError("scheme_denied", "plain http is permitted only for localhost, 127.0.0.0/8 or [::1]")
            return {"url": url, "scheme": scheme, "host": host, "port": effective_port, "exposure": "loopback", "addresses": [str(literal or "127.0.0.1")]}
        if loopback_host:
            return {"url": url, "scheme": scheme, "host": host, "port": effective_port, "exposure": "loopback", "addresses": [str(literal or "127.0.0.1")]}
        if port is not None and port not in self.https_ports:
            raise UrlPolicyError("port_denied", f"https port {port} is not in the permitted set {sorted(self.https_ports)}")

        if literal is not None:
            addresses = [literal]
        else:
            try:
                addresses = [ipaddress.ip_address(a) for a in self.resolver(host)]
            except (OSError, ValueError) as exc:
                raise UrlPolicyError("dns_failure", f"host did not resolve ({exc.__class__.__name__})") from None
            if not addresses:
                raise UrlPolicyError("dns_failure", "host resolved to no addresses")
        exposure = "public"
        for ip in addresses:
            mapped = getattr(ip, "ipv4_mapped", None) or ip
            if mapped in _METADATA:
                raise UrlPolicyError("metadata_denied", "cloud metadata addresses are never permitted")
            if mapped.is_loopback:
                raise UrlPolicyError("private_denied", "a DNS name resolving to loopback is not permitted; use localhost explicitly")
            private = (mapped.is_private or mapped.is_link_local or mapped.is_reserved or mapped.is_multicast
                       or mapped.is_unspecified or (mapped.version == 4 and mapped in _CGNAT))
            if private:
                if not self._private_allowed(mapped):
                    raise UrlPolicyError("private_denied", f"{host} resolves to a private or reserved address; add the network to browser_private_networks to permit it")
                exposure = "private"
        return {"url": url, "scheme": scheme, "host": host, "port": effective_port, "exposure": exposure,
                "addresses": [str(a) for a in addresses]}


def _is_ip(host: str) -> bool:
    return _ip_literal(host) is not None


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return None


def _ambiguous_ipv4(host: str) -> bool:
    """True for hosts that ``inet_aton`` accepts but are not canonical dotted quads."""
    if not re.fullmatch(r"[0-9a-fx.]+", host):
        return False
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def from_policy(data: dict[str, object], resolver: Resolver | None = None) -> UrlPolicy:
    ports = data.get("browser_https_ports") or [443, 8443]
    nets = data.get("browser_private_networks") or []
    return UrlPolicy(https_ports={int(p) for p in ports if str(p).isdigit()},  # type: ignore[union-attr]
                     private_networks=[str(n) for n in nets],  # type: ignore[union-attr]
                     resolver=resolver or system_resolver)
