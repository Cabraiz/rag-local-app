"""The addresses the agent connects to: a server's name never at a local or cloud metadata address.

ALLOWED_HOSTS allows names (runtime/adk.py, check_host). A name that resolves to a local or metadata
address (DNS rebinding, an /etc/hosts entry) is refused, and the addresses checked are the ones used:
`cli run` checks its spec's names before any request and keeps them for the run (pinned_names, used
by transpiler/live.py); under `adk run` or `adk web`, the first order checks the agent's own URLs the
same way and keeps them for the process (check_urls). httpx, the MCP SDK and ADK all resolve
through socket.getaddrinfo, which both replace.
"""
import contextlib
import ipaddress
import socket
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
    real, pins, before = socket.getaddrinfo, {}, (PINS, SCOPE)
    socket.getaddrinfo, PINS, SCOPE = pinning(pins, real), pins, 'run'
    try:
        yield pins
    finally:
        socket.getaddrinfo, (PINS, SCOPE) = real, before


def check_urls(urls):
    """Problems of the agent's own URLs, checked when an order starts outside `cli run` (which checked
    and pinned its spec's names before the run). The first order pins every name for the process; a
    name that did not resolve then is checked again by the next order, a name with addresses keeps them."""
    global PINS, SCOPE
    if SCOPE == 'run':
        return []
    if SCOPE is None:
        pins: dict[str, list[str]] = {}
        PROCESS.update(real=socket.getaddrinfo, pins=pins)
        socket.getaddrinfo = PROCESS['resolver'] = pinning(pins, PROCESS['real'])
        PINS, SCOPE = pins, 'process'
    problems, pins = [], PROCESS['pins']
    for url in dict.fromkeys(urls):
        resolved = resolve(url, PROCESS['real'])
        if resolved is None or pins.get(resolved[0]):
            continue
        host, addresses = resolved
        refused = [address for address in addresses if unsafe(address)]
        if refused:
            problems.append(f'{url}: "{host}" resolve para {", ".join(refused)}, {REFUSED}')
        pins[host] = [] if refused else addresses  # a refused name reaches no address in this process
    return problems


def unpin_process():
    """Undo check_urls' process-wide pin (tests: each one starts without it)."""
    global PINS, SCOPE
    if SCOPE != 'process':
        return
    if socket.getaddrinfo is PROCESS['resolver']:
        socket.getaddrinfo = PROCESS['real']
    PINS, SCOPE = None, None
    PROCESS.clear()
