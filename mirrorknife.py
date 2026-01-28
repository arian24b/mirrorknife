#!/usr/bin/env python3
"""
mirrorknife.py - stdlib-only Swiss-knife for:
  - DNS server health + "best DNS"
  - Mirror health + "best mirror" per ecosystem: Ubuntu/Debian APT, RHEL-family, PyPI (pip/uv), npm, Docker Registry
  - Docker registry introspection: _catalog + tags/list (if enabled)
  - Optional curses TUI

Cross-platform: macOS + Linux (Ubuntu/RPi). Uses system 'ping' (no raw sockets).

Refs:
- Docker registry /v2/ health: 200 or 401 is valid (auth challenge) and registry/2.0 header is expected.
- Docker registry API V2 _catalog and tags/list may be disabled by operators.
- PyPI mirrors should serve the Simple API at /simple/
- Debian/Ubuntu repositories contain dists/<suite>/Release
"""

from __future__ import annotations

import argparse
import curses
import json
import platform
import random
import re
import socket
import ssl
import struct
import subprocess
import time
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode
import urllib.request


DEFAULT_TIMEOUT = 6.0
DEFAULT_JOBS = 32

UBUNTU_SUITES = ["noble", "jammy", "focal"]
DEBIAN_SUITES = ["stable", "bookworm", "bullseye"]

# ------------------ Models ------------------


@dataclass
class Target:
    kind: str  # dns | docker | pypi | npm | ubuntu | debian | rhel
    name: str  # display name (mirror name or dns ip)
    base: str  # base URL (mirrors) or IP (dns)
    meta: Dict[str, str]  # extra knobs (dns domain, etc.)


@dataclass
class Result:
    kind: str
    name: str
    base: str
    ok: bool
    status: Optional[int]  # HTTP status (mirrors) or DNS rcode (dns)
    note: str
    probe: str  # URL or "dns"
    timings_ms: Dict[str, Optional[int]]


# ------------------ Small utils ------------------


def ms_now() -> int:
    return int(time.perf_counter() * 1000)


def is_macos() -> bool:
    return platform.system().lower() == "darwin"


def normalize_url(u: str) -> str:
    u = u.strip()
    if not u or u.startswith("#"):
        return ""
    if "://" not in u:
        u = "https://" + u
    return u.rstrip("/") + "/"


def join_url(base: str, path: str) -> str:
    if base.endswith("/") and path.startswith("/"):
        return base[:-1] + path
    if not base.endswith("/") and not path.startswith("/"):
        return base + "/" + path
    return base + path


def ping_avg_ms(host: str, count: int = 2) -> Optional[int]:
    """
    Uses system ping because raw ICMP requires privileges.
    macOS: ping -c 2 -W 1000 host    (W in ms)
    Linux: ping -c 2 -W 1 host       (W in seconds)
    """
    try:
        if is_macos():
            cmd = ["ping", "-c", str(count), "-W", "1000", host]
        else:
            cmd = ["ping", "-c", str(count), "-W", "1", host]

        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=6
        )
        out = p.stdout

        # macOS: round-trip min/avg/max/stddev = 9.123/10.456/...
        # Linux: rtt min/avg/max/mdev = 9.123/10.456/...
        m = re.search(r"=\s*([\d.]+)/([\d.]+)/", out)
        if not m:
            return None
        return int(float(m.group(2)))
    except Exception:
        return None


def load_lines(path: str) -> List[str]:
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    return out


def safe_addnstr(win, y: int, x: int, s: str, n: int, attr: int = 0) -> None:
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        # clamp length to remaining width
        n2 = min(n, max(0, w - x - 1))
        if n2 <= 0:
            return
        if attr:
            win.addnstr(y, x, s, n2, attr)
        else:
            win.addnstr(y, x, s, n2)
    except curses.error:
        pass


def safe_hline(win, y: int, x: int, ch: int, n: int) -> None:
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        n2 = min(n, max(0, w - x - 1))
        if n2 > 0:
            win.hline(y, x, ch, n2)
    except curses.error:
        pass


# ------------------ YAML-lite parser (mirrors_list.yaml) ------------------


def parse_mirrors_yaml_lite(path: str) -> List[dict]:
    """
    Supports a subset of YAML that matches your pasted format:

    mirrors:
      - name: X
        url: https://...
        packages:
          - Ubuntu
          - Debian
          - Docker Registry
          - PyPI
          - npm
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    mirrors: List[dict] = []
    cur: Optional[dict] = None
    in_packages = False

    for raw in lines:
        s = raw.rstrip("\n").strip()
        if not s or s.startswith("#"):
            continue

        if re.match(r"^mirrors\s*:\s*$", s):
            continue

        m = re.match(r"^-+\s*name\s*:\s*(.+)\s*$", s)
        if m:
            if cur:
                mirrors.append(cur)
            cur = {
                "name": m.group(1).strip().strip('"').strip("'"),
                "url": "",
                "packages": [],
            }
            in_packages = False
            continue

        if cur is None:
            continue

        m = re.match(r"^name\s*:\s*(.+)\s*$", s)
        if m:
            cur["name"] = m.group(1).strip().strip('"').strip("'")
            continue

        m = re.match(r"^url\s*:\s*(.+)\s*$", s)
        if m:
            cur["url"] = m.group(1).strip().strip('"').strip("'")
            continue

        if re.match(r"^packages\s*:\s*$", s):
            in_packages = True
            continue

        if in_packages:
            m = re.match(r"^-+\s*(.+)\s*$", s)
            if m:
                cur["packages"].append(m.group(1).strip().strip('"').strip("'"))
                continue
            if ":" in s:
                in_packages = False

    if cur:
        mirrors.append(cur)

    cleaned: List[dict] = []
    for m in mirrors:
        url = normalize_url(m.get("url", ""))
        if not url:
            continue
        pkgs = [p.strip() for p in m.get("packages", []) if p.strip()]
        cleaned.append({"name": m.get("name", url), "url": url, "packages": pkgs})
    return cleaned


def targets_from_mirror_yaml(path: str) -> List[Target]:
    mirrors = parse_mirrors_yaml_lite(path)
    out: List[Target] = []

    def has(pkg: str, pkgs: List[str]) -> bool:
        return any(p.lower() == pkg.lower() for p in pkgs)

    for m in mirrors:
        name = m["name"]
        url = m["url"]
        pkgs = m["packages"]

        if has("Docker Registry", pkgs):
            out.append(Target("docker", name, url, {}))
        if has("PyPI", pkgs):
            out.append(Target("pypi", name, url, {}))
        if has("npm", pkgs):
            out.append(Target("npm", name, url, {}))

        if has("Ubuntu", pkgs):
            out.append(Target("ubuntu", name, url, {}))
        if has("Debian", pkgs):
            out.append(Target("debian", name, url, {}))

        # RHEL-ish: CentOS/Rocky/Alma/EPEL -> treat as yum/dnf metadata
        if any(
            p.lower() in ("centos", "rocky linux", "almalinux", "epel", "fedora epel")
            for p in pkgs
        ):
            out.append(Target("rhel", name, url, {}))

    return out


# ------------------ DNS probing (UDP query + TCP connect + TCP query + ping) ------------------


def dns_build_query_a(domain: str, qid: int) -> bytes:
    # Header: ID, flags(standard query), QDCOUNT=1
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    # QNAME
    parts = domain.strip(".").split(".")
    qname = (
        b"".join(struct.pack("B", len(p)) + p.encode("ascii", "ignore") for p in parts)
        + b"\x00"
    )
    qtype = 1  # A
    qclass = 1  # IN
    return header + qname + struct.pack("!HH", qtype, qclass)


def dns_parse_rcode_and_answers(msg: bytes) -> Tuple[int, int]:
    if len(msg) < 12:
        return (99, 0)
    _id, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", msg[:12])
    rcode = flags & 0x000F
    return (rcode, an)


def dns_udp_query(
    server_ip: str, domain: str, timeout: float
) -> Tuple[Optional[int], Optional[int], str]:
    qid = random.randint(0, 65535)
    q = dns_build_query_a(domain, qid)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server_ip, 53))
        data, _ = s.recvfrom(4096)
        rcode, an = dns_parse_rcode_and_answers(data)
        if rcode == 0 and an > 0:
            return rcode, an, "OK"
        return rcode, an, f"rcode={rcode} answers={an}"
    except socket.timeout:
        return None, None, "UDP timeout"
    except Exception as e:
        return None, None, f"UDP error: {e}"
    finally:
        try:
            s.close()
        except Exception:
            pass


def dns_tcp_connect_ms(server_ip: str, timeout: float) -> Optional[int]:
    t0 = ms_now()
    try:
        s = socket.create_connection((server_ip, 53), timeout=timeout)
        s.close()
        return ms_now() - t0
    except Exception:
        return None


def dns_tcp_query(
    server_ip: str, domain: str, timeout: float
) -> Tuple[Optional[int], Optional[int], str]:
    qid = random.randint(0, 65535)
    q = dns_build_query_a(domain, qid)
    payload = struct.pack("!H", len(q)) + q  # TCP DNS length prefix

    try:
        s = socket.create_connection((server_ip, 53), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(payload)

        lp = s.recv(2)
        if len(lp) < 2:
            s.close()
            return None, None, "TCP short read (len)"
        (n,) = struct.unpack("!H", lp)

        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        s.close()

        if len(data) < 12:
            return None, None, "TCP short DNS msg"
        rcode, an = dns_parse_rcode_and_answers(data)
        if rcode == 0 and an > 0:
            return rcode, an, "OK"
        return rcode, an, f"rcode={rcode} answers={an}"
    except socket.timeout:
        return None, None, "TCP timeout"
    except Exception as e:
        return None, None, f"TCP error: {e}"


def dns_probe(ip: str, domain: str, timeout: float) -> Result:
    timings = {
        "udp_dns_ms": None,
        "tcp_53_ms": None,
        "tcp_dns_ms": None,
        "ping_ms": None,
    }

    t0 = ms_now()
    rcode, an, note_udp = dns_udp_query(ip, domain, timeout)
    if rcode is not None:
        timings["udp_dns_ms"] = ms_now() - t0

    timings["tcp_53_ms"] = dns_tcp_connect_ms(ip, timeout)

    t0 = ms_now()
    rcode2, an2, note_tcp = dns_tcp_query(ip, domain, timeout)
    if rcode2 is not None:
        timings["tcp_dns_ms"] = ms_now() - t0

    timings["ping_ms"] = ping_avg_ms(ip)

    ok = rcode == 0 and (an or 0) > 0
    status = rcode if rcode is not None else None
    note = f"UDP:{note_udp} | TCP:{note_tcp}"
    return Result("dns", ip, ip, ok, status, note, "dns", timings)


# ------------------ HTTP GET with split timings (dns/tcp/tls/ttfb/total) ------------------


def http_timed_get(
    url: str, timeout: float, insecure: bool
) -> Tuple[Optional[int], Dict[str, Optional[int]], str, Dict[str, str]]:
    """
    Split timings:
      dns_ms: getaddrinfo()
      tcp_ms: connect()
      tls_ms: handshake (https)
      ttfb_ms: time until first byte
      total_ms: until headers are read
    Returns: (status_code, timings, note, some_headers)
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if not host:
        return None, {}, "Bad URL (no host)", {}
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    timings: Dict[str, Optional[int]] = {
        "dns_ms": None,
        "tcp_ms": None,
        "tls_ms": None,
        "ttfb_ms": None,
        "total_ms": None,
    }
    headers_out: Dict[str, str] = {}

    t_total0 = ms_now()

    # DNS
    t0 = ms_now()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as e:
        timings["dns_ms"] = ms_now() - t0
        timings["total_ms"] = ms_now() - t_total0
        return None, timings, f"DNS error: {e}", {}
    timings["dns_ms"] = ms_now() - t0
    if not infos:
        timings["total_ms"] = ms_now() - t_total0
        return None, timings, "DNS returned no addresses", {}

    af, socktype, proto, canonname, sa = infos[0]

    # TCP connect
    t0 = ms_now()
    try:
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(sa)
    except Exception as e:
        timings["tcp_ms"] = ms_now() - t0
        timings["total_ms"] = ms_now() - t_total0
        return None, timings, f"TCP connect error: {e}", {}
    timings["tcp_ms"] = ms_now() - t0

    # TLS handshake
    if scheme == "https":
        t0 = ms_now()
        try:
            ctx = ssl.create_default_context()
            if insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        except Exception as e:
            timings["tls_ms"] = ms_now() - t0
            timings["total_ms"] = ms_now() - t_total0
            try:
                sock.close()
            except Exception:
                pass
            return None, timings, f"TLS error: {e}", {}
        timings["tls_ms"] = ms_now() - t0

    # Send request + measure TTFB
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: mirrorknife/1.0\r\n"
        f"Connection: close\r\n"
        f"Accept: */*\r\n\r\n"
    ).encode("ascii", "ignore")

    try:
        sock.sendall(req)
        t0 = ms_now()
        first = sock.recv(1)
        timings["ttfb_ms"] = ms_now() - t0
        if not first:
            timings["total_ms"] = ms_now() - t_total0
            sock.close()
            return None, timings, "No response", {}

        data = first
        while b"\r\n\r\n" not in data and len(data) < 128 * 1024:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
    except socket.timeout:
        try:
            sock.close()
        except Exception:
            pass
        timings["total_ms"] = ms_now() - t_total0
        return None, timings, "HTTP timeout", {}
    except Exception as e:
        try:
            sock.close()
        except Exception:
            pass
        timings["total_ms"] = ms_now() - t_total0
        return None, timings, f"HTTP error: {e}", {}

    timings["total_ms"] = ms_now() - t_total0

    # Parse status line + a few headers
    try:
        head, rest = data.split(b"\r\n", 1)
        status_line = head.decode("ascii", "ignore")
        m = re.match(r"HTTP/\d\.\d\s+(\d+)", status_line)
        if not m:
            return None, timings, f"Bad status line: {status_line[:80]}", {}

        header_block = rest.split(b"\r\n\r\n", 1)[0]
        for line in header_block.split(b"\r\n"):
            if b":" not in line:
                continue
            k, v = line.split(b":", 1)
            kk = k.decode("ascii", "ignore").strip().lower()
            vv = v.decode("utf-8", "ignore").strip()
            if kk in (
                "docker-distribution-api-version",
                "www-authenticate",
                "content-type",
                "location",
            ):
                headers_out[kk] = vv

        return int(m.group(1)), timings, "OK", headers_out
    except Exception as e:
        return None, timings, f"Parse error: {e}", {}


# ------------------ Mirror probes ------------------


def docker_probe(
    base: str, timeout: float, insecure: bool
) -> Tuple[bool, Optional[int], Dict[str, Optional[int]], str, str]:
    url = join_url(base, "v2/")
    st, tm, note, hdr = http_timed_get(url, timeout, insecure)
    # Healthy if 200 or 401 (auth challenge is normal)
    ok = st in (200, 401)
    extra = []
    if hdr.get("docker-distribution-api-version"):
        extra.append(f"api={hdr['docker-distribution-api-version']}")
    if hdr.get("www-authenticate"):
        extra.append("auth-challenge=yes")
    return ok, st, tm, (note + (" | " + " ".join(extra) if extra else "")).strip(), url


def pypi_probe(
    base: str, timeout: float, insecure: bool
) -> Tuple[bool, Optional[int], Dict[str, Optional[int]], str, str]:
    url = join_url(base, "simple/")
    st, tm, note, hdr = http_timed_get(url, timeout, insecure)
    ok = st == 200
    return ok, st, tm, note, url


def npm_probe(
    base: str, timeout: float, insecure: bool
) -> Tuple[bool, Optional[int], Dict[str, Optional[int]], str, str]:
    # npm ping often hits /-/ping or /~/ping depending on registry implementation
    candidates = ["-/ping", "~/ping", ""]
    last = (False, None, {}, "No candidates worked", base)
    for c in candidates:
        url = join_url(base, c) if c else base
        st, tm, note, hdr = http_timed_get(url, timeout, insecure)
        if st == 200:
            return True, st, tm, "OK", url
        last = (False, st, tm, f"{note} (tried {c or '/'})", url)
    return last


def apt_probe(
    base: str, kind: str, timeout: float, insecure: bool
) -> Tuple[bool, Optional[int], Dict[str, Optional[int]], str, str, str]:
    suites = UBUNTU_SUITES if kind == "ubuntu" else DEBIAN_SUITES
    # Some mirrors host multiple distros under the same domain
    subpaths = (
        ["", "ubuntu/", "debian/"] if kind == "ubuntu" else ["", "debian/", "ubuntu/"]
    )

    last = (False, None, {}, "No candidates", base, base)
    for sp in subpaths:
        base2 = join_url(base, sp) if sp else base
        for suite in suites:
            url = join_url(base2, f"dists/{suite}/Release")
            st, tm, note, hdr = http_timed_get(url, timeout, insecure)
            if st == 200:
                return (
                    True,
                    st,
                    tm,
                    f"OK (suite={suite}, subpath={sp or '/'})",
                    url,
                    base2,
                )
            last = (
                False,
                st,
                tm,
                f"{note} (suite={suite}, subpath={sp or '/'})",
                url,
                base2,
            )
    return last


def rhel_probe(
    base: str, timeout: float, insecure: bool
) -> Tuple[bool, Optional[int], Dict[str, Optional[int]], str, str, str]:
    roots = ["", "centos/", "rocky/", "almalinux/"]
    candidates = [
        "repodata/repomd.xml",
        "BaseOS/repodata/repomd.xml",
        "AppStream/repodata/repomd.xml",
        "os/repodata/repomd.xml",
        "x86_64/os/repodata/repomd.xml",
        "aarch64/os/repodata/repomd.xml",
        "9/BaseOS/x86_64/os/repodata/repomd.xml",
        "9/AppStream/x86_64/os/repodata/repomd.xml",
    ]
    last = (False, None, {}, "No candidates", base, base)
    for r in roots:
        base2 = join_url(base, r) if r else base
        for c in candidates:
            url = join_url(base2, c)
            st, tm, note, hdr = http_timed_get(url, timeout, insecure)
            if st == 200:
                return True, st, tm, f"OK ({r or '/'}{c})", url, base2
            last = (False, st, tm, f"{note} ({r or '/'}{c})", url, base2)
    return last


# ------------------ Docker introspection (catalog + tags) ------------------


def _urllib_json_get(
    url: str, timeout: float, insecure: bool
) -> Tuple[Optional[int], Optional[dict], str]:
    ctx = None
    if insecure and url.startswith("https://"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "mirrorknife/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = getattr(resp, "status", None)
            body = resp.read(1024 * 1024)
        return code, json.loads(body.decode("utf-8", "ignore")), "OK"
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTPError {e.code}"
    except Exception as e:
        return None, None, f"Error: {e}"


def docker_catalog(
    base: str, timeout: float, insecure: bool, n: int
) -> Tuple[List[str], str]:
    url = join_url(base, "v2/_catalog?" + urlencode({"n": str(n)}))
    code, data, note = _urllib_json_get(url, timeout, insecure)
    if code != 200 or not isinstance(data, dict):
        return [], f"{note} (catalog may be disabled)"
    repos = data.get("repositories", [])
    if isinstance(repos, list):
        return [str(x) for x in repos], "OK"
    return [], "Unexpected catalog format"


def docker_tags(
    base: str, repo: str, timeout: float, insecure: bool
) -> Tuple[List[str], str]:
    url = join_url(base, f"v2/{repo}/tags/list")
    code, data, note = _urllib_json_get(url, timeout, insecure)
    if code != 200 or not isinstance(data, dict):
        return [], note
    tags = data.get("tags") or []
    if isinstance(tags, list):
        return [str(x) for x in tags], "OK"
    return [], "Unexpected tags format"


# ------------------ Runner + scoring ------------------


def primary_latency(r: Result) -> int:
    if r.kind == "dns":
        return r.timings_ms.get("udp_dns_ms") or 10**9
    return r.timings_ms.get("total_ms") or 10**9


def probe_one(t: Target, timeout: float, insecure: bool) -> Result:
    if t.kind == "dns":
        return dns_probe(t.base, t.meta.get("domain", "google.com"), timeout)

    if t.kind == "docker":
        ok, st, tm, note, url = docker_probe(t.base, timeout, insecure)
        return Result("docker", t.name, t.base, ok, st, note, url, tm)

    if t.kind == "pypi":
        ok, st, tm, note, url = pypi_probe(t.base, timeout, insecure)
        return Result("pypi", t.name, t.base, ok, st, note, url, tm)

    if t.kind == "npm":
        ok, st, tm, note, url = npm_probe(t.base, timeout, insecure)
        return Result("npm", t.name, t.base, ok, st, note, url, tm)

    if t.kind in ("ubuntu", "debian"):
        ok, st, tm, note, url, effective_base = apt_probe(
            t.base, t.kind, timeout, insecure
        )
        return Result(
            t.kind,
            t.name,
            effective_base,
            ok,
            st,
            note + f" | effective_base={effective_base}",
            url,
            tm,
        )

    if t.kind == "rhel":
        ok, st, tm, note, url, effective_base = rhel_probe(t.base, timeout, insecure)
        return Result(
            "rhel",
            t.name,
            effective_base,
            ok,
            st,
            note + f" | effective_base={effective_base}",
            url,
            tm,
        )

    return Result(t.kind, t.name, t.base, False, None, "Unknown kind", "", {})


def run_checks(
    targets: List[Target], jobs: int, timeout: float, insecure: bool
) -> List[Result]:
    out: List[Result] = []
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(probe_one, t, timeout, insecure) for t in targets]
        for f in as_completed(futs):
            out.append(f.result())

    out.sort(key=lambda r: (not r.ok, primary_latency(r), r.kind, r.name))
    return out


def pick_best(results: List[Result], kind: str) -> Optional[Result]:
    cands = [r for r in results if r.kind == kind and r.ok]
    if not cands:
        return None
    cands.sort(key=lambda r: primary_latency(r))
    return cands[0]


# ------------------ Output helpers ------------------


def print_table(
    results: List[Result], kinds: Optional[List[str]] = None, limit: int = 50
) -> None:
    rows = results
    if kinds:
        ks = set(k.strip().lower() for k in kinds if k.strip())
        rows = [r for r in rows if r.kind.lower() in ks]

    print(f"{'OK':<3} {'KIND':<7} {'LAT':>7}  {'NAME':<24}  {'BASE/ADDR'}")
    print("-" * 90)
    for r in rows[:limit]:
        ok = "✅" if r.ok else "❌"
        lat = primary_latency(r)
        lat_txt = f"{lat}ms" if lat < 10**9 else "--"
        name = (r.name[:24] + "…") if len(r.name) > 24 else r.name
        print(f"{ok:<3} {r.kind:<7} {lat_txt:>7}  {name:<24}  {r.base}")


def print_best_snippets(results: List[Result], dns_domain: str) -> None:
    best_dns = pick_best(results, "dns")
    best_ubuntu = pick_best(results, "ubuntu")
    best_debian = pick_best(results, "debian")
    best_rhel = pick_best(results, "rhel")
    best_pypi = pick_best(results, "pypi")
    best_npm = pick_best(results, "npm")
    best_docker = pick_best(results, "docker")

    print("\n=== BEST PICKS ===")
    if best_dns:
        print(
            f"DNS best for {dns_domain}: {best_dns.name}  (udp_dns={best_dns.timings_ms.get('udp_dns_ms')}ms, ping={best_dns.timings_ms.get('ping_ms')}ms)"
        )
    else:
        print(f"DNS best for {dns_domain}: (none healthy)")

    def show(kind: str, r: Optional[Result]):
        if r:
            print(
                f"{kind:>6}: {r.name}  base={r.base}  total={r.timings_ms.get('total_ms')}ms"
            )
        else:
            print(f"{kind:>6}: (none healthy)")

    show("pypi", best_pypi)
    show(" npm", best_npm)
    show("dock", best_docker)
    show("ubun", best_ubuntu)
    show("debi", best_debian)
    show("rhel", best_rhel)

    print("\n=== COPY/PASTE CONFIG SNIPPETS ===")

    if best_pypi:
        base = best_pypi.base.rstrip("/")
        print("\n# pip (index url)")
        print(f"pip config set global.index-url {base}/simple")
        print("\n# uv (env var)")
        print(f"export UV_INDEX_URL={base}/simple")
        print("\n# uv (pyproject.toml)")
        print('[tool.uv.pip]\nindex-url = "' + f"{base}/simple" + '"')

    if best_npm:
        b = best_npm.base.rstrip("/") + "/"
        print("\n# npm")
        print(f"npm config set registry {b}")

    if best_docker:
        b = best_docker.base.rstrip("/")
        print("\n# Docker daemon.json")
        print('{\n  "registry-mirrors": ["' + f"{b}" + '"]\n}')

    if best_ubuntu:
        print("\n# Ubuntu APT base (use effective_base):")
        print(best_ubuntu.base)

    if best_debian:
        print("\n# Debian APT base (use effective_base):")
        print(best_debian.base)

    if best_dns:
        d = best_dns.name
        print("\n# DNS examples")
        print(f'# macOS: networksetup -setdnsservers "Wi-Fi" {d}')
        print(
            "# Linux: edit /etc/resolv.conf or use resolvectl/systemd-resolved depending on distro"
        )


# ------------------ Commands: dns / mirrors / docker / tui ------------------


def cmd_dns(args: argparse.Namespace) -> int:
    servers = load_lines(args.servers)
    domain = args.domain
    targets = [Target("dns", ip, ip, {"domain": domain}) for ip in servers]
    results = run_checks(targets, args.jobs, args.timeout, args.insecure)
    print_table(results, kinds=["dns"], limit=args.limit)
    if args.best or args.snippets:
        print_best_snippets(results, domain)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
    return 0


def cmd_mirrors(args: argparse.Namespace) -> int:
    targets = targets_from_mirror_yaml(args.config)

    # optional filter by kinds
    if args.kinds:
        ks = set(k.strip().lower() for k in args.kinds.split(",") if k.strip())
        targets = [t for t in targets if t.kind.lower() in ks]

    results = run_checks(targets, args.jobs, args.timeout, args.insecure)
    print_table(results, kinds=None, limit=args.limit)

    if args.best or args.snippets:
        # domain only matters for DNS; pass a dummy string here
        print_best_snippets(results, dns_domain="(n/a)")

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
    return 0


def cmd_docker(args: argparse.Namespace) -> int:
    base = normalize_url(args.base)
    if not base:
        print("Bad --base")
        return 2

    if args.action == "ping":
        ok, st, tm, note, url = docker_probe(base, args.timeout, args.insecure)
        r = Result("docker", base, base, ok, st, note, url, tm)
        print_table([r], limit=10)
        return 0 if ok else 1

    if args.action == "catalog":
        repos, note = docker_catalog(base, args.timeout, args.insecure, args.n)
        print(f"Catalog from {base} -> {note}")
        for r in repos:
            print(" -", r)
        return 0 if repos else 1

    if args.action == "tags":
        tags, note = docker_tags(base, args.timeout, args.insecure, args.repo)
        print(f"Tags for {args.repo} @ {base} -> {note}")
        for t in tags:
            print(" -", t)
        return 0 if tags else 1

    return 2


def cmd_tui(args: argparse.Namespace) -> int:
    targets: List[Target] = []
    if args.config:
        targets += targets_from_mirror_yaml(args.config)
    if args.servers:
        for ip in load_lines(args.servers):
            targets.append(Target("dns", ip, ip, {"domain": args.domain}))

    if args.kinds:
        ks = set(k.strip().lower() for k in args.kinds.split(",") if k.strip())
        targets = [t for t in targets if t.kind.lower() in ks]

    if not targets:
        print("No targets: provide --config and/or --servers")
        return 2

    curses.wrapper(
        _tui_main, targets, args.jobs, args.timeout, args.insecure, args.domain
    )
    return 0


def cmd_dns_live(args: argparse.Namespace) -> int:
    servers = load_lines(args.servers)
    domain = args.domain
    interval = args.interval
    targets = [Target("dns", ip, ip, {"domain": domain}) for ip in servers]

    def ui(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.timeout(150)

        results_map: Dict[str, Result] = {}
        last_run = 0.0
        running = False

        while True:
            now = time.time()

            # kick a run periodically (or first time)
            if (not running) and (now - last_run >= interval):
                running = True
                last_run = now
                results_map = {
                    t.name: Result(
                        "dns",
                        t.name,
                        t.base,
                        False,
                        None,
                        "pending",
                        "dns",
                        {
                            "udp_dns_ms": None,
                            "tcp_53_ms": None,
                            "tcp_dns_ms": None,
                            "ping_ms": None,
                        },
                    )
                    for t in targets
                }

                # run probes in background threads
                ex = ThreadPoolExecutor(max_workers=args.jobs)
                futures = {
                    ex.submit(probe_one, t, args.timeout, args.insecure): t.name
                    for t in targets
                }

            # consume finished futures (if any)
            if running:
                done_any = False
                for f in list(futures.keys()):
                    if f.done():
                        done_any = True
                        name = futures.pop(f)
                        try:
                            results_map[name] = f.result()
                        except Exception as e:
                            results_map[name] = Result(
                                "dns",
                                name,
                                name,
                                False,
                                None,
                                f"error: {e}",
                                "dns",
                                {
                                    "udp_dns_ms": None,
                                    "tcp_53_ms": None,
                                    "tcp_dns_ms": None,
                                    "ping_ms": None,
                                },
                            )
                if done_any and not futures:
                    ex.shutdown(wait=False, cancel_futures=True)
                    running = False

            # draw
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            safe_addnstr(
                stdscr,
                0,
                0,
                f"DNS LIVE | domain={domain} | interval={interval}s | r=run now | q=quit",
                w - 1,
            )
            safe_hline(stdscr, 1, 0, ord("-"), w)

            header = "OK  IP               UDP(ms) TCP53(ms) TCPDNS(ms) PING(ms)  NOTE"
            safe_addnstr(stdscr, 2, 0, header, w - 1)

            rows = sorted(
                results_map.values(),
                key=lambda r: (
                    not r.ok,
                    r.timings_ms.get("udp_dns_ms") or 10**9,
                    r.name,
                ),
            )
            max_rows = max(0, h - 4)
            for i, r in enumerate(rows[:max_rows]):
                ok = "✅" if r.ok else "❌"
                udp = r.timings_ms.get("udp_dns_ms")
                tcp53 = r.timings_ms.get("tcp_53_ms")
                tcpdns = r.timings_ms.get("tcp_dns_ms")
                ping = r.timings_ms.get("ping_ms")
                note = r.note

                line = f"{ok}  {r.name:<15} {str(udp or '--'):>7} {str(tcp53 or '--'):>8} {str(tcpdns or '--'):>9} {str(ping or '--'):>8}  {note}"
                safe_addnstr(stdscr, 3 + i, 0, line, w - 1)

            stdscr.refresh()

            # keys
            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                return
            if ch == ord("r"):
                # force immediate re-run
                last_run = 0.0

    curses.wrapper(ui)
    return 0


def _tui_main(
    stdscr,
    targets: List[Target],
    jobs: int,
    timeout: float,
    insecure: bool,
    dns_domain: str,
):
    curses.curs_set(0)
    stdscr.nodelay(False)

    results = run_checks(targets, jobs, timeout, insecure)
    selected = 0
    filter_txt = ""

    def filtered() -> List[Result]:
        if not filter_txt:
            return results
        ft = filter_txt.lower()
        return [
            r
            for r in results
            if ft in r.kind.lower()
            or ft in r.name.lower()
            or ft in r.base.lower()
            or ft in r.note.lower()
        ]

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        mid = max(48, w // 2)

        left = filtered()
        if selected >= len(left):
            selected = max(0, len(left) - 1)

        header = f"mirrorknife TUI | r=refresh  /=filter  e=export  b=best  q=quit | dns-domain={dns_domain}"
        stdscr.keypad(True)
        stdscr.timeout(150)
        stdscr.addnstr(0, 0, header, w - 1)
        stdscr.addnstr(0, max(0, w - 28), f"filter:{filter_txt}", 27)
        stdscr.hline(1, 0, ord("-"), w)

        max_rows = h - 4
        for i, r in enumerate(left[:max_rows]):
            mark = ">" if i == selected else " "
            status = "OK " if r.ok else "BAD"
            lat = primary_latency(r)
            lat_txt = f"{lat}ms" if lat < 10**9 else "--"
            line = f"{mark} {status} {r.kind:<7} {lat_txt:>7}  {r.name}"
            stdscr.addnstr(2 + i, 0, line, mid - 1)

        if left:
            r = left[selected]
            x0 = mid + 1
            stdscr.addnstr(2, x0, "Details", w - x0 - 2)
            stdscr.hline(3, x0, ord("-"), w - x0 - 1)

            details = [
                f"Type:   {r.kind}",
                f"Name:   {r.name}",
                f"Base:   {r.base}",
                f"Probe:  {r.probe}",
                f"Status: {r.status}",
                f"OK:     {r.ok}",
                f"Note:   {r.note}",
                "",
                "Timings (ms):",
            ]
            for k, v in r.timings_ms.items():
                details.append(f"  {k}: {v}")

            y = 4
            for d in details:
                if y >= h - 1:
                    break
                stdscr.addnstr(y, x0, d, w - x0 - 2)
                y += 1

        stdscr.hline(h - 2, 0, ord("-"), w)
        stdscr.addnstr(
            h - 1,
            0,
            f"Targets: {len(targets)}  Results: {len(results)}  Showing: {len(left)}",
            w - 1,
        )
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        if ch == ord("r"):
            results = run_checks(targets, jobs, timeout, insecure)
            selected = 0
            continue
        if ch == curses.KEY_DOWN:
            selected = min(selected + 1, max(0, len(left) - 1))
            continue
        if ch == curses.KEY_UP:
            selected = max(selected - 1, 0)
            continue
        if ch == ord("/"):
            curses.echo()
            stdscr.addnstr(0, max(0, w - 28), " " * 27, 27)
            stdscr.addnstr(0, max(0, w - 28), "filter:", 7)
            stdscr.move(0, max(0, w - 21))
            filter_txt = (
                stdscr.getstr(0, max(0, w - 21), 20).decode("utf-8", "ignore").strip()
            )
            curses.noecho()
            selected = 0
            continue
        if ch == ord("e"):
            with open("mirrorknife_report.json", "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)
            continue
        if ch == ord("b"):
            bests = {
                "dns": pick_best(results, "dns"),
                "ubuntu": pick_best(results, "ubuntu"),
                "debian": pick_best(results, "debian"),
                "rhel": pick_best(results, "rhel"),
                "pypi": pick_best(results, "pypi"),
                "npm": pick_best(results, "npm"),
                "docker": pick_best(results, "docker"),
            }
            lines = ["BEST PICKS:"]
            for k, v in bests.items():
                if v:
                    lines.append(f"- {k}: {v.name}  base={v.base}")
                else:
                    lines.append(f"- {k}: (none)")

            box_h = min(h - 4, len(lines) + 4)
            box_w = min(w - 4, max(len(x) for x in lines) + 4)
            top = (h - box_h) // 2
            leftx = (w - box_w) // 2

            stdscr.attron(curses.A_REVERSE)
            for yy in range(top, top + box_h):
                stdscr.addnstr(yy, leftx, " " * (box_w - 1), box_w - 1)
            stdscr.attroff(curses.A_REVERSE)
            for i, line in enumerate(lines):
                stdscr.addnstr(top + 2 + i, leftx + 2, line, box_w - 4)
            stdscr.addnstr(top + box_h - 2, leftx + 2, "press any key", box_w - 4)
            stdscr.refresh()
            stdscr.getch()
            continue


# ------------------ CLI wiring ------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mirrorknife", description="DNS + mirror health swiss-knife (stdlib-only)"
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="network timeout seconds (default 6)",
    )
    p.add_argument(
        "--jobs", type=int, default=DEFAULT_JOBS, help="parallel workers (default 32)"
    )
    p.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (not recommended)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # dns
    pd = sub.add_parser("dns", help="check DNS servers + pick best")
    pd.add_argument(
        "--servers", default="DNSs.txt", help="dns servers file (one IP per line)"
    )
    pd.add_argument(
        "--domain", default="google.com", help="domain to resolve for testing"
    )
    pd.add_argument("--limit", type=int, default=50, help="print N rows")
    pd.add_argument(
        "--best", action="store_true", help="print best DNS + helpful config snippets"
    )
    pd.add_argument(
        "--snippets",
        action="store_true",
        help="print config snippets too (same as --best)",
    )
    pd.add_argument("--json", action="store_true", help="print full JSON results")
    pd.set_defaults(func=cmd_dns)

    pl = sub.add_parser("dns-live", help="live DNS table (curses)")
    pl.add_argument(
        "--servers", default="DNSs.txt", help="dns servers file (one IP per line)"
    )
    pl.add_argument(
        "--domain", default="google.com", help="domain to resolve for testing"
    )
    pl.add_argument(
        "--interval", type=int, default=15, help="seconds between test rounds"
    )
    pl.set_defaults(func=cmd_dns_live)

    # mirrors
    pm = sub.add_parser("mirrors", help="check mirrors from YAML + pick best per kind")
    pm.add_argument(
        "--config",
        default="mirrors_list.yaml",
        help="mirror YAML file (YAML-lite parser)",
    )
    pm.add_argument(
        "--kinds",
        default="",
        help="comma list filter: ubuntu,debian,rhel,pypi,npm,docker",
    )
    pm.add_argument("--limit", type=int, default=80, help="print N rows")
    pm.add_argument(
        "--best", action="store_true", help="print best per kind + config snippets"
    )
    pm.add_argument(
        "--snippets",
        action="store_true",
        help="print config snippets too (same as --best)",
    )
    pm.add_argument("--json", action="store_true", help="print full JSON results")
    pm.set_defaults(func=cmd_mirrors)

    # docker
    pk = sub.add_parser("docker", help="docker registry tools: ping / catalog / tags")
    pk.add_argument(
        "--base",
        required=True,
        help="registry base URL, e.g. https://registry.example.com/",
    )
    dk = pk.add_subparsers(dest="action", required=True)

    dkp = dk.add_parser("ping", help="health-check registry via /v2/")
    dkp.set_defaults(func=cmd_docker)

    dkc = dk.add_parser(
        "catalog", help="list repositories via /v2/_catalog (if enabled)"
    )
    dkc.add_argument("--n", type=int, default=50, help="max repos to request")
    dkc.set_defaults(func=cmd_docker)

    dkt = dk.add_parser(
        "tags", help="list tags for a repository via /v2/<repo>/tags/list"
    )
    dkt.add_argument(
        "--repo", required=True, help="repository name, e.g. library/alpine"
    )
    dkt.set_defaults(func=cmd_docker)

    # tui
    pt = sub.add_parser("tui", help="interactive curses UI over DNS + mirrors")
    pt.add_argument("--config", default="mirrors_list.yaml", help="mirror YAML file")
    pt.add_argument("--servers", default="DNSs.txt", help="dns servers file")
    pt.add_argument("--domain", default="google.com", help="dns test domain")
    pt.add_argument("--kinds", default="", help="comma list filter kinds")
    pt.set_defaults(func=cmd_tui)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
