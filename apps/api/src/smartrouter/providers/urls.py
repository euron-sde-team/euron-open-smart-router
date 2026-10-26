"""Endpoint URL checks: a base URL is something an admin types, and the API and worker then
make requests to it with a sealed credential attached. Without a check, that turns the API
into a proxy for cloud metadata endpoints (instance credentials) and other internal hosts.

Always refused: link-local addresses (169.254.0.0/16, fe80::/10, which include the AWS, GCP
and Azure metadata endpoints), the AWS IPv6 metadata address, multicast, unspecified and
reserved addresses. Loopback and private networks are allowed by default because local
models (Ollama, vLLM inside the VPC) are a core use; set ENDPOINT_ALLOW_PRIVATE=false to
refuse them too.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from smartrouter.core.errors import AppError

AWS_IPV6_METADATA = ipaddress.ip_address("fd00:ec2::254")
# Shared address space (carrier-grade NAT): internal, but Python does not call it private.
CGNAT = ipaddress.ip_network("100.64.0.0/10")


class EndpointUrlError(AppError):
    code = "endpoint_url_blocked"


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_private: bool) -> str | None:
    if ip.is_link_local or ip == AWS_IPV6_METADATA:
        return "a link-local or cloud metadata address"
    if ip.is_unspecified or ip.is_multicast:
        return "an unspecified or multicast address"
    # Before the reserved check: Python counts ::1 (IPv6 loopback) as reserved, and 0.0.0.0
    # as private, so the order of these checks matters.
    if ip.is_loopback or ip.is_private or (ip.version == 4 and ip in CGNAT):
        if not allow_private:
            return "a private or loopback address (ENDPOINT_ALLOW_PRIVATE is false)"
        return None
    if ip.is_reserved:
        return "a reserved address"
    return None


async def check_endpoint_url(url: str, *, allow_private: bool) -> None:
    host = urlsplit(url).hostname
    if not host:
        raise EndpointUrlError(
            "The base URL has no host. Use a full URL such as https://api.example.com/v1"
        )
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EndpointUrlError(
            f"Cannot resolve {host}. Check the host name, or that the API can reach it."
        ) from exc
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if reason := _blocked(ip, allow_private):
            raise EndpointUrlError(
                f"{host} resolves to {ip}, {reason}. Endpoints cannot point there."
            )
