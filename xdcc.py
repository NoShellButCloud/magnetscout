# -*- coding: utf-8 -*-
"""Ricerca XDCC: adattatori verso i motori di ricerca pubblici.

XDCC e' un metodo di trasferimento file su IRC: un "bot" tiene dei pacchetti
numerati e li invia su richiesta con ``/msg <bot> xdcc send #<numero>``. Qui si
cercano i pacchetti nei due motori piu' completi:

  xdcc.eu   indice multi-rete (Abjects, Rizon, ...): fornisce server, canale,
            bot, slot, dimensione, nome e il comando esatto da inviare
  NIBL      indice specializzato in anime/manga su Rizon (API JSON)

Ogni risultato viene normalizzato con gli stessi campi dei magnet, cosi' la
tabella e il downloader funzionano allo stesso modo per entrambi.
"""
from __future__ import annotations

import html as htmllib
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from sourcebase import http_json, http_text, to_int
from textutil import detect_quality, detect_resolution

TIMEOUT = 25

# ---------------------------------------------------------------------------
# Utilita' comuni
# ---------------------------------------------------------------------------
_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
_SIZE_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*([kmgt]?)\s*(?:i?b)?\b", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_EPISODE_PATTERNS = (
    re.compile(r"\bS\d{1,2}\s?E(\d{1,3})\b", re.I),
    re.compile(r"\bE(?:P|pisode)?\.?\s?(\d{1,3})\b"),
    re.compile(r"\s-\s(\d{1,3})(?:v\d)?\b"),
    re.compile(r"\s(\d{1,3})(?:v\d)?\s?\[[0-9A-Fa-f]{6,}\]"),
)
_SEASON_RE = re.compile(r"\bS(\d{1,2})\b")


def parse_xdcc_size(value):
    """Dimensione testuale XDCC ('15G', '44M', '1,5 GB', '700K') in byte."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _SIZE_RE.search(str(value))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return int(number * _SIZE_UNITS.get(match.group(2).lower(), 1))


def detect_episode(name: str):
    """Numero di episodio ricavato dal nome del pacchetto (None se assente)."""
    for pattern in _EPISODE_PATTERNS:
        match = pattern.search(name or "")
        if match:
            value = to_int(match.group(1))
            if value:
                return value
    return None


def detect_season(name: str):
    match = _SEASON_RE.search(name or "")
    return to_int(match.group(1)) if match else None


def clean_text(value: str) -> str:
    text = htmllib.unescape(_TAG_RE.sub("", value or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def timestamp(value):
    """Timestamp unix da '2026-10-18 20:03:12' (o gia' numerico)."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    from datetime import datetime, timezone
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text[:19], pattern)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def make_result(**fields) -> dict:
    """Risultato XDCC normalizzato (stessa forma dei risultati magnet)."""
    name = fields.get("name") or ""
    size = fields.get("size_bytes")
    if not size:
        size = parse_xdcc_size(fields.get("size_text"))
    return {
        "id": fields["id"],
        "source": fields["source"],
        "source_label": fields["source_label"],
        "network": fields.get("network") or "",
        "server": fields.get("server") or "",
        "port": fields.get("port") or 6697,
        "channel": fields.get("channel") or "",
        "bot": fields.get("bot") or "",
        "pack": str(fields.get("pack") or ""),
        "command": fields.get("command") or "",
        "name": name,
        "size_bytes": size,
        "gets": fields.get("gets"),
        "episode": detect_episode(name),
        "season": detect_season(name),
        "date_ts": fields.get("date_ts"),
        "resolution": detect_resolution(name),
        "quality": detect_quality(name),
        "downloadable": bool(fields.get("server") and fields.get("bot") and fields.get("pack")),
        "kind": fields.get("kind") or "",
    }


class XdccSource:
    """Interfaccia minima di un motore di ricerca XDCC."""

    id = ""
    label = ""

    def search(self, query: str, limit: int = 200):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# xdcc.eu - indice multi-rete
# ---------------------------------------------------------------------------
class XdccEuSource(XdccSource):
    id = "xdcc.eu"
    label = "xdcc.eu"
    BASE = "https://www.xdcc.eu"
    # <tr><td>Rete</td><td>Canale + link info</td><td>Bot</td><td>#slot</td><td>Get</td><td>Size</td><td>Nome</td></tr>
    _ROW_RE = re.compile(
        r"<tr><td>(?P<network>[^<]*)</td>"
        r"<td>(?P<channel>.*?)</td>"
        r"<td>(?P<bot>.*?)</td>"
        r"<td>#?(?P<pack>\d+)</td>"
        r"<td>(?P<gets>[\d]*)[^<]*</td>"
        r"<td>(?P<size>[^<]*)</td>"
        r"<td>(?P<name>.*?)</td></tr>",
        re.S)
    _INFO_RE = re.compile(
        r'data-s="(?P<server>[^"]+)"\s+data-c="(?P<channel>[^"]*)"\s+'
        r'data-p="(?P<command>[^"]*)"')
    _CHANNEL_RE = re.compile(r"(#[\w\-\[\]{}|`^]+)")

    def search(self, query: str, limit: int = 200):
        url = (self.BASE + "/search.php?" +
               urllib.parse.urlencode({"searchkey": query}))
        page = http_text(url, timeout=TIMEOUT)
        out = []
        for match in self._ROW_RE.finditer(page):
            row = match.groupdict()
            info = self._INFO_RE.search(row["channel"])
            channel = ""
            if info:
                channel = info.group("channel") or ""
                server = info.group("server")
                command = htmllib.unescape(info.group("command"))
            else:
                channel_match = self._CHANNEL_RE.search(clean_text(row["channel"]))
                channel = channel_match.group(1) if channel_match else ""
                server = ""
                command = ""
            bot = clean_text(row["bot"])
            pack = row["pack"]
            if not info:
                command = f"{bot} xdcc send #{pack}" if bot else ""
            result = make_result(
                id=f"xdcc.eu:{server}:{bot}:{pack}",
                source=self.id, source_label=self.label,
                network=clean_text(row["network"]), server=server, channel=channel,
                bot=bot, pack=pack, command=command or f"{bot} xdcc send #{pack}",
                name=clean_text(row["name"]),
                size_text=clean_text(row["size"]),
                gets=to_int(row["gets"]),
                kind="xdcc",
            )
            if result["name"]:
                out.append(result)
            if len(out) >= limit:
                break
        return out, None


# ---------------------------------------------------------------------------
# NIBL - anime/manga su Rizon (API JSON)
# ---------------------------------------------------------------------------
class NiblSource(XdccSource):
    id = "nibl"
    label = "NIBL"
    API = "https://api.nibl.co.uk/nibl"
    BOTS_TTL = 6 * 3600
    _bots: dict = {}
    _bots_ts = 0.0
    _lock = threading.Lock()

    def _bot_names(self, force: bool = False) -> dict:
        with self._lock:
            fresh = self._bots and time.time() - self._bots_ts < self.BOTS_TTL
            if fresh and not force:
                return self._bots
        data = http_json(self.API + "/bots", timeout=TIMEOUT)
        bots = {}
        for bot in (data.get("content") or []) if isinstance(data, dict) else []:
            if isinstance(bot, dict) and bot.get("id") is not None:
                bots[str(bot["id"])] = str(bot.get("name") or "")
        if bots:
            with self._lock:
                self._bots = bots
                self._bots_ts = time.time()
        return bots

    def search(self, query: str, limit: int = 200, episode=None):
        params = {"query": query, "results": max(20, min(int(limit), 300))}
        if episode:
            params["episodeNumber"] = int(episode)
        url = self.API + "/search?" + urllib.parse.urlencode(params)
        data = http_json(url, timeout=TIMEOUT)
        content = (data.get("content") or []) if isinstance(data, dict) else []
        bots = self._bot_names()
        missing = {str(item.get("botId")) for item in content
                   if isinstance(item, dict)} - set(bots)
        missing.discard("None")
        if missing:
            bots = self._bot_names(force=True)
        out = []
        for item in content:
            if not isinstance(item, dict):
                continue
            bot = bots.get(str(item.get("botId")), "")
            pack = str(item.get("number") or "")
            result = make_result(
                id=f"nibl:{item.get('botId')}:{pack}",
                source=self.id, source_label=self.label,
                network="Rizon", server="irc.rizon.net", channel="#nibl",
                bot=bot, pack=pack,
                command=f"{bot} xdcc send #{pack}" if bot else "",
                name=str(item.get("name") or ""),
                size_bytes=item.get("sizekbits"),
                size_text=item.get("size"),
                date_ts=timestamp(item.get("lastModified")),
                kind="xdcc",
            )
            if result["name"]:
                out.append(result)
        out.sort(key=lambda item: (item["episode"] is None, item["episode"] or 0, item["name"]))
        return out[:limit], None


SEARCH_SOURCES = (XdccEuSource(), NiblSource())


def search_all(query: str, limit: int = 200, episode=None):
    """Interroga tutti i motori XDCC in parallelo.

    Ritorna (risultati deduplicati, statistiche per motore): un motore lento o
    non raggiungibile non blocca gli altri.
    """
    query = (query or "").strip()
    stats = []
    if len(query) < 2:
        return [], [{"id": source.id, "label": source.label, "ok": False, "count": 0,
                     "ms": 0, "error": "serve una ricerca di almeno 2 caratteri"}
                    for source in SEARCH_SOURCES]

    def run(source):
        started = time.time()
        try:
            if isinstance(source, NiblSource):
                items, note = source.search(query, limit, episode)
            else:
                items, note = source.search(query, limit)
            return {"id": source.id, "label": source.label, "ok": True,
                    "count": len(items), "ms": int((time.time() - started) * 1000),
                    "error": None, "note": note}, items
        except Exception as exc:  # noqa: BLE001
            return {"id": source.id, "label": source.label, "ok": False, "count": 0,
                    "ms": int((time.time() - started) * 1000),
                    "error": str(exc)[:200], "note": None}, []

    per_source = []
    with ThreadPoolExecutor(max_workers=len(SEARCH_SOURCES)) as pool:
        for stat, items in pool.map(run, SEARCH_SOURCES):
            stats.append(stat)
            per_source.append(items)

    if episode:
        wanted = to_int(episode)
        if wanted:
            per_source = [[item for item in items
                           if item["episode"] is None or item["episode"] == wanted]
                          for item in per_source]

    # Alterna le fonti (un risultato per fonte in ogni giro) cosi' nessun motore
    # satura l'elenco, e togli i doppioni fra i motori.
    queues = [list(items) for items in per_source]
    deduped = []
    seen = set()
    while len(deduped) < limit and any(queues):
        for queue in queues:
            if len(deduped) >= limit:
                break
            while queue:
                item = queue.pop(0)
                key = (item["bot"].lower(), item["pack"], (item["name"] or "").lower())
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(item)
                break
    return deduped, stats
