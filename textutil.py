# -*- coding: utf-8 -*-
"""Estrazione e normalizzazione di metadati dai titoli dei torrent.

Nessuna dipendenza esterna: solo libreria standard.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Risoluzione
# ---------------------------------------------------------------------------
_RES_RE = re.compile(r"\b(2160p|4k|uhd|1080p|720p|576p|480p|360p)\b", re.IGNORECASE)
_RES_RANK = {"360p": 1, "480p": 2, "576p": 2, "720p": 3, "1080p": 4, "2160p": 5}
_3D_RE = re.compile(r"\b(3d|hsbs|half-?sbs|sbs)\b", re.IGNORECASE)


def detect_resolution(title: str) -> str | None:
    """Ritorna la risoluzione piu' alta trovata nel titolo ('1080p', '2160p', ...)."""
    found = []
    for match in _RES_RE.finditer(title):
        token = match.group(1).lower()
        if token in ("4k", "uhd"):
            token = "2160p"
        found.append(token)
    if not found:
        return None
    return max(found, key=lambda t: _RES_RANK.get(t, 0))


def detect_3d(title: str) -> bool:
    return bool(_3D_RE.search(title))


def resolution_bucket(res: str | None) -> str | None:
    """Raggruppa 480p/576p sotto '480p' per il filtro."""
    if res in ("480p", "576p"):
        return "480p"
    return res


def resolution_rank(res: str | None) -> int:
    return _RES_RANK.get(res or "", 0)


# ---------------------------------------------------------------------------
# Qualita'
# ---------------------------------------------------------------------------
_QUALITY_RULES = [
    ("remux", r"\b(remux|bdremux)\b"),
    ("bluray", r"\b(blu-?ray|bdrip|brrip|bd25|bd50|bd5|bd9)\b"),
    ("web", r"\b(web-?dl|webrip|web|amzn|nf|dsnp|atvp|hmax|pcok)\b"),
    ("hdtv", r"\b(hdtv|pdtv|hdrip|dsr)\b"),
    ("dvd", r"\b(dvdrip|dvd-?r|dvd5|dvd9|dvdr)\b"),
    ("cam", r"\b(cam|ts|tc|telesync|telecine|hdts|hdcam)\b"),
]


def detect_quality(title: str) -> str | None:
    low = title.lower()
    for name, pattern in _QUALITY_RULES:
        if re.search(pattern, low):
            return name
    return None


# ---------------------------------------------------------------------------
# Dimensioni
# ---------------------------------------------------------------------------
_SIZE_RE = re.compile(r"([\d][\d.,]*)\s*(bytes|tib|tb|gib|gb|mib|mb|kib|kb|b)\b", re.IGNORECASE)
_SIZE_MULT = {
    "b": 1, "bytes": 1, "kb": 1000, "kib": 1024, "mb": 1000 ** 2, "mib": 1024 ** 2,
    "gb": 1000 ** 3, "gib": 1024 ** 3, "tb": 1000 ** 4, "tib": 1024 ** 4,
}


def parse_size(value) -> int | None:
    """Converte '1.4 GB', '487.87 MB', '1654095946' in byte (int)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = _SIZE_RE.search(text)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return int(number * _SIZE_MULT[match.group(2).lower()])


def human_size(num) -> str:
    if not num:
        return "—"
    for unit, div in (("TiB", 2 ** 40), ("GiB", 2 ** 30), ("MiB", 2 ** 20), ("KiB", 2 ** 10)):
        if num >= div:
            val = num / div
            return f"{val:.2f} {unit}" if val < 100 else f"{val:.0f} {unit}"
    return f"{int(num)} B"


# ---------------------------------------------------------------------------
# Lingue: i tag "parola" (es. Italian/English) sono case-insensitive, i codici
# di scena (ITA, ENG, FRA...) sono in maiuscolo e vengono cercati case-sensitive
# per ridurre i falsi positivi con i titoli dei film.
# ---------------------------------------------------------------------------
_LANG_RULES = [
    ("it", r"(?i:\b(italian[aeo]?|ita)\b)"),
    ("multi", r"(?i:\b(multi|multilingua|dual)\b)"),
    ("en", r"(?i:\b(english)\b)|\b(ENG)\b"),
    ("fr", r"(?i:\b(french|truefrench)\b)|\b(FRA|FRE|VFF|VFQ)\b"),
    ("es", r"(?i:\b(spanish|castellano|latino|subtitulado|espanol|español)\b)|\b(SPA|ESP|CAST)\b"),
    ("de", r"(?i:\b(german)\b)|\b(GER|DEU)\b"),
    ("pt", r"(?i:\b(portuguese|dublado)\b)|\b(POR|PTBR|PT-BR)\b"),
    ("ru", r"(?i:\b(russian)\b)|\b(RUS)\b"),
    ("ja", r"(?i:\b(japanese)\b)|\b(JPN|JAP)\b"),
    ("ko", r"(?i:\b(korean)\b)|\b(KOR)\b"),
    ("zh", r"(?i:\b(mandarin|cantonese|chinese)\b)|\b(CHI|CHS|CHT)\b"),
    ("hi", r"(?i:\b(hindi)\b)|\b(HIN)\b"),
    ("ar", r"(?i:\b(arabic)\b)|\b(ARA)\b"),
    ("tr", r"(?i:\b(turkish)\b)|\b(TUR)\b"),
    ("pl", r"(?i:\b(polish)\b)|\b(PL|POL)\b"),
    ("nl", r"(?i:\b(dutch)\b)|\b(DUT|NLD)\b"),
    ("sv", r"(?i:\b(swedish)\b)|\b(SWE)\b"),
    ("no", r"(?i:\b(norwegian)\b)|\b(NOR)\b"),
    ("da", r"(?i:\b(danish)\b)|\b(DAN)\b"),
    ("fi", r"(?i:\b(finnish)\b)|\b(FIN)\b"),
    ("el", r"(?i:\b(greek)\b)|\b(GRE|ELL)\b"),
    ("he", r"(?i:\b(hebrew)\b)|\b(HEB)\b"),
    ("hu", r"(?i:\b(hungarian)\b)|\b(HUN)\b"),
    ("cs", r"(?i:\b(czech)\b)|\b(CZE|CES)\b"),
    ("ro", r"(?i:\b(romanian)\b)|\b(RON|RUM)\b"),
    ("uk", r"(?i:\b(ukrainian)\b)|\b(UKR)\b"),
]
_SUB_IT_RE = re.compile(r"(?i:sub[\s.-]?ita\b|\bsubita\b)")
_PRIMARY_PRIORITY = ["multi", "it", "en", "fr", "es", "de", "pt", "ru", "ja", "ko", "zh",
                     "hi", "ar", "tr", "pl", "nl", "sv", "no", "da", "fi", "el", "he",
                     "hu", "cs", "ro", "uk"]


def detect_langs(title: str) -> tuple[list[str], bool]:
    """Rileva le lingue citate nel titolo. Ritorna (codici, sub_ita)."""
    langs: list[str] = []
    for code, pattern in _LANG_RULES:
        if re.search(pattern, title) and code not in langs:
            langs.append(code)
    sub = bool(_SUB_IT_RE.search(title))
    if sub and "it" not in langs:
        langs.append("it")
    return langs, sub


def primary_lang(langs: list[str]) -> str | None:
    for code in _PRIMARY_PRIORITY:
        if code in langs:
            return code
    return langs[0] if langs else None


_LANG_ALIASES = {
    "english": "en", "eng": "en", "italian": "it", "ita": "it",
    "french": "fr", "fre": "fr", "fra": "fr", "spanish": "es", "spa": "es",
    "castellano": "es", "latino": "es", "german": "de", "ger": "de", "deu": "de",
    "portuguese": "pt", "por": "pt", "russian": "ru", "rus": "ru",
    "japanese": "ja", "jpn": "ja", "korean": "ko", "kor": "ko",
    "chinese": "zh", "chi": "zh", "mandarin": "zh", "hindi": "hi", "hin": "hi",
    "arabic": "ar", "ara": "ar", "turkish": "tr", "tur": "tr", "polish": "pl",
    "pol": "pl", "dutch": "nl", "nld": "nl", "dut": "nl", "swedish": "sv",
    "swe": "sv", "norwegian": "no", "nor": "no", "danish": "da", "dan": "da",
    "finnish": "fi", "fin": "fi", "greek": "el", "gre": "el", "ell": "el",
    "hebrew": "he", "heb": "he", "hungarian": "hu", "hun": "hu", "czech": "cs",
    "cze": "cs", "ces": "cs", "romanian": "ro", "ron": "ro", "rum": "ro",
    "ukrainian": "uk", "ukr": "uk", "multi": "multi", "multiple": "multi",
}


def norm_lang(value) -> str | None:
    """Normalizza una lingua proveniente dai metadati di una fonte (nome o codice)."""
    langs = norm_langs(value)
    return langs[0] if langs else None


def norm_langs(value) -> list[str]:
    """Estrae *tutte* le lingue da un metadato (stringa singola, elenco o stringa
    con piu' lingue: "Italian, English")."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            for code in norm_langs(item):
                if code not in out:
                    out.append(code)
        return out

    from taxonomy import LANG_NAMES
    text = str(value).strip().lower()
    if len(text) == 2:
        return [text] if text in LANG_NAMES else []
    out = []
    for chunk in re.split(r"[,;/|+&]+", text):
        code = _LANG_ALIASES.get(chunk.strip())
        if code and code not in out:
            out.append(code)
    if not out:
        code = _LANG_ALIASES.get(text)
        if code:
            out.append(code)
    return out


# ---------------------------------------------------------------------------
# Confronto dei titoli ignorando i separatori (. _ - ' spazio, parentesi, ecc.)
# ---------------------------------------------------------------------------
_SEPARATOR_RE = re.compile(r"[^0-9a-z]+")


def normalize_title(text: str) -> str:
    """Titolo in minuscolo, senza accenti, con i separatori ridotti a spazio singolo."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SEPARATOR_RE.sub(" ", stripped.lower()).strip()


def query_words(text: str) -> list[str]:
    normalized = normalize_title(text)
    return normalized.split(" ") if normalized else []


def exact_title_matches(title: str, query: str, mode: str) -> bool:
    """Confronto esatto ignorando i separatori.

    mode = "words": ogni parola cercata deve comparire come parola intera
                    nel titolo (es. "the matrix" -> "The.Matrix.Reloaded").
    mode = "title": il titolo, normalizzato, deve essere identico alla query
                    (es. "night of the living dead" -> "Night of the Living Dead").
    """
    if not mode:
        return True
    words = query_words(query)
    if not words:
        return True
    title_words = query_words(title)
    if mode == "title":
        return title_words == words
    words_set = set(title_words)
    return all(word in words_set for word in words)
