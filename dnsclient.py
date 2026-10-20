# -*- coding: utf-8 -*-
"""Risoluzione DNS non filtrata per aggirare i DNS dell'ISP.

Molti provider filtrano o avvelenano le risposte DNS verso gli indici
torrent: il sito esiste ma "non risolve". Qui le richieste HTTP/HTTPS vengono
indirizzate agli IP ottenuti da resolver pubblici, **mantenendo la verifica del
certificato TLS** sul nome del sito (SNI e hostname check invariati).

Ordine dei tentativi per un nome:
  1. DNS over HTTPS: Cloudflare (1.1.1.1) e poi Google (8.8.8.8)
  2. DNS classico UDP verso i server pubblici configurati (default 1.1.1.1, 8.8.8.8)
  3. resolver di sistema (ultima risorsa)

Uso: chiamare configure(...) e install() una volta all'avvio. Da quel momento
urllib.request.urlopen usa gli indirizzi risolti qui.
"""
from __future__ import annotations

import http.client
import json
import random
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request

USER_AGENT = "MagnetScout/1.0 (+resolver DNS locale)"
DOH_ENDPOINTS = (
    ("cloudflare", "https://cloudflare-dns.com/dns-query"),
    ("google", "https://dns.google/resolve"),
)
DOH_HOSTS = frozenset({"cloudflare-dns.com", "dns.google", "one.one.one.one"})
DEFAULT_UDP_SERVERS = ("1.1.1.1", "8.8.8.8")
MODES = ("auto", "doh", "udp", "off")

CACHE_TTL = 600
FAILURE_TTL = 30
DOH_TIMEOUT = 6
UDP_TIMEOUT = 4

_SYSTEM_URLOPEN = urllib.request.urlopen


class DnsError(Exception):
    """Risoluzione non riuscita."""


_mode = "auto"
_udp_servers = list(DEFAULT_UDP_SERVERS)
_cache: dict[str, tuple[float, list[str], str]] = {}
_lock = threading.RLock()
_opener = None
_counters = {"doh": 0, "udp": 0, "system": 0, "cache": 0, "failed": 0}


# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
def configure(mode: str = "auto", udp_servers=None) -> None:
    global _mode, _udp_servers
    mode = (mode or "auto").strip().lower()
    if mode not in MODES:
        raise ValueError(f"modalita' DNS non valida: {mode} (usa {', '.join(MODES)})")
    _mode = mode
    if udp_servers:
        _udp_servers = [s.strip() for s in udp_servers if s.strip()]
    with _lock:
        _cache.clear()


def mode() -> str:
    return _mode


def resolver_label() -> str:
    if _mode == "off":
        return "DNS di sistema"
    parts = []
    if _mode in ("auto", "doh"):
        parts.append("DoH Cloudflare/Google")
    if _mode in ("auto", "udp"):
        parts.append("UDP " + ", ".join(_udp_servers))
    return "DNS non filtrato (" + " + ".join(parts) + ", fallback sistema)"


def stats() -> dict:
    with _lock:
        cached = len(_cache)
    return {"mode": _mode, "udp_servers": list(_udp_servers), **dict(_counters), "cache": cached}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _is_ip(value: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, value)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, value)
        return True
    except OSError:
        return False


def _store(host: str, ips: list[str], source: str, ttl: int) -> None:
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[host] = (time.time() + ttl, list(ips), source)


def _system_lookup(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    ips = []
    for info in infos:
        address = info[4][0]
        if address not in ips:
            ips.append(address)
    if not ips:
        raise DnsError(f"il resolver di sistema non ha indirizzi per {host}")
    return ips


def _order_ipv4_first(ips: list[str]) -> list[str]:
    v4 = [ip for ip in ips if ":" not in ip]
    v6 = [ip for ip in ips if ":" in ip]
    return v4 + v6


# ---------------------------------------------------------------------------
# DNS over HTTPS
# ---------------------------------------------------------------------------
def _doh_lookup(endpoint: str, host: str, record_type: str = "A") -> list[str]:
    target = f"{endpoint}?name={urllib.parse.quote(host)}&type={record_type}"
    request = urllib.request.Request(target, headers={
        "Accept": "application/dns-json", "User-Agent": USER_AGENT})
    # il bootstrap del resolver usa il DNS di sistema: qui non si deve ricadere in se'
    with _SYSTEM_URLOPEN(request, timeout=DOH_TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    status = data.get("Status")
    if status not in (0, None):
        raise DnsError(f"risposta DoH con codice {status}")
    wanted = 1 if record_type == "A" else 28
    ips = []
    for answer in data.get("Answer") or []:
        if answer.get("type") == wanted and _is_ip(str(answer.get("data") or "")):
            ips.append(answer["data"])
    if not ips:
        raise DnsError(f"nessun record {record_type} per {host}")
    return ips


# ---------------------------------------------------------------------------
# DNS classico in UDP (nessuna dipendenza esterna)
# ---------------------------------------------------------------------------
def _encode_name(host: str) -> bytes:
    return b"".join(struct.pack(">B", len(part)) + part.encode("ascii")
                    for part in host.split(".") if part) + b"\x00"


def _skip_name(data: bytes, offset: int) -> int:
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length
    raise DnsError("nome DNS troncato")


def _decode_name(data: bytes, offset: int, depth: int = 0) -> str:
    if depth > 10:
        return ""
    parts = []
    while offset < len(data):
        length = data[offset]
        if length == 0:
            break
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack(">H", data[offset:offset + 2])[0] & 0x3FFF
            parts.append(_decode_name(data, pointer, depth + 1))
            break
        parts.append(data[offset + 1:offset + 1 + length].decode("ascii", "replace"))
        offset += 1 + length
    return ".".join(part for part in parts if part)


def _parse_a_records(data: bytes, query_id: int):
    """Ritorna (indirizzi, cname)."""
    if len(data) < 12:
        raise DnsError("risposta DNS troppo corta")
    rid, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if rid != query_id:
        raise DnsError("identificativo DNS non corrispondente")
    if flags & 0x000F:
        raise DnsError(f"il server DNS ha risposto con codice {flags & 0x000F}")

    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset) + 4

    ips, cname = [], None
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlength]
        offset += rdlength
        if rtype == 1 and rdlength == 4:
            ips.append(".".join(str(byte) for byte in rdata))
        elif rtype == 5 and offset - rdlength <= len(data):
            cname = _decode_name(data, offset - rdlength)
    return ips, cname


def _udp_query(server: str, host: str, query_id: int) -> bytes:
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    question = _encode_name(host) + struct.pack(">HH", 1, 1)
    packet = header + question
    info = socket.getaddrinfo(server, 53, socket.AF_INET, socket.SOCK_DGRAM)[0]
    sock = socket.socket(info[0], info[1], info[2])
    sock.settimeout(UDP_TIMEOUT)
    try:
        sock.sendto(packet, info[4])
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    return data


def _udp_lookup(host: str, server: str) -> list[str]:
    query_id = random.randint(0, 0xFFFF)
    ips, cname = _parse_a_records(_udp_query(server, host, query_id), query_id)
    if not ips and cname and cname.lower() != host.lower():
        query_id = random.randint(0, 0xFFFF)
        ips, _ = _parse_a_records(_udp_query(server, cname, query_id), query_id)
    if not ips:
        raise DnsError(f"nessun record A per {host} da {server}")
    return ips


# ---------------------------------------------------------------------------
# Risoluzione
# ---------------------------------------------------------------------------
def resolve(host: str, use_cache: bool = True) -> list[str]:
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        raise DnsError("nome host vuoto")
    if _is_ip(host):
        return [host]

    if use_cache:
        with _lock:
            hit = _cache.get(host)
        if hit and hit[0] > time.time():
            if hit[1]:
                _counters["cache"] += 1
                return list(hit[1])
            raise DnsError(hit[2] or f"risoluzione di {host} non riuscita (in cache)")

    if _mode == "off" or host in DOH_HOSTS:
        try:
            ips = _order_ipv4_first(_system_lookup(host))
            _counters["system"] += 1
            _store(host, ips, "sistema", CACHE_TTL)
            return ips
        except Exception as exc:
            _store(host, [], f"resolver di sistema: {exc}", FAILURE_TTL)
            raise DnsError(f"impossibile risolvere {host}: {exc}")

    errors = []
    ips: list[str] = []
    source = ""

    if _mode in ("auto", "doh"):
        for name, endpoint in DOH_ENDPOINTS:
            for record_type in ("A", "AAAA"):
                try:
                    ips = _doh_lookup(endpoint, host, record_type)
                except Exception as exc:
                    errors.append(f"DoH {name} {record_type}: {exc}")
                    continue
                source = f"DoH {name}"
                break
            if ips:
                break

    if not ips and _mode in ("auto", "udp"):
        for server in _udp_servers:
            try:
                ips = _udp_lookup(host, server)
            except Exception as exc:
                errors.append(f"UDP {server}: {exc}")
                continue
            source = f"UDP {server}"
            break

    if not ips:
        try:
            ips = _system_lookup(host)
            source = "sistema"
        except Exception as exc:
            errors.append(f"sistema: {exc}")

    if not ips:
        _counters["failed"] += 1
        detail = "; ".join(errors[:3])
        _store(host, [], f"non risolto ({detail})", FAILURE_TTL)
        raise DnsError(f"impossibile risolvere {host}: {detail}")

    ips = _order_ipv4_first(list(dict.fromkeys(ips)))
    if source.startswith("DoH"):
        _counters["doh"] += 1
    elif source.startswith("UDP"):
        _counters["udp"] += 1
    else:
        _counters["system"] += 1
    _store(host, ips, source, CACHE_TTL)
    return ips


def resolve_info(host: str, fresh: bool = False) -> dict:
    """Diagnostica: indirizzi, da quale resolver e statistiche."""
    try:
        ips = resolve(host, use_cache=not fresh)
    except Exception as exc:
        return {"host": host, "ok": False, "error": str(exc), "stats": stats()}
    with _lock:
        entry = _cache.get((host or "").strip().lower().rstrip("."))
    return {"host": host, "ok": True, "ips": ips, "source": entry[2] if entry else "?",
            "stats": stats()}


# ---------------------------------------------------------------------------
# Connessioni HTTP/HTTPS che usano gli indirizzi risolti qui
# ---------------------------------------------------------------------------
class PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        if self._tunnel_host:
            return super().connect()
        last_error = None
        for ip in resolve(self.host):
            try:
                self.sock = socket.create_connection((ip, self.port), self.timeout,
                                                     self.source_address)
                return
            except OSError as exc:
                last_error = exc
        raise last_error or OSError(f"nessun indirizzo utilizzabile per {self.host}")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host:
            return super().connect()
        last_error = None
        for ip in resolve(self.host):
            try:
                raw = socket.create_connection((ip, self.port), self.timeout, self.source_address)
            except OSError as exc:
                last_error = exc
                continue
            try:
                # il certificato resta verificato sul nome richiesto (SNI + hostname check)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except Exception as exc:  # SSL/certificato: prova l'indirizzo successivo
                raw.close()
                last_error = exc
        raise last_error or OSError(f"nessun indirizzo utilizzabile per {self.host}")


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(PinnedHTTPConnection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        # stesso comportamento dell'handler standard, ma con la nostra connessione
        return self.do_open(PinnedHTTPSConnection, req, context=self._context)


def build_opener():
    return urllib.request.build_opener(PinnedHTTPHandler(), PinnedHTTPSHandler())


def open_url(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
    """Come urllib.request.urlopen, ma con DNS non filtrato."""
    if _mode == "off" or kwargs:
        return _SYSTEM_URLOPEN(url, data, timeout, **kwargs)
    if _opener is None:
        install()
    return _opener.open(url, data=data, timeout=timeout)


def install() -> bool:
    """Instrada urllib.request.urlopen sulle connessioni con DNS non filtrato."""
    global _opener
    if _mode == "off":
        return False
    if _opener is None:
        _opener = build_opener()
    urllib.request.urlopen = open_url
    return True
