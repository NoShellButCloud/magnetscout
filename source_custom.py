# -*- coding: utf-8 -*-
"""Fonti personalizzate aggiunte dall'utente.

Una fonte custom e' una piccola descrizione (JSON) salvata in
custom_sources.json; tipi supportati:

  rss   feed RSS/Atom (come nyaa): si sostituisce {query} e si leggono gli item
  json  API JSON: si estrae la lista dei risultati (results_path) e, se serve,
                     si mappano i campi con "fields"
  html  pagina HTML che contiene link magnet: si estraggono direttamente
  link  nessun risultato: si mostra un collegamento alla ricerca sul sito

Per ogni tipo la ricerca usa search_url con {query} al posto del testo cercato.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse

from sourcebase import (Filters, Source, http_json, http_text, iso_ts,
                        lang_fields, make_result, magnet_from_url,
                        parse_feed_items, slugify, to_int, extract_magnets)
from textutil import (detect_3d, detect_quality, detect_resolution, parse_size,
                      primary_lang)
from torrent import DEFAULT_TRACKERS, build_magnet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_FILE = os.path.join(BASE_DIR, "custom_sources.json")

KINDS = ("rss", "json", "html", "link")
KIND_LABELS = {
    "rss": "Feed RSS/Atom",
    "json": "API JSON",
    "html": "Pagina HTML con magnet",
    "link": "Solo collegamento alla ricerca",
}
def builtin_ids() -> tuple:
    """Id delle fonti incluse (importato qui per non creare cicli)."""
    from sources_builtin import BUILTIN_SOURCES

    return tuple(source.id for source in BUILTIN_SOURCES)
FIELD_KEYS = ("title", "magnet", "info_hash", "size", "seeds", "leechers",
              "date", "url", "torrent_url")

_lock = threading.RLock()
_state = {"mtime": None, "definitions": []}


# ---------------------------------------------------------------------------
# Persistenza
# ---------------------------------------------------------------------------
def _read_file() -> list[dict]:
    if not os.path.isfile(CUSTOM_FILE):
        return []
    try:
        with open(CUSTOM_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("sources") or []
    return [d for d in data if isinstance(d, dict)]


def definitions() -> list[dict]:
    """Definizioni correnti (ricaricate se il file cambia sul disco)."""
    with _lock:
        try:
            mtime = os.path.getmtime(CUSTOM_FILE)
        except OSError:
            mtime = None
        if mtime != _state["mtime"]:
            _state["definitions"] = _read_file()
            _state["mtime"] = mtime
        return [dict(d) for d in _state["definitions"]]


def _write(defs: list[dict]) -> None:
    tmp = CUSTOM_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(defs, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, CUSTOM_FILE)
    with _lock:
        _state["definitions"] = [dict(d) for d in defs]
        try:
            _state["mtime"] = os.path.getmtime(CUSTOM_FILE)
        except OSError:
            _state["mtime"] = None


# ---------------------------------------------------------------------------
# Validazione
# ---------------------------------------------------------------------------
def validate(definition: dict, existing_ids=()) -> dict:
    if not isinstance(definition, dict):
        raise ValueError("definizione non valida")

    label = str(definition.get("label") or "").strip()
    if not label:
        raise ValueError("serve un nome per la fonte")
    if len(label) > 80:
        raise ValueError("nome troppo lungo (massimo 80 caratteri)")

    kind = str(definition.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("tipo non valido: usa " + ", ".join(KINDS))

    source_id = slugify(str(definition.get("id") or label))
    if source_id in builtin_ids():
        raise ValueError(f"l'id '{source_id}' e' gia' usato da una fonte inclusa")
    if source_id in existing_ids:
        raise ValueError(f"esiste gia' una fonte con id '{source_id}'")

    search_url = str(definition.get("search_url") or "").strip()
    if kind in ("rss", "json", "html"):
        if not search_url:
            raise ValueError("serve l'URL di ricerca")
        if not re.match(r"^https?://", search_url, re.IGNORECASE):
            raise ValueError("l'URL deve iniziare con http:// o https://")
        if "{query}" not in search_url:
            raise ValueError('l\'URL deve contenere "{query}" dove va il testo cercato')
    elif search_url and not re.match(r"^https?://", search_url, re.IGNORECASE):
        raise ValueError("l'URL deve iniziare con http:// o https://")

    media = str(definition.get("media") or "").strip().lower()
    if media not in ("", "movie", "tv"):
        media = ""

    results_path = str(definition.get("results_path") or "").strip()[:200]

    fields = definition.get("fields") or {}
    clean_fields = {}
    if isinstance(fields, dict):
        for key, value in fields.items():
            if key in FIELD_KEYS and isinstance(value, str) and value.strip():
                clean_fields[key] = value.strip()[:120]

    return {
        "id": source_id,
        "label": label,
        "kind": kind,
        "search_url": search_url,
        "description": str(definition.get("description") or "").strip()[:200],
        "media": media,
        "results_path": results_path,
        "fields": clean_fields,
        "enabled": bool(definition.get("enabled", True)),
    }


def upsert(definition: dict) -> dict:
    """Aggiunge o aggiorna una fonte personalizzata e ritorna la definizione."""
    current = definitions()
    candidate_id = str(definition.get("id") or "").strip()
    existing_ids = [d.get("id") for d in current]
    if candidate_id:
        # aggiornamento: l'id della fonte che si sta modificando non conta
        existing_ids = [i for i in existing_ids if i != candidate_id]
    cleaned = validate(definition, existing_ids)
    current = [d for d in current if d.get("id") != cleaned["id"]]
    current.append(cleaned)
    current.sort(key=lambda d: str(d.get("label") or "").lower())
    _write(current)
    return cleaned


def delete(source_id: str) -> bool:
    source_id = str(source_id or "").strip()
    current = definitions()
    remaining = [d for d in current if d.get("id") != source_id]
    if len(remaining) == len(current):
        return False
    _write(remaining)
    return True


def sources() -> list["CustomSource"]:
    return [CustomSource(d) for d in definitions() if d.get("enabled", True)]


# ---------------------------------------------------------------------------
# Adattatore
# ---------------------------------------------------------------------------
def _pick(item: dict, path: str, defaults=()):
    """Legge un campo da un dizionario, con percorso puntato opzionale."""
    if path:
        node = item
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        return node
    for name in defaults:
        if isinstance(item, dict) and item.get(name) not in (None, ""):
            return item[name]
    return None


def _resolve_items(data, results_path: str):
    if results_path:
        node = data
        for part in results_path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return []
        return node if isinstance(node, list) else []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "torrents", "hits", "items", "data", "movies", "list"):
            node = data.get(key)
            if isinstance(node, list):
                return node
            if isinstance(node, dict):
                for nested in ("results", "torrents", "hits", "items", "movies"):
                    if isinstance(node.get(nested), list):
                        return node[nested]
    return []


class CustomSource(Source):
    custom = True
    default = False

    def __init__(self, definition: dict):
        self.definition = dict(definition)
        self.id = definition.get("id") or "custom"
        self.label = definition.get("label") or self.id
        self.description = definition.get("description") or (
            KIND_LABELS.get(definition.get("kind"), "fonte personalizzata"))
        self.kind = definition.get("kind") or "link"
        self.default = bool(definition.get("default", True))

    # ------------------------------------------------------------- helpers
    def _url(self, query: str) -> str:
        template = self.definition.get("search_url") or ""
        if "{query}" in template:
            return template.replace("{query}", urllib.parse.quote_plus(query))
        return template

    def _result(self, **kw):
        media = self.definition.get("media") or kw.pop("media", "movie")
        return make_result(source=self.id, source_label=self.label,
                           media=media, **kw)

    # ------------------------------------------------------------- ricerca
    def search(self, filters: Filters):
        method = getattr(self, "_search_" + self.kind, None)
        if method is None:
            return [], f"tipo di fonte non supportato ({self.kind})"
        if not self.definition.get("search_url"):
            if self.kind != "link":
                return [], "URL di ricerca non configurato"
        return method(filters)

    # -- link ---------------------------------------------------------------
    def _search_link(self, filters: Filters):
        url = self._url(filters.q) or self.definition.get("search_url") or ""
        return [make_result(
            id=f"{self.id}:link",
            source=self.id, source_label=self.label,
            title=f'Cerca "{filters.q}" su {self.label}',
            link_out=True, url=url, media="", rank=0,
        )], "questa fonte non espone i risultati: si apre la ricerca sul sito"

    # -- rss ----------------------------------------------------------------
    def _search_rss(self, filters: Filters):
        text = http_text(self._url(filters.q), timeout=30)
        out = []
        for item in parse_feed_items(text):
            name = (item.get("title") or "").strip()
            if not name:
                continue
            info_hash = item.get("hash")
            magnet = item.get("magnet")
            if not magnet and info_hash:
                magnet = build_magnet(info_hash, name, DEFAULT_TRACKERS)
            torrent_url = item.get("torrent_url")
            if not magnet and not torrent_url:
                continue
            langs, sub = lang_fields(name)
            out.append(self._result(
                id=f"{self.id}:{info_hash or name[:40]}",
                title=name, magnet=magnet, info_hash=info_hash,
                size_bytes=parse_size(item.get("size")),
                seeds=to_int(item.get("seeds")), leechers=to_int(item.get("leechers")),
                date_ts=iso_ts(item.get("date")), url=item.get("link"),
                torrent_url=torrent_url, needs_magnet=bool(torrent_url and not magnet),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name), rank=len(out),
            ))
        return out, None

    # -- json ---------------------------------------------------------------
    def _search_json(self, filters: Filters):
        data = http_json(self._url(filters.q), timeout=30)
        items = _resolve_items(data, self.definition.get("results_path") or "")
        fields = self.definition.get("fields") or {}
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(_pick(item, fields.get("title"),
                             ("title", "name", "torrent_name", "n")) or "").strip()
            info_hash = str(_pick(item, fields.get("info_hash"),
                                  ("info_hash", "infohash", "hash")) or "").strip().lower()
            magnet = _pick(item, fields.get("magnet"), ("magnet", "magnetUrl", "magnet_url"))
            if magnet and not str(magnet).startswith("magnet:"):
                magnet = None
            torrent_url = _pick(item, fields.get("torrent_url"),
                                ("torrent_url", "torrent", "download", "enclosure", "link"))
            if torrent_url and not str(torrent_url).startswith(("http://", "https://", "magnet:")):
                torrent_url = None
            if not name and not info_hash:
                continue
            if not magnet:
                if info_hash and re.match(r"^[a-f0-9]{40}$", info_hash):
                    magnet = build_magnet(info_hash, name, DEFAULT_TRACKERS)
                elif torrent_url and str(torrent_url).startswith("magnet:"):
                    magnet, torrent_url = torrent_url, None
            if not magnet and not torrent_url:
                continue
            size_raw = _pick(item, fields.get("size"), ("size_bytes", "size", "bytes", "length"))
            size_bytes = size_raw if isinstance(size_raw, int) else parse_size(size_raw)
            date_raw = _pick(item, fields.get("date"), ("date", "created_unix", "added", "published"))
            date_ts = date_raw if isinstance(date_raw, int) else (to_int(date_raw) or iso_ts(date_raw))
            url = _pick(item, fields.get("url"), ("url", "details", "page", "link"))
            if url and not str(url).startswith("http"):
                url = None
            langs, sub = lang_fields(name)
            out.append(self._result(
                id=f"{self.id}:{info_hash or len(out)}",
                title=name or (info_hash or "torrent"),
                magnet=magnet, info_hash=info_hash or None,
                size_bytes=size_bytes,
                seeds=to_int(_pick(item, fields.get("seeds"), ("seeders", "seeds"))),
                leechers=to_int(_pick(item, fields.get("leechers"), ("leechers", "peers"))),
                date_ts=date_ts, url=url,
                torrent_url=torrent_url if str(torrent_url or "").startswith("http") else None,
                needs_magnet=bool(torrent_url and str(torrent_url).startswith("http") and not magnet),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name), rank=len(out),
            ))
        return out, None

    # -- html ---------------------------------------------------------------
    def _search_html(self, filters: Filters):
        page = http_text(self._url(filters.q), timeout=30)
        out = []
        for found in extract_magnets(page):
            name = (found.get("title") or "").strip() or found["hash"]
            langs, sub = lang_fields(name)
            out.append(self._result(
                id=f"{self.id}:{found['hash']}",
                title=name, magnet=found["magnet"], info_hash=found["hash"],
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name), rank=len(out),
            ))
        return out, None


def magnet_for_torrent_url(url: str) -> str:
    return magnet_from_url(url)
