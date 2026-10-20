# -*- coding: utf-8 -*-
"""Tassonomie condivise: nomi lingue, etichette qualita', generi."""
from __future__ import annotations

LANG_NAMES = {
    "it": "Italiano", "en": "Inglese", "multi": "Multi (piu' lingue)",
    "fr": "Francese", "es": "Spagnolo", "de": "Tedesco", "pt": "Portoghese",
    "ru": "Russo", "ja": "Giapponese", "ko": "Coreano", "zh": "Cinese",
    "hi": "Hindi", "ar": "Arabo", "tr": "Turco", "pl": "Polacco",
    "nl": "Olandese", "sv": "Svedese", "no": "Norvegese", "da": "Danese",
    "fi": "Finlandese", "el": "Greco", "he": "Ebraico", "hu": "Ungherese",
    "cs": "Ceco", "ro": "Rumeno", "uk": "Ucraino",
}

# Ordine di visualizzazione dei filtro lingua nell'interfaccia.
LANG_ORDER = ["it", "en", "multi", "fr", "es", "de", "pt", "ru", "ja", "ko", "zh",
              "ar", "pl", "tr", "nl", "sv", "no", "da", "fi", "el", "he", "hu",
              "cs", "ro", "uk", "hi"]

QUALITY_LABELS = {
    "remux": "Remux", "bluray": "BluRay", "web": "WEB", "hdtv": "HDTV",
    "dvd": "DVD", "cam": "CAM/TS",
}

# Generi canonici (slug -> etichetta italiana). I valori sono condivisi tra UI
# e adattatori; ogni fonte mappa i propri generi su questi slug.
GENRE_LABELS = {
    "azione": "Azione", "avventura": "Avventura", "animazione": "Animazione",
    "commedia": "Commedia", "crime": "Crime", "documentario": "Documentario",
    "dramma": "Drammatico", "famiglia": "Famiglia", "fantasy": "Fantasy",
    "fantascienza": "Fantascienza", "guerra": "Guerra", "horror": "Horror",
    "mistero": "Mistero", "musical": "Musical", "romantico": "Romantico",
    "storico": "Storico", "thriller": "Thriller", "western": "Western",
}

_YTS_GENRE_SLUGS = {
    "action": "azione", "adventure": "avventura", "animation": "animazione",
    "comedy": "commedia", "crime": "crime", "documentary": "documentario",
    "drama": "dramma", "family": "famiglia", "fantasy": "fantasy",
    "history": "storico", "horror": "horror", "music": "musical",
    "mystery": "mistero", "romance": "romantico", "sci-fi": "fantascienza",
    "science fiction": "fantascienza", "thriller": "thriller", "war": "guerra",
    "western": "western",
}


def canon_genre_yts(name: str) -> str | None:
    return _YTS_GENRE_SLUGS.get(str(name).strip().lower())


# Collection di archive.org -> generi canonici (dove ha senso).
ARCHIVE_COLLECTION_GENRES = {
    "animationandcartoons": ["animazione"],
    "classic_cartoons": ["animazione"],
    "SciFi_Horror": ["horror", "fantascienza"],
    "prelinger": ["documentario"],
}
