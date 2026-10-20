# -*- coding: utf-8 -*-
"""Percorsi dell'applicazione, validi da sorgente e da eseguibile.

MagnetScout puo' girare in tre modi:

- da sorgente (``python server.py``): tutto accanto ai .py;
- come eseguibile PyInstaller onefile: le risorse stanno in ``sys._MEIPASS``,
  l'exe e' ``sys.executable``;
- come eseguibile Nuitka onefile: le risorse stanno nella cartella temporanea
  del bundle (``__compiled__.containing_dir``), mentre il percorso originale
  dell'exe e' in ``__compiled__.original_argv0``.

In tutti i casi i dati dell'utente devono restare **accanto all'eseguibile**,
mai nella cartella temporanea: qui la distinzione e' esplicita, una volta sola.

- ``RESOURCE_DIR``: file in sola lettura inclusi nel pacchetto (``static/``).
- ``DATA_DIR``:     dati dell'utente (preferenze, fonti, coda, download).
"""
from __future__ import annotations

import os
import sys

_compiled = globals().get("__compiled__")
FROZEN = bool(getattr(sys, "frozen", False)) or _compiled is not None


def _bundle_dir() -> str:
    """Cartella da cui leggere le risorse incluse nel pacchetto."""
    meipass = getattr(sys, "_MEIPASS", None)          # PyInstaller
    if meipass:
        return meipass
    # Nuitka: __file__ sta nella cartella di estrazione (onefile) o nella
    # cartella di distribuzione (standalone). NON usare containing_dir:
    # in onefile punta alla cartella di build, dove static/ non c'e'.
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "static", "index.html")):
        return here
    containing = getattr(_compiled, "containing_dir", None)
    if containing and os.path.isfile(os.path.join(containing, "static", "index.html")):
        return containing
    return here


def _executable_dir() -> str:
    """Cartella dell'eseguibile originale, anche in modalita' onefile."""
    candidates = (
        getattr(_compiled, "original_argv0", None),   # Nuitka: exe lanciato
        os.environ.get("NUITKA_ONEFILE_BINARY"),      # fallback Nuitka
        sys.argv[0] if sys.argv else None,
        sys.executable,
    )
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.dirname(os.path.abspath(candidate))
    return os.path.dirname(os.path.abspath(sys.executable))


if FROZEN:
    RESOURCE_DIR = _bundle_dir()
    DATA_DIR = _executable_dir()
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = RESOURCE_DIR

STATIC_DIR = os.path.join(RESOURCE_DIR, "static")


def data_path(name: str) -> str:
    """Percorso di un file di stato dell'utente (preferenze, cache, coda)."""
    return os.path.join(DATA_DIR, name)
