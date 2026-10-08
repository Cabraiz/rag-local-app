"""Where the agent connects: a host that ALLOWED_HOSTS allows, at the address checked for its name.

ALLOWED_HOSTS allows names (check_host; the transpiler checks a spec's servers against the same list). A
name that resolves to a local or cloud metadata address (DNS rebinding, an /etc/hosts entry) is refused,
and the addresses checked are the ones used, for one `cli run` (pinned_names) or, under `adk run` and
`adk web`, for the process (check_urls). Only the runtime's own clients use them: ADK's toolsets, its REST
tool and the MCP SDK each take an httpx client factory, and each gets one here (client, mcp_client) whose
transport connects to the address checked, the name kept for the Host header and TLS.
"""
import contextlib
import ipaddress
import os
import re
import socket
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx
import httpx2  # the MCP SDK's httpx

# The hosts a server URL may name unless ALLOWED_HOSTS ("host" for any port, "host:port" for one) says
# otherwise. A spec cannot point the agent at another host or port (SSRF: 169.254.169.254, the internal
# network, localhost:2375); the deployment can.
DEFAULT_ALLOWED_HOSTS = 'ocr:8001,rag:8002,api:8000'
ALLOWED_HOST = re.compile(r'[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?')  # "host" or "host:port"
# Besides loopback, link-local (169.254.169.254, fe80::), 0.0.0.0 and multicast: unique local IPv6
# (fd00:ec2::254 is AWS's metadata), the shared 100.64.0.0/10 (100.100.100.200 is Alibaba Cloud's)
# and Azure's 168.63.129.16. Private IPv4 stays allowed: it is where Docker puts the compose services.
LOCAL_NETWORKS = [ipaddress.ip_network(network) for network in ('fc00::/7', '100.64.0.0/10', '168.63.129.16/32')]
REFUSED = ('um endereço local ou de metadados de nuvem (ex.: 127.0.0.1, 169.254.169.254, fd00:ec2::254); só um host '
           'escrito como esse endereço (IP ou localhost) e listado em ALLOWED_HOSTS pode apontar para ele')

PINS: dict[str, list[str]] | None = None  # name -> addresses checked, while names are pinned
SCOPE: str | None = None  # 'run' inside pinned_names() (cli run), 'process' after check_urls (adk run/web)
LOCK = threading.RLock()  # adk web runs orders, and toolsets, on several threads and loops at once


def allowed_hosts() -> list[str]:
    """ALLOWED_HOSTS, or the default when it names no host (unset, blank, only commas)."""
    hosts = [host.strip().lower() for host in os.environ.get('ALLOWED_HOSTS', '').split(',') if host.strip()]
    return hosts or DEFAULT_ALLOWED_HOSTS.split(',')


def host_and_port(url: str) -> tuple[str, int]:
    """The URL's host, lower case, and its port: the scheme's by default, 0 when it is not one (over 65535)."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = 0
    return (parts.hostname or '').lower(), (443 if parts.scheme == 'https' else 80) if port is None else port


def host_allowed(url: str, hosts: list[str]) -> bool:
    host, port = host_and_port(url)
    return urlsplit(url).scheme in ('http', 'https') and port > 0 and (host in hosts or f'{host}:{port}' in hosts)


def check_host(url: str) -> None:
    """Raise ValueError unless ALLOWED_HOSTS allows the URL: checked again where agent.py runs, which may be
    older than ALLOWED_HOSTS, edited by hand or run without the CLI."""
    hosts = allowed_hosts()
    if not host_allowed(url, hosts):
        host, port = host_and_port(url)
        raise ValueError(f'host "{host}:{port}" fora de ALLOWED_HOSTS ({",".join(hosts)}); '
                         'gere o agent.py de novo ou inclua o host em ALLOWED_HOSTS')


def unsafe(address: str) -> bool:
    """A local or cloud metadata address, also written as IPv4 inside IPv6."""
    ip = ipaddress.ip_address(address.split('%')[0])
    ip = getattr(ip, 'ipv4_mapped', None) or ip
    return (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
            or any(ip in network for network in LOCAL_NETWORKS if network.version == ip.version))


def host_of(url: str) -> str | None:
    """The URL's host as it is resolved, or None for a host written as its address (an IP, or localhost)."""
    host = (urlsplit(url).hostname or '').lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None if host == 'localhost' else host
    return None


@contextlib.contextmanager
def pinned_names() -> Iterator[dict[str, list[str]]]:
    """While inside (a `cli run`), the runtime's clients reach a name the run's check resolved at those
    addresses. A DNS answer that changes during the run (rebinding) is never used."""
    global PINS, SCOPE
    with LOCK:
        before, pins = (PINS, SCOPE), dict[str, list[str]]()
        PINS, SCOPE = pins, 'run'
    try:
        yield pins
    finally:
        with LOCK:
            PINS, SCOPE = before


def pin(urls: Iterable[str], pins: dict[str, list[str]]) -> dict[str, tuple[str, list[str]]]:
    """Pin each URL's name with no address yet to the addresses it resolves to now, or to none (no later
    answer, 127.0.0.1 after a rebinding, is used) when it does not resolve or one of them is local or of
    cloud metadata. url -> (its name, the addresses refused), for the names refused."""
    refused = {}
    for url in dict.fromkeys(urls):
        host = host_of(url)
        if host is None or pins.get(host):
            continue
        try:
            infos = socket.getaddrinfo(host, host_and_port(url)[1], type=socket.SOCK_STREAM)
        except OSError:
            infos = []
        addresses = sorted({str(info[4][0]) for info in infos})
        bad = [address for address in addresses if unsafe(address)]
        if bad:
            refused[url] = (host, bad)
        pins[host] = [] if bad else addresses  # a refused name reaches no address meanwhile
    return refused


def check_urls(urls: Iterable[str]) -> list[str]:
    """Problems of the agent's own URLs, outside `cli run` (which pinned its names already): when an order
    starts and before any toolset connects (runtime/adk.py). A name with addresses keeps them for the
    process (restart `adk web` if a service changes address); one without is checked again next time."""
    global PINS, SCOPE
    with LOCK:
        if SCOPE == 'run':
            return []
        if PINS is None or SCOPE is None:
            PINS, SCOPE = {}, 'process'
        return [f'{url}: "{host}" resolve para {", ".join(bad)}, {REFUSED}' for url, (host, bad) in pin(urls, PINS).items()]


def pinned(request: Any, refused: Callable[..., Exception]) -> Any:
    """The request sent to the first address checked for its host, the name kept for the Host header and
    TLS (SNI and the certificate), or itself when its host is not pinned. A name pinned to no address
    raises `refused`, the client's ConnectError."""
    host = request.url.host
    with LOCK:
        addresses = None if PINS is None else PINS.get(host.lower())
    if addresses is None:
        return request
    if not addresses:
        raise refused(f'"{host}" não resolveu no início da execução', request=request)
    return type(request)(request.method, request.url.copy_with(host=addresses[0]), headers=request.headers,
                         stream=request.stream, extensions={**request.extensions, 'sni_hostname': host})


class Pinned(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await super().handle_async_request(pinned(request, httpx.ConnectError))


class PinnedMcp(httpx2.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await super().handle_async_request(pinned(request, httpx2.ConnectError))


def client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=Pinned(), **kwargs)


def mcp_client(headers: dict[str, str] | None = None, timeout: httpx2.Timeout | None = None,
               auth: httpx2.Auth | None = None) -> httpx2.AsyncClient:
    """The MCP SDK's client (create_mcp_http_client, with its default timeouts) over the pinned addresses."""
    return httpx2.AsyncClient(headers=headers, timeout=timeout or httpx2.Timeout(30.0, read=300.0), auth=auth,
                              transport=PinnedMcp())
