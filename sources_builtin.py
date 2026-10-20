# -*- coding: utf-8 -*-
"""Adattatori verso gli indici pubblici inclusi in MagnetScout.

Ogni fonte implementa Source.search(filters) e ritorna (risultati, nota).
I risultati sono dizionari normalizzati (vedi make_result in sourcebase).
"""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from html import unescape

from paths import data_path
from sourcebase import (Filters, MAGNET_RE, Source, feed_ts, guess_media,
                        http_json, http_json_post, http_text, http_bytes,
                        iso_ts, lang_fields, looks_adult, magnet_hash,
                        make_result, normalize_info_hash, parse_feed_items,
                        strip_tags, to_int, year_ts)
from taxonomy import ARCHIVE_COLLECTION_GENRES, canon_genre_yts
from textutil import (detect_3d, detect_quality, detect_resolution, norm_langs,
                      parse_size, primary_lang)
from torrent import DEFAULT_TRACKERS, build_magnet, torrent_info


class ApibaySource(Source):
    """The Pirate Bay via l'API JSON pubblica apibay.org."""

    id = "apibay"
    label = "The Pirate Bay"
    description = "Indice pubblico (apibay.org); qualita'/lingua dedotte dal nome del file"
    CATS = {
        "movie": ["201", "202", "207", "209"],
        "tv": ["205", "208"],
        "": ["201", "202", "207", "209", "205", "208"],
    }
    TV_CATS = {"205", "208"}

    def _fetch_cat(self, query: str, cat: str):
        url = "https://apibay.org/q.php?q=" + urllib.parse.quote(query) + "&cat=" + cat
        try:
            data = http_json(url)
        except Exception as exc:  # una categoria puo' fallire senza uccidere la fonte
            return None, str(exc)
        return data if isinstance(data, list) else [], None

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        cats = self.CATS.get(filters.mtype, self.CATS[""])
        items: dict[str, dict] = {}
        errors = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            fetches = list(pool.map(lambda c: self._fetch_cat(filters.q, c), cats))
        for data, error in fetches:
            if error:
                errors.append(error)
                continue
            for item in data:
                if str(item.get("id")) == "0" or item.get("name") == "No results returned":
                    continue
                info_hash = str(item.get("info_hash") or "").strip().lower()
                if info_hash and info_hash not in items:
                    items[info_hash] = item
        if not items and errors:
            raise RuntimeError(errors[0])

        out = []
        for rank, (info_hash, item) in enumerate(items.items()):
            name = str(item.get("name") or "")
            langs, sub = lang_fields(name)
            category = str(item.get("category") or "")
            imdb = item.get("imdb") or None
            if imdb and not re.match(r"^tt\d+$", str(imdb)):
                imdb = None
            out.append(make_result(
                id="apibay:" + str(item.get("id")),
                source=self.id, source_label=self.label,
                title=name,
                magnet=build_magnet(info_hash, name, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=parse_size(item.get("size")),
                seeds=to_int(item.get("seeders")),
                leechers=to_int(item.get("leechers")),
                date_ts=to_int(item.get("added")),
                url="https://thepiratebay.org/description.php?id=" + str(item.get("id")),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name),
                media="tv" if category in self.TV_CATS else "movie",
                imdb=imdb, rank=rank,
            ))
        return out, None


class YtsSource(Source):
    """YTS via API v2, con fallback su piu' mirror."""

    id = "yts"
    label = "YTS"
    description = "Film in 720p/1080p/4K con genere e lingua nei metadati"
    HOSTS = ["yts.mx", "yts.lt", "yts.rs", "yts.gg"]
    _base = None
    _lock = threading.Lock()

    _GENRE_PARAM = {
        "azione": "Action", "avventura": "Adventure", "animazione": "Animation",
        "commedia": "Comedy", "crime": "Crime", "documentario": "Documentary",
        "dramma": "Drama", "famiglia": "Family", "fantasy": "Fantasy",
        "fantascienza": "Sci-Fi", "guerra": "War", "horror": "Horror",
        "mistero": "Mystery", "musical": "Music", "romantico": "Romance",
        "storico": "History", "thriller": "Thriller", "western": "Western",
    }
    _QUALITY_PARAM = {"720p": "720p", "1080p": "1080p", "2160p": "2160p"}
    _TYPE_QUALITY = {"bluray": "bluray", "web": "web", "hdtv": "hdtv",
                     "dvd": "dvd", "remux": "remux"}

    def _api(self, path: str, params: dict):
        with self._lock:
            first = self._base
        hosts = ([first] + [h for h in self.HOSTS if h != first]) if first else list(self.HOSTS)
        last_error = None
        for host in hosts:
            try:
                url = f"https://{host}{path}?" + urllib.parse.urlencode(params)
                data = http_json(url)
                with self._lock:
                    self._base = host
                return data
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"nessun mirror YTS raggiungibile ({last_error})")

    def search(self, filters: Filters):
        if filters.mtype == "tv":
            return [], "questa fonte copre solo film"
        genre_param = None
        if filters.genre:
            genre_param = self._GENRE_PARAM.get(filters.genre)
            if not genre_param:
                return [], "genere non mappato su questa fonte"
        params = {"query_term": filters.q, "limit": 50, "page": 1}
        if genre_param:
            params["genre"] = genre_param
        quality_param = self._QUALITY_PARAM.get(filters.res)
        if quality_param:
            params["quality"] = quality_param

        data = self._api("/api/v2/list_movies.json", params)
        movies = ((data or {}).get("data") or {}).get("movies") or []
        out = []
        for movie in movies:
            title = movie.get("title_long") or movie.get("title") or ""
            year = movie.get("year")
            if year and str(year) not in str(title):
                full_title = f"{title} ({year})"
            else:
                full_title = title
            langs, sub = lang_fields(full_title, movie.get("language"))
            genres = [s for s in (canon_genre_yts(g) for g in movie.get("genres") or []) if s]
            slug = movie.get("slug") or ""
            host = self._base or self.HOSTS[0]
            for torrent in movie.get("torrents") or []:
                info_hash = str(torrent.get("hash") or "").strip()
                if not info_hash:
                    continue
                type_quality = self._TYPE_QUALITY.get(str(torrent.get("type") or "").lower())
                quality = detect_quality(full_title) or type_quality
                label = f"{full_title} [{torrent.get('quality', '')}]"
                out.append(make_result(
                    id="yts:" + info_hash.lower(),
                    source=self.id, source_label=self.label,
                    title=full_title,
                    magnet=build_magnet(info_hash, label, DEFAULT_TRACKERS),
                    info_hash=info_hash.lower(),
                    size_bytes=torrent.get("size_bytes") or parse_size(torrent.get("size")),
                    seeds=to_int(torrent.get("seeds")),
                    leechers=to_int(torrent.get("peers")),
                    date_ts=to_int(torrent.get("date_uploaded_unix")),
                    url=f"https://{host}/movies/{slug}",
                    lang=primary_lang(langs), langs=langs, sub=sub,
                    resolution=torrent.get("quality") or detect_resolution(full_title),
                    quality=quality, media="movie", genres=genres,
                    imdb=movie.get("imdb_code") or None, rank=len(out),
                ))
        return out, None


class ArchiveSource(Source):
    """Internet Archive: film di pubblico dominio / licenze aperte.

    Il magnet non e' fornito dall'indice: viene generato al volo dal file
    .torrent dell'item (vedi archive_magnet e l'endpoint /api/magnet).
    """

    id = "archive"
    label = "Internet Archive"
    description = "Film di pubblico dominio; il magnet viene generato dal file .torrent"
    BASE = "https://archive.org"
    COLLECTIONS = ["feature_films", "silent_films", "SciFi_Horror",
                   "animationandcartoons", "classic_cartoons", "short_films",
                   "moviesandfilms", "prelinger"]
    _GENRE_COLLECTIONS = {
        "animazione": ["animationandcartoons", "classic_cartoons"],
        "documentario": ["prelinger"],
        "horror": ["SciFi_Horror"],
        "fantascienza": ["SciFi_Horror"],
    }
    _LANG_QUERY = {
        "it": "italian OR ita", "en": "english OR eng", "fr": "french OR fra OR fre",
        "es": "spanish OR spa", "de": "german OR ger OR deu", "pt": "portuguese OR por",
        "ru": "russian OR rus", "ja": "japanese OR jpn", "ko": "korean OR kor",
        "zh": "chinese OR chi", "hi": "hindi", "ar": "arabic", "tr": "turkish",
        "pl": "polish", "nl": "dutch", "sv": "swedish", "no": "norwegian",
        "da": "danish", "fi": "finnish", "el": "greek", "he": "hebrew",
        "hu": "hungarian", "cs": "czech", "ro": "romanian", "uk": "ukrainian",
    }

    def search(self, filters: Filters):
        clean = re.sub(r'["\(\)\[\]\{\}\^~\\/:\?]', " ", filters.q).strip()
        if not clean:
            return [], None
        parts = [f'title:("{clean}")', "mediatype:movies"]
        if filters.mtype == "tv":
            parts.append("collection:classic_tv")
        elif filters.genre:
            collections = self._GENRE_COLLECTIONS.get(filters.genre)
            if not collections:
                return [], "genere non mappato su questa fonte (prova YTS)"
            parts.append("(" + " OR ".join("collection:" + c for c in collections) + ")")
        else:
            parts.append("(" + " OR ".join("collection:" + c for c in self.COLLECTIONS) + ")")
        if filters.lang == "it":
            parts.append("language:(italian OR ita)")
        elif filters.lang and filters.lang != "multi":
            terms = self._LANG_QUERY.get(filters.lang)
            if terms:
                parts.append(f"language:({terms})")

        params = [
            ("q", " AND ".join(parts)), ("rows", "50"), ("page", "1"),
            ("output", "json"), ("sort[]", "downloads desc"),
            ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "year"),
            ("fl[]", "language"), ("fl[]", "item_size"), ("fl[]", "downloads"),
            ("fl[]", "publicdate"), ("fl[]", "collection"),
        ]
        url = self.BASE + "/advancedsearch.php?" + urllib.parse.urlencode(params)
        data = http_json(url)
        docs = ((data or {}).get("response") or {}).get("docs") or []
        out = []
        for rank, doc in enumerate(docs):
            identifier = doc.get("identifier")
            if not identifier:
                continue
            title = doc.get("title") or identifier
            lang_codes = norm_langs(doc.get("language"))
            collections = doc.get("collection") or []
            genres = sorted({slug for coll in collections
                             for slug in ARCHIVE_COLLECTION_GENRES.get(coll, [])})
            date_ts = iso_ts(doc.get("publicdate")) or year_ts(doc.get("year"))
            out.append(make_result(
                id="archive:" + identifier,
                source=self.id, source_label=self.label,
                title=title, magnet=None, needs_magnet=True,
                size_bytes=doc.get("item_size") or None,
                date_ts=date_ts,
                url=f"https://archive.org/details/{identifier}",
                lang=primary_lang(lang_codes), langs=lang_codes,
                media="tv" if filters.mtype == "tv" else "movie",
                genres=genres, rank=rank,
            ))
        return out, None


# ---------------------------------------------------------------------------
# Generazione del magnet per gli item di archive.org (dal file .torrent)
# ---------------------------------------------------------------------------
_magnet_cache: dict[str, str] = {}
_magnet_lock = threading.Lock()


def archive_magnet(identifier: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9._-]", "", identifier or "")
    if not ident:
        raise ValueError("identificatore non valido")
    with _magnet_lock:
        cached = _magnet_cache.get(ident)
    if cached:
        return cached

    meta = http_json(f"https://archive.org/metadata/{ident}", timeout=30)
    files = [f.get("name") for f in (meta.get("files") or []) if f.get("name")]
    torrent_name = next((n for n in files if n.endswith("_archive.torrent")), None)
    if not torrent_name:
        torrent_name = next((n for n in files if n.endswith(".torrent")), None)
    if not torrent_name:
        raise RuntimeError("nessun file .torrent disponibile per questo elemento")

    url = f"https://archive.org/download/{ident}/{urllib.parse.quote(torrent_name)}"
    info = torrent_info(http_bytes(url, timeout=90))
    magnet = build_magnet(
        info["info_hash"], info.get("name") or ident,
        info["trackers"] or DEFAULT_TRACKERS, info["webseeds"])
    with _magnet_lock:
        _magnet_cache[ident] = magnet
    return magnet


class KnabenSource(Source):
    """Knaben: aggregatore (The Pirate Bay, 1337x e altri) con API JSON.

    L'API richiede esattamente i campi "query" e "search_type" ("100%" o
    "score"): con nomi diversi la ricerca viene ignorata e tornano sempre gli
    stessi risultati. Le categorie non sono filtrabili in modo affidabile,
    quindi si filtra qui il campo testuale "category".
    """

    id = "knaben"
    label = "Knaben"
    description = "Aggregatore multi-indice (The Pirate Bay, 1337x, ...) via API JSON"
    API = "https://api.knaben.org/v1"

    @staticmethod
    def _media(category: str):
        category = str(category or "")
        if category.startswith("TV"):
            return "tv"
        if category.startswith("Movies") or category.startswith("Anime"):
            return "movie"
        return None

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        body = {
            "search_type": "100%",
            "search_field": "title",
            "query": filters.q,
            "order_by": "seeders",
            "order_direction": "desc",
            "from": 0,
            "size": 100,
            "hide_unsafe": False,
            "hide_xxx": True,
        }
        data = http_json_post(self.API, body, timeout=25)
        hits = (data or {}).get("hits") or []
        out = []
        skipped = 0
        for hit in hits:
            media = self._media(hit.get("category"))
            if not media:
                skipped += 1
                continue
            if filters.mtype and media != filters.mtype:
                continue
            name = str(hit.get("title") or "")
            info_hash = str(hit.get("hash") or "").strip().lower() or None
            magnet = hit.get("magnetUrl") or None
            torrent_url = None
            if not magnet:
                if info_hash:
                    magnet = build_magnet(info_hash, name, DEFAULT_TRACKERS)
                elif hit.get("link"):
                    torrent_url = hit["link"]
            if not magnet and not torrent_url:
                continue
            langs, sub = lang_fields(name)
            out.append(make_result(
                id="knaben:" + str(hit.get("id") or info_hash or len(out)),
                source=self.id, source_label=self.label, title=name,
                magnet=magnet, info_hash=info_hash,
                size_bytes=to_int(hit.get("bytes")),
                seeds=to_int(hit.get("seeders")), leechers=to_int(hit.get("peers")),
                date_ts=iso_ts(hit.get("date")), url=hit.get("details"),
                torrent_url=torrent_url, needs_magnet=bool(torrent_url),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name), media=media, rank=len(out),
            ))
        note = None
        if skipped and not out:
            note = "nessun risultato con categoria film/serie"
        return out, note


class TorrentsCsvSource(Source):
    """Torrents-CSV: database pubblico di metadati (nessuna categoria)."""

    id = "torrents-csv"
    label = "Torrents-CSV"
    description = "Database pubblico di metadati (infohash, seeders, dimensione)"
    API = "https://torrents-csv.com/service/search"

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        if filters.mtype == "tv":
            return [], "questa fonte non espone la categoria film/serie"
        url = self.API + "?" + urllib.parse.urlencode({"q": filters.q, "size": 60})
        data = http_json(url)
        items = (data or {}).get("torrents") or []
        out = []
        for item in items:
            name = str(item.get("name") or "")
            info_hash = str(item.get("infohash") or "").strip().lower()
            if not name or not info_hash:
                continue
            seeds = to_int(item.get("seeders"))
            if seeds is not None and seeds <= 0:
                continue
            langs, sub = lang_fields(name)
            out.append(make_result(
                id="torrents-csv:" + info_hash,
                source=self.id, source_label=self.label, title=name,
                magnet=build_magnet(info_hash, name, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=to_int(item.get("size_bytes")),
                seeds=seeds, leechers=to_int(item.get("leechers")),
                date_ts=to_int(item.get("created_unix")),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name), media="movie", rank=len(out),
            ))
        return out, None


class NyaaSource(Source):
    """Nyaa: anime e live action via feed RSS."""

    id = "nyaa"
    label = "Nyaa"
    description = "Anime e live action via feed RSS (non distingue film e serie)"
    BASE = "https://nyaa.si"

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        if filters.mtype == "tv":
            return [], "questa fonte non distingue film e serie"
        params = {"page": "rss", "q": filters.q, "f": "0", "s": "seeders", "o": "desc"}
        text = http_text(self.BASE + "/?" + urllib.parse.urlencode(params), timeout=30)
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
            out.append(make_result(
                id="nyaa:" + (info_hash or name[:40]),
                source=self.id, source_label=self.label, title=name,
                magnet=magnet, info_hash=info_hash,
                size_bytes=parse_size(item.get("size")),
                seeds=to_int(item.get("seeds")), leechers=to_int(item.get("leechers")),
                date_ts=iso_ts(item.get("date")), url=item.get("link"),
                torrent_url=torrent_url, needs_magnet=bool(torrent_url and not magnet),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(name), is3d=detect_3d(name),
                quality=detect_quality(name), media="movie", rank=len(out),
            ))
        return out, None


class RutorSource(Source):
    """RuTor (rutor.info): grande indice russo generalista.

    Nelle righe di risultato ci sono magnet (info hash in hex), dimensione,
    data e numero di seed: il magnet viene ricostruito con il titolo vero e i
    tracker predefiniti (quello originale contiene solo "dn=rutor.info").
    """

    id = "rutor"
    label = "RuTor"
    description = "Indice russo generalista (film, serie, libri) con magnet nelle pagine di ricerca"
    BASE = "https://rutor.info"
    _ROW_RE = re.compile(r'<tr class="(?:gai|tum|backgr)">(.*?)</tr>', re.S)
    _MAGNET_RE = re.compile(r'href="(magnet:\?xt=urn:btih:[0-9a-fA-F]{40})')
    _DETAIL_RE = re.compile(r'<a href="(/torrent/\d+/[^"]*)"[^>]*>(.*?)</a>', re.S)
    _SIZE_CELLS_RE = re.compile(r'<td align="right">(.*?)</td>', re.S)
    _SIZE_UNIT_RE = re.compile(r"\b[KMGT]?B\b", re.I)
    _SEEDS_RE = re.compile(r'alt="S"[^>]*/?>\s*&nbsp;\s*(\d+)')
    _LEECH_RE = re.compile(r'alt="L"[^>]*/?>\s*<span class="red">\s*&nbsp;\s*(\d+)')
    _DATE_RE = re.compile(r"^(\d{1,2})\s+(\D{3})\s+(\d{2})$")
    _MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
               "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

    def _date_ts(self, text: str):
        match = self._DATE_RE.match(strip_tags(text))
        if not match:
            return None
        day, month, year = match.groups()
        month_number = self._MONTHS.get(month.lower())
        if not month_number:
            return None
        try:
            from datetime import datetime, timezone
            return int(datetime(2000 + int(year), month_number, int(day),
                                tzinfo=timezone.utc).timestamp())
        except ValueError:
            return None

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        url = (self.BASE + "/search/0/0/000/0/" +
               urllib.parse.quote(filters.q, safe=""))
        page = http_text(url, timeout=25)
        out = []
        for row in self._ROW_RE.findall(page):
            magnet_match = self._MAGNET_RE.search(row)
            detail_match = self._DETAIL_RE.search(row)
            if not magnet_match or not detail_match:
                continue
            title = strip_tags(detail_match.group(2))
            info_hash = normalize_info_hash(magnet_hash(magnet_match.group(1)))
            if not title or not info_hash or looks_adult(title):
                continue
            size_match = next(
                (strip_tags(cell) for cell in reversed(self._SIZE_CELLS_RE.findall(row))
                 if self._SIZE_UNIT_RE.search(strip_tags(cell))), None)
            seeds_match = self._SEEDS_RE.search(row)
            leech_match = self._LEECH_RE.search(row)
            date_match = re.search(r"<td>(.*?)</td>", row, re.S)
            media = guess_media(title)
            if filters.mtype and media != filters.mtype:
                continue
            langs, sub = lang_fields(title)
            out.append(make_result(
                id="rutor:" + info_hash,
                source=self.id, source_label=self.label, title=title,
                magnet=build_magnet(info_hash, title, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=parse_size(size_match) if size_match else None,
                seeds=to_int(seeds_match.group(1)) if seeds_match else None,
                leechers=to_int(leech_match.group(1)) if leech_match else None,
                date_ts=self._date_ts(date_match.group(1)) if date_match else None,
                url=self.BASE + detail_match.group(1),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(title), is3d=detect_3d(title),
                quality=detect_quality(title), media=media, rank=len(out),
            ))
        return out, None


class TorrentKittySource(Source):
    """TorrentKitty: indice generalista con magnet nella pagina dei risultati."""

    id = "torrentkitty"
    label = "TorrentKitty"
    description = "Indice generalista con magnet, dimensione e data nella pagina di ricerca"
    BASE = "https://www.torrentkitty.tv"
    _ROW_RE = re.compile(
        r'<tr><td class="name">(.*?)</td>\s*<td class="size">(.*?)</td>\s*'
        r'<td class="date">(.*?)</td>\s*<td class="action">(.*?)</td></tr>',
        re.S | re.I)
    _INFO_RE = re.compile(r'href="/information/([0-9A-Fa-f]{40})"')

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        url = self.BASE + "/search/" + urllib.parse.quote(filters.q, safe="") + "/"
        page = http_text(url, timeout=25)
        out = []
        for row in self._ROW_RE.findall(page):
            name_cell, size_cell, date_cell, action_cell = row
            title = strip_tags(name_cell)
            info_match = self._INFO_RE.search(action_cell)
            info_hash = normalize_info_hash(
                info_match.group(1) if info_match else magnet_hash(action_cell))
            if not title or not info_hash or looks_adult(title):
                continue
            media = guess_media(title)
            if filters.mtype and media != filters.mtype:
                continue
            langs, sub = lang_fields(title)
            out.append(make_result(
                id="torrentkitty:" + info_hash,
                source=self.id, source_label=self.label, title=title,
                magnet=build_magnet(info_hash, title, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=parse_size(strip_tags(size_cell)),
                date_ts=iso_ts(strip_tags(date_cell)),
                url=self.BASE + "/information/" + info_hash,
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(title), is3d=detect_3d(title),
                quality=detect_quality(title), media=media, rank=len(out),
            ))
        return out, None


class TntVillageSource(Source):
    """TNT Village Database: archivio storico dei torrent italiani.

    L'archivio non pubblica i magnet ma i file .torrent su itorrents.org: il
    magnet viene ricostruito dall'info hash presente nell'URL. Non sono
    indicate le fonti (seed), quindi l'ordinamento per seed li tiene in coda.
    """

    id = "tntvillage"
    label = "TNT Village DB"
    description = "Archivio storico italiano TNT Village (magnet ricostruito dall'info hash)"
    BASE = "https://tntvillage-db.programmiedovetrovarli.it"
    _ROW_RE = re.compile(
        r'<td class="px-6 py-4">\s*<article>\s*<a href="([^"]+)"[^>]*>\s*(.*?)\s*</a>'
        r'.*?</article>\s*</td>\s*'
        r'<td class="px-6 py-4 text-gray-700 font-medium">\s*(.*?)\s*</td>\s*'
        r'<td class="px-6 py-4 text-center">\s*<a\s+href="https://itorrents\.org/torrent/'
        r'([0-9A-Fa-f]{40})\.torrent"',
        re.S)

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        url = self.BASE + "/?search=" + urllib.parse.quote(filters.q, safe="")
        page = http_text(url, timeout=25)
        out = []
        for detail, title_html, size_html, raw_hash in self._ROW_RE.findall(page):
            title = strip_tags(title_html)
            info_hash = normalize_info_hash(raw_hash)
            if not title or not info_hash or looks_adult(title):
                continue
            media = guess_media(title)
            if filters.mtype and media != filters.mtype:
                continue
            langs, sub = lang_fields(title)
            out.append(make_result(
                id="tntvillage:" + info_hash,
                source=self.id, source_label=self.label, title=title,
                magnet=build_magnet(info_hash, title, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=parse_size(strip_tags(size_html)),
                url=detail,
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(title), is3d=detect_3d(title),
                quality=detect_quality(title), media=media, rank=len(out),
            ))
        return out, None


class AnimeToshoSource(Source):
    """AnimeTosho: anime e live action (magnet e dimensione nella pagina)."""

    id = "animetosho"
    label = "AnimeTosho"
    description = "Anime e live action; magnet e dimensione dalla pagina di ricerca"
    BASE = "https://animetosho.org"
    _ENTRY_RE = re.compile(
        r'<div class="link"><a href="(https://animetosho\.org/view/[^"]+)">(.*?)</a></div>',
        re.S)
    _SIZE_RE = re.compile(r'title="Total file size:\s*([\d,]+)\s*bytes"')
    _MAGNET_RE = re.compile(r'href="(magnet:[^"]+)"')
    _TORRENT_RE = re.compile(r'href="(https?://[^"]+\.torrent)"')

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        params = {"q": filters.q}
        page = http_text(self.BASE + "/search?" + urllib.parse.urlencode(params), timeout=30)
        entries = list(self._ENTRY_RE.finditer(page))
        out = []
        for index, entry in enumerate(entries):
            end = entries[index + 1].start() if index + 1 < len(entries) else len(page)
            block = page[entry.end():min(end, entry.end() + 6000)]
            title = strip_tags(entry.group(2))
            magnet_match = self._MAGNET_RE.search(block)
            if not title or not magnet_match:
                continue
            magnet = unescape(magnet_match.group(1))
            info_hash = normalize_info_hash(magnet_hash(magnet))
            if not info_hash:
                continue
            media = guess_media(title, default="movie")
            if filters.mtype and media != filters.mtype:
                continue
            langs, sub = lang_fields(title)
            size_match = self._SIZE_RE.search(block)
            torrent_match = self._TORRENT_RE.search(block)
            out.append(make_result(
                id="animetosho:" + info_hash,
                source=self.id, source_label=self.label, title=title,
                magnet=build_magnet(info_hash, title, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=(int(size_match.group(1).replace(",", ""))
                            if size_match else None),
                torrent_url=torrent_match.group(1) if torrent_match else None,
                url=entry.group(1),
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(title), is3d=detect_3d(title),
                quality=detect_quality(title), media=media, rank=len(out),
            ))
        return out, None


class TokyoToshoSource(Source):
    """Tokyo Toshokan: anime via feed RSS (magnet nel corpo del feed)."""

    id = "tokyotosho"
    label = "Tokyo Toshokan"
    description = "Anime via feed RSS (magnet nel feed; sezione per adulti esclusa)"
    BASE = "https://www.tokyotosho.info"
    _ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
    _SIZE_RE = re.compile(r"Size:\s*([\d.,]+\s*[KMGT]?B)", re.I)

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        params = {"filter": "1", "terms": filters.q}
        text = http_text(self.BASE + "/rss.php?" + urllib.parse.urlencode(params),
                         timeout=30)
        out = []
        for item in self._ITEM_RE.findall(text):
            title_match = re.search(r"<title>(.*?)</title>", item, re.S)
            title = strip_tags(title_match.group(1)) if title_match else ""
            magnet_match = MAGNET_RE.search(item)
            if not title or not magnet_match:
                continue
            info_hash = normalize_info_hash(magnet_hash(magnet_match.group()))
            if not info_hash or looks_adult(title):
                continue
            media = guess_media(title, default="tv")
            if filters.mtype and media != filters.mtype:
                continue
            size_match = self._SIZE_RE.search(item)
            date_match = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
            link_match = re.search(r"<guid><!\[CDATA\[(.*?)\]\]>\s*</guid>", item, re.S)
            langs, sub = lang_fields(title)
            out.append(make_result(
                id="tokyotosho:" + info_hash,
                source=self.id, source_label=self.label, title=title,
                magnet=build_magnet(info_hash, title, DEFAULT_TRACKERS),
                info_hash=info_hash,
                size_bytes=parse_size(size_match.group(1)) if size_match else None,
                date_ts=feed_ts(date_match.group(1)) if date_match else None,
                url=link_match.group(1) if link_match else None,
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(title), is3d=detect_3d(title),
                quality=detect_quality(title), media=media, rank=len(out),
            ))
        return out, None


class SubsPleaseSource(Source):
    """SubsPlease: episodi anime in 480p/720p/1080p con magnet (API JSON)."""

    id = "subsplease"
    label = "SubsPlease"
    description = "Anime sub (480p/720p/1080p) con magnet, ricerca nell'API JSON"
    API = "https://subsplease.org/api/"
    _XL_RE = re.compile(r"[?&]xl=(\d+)")

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        params = {"f": "search", "tz": "UTC", "s": filters.q}
        data = http_json(self.API + "?" + urllib.parse.urlencode(params), timeout=25)
        if isinstance(data, list):
            return [], None  # nessun risultato: l'API risponde con una lista vuota
        if not isinstance(data, dict):
            return [], "risposta inattesa dall'API"
        out = []
        for name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            for download in entry.get("downloads") or []:
                magnet = (download or {}).get("magnet")
                if not magnet:
                    continue
                res = str(download.get("res") or "").strip()
                title = f"[SubsPlease] {name}" + (f" ({res}p)" if res else "")
                info_hash = normalize_info_hash(magnet_hash(magnet))
                if not info_hash:
                    continue
                length = self._XL_RE.search(magnet)
                langs, sub = lang_fields(title)
                out.append(make_result(
                    id="subsplease:" + info_hash,
                    source=self.id, source_label=self.label, title=title,
                    magnet=build_magnet(info_hash, title, DEFAULT_TRACKERS),
                    info_hash=info_hash,
                    size_bytes=int(length.group(1)) if length else None,
                    date_ts=feed_ts(entry.get("release_date")),
                    url=download.get("torrent") or None,
                    torrent_url=download.get("torrent") or None,
                    lang=primary_lang(langs), langs=langs, sub=sub,
                    resolution=res + "p" if res else detect_resolution(title),
                    quality=detect_quality(title), media="tv", rank=len(out),
                ))
        return out, None


class AcademicTorrentsSource(Source):
    """Academic Torrents: corsi e materiali accademici di pubblico dominio.

    Il sito non espone una ricerca: si scarica una volta al giorno il catalogo
    XML (circa 3 MB) e si cerca in locale.
    """

    id = "academictorrents"
    label = "Academic Torrents"
    description = "Corsi e materiali accademici; catalogo scaricato una volta al giorno"
    default = False
    DB_URL = "https://academictorrents.com/database.xml"
    TTL_SECONDS = 24 * 3600
    CACHE_FILE = data_path("academictorrents_cache.xml")
    _ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
    _FIELD_RE = re.compile(r"<(\w+)>(.*?)</\1>", re.S)
    _cache: list = []
    _cache_ts = 0.0
    _lock = threading.Lock()

    def _catalog(self):
        with self._lock:
            if self._cache and time.time() - self._cache_ts < self.TTL_SECONDS:
                return self._cache
        raw = None
        try:
            if os.path.isfile(self.CACHE_FILE) and \
                    time.time() - os.path.getmtime(self.CACHE_FILE) < self.TTL_SECONDS:
                with open(self.CACHE_FILE, "r", encoding="utf-8") as handle:
                    raw = handle.read()
        except OSError:
            raw = None
        if raw is None:
            raw = http_text(self.DB_URL, timeout=90)
            try:
                with open(self.CACHE_FILE, "w", encoding="utf-8") as handle:
                    handle.write(raw)
            except OSError:
                pass
        items = []
        for block in self._ITEM_RE.findall(raw):
            fields = {}
            for key, value in self._FIELD_RE.findall(block):
                fields.setdefault(key.lower(), value.strip())
            info_hash = normalize_info_hash(fields.get("infohash"))
            title = strip_tags(fields.get("title"))
            if not info_hash or not title:
                continue
            items.append({
                "title": title, "hash": info_hash,
                "size": parse_size(fields.get("size")),
                "link": strip_tags(fields.get("link") or fields.get("guid")),
                "category": strip_tags(fields.get("category")),
            })
        with self._lock:
            self._cache = items
            self._cache_ts = time.time()
        return items

    def search(self, filters: Filters):
        if filters.genre:
            return [], "il filtro genere non e' disponibile su questa fonte"
        words = [w for w in re.split(r"\W+", filters.q.lower()) if len(w) > 2]
        if not words:
            return [], "serve una ricerca di almeno 3 lettere"
        out = []
        for item in self._catalog():
            haystack = item["title"].lower()
            if not all(word in haystack for word in words):
                continue
            langs, sub = lang_fields(item["title"])
            out.append(make_result(
                id="academictorrents:" + item["hash"],
                source=self.id, source_label=self.label, title=item["title"],
                magnet=build_magnet(item["hash"], item["title"], DEFAULT_TRACKERS),
                info_hash=item["hash"], size_bytes=item["size"], url=item["link"],
                lang=primary_lang(langs), langs=langs, sub=sub,
                resolution=detect_resolution(item["title"]),
                quality=detect_quality(item["title"]), media="movie",
                rank=len(out),
            ))
            if len(out) >= 60:
                break
        note = None
        if not out:
            note = "nessuna corrispondenza nel catalogo locale"
        return out, note


BUILTIN_SOURCES = [ApibaySource(), YtsSource(), ArchiveSource(),
                   KnabenSource(), TorrentsCsvSource(), NyaaSource(),
                   RutorSource(), TorrentKittySource(), TntVillageSource(),
                   AnimeToshoSource(), TokyoToshoSource(), SubsPleaseSource(),
                   AcademicTorrentsSource()]

