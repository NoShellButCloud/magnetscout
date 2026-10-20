# -*- coding: utf-8 -*-
"""Registro delle fonti e orchestrazione della ricerca.

Le fonti incluse vivono in sources_builtin.py, quelle aggiunte dall'utente in
custom_sources.json (vedi source_custom.py), la scoperta automatica in
sourcecrawl.py. Questo modulo espone un'unica interfaccia usata dal server.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from source_custom import (KIND_LABELS, KINDS, CustomSource,  # noqa: F401
                           delete as _delete_custom, definitions as custom_definitions,
                           sources as custom_sources, upsert as _upsert_custom)
from sourcebase import (Filters, Source, dedupe, magnet_from_url, matches,  # noqa: F401
                        sort_results)
from sourcecrawl import discover  # noqa: F401
from sources_builtin import (BUILTIN_SOURCES, ArchiveSource, ApibaySource,  # noqa: F401
                             KnabenSource, NyaaSource, TorrentsCsvSource, YtsSource,
                             archive_magnet)


def current_sources() -> list[Source]:
    """Fonti incluse + fonti personalizzate abilitate."""
    return list(BUILTIN_SOURCES) + custom_sources()


def registry() -> dict[str, Source]:
    return {source.id: source for source in current_sources()}


def source_infos() -> list[dict]:
    return [source.info() for source in current_sources()]


def upsert_custom_source(definition: dict) -> dict:
    return _upsert_custom(definition)


def delete_custom_source(source_id: str) -> bool:
    return _delete_custom(source_id)


def search_all(filters: Filters, source_ids=None, max_per_source: int = 60):
    """Interroga le fonti in parallelo e ritorna (risultati, statistiche)."""
    index = registry()
    ids = [i for i in (source_ids or list(index)) if i in index]
    sources = [index[i] for i in ids]

    def run(source: Source):
        started = time.time()
        try:
            items, note = source.search(filters)
            items = [r for r in items if matches(r, filters)]
            items.sort(key=lambda r: r["rank"])
            items = items[:max_per_source]
            stat = {"id": source.id, "label": source.label, "ok": True,
                    "count": len(items), "ms": int((time.time() - started) * 1000),
                    "error": None, "note": note}
        except Exception as exc:  # una fonte giu' non deve bloccare le altre
            items = []
            stat = {"id": source.id, "label": source.label, "ok": False,
                    "count": 0, "ms": int((time.time() - started) * 1000),
                    "error": str(exc), "note": None}
        return source.id, items, stat

    with ThreadPoolExecutor(max_workers=8) as pool:
        gathered = list(pool.map(run, sources))

    results = [r for _, items, _ in gathered for r in items]
    stats = [stat for _, _, stat in gathered]
    return dedupe(results), stats


def magnet_for_torrent_url(url: str) -> str:
    """Magnet generato da un file .torrent (fonti custom, link di altri indici)."""
    return magnet_from_url(url)
