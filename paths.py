# -*- coding: utf-8 -*-
"""Percorsi dell'applicazione, validi sia da sorgente sia da eseguibile.

Quando MagnetScout gira come ``.exe`` (PyInstaller, modalita' onefile) il
codice e le risorse vivono in una cartella temporanea, mentre i dati
dell'utente devono restare accanto all'eseguibile: qui la distinzione e'
esplicita, una volta sola.

- ``RESOURCE_DIR``: file in sola lettura inclusi nel pacchetto (``static/``).
- ``DATA_DIR``:     dati dell'utente (preferenze, fonti, coda, download),
                    accanto all'eseguibile quando il programma e' congelato,
                    accanto ai sorgenti quando gira da sorgente.
"""
from __future__ import annotations

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # sys.executable e' il .exe; _MEIPASS e' la cartella temporanea del bundle.
    RESOURCE_DIR = getattr(sys, "_MEIPASS",
                           os.path.dirname(os.path.abspath(sys.executable)))
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = RESOURCE_DIR

STATIC_DIR = os.path.join(RESOURCE_DIR, "static")


def data_path(name: str) -> str:
    """Percorso di un file di stato dell'utente (preferenze, cache, coda)."""
    return os.path.join(DATA_DIR, name)
