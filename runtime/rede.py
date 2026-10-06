"""The addresses the agent connects to: a server's name never at a local or cloud metadata address.

ALLOWED_HOSTS allows names (runtime/adk.py, check_host). A name that resolves to a local or metadata
address (DNS rebinding, an /etc/hosts entry) is refused, and the addresses checked are the ones used:
`cli run` checks its spec's names before any request and keeps them for the run (pinned_names, used
by transpiler/live.py); under `adk run` or `adk web`, the first order checks the agent's own URLs the
same way and keeps them for the process (check_urls; every toolset runs it before it connects).

Why socket.getaddrinfo, process-wide: the connections are made by clients the agent does not own (the
MCP SDK, ADK's RestApiTool, httpx), none of which takes a resolver. The pin answers only for the spec's
names, for one run (`cli run`) or the process (`adk run`, `adk web`), and changes only under LOCK.
"""
import contextlib
import ipaddress
import socket
import threading
from urllib.parse import urlsplit

# Besides loopback, link-local (169.254.169.254, fe80::), 0.0.0.0 and multicast: unique local IPv6
# (fd00:ec2::254 is AWS's metadata), the shared 100.64.0.0/10 (100.100.100.200 is Alibaba Cloud's)
# and Azure's 168.63.129.16. Private IPv4 stays allowed: it is where Docker puts the compose services.
LOCAL_NETWORKS = [ipaddress.ip_network(network) for network in ('fc00::/7', '100.64.0.0/10', '168.63.129.16/32')]
REFUSED = ('um endereço local ou de metadados de nuvem (ex.: 127.0.0.1, 169.254.169.254, fd00:ec2::254); só um host '
           'escrito como esse endereço (IP ou localhost) e listado em ALLOWED_HOSTS pode apontar para ele')

PINS: dict[str, list[str]] | None = None  # name -> addresses checked, while names are pinned
SCOPE: str | None = None  # 'run' inside pinned_names() (cli run), 'process' after check_urls (adk run/web)
PROCESS: dict = {}  # the process-wide pin: its resolver, the one it replaced, its names
LOCK = threading.RLock()  # adk web runs orders, and toolsets, on several threads and loops at once


def unsafe(address):
    """A local or cloud metadata address, also written as IPv4 inside IPv6."""
    ip = ipaddress.ip_address(address.split('%')[0])
    ip = getattr(ip, 'ipv4_mapped', None) or ip
    return (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
            or any(ip in network for network in LOCAL_NETWORKS if network.version == ip.version))


def is_address(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def resolve(url, lookup=None):
    """(host, sorted addresses) of the URL's name, or None for a host written as its address (an IP, or
    localhost), which is not resolved. A name that does not resolve has no address."""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if host == 'localhost' or is_address(host):
        return None
    try:
        infos = (lookup or socket.getaddrinfo)(host, parts.port or (443 if parts.scheme == 'https' else 80),
                                               type=socket.SOCK_STREAM)
    except OSError:  # pinned to no address: the request that follows fails as "server down", and no
        infos = []    # later answer (127.0.0.1, after a rebinding) is used
    return host, sorted({str(info[4][0]) for info in infos})


def pinning(pins, real):
    """socket.getaddrinfo that answers a pinned name with its addresses (with the port each lookup asks)."""
    def getaddrinfo(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        addresses = pins.get((name or '').lower())
        if addresses is None:
            return real(host, port, *args, **kwargs)
        if not addresses:
            raise socket.gaierror(socket.EAI_NONAME, f'"{name}" não resolveu no início da execução')
        return [info for address in addresses for info in real(address, port, *args, **kwargs)]
    return getaddrinfo


@contextlib.contextmanager
def pinned_names():
    """While inside (a `cli run`), a name that the run's check resolved keeps those addresses for every
    client of the process. A DNS answer that changes during the run (rebinding) is never used."""
    global PINS, SCOPE
    with LOCK:
        real, pins, before = socket.getaddrinfo, {}, (PINS, SCOPE)
        socket.getaddrinfo, PINS, SCOPE = pinning(pins, real), pins, 'run'
    try:
        yield pins
    finally:
        with LOCK:
            socket.getaddrinfo, (PINS, SCOPE) = real, before


def host_of(url):
    """The URL's host as it is resolved, or None for a host written as its address (an IP, or localhost)."""
    host = (urlsplit(url).hostname or '').lower()
    return None if host == 'localhost' or is_address(host) else host


def check_urls(urls):
    """Problems of the agent's own URLs, checked outside `cli run` (which checked and pinned its spec's
    names before the run) when an order starts and before any toolset connects (runtime/adk.py). The
    first check pins every name for the process: the addresses checked are the ones every connection
    uses. A name that did not resolve, or that was refused, is checked again the next time; a name
    with addresses keeps them until the process ends (restart `adk web` if a service changes address)."""
    global PINS, SCOPE
    with LOCK:
        if SCOPE == 'run':
            return []
        if SCOPE is None:
            pins: dict[str, list[str]] = {}
            PROCESS.update(real=socket.getaddrinfo, pins=pins)
            socket.getaddrinfo = PROCESS['resolver'] = pinning(pins, PROCESS['real'])
            PINS, SCOPE = pins, 'process'
        problems, pins = [], PROCESS['pins']
        for url in dict.fromkeys(urls):
            host = host_of(url)
            if host is None or pins.get(host):
                continue
            _, addresses = resolve(url, PROCESS['real'])
            refused = [address for address in addresses if unsafe(address)]
            if refused:
                problems.append(f'{url}: "{host}" resolve para {", ".join(refused)}, {REFUSED}')
            pins[host] = [] if refused else addresses  # a refused name reaches no address meanwhile
        return problems


def unpin_process():
    """Undo check_urls' process-wide pin (tests: each one starts without it). Its resolver is put back
    only if nothing replaced it since; otherwise it is left in place with no pins, so it only passes
    each lookup on to the resolver it wrapped."""
    global PINS, SCOPE
    with LOCK:
        if SCOPE != 'process':
            return
        PROCESS['pins'].clear()
        if socket.getaddrinfo is PROCESS['resolver']:
            socket.getaddrinfo = PROCESS['real']
        PINS, SCOPE = None, None
        PROCESS.clear()
