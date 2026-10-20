# -*- coding: utf-8 -*-
"""Lettura di file .torrent (bencode) e costruzione di link magnet."""
from __future__ import annotations

import hashlib
from urllib.parse import quote

DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.dler.org:6969/announce",
]


def _bdecode(data: bytes, pos: int):
    """Decoder bencode minimale: ritorna (valore, posizione_successiva)."""
    char = data[pos:pos + 1]
    if char == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if char == b"l":
        pos += 1
        out = []
        while data[pos:pos + 1] != b"e":
            value, pos = _bdecode(data, pos)
            out.append(value)
        return out, pos + 1
    if char == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            key, pos = _bdecode(data, pos)
            value, pos = _bdecode(data, pos)
            out[key] = value
        return out, pos + 1
    if char == b"e":
        raise ValueError("bencode non valido")
    colon = data.index(b":", pos)
    size = int(data[pos:colon])
    start = colon + 1
    return data[start:start + size], start + size


def torrent_info(data: bytes) -> dict:
    """Estrae info_hash (hex), nome, tracker e webseed da un .torrent."""
    if data[:1] != b"d":
        raise ValueError("file torrent non valido")
    pos = 1
    top: dict = {}
    info_raw = None
    while data[pos:pos + 1] != b"e":
        key, pos = _bdecode(data, pos)
        start = pos
        value, pos = _bdecode(data, pos)
        top[key] = value
        if key == b"info":
            info_raw = data[start:pos]
    if info_raw is None:
        raise ValueError("blocco 'info' non trovato nel torrent")
    info_hash = hashlib.sha1(info_raw).hexdigest()

    info = top.get(b"info") or {}
    name = info.get(b"name") if isinstance(info, dict) else None
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")

    trackers: list[str] = []
    announce = top.get(b"announce")
    if isinstance(announce, bytes):
        trackers.append(announce.decode("utf-8", "replace"))
    announce_list = top.get(b"announce-list")
    if isinstance(announce_list, list):
        for tier in announce_list:
            entries = tier if isinstance(tier, list) else [tier]
            for entry in entries:
                if isinstance(entry, bytes):
                    trackers.append(entry.decode("utf-8", "replace"))

    webseeds: list[str] = []
    url_list = top.get(b"url-list")
    if isinstance(url_list, bytes):
        webseeds.append(url_list.decode("utf-8", "replace"))
    elif isinstance(url_list, list):
        for entry in url_list:
            if isinstance(entry, bytes):
                webseeds.append(entry.decode("utf-8", "replace"))

    return {"info_hash": info_hash, "name": name, "trackers": trackers, "webseeds": webseeds}


def build_magnet(info_hash: str, name: str | None = None,
                 trackers=(), webseeds=()) -> str:
    parts = ["xt=urn:btih:" + info_hash]
    if name:
        parts.append("dn=" + quote(name, safe=""))
    for tracker in dict.fromkeys(trackers):
        parts.append("tr=" + quote(tracker, safe=""))
    for webseed in dict.fromkeys(webseeds):
        parts.append("ws=" + quote(webseed, safe=""))
    return "magnet:?" + "&".join(parts)
