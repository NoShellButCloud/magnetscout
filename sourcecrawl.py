# -*- coding: utf-8 -*-
"""Scoperta di nuove fonti a partire da una parola chiave.

Flusso: si cercano i domini citati sul web (DuckDuckGo, Reddit best-effort),
si scartano i domini noti/non pertinenti, si prova ciascun dominio per capire
se espone un feed RSS, una pagina con magnet o solo una ricerca, e infine si
propongono le fonti utilizzabili pronte da aggiungere.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from sourcebase import (USER_AGENT, extract_magnets, parse_feed_items, slugify)
from source_custom import KIND_LABELS, builtin_ids, definitions

DISCOVER_TIMEOUT = 9
MAX_DOMAINS = 8
PER_PROVIDER_LIMIT = 25

# domini che non sono indici torrent (motori, social, blog, guide, vpn...)
DENY_TOKENS = (
    "reddit", "duckduckgo", "google.", "bing.", "yandex", "brave.com", "yahoo",
    "wikipedia", "youtube", "youtu.be", "facebook", "instagram", "twitter",
    "x.com", "tiktok", "linkedin", "pinterest", "t.me", "telegram", "discord",
    "github", "gitlab", "npmjs", "pypi", "medium.com", "quora", "stackoverflow",
    "stackexchange", "amazon.", "ebay.", "play.google", "apple.com", "microsoft",
    "adobe", "torrentfreak", "blog", "wordpress", "blogspot", "vpn", "proxy",
    "guide", "tutorial", "aranzulla", "leganerd", "ddlitalia", "pastebin",
    "facebook.com", "change.org", "trustpilot", "scamadviser", "urlscan",
    "virustotal", "archive.org", "web.archive", "whois", "similarweb",
)

# domini gia' coperti dalle fonti incluse
BUILTIN_HOSTS = ("apibay.org", "thepiratebay.org", "piratebay", "yts.mx", "yts.lt",
                 "yts.rs", "yts.gg", "knaben.org", "knaben.eu", "knaben.xyz",
                 "torrents-csv.com", "nyaa.si", "archive.org", "1337x.to")

RSS_TEMPLATES = ("/?page=rss&q={query}", "/rss?q={query}", "/rss.xml?q={query}",
                 "/feed?q={query}", "/atom.xml?q={query}", "/?q={query}&page=rss",
                 "/search?q={query}&page=rss", "/rss/?q={query}")
HTML_TEMPLATES = ("/?q={query}", "/search?q={query}", "/search/{query}", "/?s={query}",
                  "/torrents.php?search={query}", "/index.php?search={query}",
                  "/search/all/{query}/", "/search/{query}/1/", "/browse.php?search={query}")
PROBE_QUERY = "matrix"


def _fetch(url: str, timeout: int = DISCOVER_TIMEOUT):
    """Ritorna (status, testo, url_finale); status 0 se la connessione fallisce."""
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, raw.decode("utf-8", "replace"), response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, "", url
    except Exception:
        return 0, "", url


def _title_of(page: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", page or "", re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
    return re.sub(r"\s+", " ", text).strip()[:90]


# ---------------------------------------------------------------------------
# Provider di ricerca (da dove si ricavano i domini)
# ---------------------------------------------------------------------------
def _ddg_urls(keyword: str) -> list[str]:
    urls = []
    for query in (f"{keyword} torrent", f"{keyword} magnet link"):
        status, page, _ = _fetch("https://html.duckduckgo.com/html/?q=" +
                                 urllib.parse.quote_plus(query), 12)
        if status != 200:
            if not urls:
                raise RuntimeError(f"DuckDuckGo ha risposto HTTP {status}")
            continue
        for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', page):
            href = htmllib.unescape(href)
            if "uddg=" in href:
                parsed = urllib.parse.urlparse(href if href.startswith("http") else "https:" + href)
                target = (urllib.parse.parse_qs(parsed.query).get("uddg") or [""])[0]
                if target:
                    urls.append(target)
            elif href.startswith("http"):
                urls.append(href)
    return urls


def _reddit_urls(keyword: str) -> list[str]:
    urls = []
    last = ""
    for base in ("https://www.reddit.com", "https://old.reddit.com"):
        status, page, _ = _fetch(f"{base}/search.json?q={urllib.parse.quote(keyword)}&limit=50", 12)
        if status != 200:
            last = f"HTTP {status}"
            continue
        try:
            data = json.loads(page)
        except ValueError:
            last = "risposta non JSON (accesso bloccato)"
            continue
        for child in ((data.get("data") or {}).get("children") or []):
            node = child.get("data") or {}
            for key in ("url_overridden_by_dest", "url"):
                if node.get(key):
                    urls.append(node[key])
            urls.extend(re.findall(r"https?://[^\s)\]\"'<>]+", node.get("selftext") or ""))
    if not urls:
        raise RuntimeError(f"Reddit non utilizzabile da questo indirizzo ({last or 'nessuna risposta'})")
    return urls


# ---------------------------------------------------------------------------
# Estrazione e pulizia dei domini
# ---------------------------------------------------------------------------
def _domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url if "//" in url else "http://" + url).netloc
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0].lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
        return ""
    return host


def _is_denied(domain: str) -> bool:
    return any(token in domain for token in DENY_TOKENS)


def _candidate_domains(urls: list[str], keyword: str) -> list[str]:
    counts: dict[str, int] = {}
    order: list[str] = []
    for url in urls[:PER_PROVIDER_LIMIT * 4]:
        domain = _domain_of(url)
        if not domain or _is_denied(domain) or any(h in domain for h in BUILTIN_HOSTS):
            continue
        if domain not in counts:
            order.append(domain)
            counts[domain] = 0
        counts[domain] += 1
    keyword_lower = keyword.lower()
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", keyword_lower) and keyword_lower not in counts:
        # se la parola chiave e' gia' un dominio va provata per prima
        order.insert(0, keyword_lower)
        counts[keyword_lower] = 1
    # piu' citato = piu' probabile; a parita' si mantiene l'ordine di comparsa
    return sorted(order, key=lambda domain: -counts[domain])


# ---------------------------------------------------------------------------
# Prova di un dominio
# ---------------------------------------------------------------------------
def _probe_rss(base: str) -> tuple[str, list[dict]]:
    for template in RSS_TEMPLATES:
        target = base.rstrip("/") + template.replace("{query}", urllib.parse.quote_plus(PROBE_QUERY))
        status, page, _ = _fetch(target, DISCOVER_TIMEOUT)
        if status != 200 or "<" not in page[:200]:
            continue
        items = parse_feed_items(page)
        usable = [i for i in items if i.get("hash") or i.get("magnet") or i.get("torrent_url")]
        if usable:
            return template, usable
    return "", []


def _probe_html(base: str) -> tuple[str, int, str]:
    for template in HTML_TEMPLATES:
        target = base.rstrip("/") + template.replace("{query}", urllib.parse.quote_plus(PROBE_QUERY))
        status, page, _ = _fetch(target, DISCOVER_TIMEOUT)
        if status != 200:
            continue
        found = extract_magnets(page)
        if len(found) >= 3:
            return template, len(found), (found[0].get("title") or "")[:80]
    return "", 0, ""


def probe(domain: str, timeout: int = DISCOVER_TIMEOUT) -> dict:
    result = {"domain": domain, "home_url": f"https://{domain}/", "ok": False,
              "status": 0, "title": "", "kind": "", "kind_label": "",
              "search_url": "", "magnets": 0, "sample": "", "note": "", "score": 0}

    status, page, final = _fetch(result["home_url"], timeout)
    if status != 200:
        status2, page2, final2 = _fetch(f"http://{domain}/", timeout)
        if status2 == 200:
            status, page, final = status2, page2, final2
            result["home_url"] = f"http://{domain}/"
    result["status"] = status
    if status != 200:
        result["note"] = f"sito non raggiungibile (HTTP {status or 'errore di rete'})"
        return result

    result["ok"] = True
    result["title"] = _title_of(page)
    lowered = page.lower()
    title_lower = result["title"].lower()
    torrent_hits = lowered.count("torrent")
    magnet_hits = lowered.count("magnet")
    looks_like_torrent = (torrent_hits >= 8 or magnet_hits >= 4
                          or "torrent" in title_lower or "magnet" in title_lower)

    # 1) altri indici? feed RSS con risultati veri
    template, items = _probe_rss(result["home_url"])
    if template:
        result["kind"] = "rss"
        result["kind_label"] = KIND_LABELS["rss"]
        result["search_url"] = result["home_url"].rstrip("/") + template
        result["sample"] = (items[0].get("title") or "")[:80]
        result["score"] = 100 + min(len(items), 20)
        return result

    # 2) pagina HTML con link magnet
    template, count, sample = _probe_html(result["home_url"])
    if template:
        result["kind"] = "html"
        result["kind_label"] = KIND_LABELS["html"]
        result["search_url"] = result["home_url"].rstrip("/") + template
        result["magnets"] = count
        result["sample"] = sample
        result["score"] = 60 + min(count, 30)
        return result

    # 3) niente risultati strutturati: solo un collegamento alla ricerca
    if looks_like_torrent:
        status, page2, _ = _fetch(result["home_url"].rstrip("/") + "/?q=" + PROBE_QUERY, timeout)
        search_url = (result["home_url"].rstrip("/") + "/?q={query}") if status == 200 \
            else result["home_url"].rstrip("/") + "/"
        result["kind"] = "link"
        result["kind_label"] = KIND_LABELS["link"]
        result["search_url"] = search_url
        result["magnets"] = len(extract_magnets(page))
        result["note"] = "nessun feed o elenco con magnet: si aprira' la ricerca sul sito"
        result["score"] = 20 + min(result["magnets"], 10)
        return result

    result["note"] = "sembra un sito generico (nessun riferimento a torrent/magnet)"
    result["score"] = 0
    return result

def _definition(proposal: dict) -> dict:
    label = proposal["title"] or proposal["domain"]
    label = re.sub(r"\s*[-|·]\s*(home|homepage|torrent|index).*$", "", label, flags=re.IGNORECASE).strip()
    return {
        "id": slugify(proposal["domain"]),
        "label": (label or proposal["domain"])[:80],
        "kind": proposal["kind"],
        "search_url": proposal["search_url"],
        "description": f"Scoperta automaticamente da {proposal['domain']} ({proposal['kind_label']})",
    }


# ---------------------------------------------------------------------------
# API principale
# ---------------------------------------------------------------------------
def discover(keyword: str, max_candidates: int = MAX_DOMAINS) -> dict:
    keyword = (keyword or "").strip()
    if len(keyword) < 3:
        raise ValueError("inserisci almeno 3 caratteri")

    providers = []
    urls: list[str] = []
    for provider_id, label, fetch in (("duckduckgo", "DuckDuckGo", _ddg_urls),
                                      ("reddit", "Reddit", _reddit_urls)):
        try:
            found = fetch(keyword)
            providers.append({"id": provider_id, "label": label, "ok": True,
                              "found": len(found), "error": None})
            urls.extend(found)
        except Exception as exc:
            providers.append({"id": provider_id, "label": label, "ok": False,
                              "found": 0, "error": str(exc)})

    known_hosts = set(builtin_ids())
    for definition in definitions():
        _ = definition
        known_hosts.add(definition.get("id"))
    known_hosts.add(slugify(keyword, ""))

    domains = _candidate_domains(urls, keyword)
    candidates = []
    discarded = []
    if domains:
        with ThreadPoolExecutor(max_workers=6) as pool:
            probed = list(pool.map(probe, domains[: max_candidates * 2]))
        for proposal in probed:
            definition = _definition(proposal)
            if not proposal["kind"]:
                discarded.append({"domain": proposal["domain"], "reason": proposal["note"]})
                continue
            if definition["id"] in known_hosts:
                discarded.append({"domain": proposal["domain"],
                                  "reason": "fonte gia' presente nell'elenco"})
                continue
            proposal["definition"] = definition
            candidates.append(proposal)
    candidates.sort(key=lambda p: (-p["score"], p["domain"]))

    return {
        "keyword": keyword,
        "providers": providers,
        "domains_checked": len(domains),
        "candidates": candidates[:max_candidates],
        "discarded": discarded[:10],
    }
