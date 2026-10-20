# -*- coding: utf-8 -*-
"""Preferenze dell'utente: filtri e fonti selezionate, salvati su disco.

Il file preferences.json ha questa forma:

    {
      "last":     {"filters": {...}, "sources": ["apibay", ...], "updated": 1700000000},
      "profiles": {"Film in italiano": {"filters": {...}, "sources": [...], "updated": 1700000000}}
    }

"last" e' l'ultimo stato usato (ripristinato all'avvio), "profiles" sono i
profili con un nome scelto dall'utente. Il file vive accanto al progetto, come
custom_sources.json, e non lascia mai la macchina.
"""
from __future__ import annotations

import json
import os
import threading
import time

from paths import data_path

PREFS_FILE = data_path("preferences.json")

FILTER_KEYS = ("q", "mtype", "lang", "genre", "res", "quality", "size", "match", "sort")
MAX_PROFILES = 50
MAX_NAME = 60
MAX_SOURCES = 60

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Pulizia dei valori in arrivo dal browser
# ---------------------------------------------------------------------------
def _clean_filters(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in FILTER_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        out[key] = str(value)[:120]
    return out


def _clean_sources(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part for part in raw.split(",") if part]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        text = str(item).strip()[:60]
        if text and text not in out:
            out.append(text)
    return out[:MAX_SOURCES]


def _clean_name(raw) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise ValueError("serve un nome per il profilo")
    if len(name) > MAX_NAME:
        raise ValueError(f"nome troppo lungo (massimo {MAX_NAME} caratteri)")
    return name


# ---------------------------------------------------------------------------
# Persistenza
# ---------------------------------------------------------------------------
def _read() -> dict:
    if not os.path.isfile(PREFS_FILE):
        return {"profiles": {}, "last": None}
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"profiles": {}, "last": None}
    profiles = data.get("profiles") if isinstance(data, dict) else None
    return {"profiles": profiles if isinstance(profiles, dict) else {},
            "last": data.get("last") if isinstance(data, dict) else None}


def _write(data: dict) -> None:
    tmp = PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, PREFS_FILE)


# ---------------------------------------------------------------------------
# Interfaccia usata dal server
# ---------------------------------------------------------------------------
def state() -> dict:
    """Profili salvati (ordinati per nome) e ultimo stato usato."""
    with _lock:
        data = _read()
    profiles = []
    for name, entry in data["profiles"].items():
        if not isinstance(entry, dict):
            continue
        profiles.append({
            "name": name,
            "filters": _clean_filters(entry.get("filters")),
            "sources": _clean_sources(entry.get("sources")),
            "updated": entry.get("updated"),
        })
    profiles.sort(key=lambda item: item["name"].lower())
    last = data["last"] if isinstance(data["last"], dict) else None
    return {"profiles": profiles, "last": last,
            "file": os.path.basename(PREFS_FILE),
            "limits": {"max_profiles": MAX_PROFILES, "max_name": MAX_NAME}}


def save_profile(name, filters, sources) -> dict:
    clean_name = _clean_name(name)
    with _lock:
        data = _read()
        if clean_name not in data["profiles"] and len(data["profiles"]) >= MAX_PROFILES:
            raise ValueError(f"troppi profili salvati (massimo {MAX_PROFILES})")
        data["profiles"][clean_name] = {
            "filters": _clean_filters(filters),
            "sources": _clean_sources(sources),
            "updated": int(time.time()),
        }
        _write(data)
    return {"name": clean_name}


def delete_profile(name) -> bool:
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        return False
    with _lock:
        data = _read()
        if clean_name not in data["profiles"]:
            return False
        data["profiles"].pop(clean_name, None)
        _write(data)
    return True


def save_last(filters, sources) -> dict:
    """Salva l'ultimo stato usato (filtri + fonti)."""
    entry = {"filters": _clean_filters(filters),
             "sources": _clean_sources(sources),
             "updated": int(time.time())}
    with _lock:
        data = _read()
        data["last"] = entry
        _write(data)
    return entry


def import_profiles(payload) -> dict:
    """Importa profili esportati: {"profiles": [...]} oppure {nome: {...}}."""
    if not isinstance(payload, dict):
        raise ValueError("serve un oggetto JSON")
    entries = payload.get("profiles", payload)
    imported = 0
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            save_profile(entry.get("name"), entry.get("filters"), entry.get("sources"))
            imported += 1
    elif isinstance(entries, dict):
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            save_profile(name, entry.get("filters"), entry.get("sources"))
            imported += 1
    else:
        raise ValueError("formato non riconosciuto: attesi profili in un elenco o in un oggetto")
    if not imported:
        raise ValueError("nessun profilo valido nel file")
    return {"imported": imported}
