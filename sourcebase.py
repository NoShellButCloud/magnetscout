# -*- coding: utf-8 -*-
"""Elementi condivisi fra le fonti: helper HTTP, filtri e utilita' comuni.

Qui vivono le parti che non dipendono da un indice specifico: i filtri
richiesti dall'utente, la normalizzazione dei risultati, il filtro centrale,
la deduplica, l'ordinamento e i parser generici (feed RSS/Atom, magnet).
"""
from __future__ import annotations

import html as htmllib
import json
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from textutil import (detect_3d, detect_langs, detect_quality, detect_resolution,
                      exact_title_matches, norm_langs, parse_size,
                      primary_lang,
                      resolution_bucket)
from torrent import DEFAULT_TRACKERS, build_magnet, torrent_info

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 MagnetScout/1.0")
TIMEOUT = 20
GIB = 1024 ** 3

MAGNET_RE = re.compile(r"magnet:\?xt=[^\s\"'<>\\]+", re.IGNORECASE)
BTIH_RE = re.compile(r"xt=urn:btih:([A-Za-z0-9]{32,40})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_fetch(url: str, data=None, headers=None, timeout: int = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def http_json(url: str, timeout: int = TIMEOUT, data=None, headers=None):
    raw = http_fetch(url, data=data, timeout=timeout,
                     headers={"Accept": "application/json, */*", **(headers or {})})
    return json.loads(raw.decode("utf-8", "replace"))


def http_json_post(url: str, payload: dict, timeout: int = TIMEOUT):
    body = json.dumps(payload).encode("utf-8")
    return http_json(url, timeout=timeout, data=body,
                     headers={"Content-Type": "application/json"})


def http_text(url: str, timeout: int = TIMEOUT) -> str:
    return http_fetch(url, timeout=timeout).decode("utf-8", "replace")


def http_bytes(url: str, timeout: int = TIMEOUT) -> bytes:
    return http_fetch(url, timeout=timeout)


# ---------------------------------------------------------------------------
# Filtri e risultati normalizzati
# ---------------------------------------------------------------------------
@dataclass
class Filters:
    q: str = ""
    lang: str = ""      # codice lingua o "" (tutte)
    mtype: str = ""     # "", "movie", "tv"
    res: str = ""       # "", "480p", "720p", "1080p", "2160p", "3d"
    genre: str = ""     # slug genere o ""
    quality: str = ""   # remux|bluray|web|hdtv|dvd|cam o ""
    max_size_gb: float = 0  # dimensione massima in GB (0 = nessun limite)
    match: str = ""       # "" | "words" | "title": confronto ignorando i separatori


def make_result(**kw) -> dict:
    base = {
        "id": "", "source": "", "source_label": "", "title": "",
        "magnet": None, "info_hash": None, "size_bytes": None,
        "seeds": None, "leechers": None, "date_ts": None, "url": None,
        "lang": None, "langs": [], "sub": False, "resolution": None,
        "is3d": False, "quality": None, "media": "movie", "genres": [],
        "imdb": None, "needs_magnet": False, "rank": 0,
        "torrent_url": None, "link_out": False,
    }
    base.update(kw)
    return base


def lang_fields(title: str, meta_lang=None):
    langs, sub = detect_langs(title)
    for code in norm_langs(meta_lang):
        if code not in langs:
            langs.append(code)
    return langs, sub


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def iso_ts(value):
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def year_ts(value):
    if not value:
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    if not match:
        return None
    try:
        return int(datetime(int(match.group(0)), 1, 1, tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def slugify(text: str, fallback: str = "fonte") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug or fallback)[:40]


class Source:
    id = ""
    label = ""
    description = ""
    default = True
    custom = False

    def search(self, filters: Filters):
        raise NotImplementedError

    def info(self) -> dict:
        return {"id": self.id, "label": self.label,
                "description": self.description, "default": self.default,
                "custom": self.custom}


# ---------------------------------------------------------------------------
# Filtro centrale, deduplica, ordinamento
# ---------------------------------------------------------------------------
def matches(result: dict, filters: Filters) -> bool:
    """Verifica un risultato normalizzato contro i filtri richiesti.

    I risultati "link_out" (semplici collegamenti alla ricerca di una fonte
    che non espone risultati) non hanno i metadati per essere filtrati: vengono
    sempre tenuti, cosi' la fonte resta visibile all'utente.
    """
    if result.get("link_out"):
        return True
    if filters.mtype and result["media"] != filters.mtype:
        return False
    if filters.res:
        if filters.res == "3d":
            if not result["is3d"]:
                return False
        elif resolution_bucket(result["resolution"]) != filters.res:
            return False
    if filters.quality and result["quality"] != filters.quality:
        return False
    if filters.genre and filters.genre not in (result.get("genres") or []):
        return False
    if filters.max_size_gb:
        size = result.get("size_bytes")
        if not size or size > filters.max_size_gb * GIB:
            return False
    if filters.match and not exact_title_matches(result["title"], filters.q, filters.match):
        return False
    if filters.lang:
        langs = set(_result_langs(result))
        if filters.lang == "multi":
            # "MULTI" significa che l'utente non sa quali lingue contenga: vale solo
            # se il titolo lo dichiara o se sono riconosciute almeno due lingue.
            if "multi" not in langs and len(langs - {"multi"}) < 2:
                return False
        elif filters.lang not in langs:
            # Filtrare per "italiano" non basta un generico "MULTI": la lingua
            # richiesta deve comparire davvero nei metadati o nel titolo.
            return False
    return True


def _result_langs(result: dict) -> list[str]:
    """Tutte le lingue conosciute per un risultato (codice principale + elenco)."""
    langs = list(result.get("langs") or [])
    code = result.get("lang")
    if code and code not in langs:
        langs.append(code)
    return langs


def dedupe(results: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for result in results:
        key = (result["info_hash"] or "").lower() or ("t:" + result["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(result)
    return out


def _round_robin(results: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for result in results:
        groups.setdefault(result["source"], []).append(result)
    for group in groups.values():
        group.sort(key=lambda r: r["rank"])
    merged = []
    while groups:
        for source in list(groups):
            group = groups[source]
            merged.append(group.pop(0))
            if not group:
                del groups[source]
    return merged


def sort_results(results: list[dict], sort: str) -> list[dict]:
    if sort == "size":
        return sorted(results, key=lambda r: -(r["size_bytes"] or 0))
    if sort == "date":
        return sorted(results, key=lambda r: -(r["date_ts"] or 0))
    if sort == "relevance":
        return _round_robin(results)
    return sorted(results, key=lambda r: -(r["seeds"] if r["seeds"] is not None else -1))


# ---------------------------------------------------------------------------
# Magnet: lettura, nome del torrent, generazione da un file .torrent
# ---------------------------------------------------------------------------
def magnet_name(magnet: str) -> str:
    match = re.search(r"[?&]dn=([^&]+)", magnet or "")
    if not match:
        return ""
    value = match.group(1).replace("+", " ")
    try:
        return urllib.parse.unquote(value)
    except Exception:
        return value


def magnet_hash(magnet: str):
    match = BTIH_RE.search(magnet or "")
    return match.group(1).lower() if match else None


_BASE32_RE = re.compile(r"^[A-Za-z2-7]{32}$")
_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def normalize_info_hash(value):
    """Normalizza un info hash in hex minuscolo (40 caratteri).

    Molti indici (animetosho, tokyotosho, TNT Village) pubblicano l'hash in
    base32: convertirlo rende confrontabili e deduplicabili i risultati.
    """
    text = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    if _HEX40_RE.match(text):
        return text.lower()
    if _HEX32_RE.match(text):
        return text.lower()
    if _BASE32_RE.match(text):
        import base64

        try:
            return base64.b32decode(text.upper()).hex()
        except Exception:  # noqa: BLE001
            return text.lower()
    return None


_ADULT_RE = re.compile(
    r"(?:^|[^a-z])(xxx|porn|porno|brazzers|onlyfans|hentai|r18|jav)(?:[^a-z]|$)", re.I)


def looks_adult(title: str) -> bool:
    """Esclude i titoli per adulti dagli indici generalisti."""
    return bool(_ADULT_RE.search(title or ""))


_TV_MARK_RE = re.compile(
    r"\bS\d{1,2}\s?E\d{1,3}\b|\bS\d{1,2}\b(?!\d)|(?<!\d)\d{1,2}x\d{2}\b"
    r"|\bseason\s*\d|\bstagione\s*\d|\bсезон\b|\bcomplete\s+series\b", re.I)


def guess_media(title: str, default: str = "movie") -> str:
    """Deduce film o serie dal nome (usato dagli indici senza categorie)."""
    return "tv" if _TV_MARK_RE.search(title or "") else default


_template_cache: dict[str, str] = {}
_template_lock = threading.Lock()


def magnet_from_url(torrent_url: str) -> str:
    """Scarica un file .torrent e ne ricava il magnet (con cache in memoria)."""
    parsed = urllib.parse.urlparse(torrent_url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("serve un URL http/https di un file .torrent")
    with _template_lock:
        cached = _template_cache.get(torrent_url)
    if cached:
        return cached
    raw = http_bytes(torrent_url, timeout=60)
    info = torrent_info(raw)
    magnet = build_magnet(info["info_hash"], info.get("name") or parsed.path.rsplit("/", 1)[-1],
                          info["trackers"] or DEFAULT_TRACKERS, info["webseeds"])
    with _template_lock:
        if len(_template_cache) > 200:
            _template_cache.clear()
        _template_cache[torrent_url] = magnet
    return magnet


# ---------------------------------------------------------------------------
# Parser generico di feed RSS/Atom (usato da nyaa e dalle fonti custom)
# ---------------------------------------------------------------------------
def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _feed_ts(value):
    if not value:
        return None
    parsed = iso_ts(value)
    if parsed:
        return parsed
    try:
        moment = parsedate_to_datetime(str(value))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return int(moment.timestamp())
    except (TypeError, ValueError):
        return None


def feed_ts(value):
    """Data di un feed RSS/Atom in timestamp (ISO o formato RFC 822)."""
    return _feed_ts(value)


def parse_feed_items(text: str) -> list[dict]:
    """Trasforma un feed RSS/Atom in una lista di dizionari normalizzati."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring((text or "").strip())
    except ET.ParseError:
        return []

    entries = []
    for node in root.iter():
        if _local_name(node.tag) in ("item", "entry"):
            entries.append(node)

    items = []
    for entry in entries:
        data = {"title": "", "link": None, "magnet": None, "hash": None,
                "size": None, "seeds": None, "leechers": None, "date": None,
                "category": None, "torrent_url": None, "description": ""}
        for child in entry:
            name = _local_name(child.tag)
            value = (child.text or "").strip()
            if name == "title":
                data["title"] = value
            elif name == "link":
                data["link"] = data["link"] or child.get("href") or value
            elif name in ("enclosure",):
                url = child.get("url")
                if url:
                    data["torrent_url"] = data["torrent_url"] or url
            elif name == "infohash" or name == "hash":
                data["hash"] = data["hash"] or value.lower()
            elif name in ("seeders", "seeds"):
                data["seeds"] = data["seeds"] or value
            elif name in ("leechers", "leechs", "peers"):
                data["leechers"] = data["leechers"] or value
            elif name == "size":
                data["size"] = data["size"] or value
            elif name in ("pubdate", "published", "updated", "date"):
                data["date"] = data["date"] or value
            elif name == "category":
                data["category"] = data["category"] or value
            elif name == "description":
                data["description"] = value[:400]
        for candidate in (data["link"], data["torrent_url"], data["description"]):
            if not data["magnet"] and candidate and str(candidate).startswith("magnet:"):
                data["magnet"] = candidate
        if data["torrent_url"] and not str(data["torrent_url"]).lower().endswith(".torrent"):
            data["torrent_url"] = None
        if not data["hash"] and data["magnet"]:
            data["hash"] = magnet_hash(data["magnet"])
        items.append(data)
    return items


# ---------------------------------------------------------------------------
# Estrazione dei magnet da una pagina HTML (fonti custom di tipo "html")
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(text: str) -> str:
    """Testo di una cella HTML: senza tag, entita' risolte, spazi compattati."""
    clean = htmllib.unescape(_TAG_RE.sub(" ", text or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", clean).strip()


def extract_magnets(page: str) -> list[dict]:
    """Estrae i magnet da una pagina HTML, con l'etichetta piu' vicina."""
    out = []
    seen = set()
    if not page:
        return out

    for match in re.finditer(r"<a\b[^>]*href\s*=\s*[\"'](magnet:[^\"']+)[\"'][^>]*>(.*?)</a>",
                             page, re.IGNORECASE | re.DOTALL):
        magnet = htmllib.unescape(match.group(1))
        info_hash = magnet_hash(magnet)
        if not info_hash or info_hash in seen:
            continue
        seen.add(info_hash)
        label = htmllib.unescape(_TAG_RE.sub(" ", match.group(2)))
        label = re.sub(r"\s+", " ", label).strip()
        out.append({"magnet": magnet, "hash": info_hash,
                    "title": label or magnet_name(magnet)})

    for magnet in MAGNET_RE.findall(page):
        magnet = htmllib.unescape(magnet)
        info_hash = magnet_hash(magnet)
        if not info_hash or info_hash in seen:
            continue
        seen.add(info_hash)
        out.append({"magnet": magnet, "hash": info_hash,
                    "title": magnet_name(magnet)})
    return out
