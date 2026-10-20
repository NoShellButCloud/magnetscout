# -*- coding: utf-8 -*-
"""MagnetScout: server web locale per cercare magnet di film e serie TV.

Solo libreria standard. Avvio:  python server.py
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import dnsclient
import preferences
import xdcc
import xdccdl
from taxonomy import GENRE_LABELS, LANG_NAMES, LANG_ORDER, QUALITY_LABELS
from textutil import human_size, resolution_rank
from sources import (KIND_LABELS, KINDS, Filters, archive_magnet,
                     current_sources, custom_definitions, delete_custom_source,
                     discover, magnet_for_torrent_url, search_all,
                     sort_results, upsert_custom_source)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

CACHE_TTL_SECONDS = 180
_cache: dict = {}
_cache_lock = threading.Lock()

SORTS = ("seeds", "relevance", "date", "size")

RESOLUTIONS = [
    {"code": "480p", "label": "480p / 576p"},
    {"code": "720p", "label": "720p"},
    {"code": "1080p", "label": "1080p"},
    {"code": "2160p", "label": "4K (2160p)"},
    {"code": "3d", "label": "3D"},
]
TYPES = [{"code": "movie", "label": "Film"}, {"code": "tv", "label": "Serie TV"}]
MAX_SIZE_GB = 10
# Scala non lineare del cursore "Max dimensione": 0,1 GB per volta fino a 1 GB,
# poi 0,5 GB per volta fino a 10 GB, quindi "nessun limite" (0).
SIZE_LADDER = [round(0.1 * i, 1) for i in range(1, 11)]
SIZE_LADDER += [round(1.0 + 0.5 * i, 1) for i in range(1, 19)]
SIZE_FILTER = {"default": 5, "max": MAX_SIZE_GB, "ladder": SIZE_LADDER + [0]}
MATCH_MODES = [
    {"code": "", "label": "Tutte"},
    {"code": "words", "label": "Parole esatte"},
    {"code": "title", "label": "Titolo identico"},
]
MATCH_CODES = tuple(mode["code"] for mode in MATCH_MODES)
SORT_OPTIONS = [
    {"code": "seeds", "label": "Piu' seed"},
    {"code": "relevance", "label": "Rilevanza"},
    {"code": "date", "label": "Piu' recenti"},
    {"code": "size", "label": "Dimensione"},
]


def meta_payload() -> dict:
    return {
        "sources": [s.info() for s in current_sources()],
        "custom_sources": custom_definitions(),
        "custom_kinds": [{"code": k, "label": KIND_LABELS[k]} for k in KINDS],
        "dns": {"mode": dnsclient.mode(), "label": dnsclient.resolver_label()},
        "languages": [{"code": c, "label": LANG_NAMES[c]} for c in LANG_ORDER],
        "genres": [{"slug": s, "label": l} for s, l in GENRE_LABELS.items()],
        "qualities": [{"code": c, "label": l} for c, l in QUALITY_LABELS.items()],
        "resolutions": RESOLUTIONS,
        "types": TYPES,
        "sorts": SORT_OPTIONS,
        "size_filter": SIZE_FILTER,
        "match_modes": MATCH_MODES,
        "xdcc": {
            "search_sources": [{"id": s.id, "label": s.label} for s in xdcc.SEARCH_SOURCES],
            "download_dir": xdccdl.manager.settings.get("download_dir"),
            "max_parallel": xdccdl.MAX_PARALLEL,
        },
    }


def enrich(result: dict) -> dict:
    result = dict(result)
    result["size_human"] = human_size(result.get("size_bytes"))
    code = result.get("lang")
    label = LANG_NAMES.get(code) if code else None
    if label and result.get("sub"):
        label += " (sub)"
    result["lang_label"] = label
    result["quality_label"] = QUALITY_LABELS.get(result.get("quality") or "")
    result["genre_labels"] = [GENRE_LABELS.get(g, g) for g in result.get("genres") or []]
    result.pop("rank", None)
    return result


def run_search(params: dict):
    query = (params.get("q") or [""])[0].strip()
    if len(query) < 2:
        return 400, {"error": "Inserisci almeno 2 caratteri"}
    try:
        size_gb = float((params.get("size") or ["0"])[0] or 0)
    except ValueError:
        size_gb = 0.0
    if size_gb <= 0:
        size_gb = 0.0
    else:
        size_gb = min(round(size_gb, 2), float(MAX_SIZE_GB))
    match_mode = (params.get("match") or [""])[0]
    if match_mode not in MATCH_CODES:
        match_mode = ""

    filters = Filters(
        q=query,
        lang=(params.get("lang") or [""])[0],
        mtype=(params.get("mtype") or [""])[0],
        res=(params.get("res") or [""])[0],
        genre=(params.get("genre") or [""])[0],
        quality=(params.get("quality") or [""])[0],
        max_size_gb=size_gb,
        match=match_mode,
    )
    sort = (params.get("sort") or ["seeds"])[0]
    if sort not in SORTS:
        sort = "seeds"
    source_ids = [s for s in (params.get("sources") or [""])[0].split(",") if s] or None

    cache_key = json.dumps([filters.q, filters.lang, filters.mtype, filters.res,
                            filters.genre, filters.quality, size_gb, match_mode,
                            sort, source_ids], ensure_ascii=False)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return 200, hit[1]

    started = time.time()
    results, stats = search_all(filters, source_ids)
    results = sort_results(results, sort)
    payload = {
        "query": {"q": filters.q, "lang": filters.lang, "mtype": filters.mtype,
                  "res": filters.res, "genre": filters.genre,
                  "quality": filters.quality, "size": size_gb,
                  "match": match_mode, "sort": sort},
        "total": len(results),
        "took_ms": int((time.time() - started) * 1000),
        "sources": stats,
        "results": [enrich(r) for r in results],
    }
    with _cache_lock:
        if len(_cache) > 200:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[cache_key] = (now, payload)
    return 200, payload


def run_xdcc_search(params: dict):
    query = (params.get("q") or [""])[0].strip()
    if len(query) < 2:
        return 400, {"error": "Inserisci almeno 2 caratteri"}
    try:
        limit = int((params.get("limit") or ["150"])[0] or 150)
    except ValueError:
        limit = 150
    limit = max(10, min(limit, 400))
    episode = (params.get("episode") or [""])[0].strip()
    episode_value = int(episode) if episode.isdigit() else None
    started = time.time()
    try:
        results, stats = xdcc.search_all(query, limit=limit, episode=episode_value)
    except Exception as exc:
        return 502, {"error": f"ricerca XDCC fallita: {exc}"}
    return 200, {
        "query": {"q": query, "episode": episode_value, "limit": limit},
        "total": len(results),
        "took_ms": int((time.time() - started) * 1000),
        "sources": stats,
        "results": [enrich(r) for r in results],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MagnetScout/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- helpers
    def send_payload(self, code: int, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_static(self, relative: str):
        path = os.path.normpath(os.path.join(STATIC_DIR, relative))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            return self.send_payload(404, {"error": "file non trovato"})
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        with open(path, "rb") as handle:
            self.send_payload(200, handle.read(), content_type)

    # ------------------------------------------------------------- routing
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self.serve_static("index.html")
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if path == "/favicon.ico":
                return self.send_payload(200, b"", "image/x-icon")
            if path == "/api/meta":
                return self.send_payload(200, meta_payload())
            if path == "/api/search":
                code, payload = run_search(params)
                return self.send_payload(code, payload)
            if path == "/api/custom-sources":
                return self.send_payload(200, {"sources": custom_definitions(),
                                               "kinds": [{"code": k, "label": KIND_LABELS[k]}
                                                         for k in KINDS]})
            if path == "/api/preferences":
                return self.send_payload(200, preferences.state())
            if path == "/api/discover":
                keyword = (params.get("q") or [""])[0].strip()
                try:
                    return self.send_payload(200, discover(keyword))
                except ValueError as exc:
                    return self.send_payload(400, {"error": str(exc)})
                except Exception as exc:
                    return self.send_payload(502, {"error": f"ricerca delle fonti fallita: {exc}"})
            if path == "/api/dns":
                host = (params.get("host") or [""])[0]
                if not host:
                    return self.send_payload(400, {"error": "serve il parametro host"})
                fresh = (params.get("fresh") or [""])[0] in ("1", "true", "si", "sì")
                return self.send_payload(200, dnsclient.resolve_info(host, fresh=fresh))
            if path == "/api/magnet":
                torrent_url = (params.get("url") or [""])[0].strip()
                identifier = (params.get("id") or [""])[0]
                try:
                    if torrent_url:
                        return self.send_payload(200, {"magnet": magnet_for_torrent_url(torrent_url)})
                    magnet = archive_magnet(identifier)
                    return self.send_payload(200, {"magnet": magnet})
                except Exception as exc:
                    return self.send_payload(502, {"error": f"generazione magnet fallita: {exc}"})
            if path == "/api/xdcc/search":
                code, payload = run_xdcc_search(params)
                return self.send_payload(code, payload)
            if path == "/api/xdcc/downloads":
                return self.send_payload(200, xdccdl.manager.snapshot())
            return self.send_payload(404, {"error": "endpoint sconosciuto"})
        except Exception as exc:  # pragma: no cover - rete/parsing
            return self.send_payload(500, {"error": f"errore interno: {exc}"})

    # ------------------------------------------------------------- scrittura
    def read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 100_000:
            raise ValueError("corpo della richiesta mancante o troppo grande")
        return json.loads(self.rfile.read(length).decode("utf-8", "replace"))

    def custom_sources_response(self) -> dict:
        return {"sources": custom_definitions(), "meta": meta_payload()}

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/custom-sources":
                try:
                    definition = self.read_json_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.send_payload(400, {"error": f"JSON non valido: {exc}"})
                try:
                    saved = upsert_custom_source(definition)
                except ValueError as exc:
                    return self.send_payload(400, {"error": str(exc)})
                payload = self.custom_sources_response()
                payload["saved"] = saved
                return self.send_payload(200, payload)
            if path in ("/api/preferences", "/api/preferences/last",
                        "/api/preferences/import"):
                try:
                    body = self.read_json_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.send_payload(400, {"error": f"JSON non valido: {exc}"})
                try:
                    if path == "/api/preferences":
                        result = preferences.save_profile(
                            body.get("name"), body.get("filters"),
                            body.get("sources"))
                        payload = preferences.state()
                        payload.update({"saved": result["name"]})
                        return self.send_payload(200, payload)
                    if path == "/api/preferences/last":
                        payload = preferences.state()
                        payload["last"] = preferences.save_last(
                            body.get("filters"), body.get("sources"))
                        return self.send_payload(200, payload)
                    payload = preferences.state()
                    payload.update(preferences.import_profiles(body))
                    return self.send_payload(200, payload)
                except ValueError as exc:
                    return self.send_payload(400, {"error": str(exc)})
            if path == "/api/xdcc/downloads":
                try:
                    body = self.read_json_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.send_payload(400, {"error": f"JSON non valido: {exc}"})
                try:
                    result = xdccdl.manager.add(
                        server=body.get("server"), port=body.get("port") or 6697,
                        tls=body.get("tls", True), channel=body.get("channel"),
                        bot=body.get("bot"), pack=body.get("pack"),
                        command=body.get("command"), name=body.get("name"),
                        size_bytes=body.get("size_bytes"), network=body.get("network"))
                except ValueError as exc:
                    return self.send_payload(400, {"error": str(exc)})
                payload = xdccdl.manager.snapshot()
                payload.update(result)
                return self.send_payload(200, payload)
            if path == "/api/xdcc/downloads/action":
                try:
                    body = self.read_json_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.send_payload(400, {"error": f"JSON non valido: {exc}"})
                action = str(body.get("action") or "").strip()
                job_id = str(body.get("id") or "").strip()
                if action == "clear":
                    removed = xdccdl.manager.clear_finished()
                    payload = xdccdl.manager.snapshot()
                    payload["removed"] = removed
                    return self.send_payload(200, payload)
                if action == "open":
                    try:
                        folder = xdccdl.manager.open_folder(job_id or None)
                    except RuntimeError as exc:
                        return self.send_payload(500, {"error": str(exc)})
                    payload = xdccdl.manager.snapshot()
                    payload["folder"] = folder
                    return self.send_payload(200, payload)
                if not job_id:
                    return self.send_payload(400, {"error": "serve l'id del download"})
                if action in ("cancel", "pause"):
                    if not xdccdl.manager.cancel(job_id, pause=(action == "pause")):
                        return self.send_payload(404, {"error": "download non trovato"})
                elif action in ("retry", "resume"):
                    if xdccdl.manager.retry(job_id) is None:
                        return self.send_payload(404, {"error": "download non trovato"})
                elif action == "remove":
                    delete_file = bool(body.get("delete_file"))
                    if not xdccdl.manager.remove(job_id, delete_file=delete_file):
                        return self.send_payload(404, {"error": "download non trovato"})
                else:
                    return self.send_payload(400, {"error": "azione non riconosciuta"})
                return self.send_payload(200, xdccdl.manager.snapshot())
            if path == "/api/xdcc/settings":
                try:
                    body = self.read_json_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.send_payload(400, {"error": f"JSON non valido: {exc}"})
                try:
                    xdccdl.manager.update_settings(
                        download_dir=body.get("download_dir"),
                        max_parallel=body.get("max_parallel"),
                        nick=body.get("nick"))
                except (ValueError, OSError) as exc:
                    return self.send_payload(400, {"error": f"impostazione non valida: {exc}"})
                return self.send_payload(200, xdccdl.manager.snapshot())
            return self.send_payload(404, {"error": "endpoint sconosciuto"})
        except Exception as exc:
            return self.send_payload(500, {"error": f"errore interno: {exc}"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/custom-sources":
                source_id = (params.get("id") or [""])[0].strip()
                if not delete_custom_source(source_id):
                    return self.send_payload(404, {"error": "fonte non trovata"})
                payload = self.custom_sources_response()
                payload["removed"] = source_id
                return self.send_payload(200, payload)
            if parsed.path == "/api/preferences":
                name = (params.get("name") or [""])[0]
                if not preferences.delete_profile(name):
                    return self.send_payload(404, {"error": "profilo non trovato"})
                payload = preferences.state()
                payload["removed"] = name
                return self.send_payload(200, payload)
            return self.send_payload(404, {"error": "endpoint sconosciuto"})
        except Exception as exc:
            return self.send_payload(500, {"error": f"errore interno: {exc}"})

    def log_message(self, fmt, *args):
        if self.path.startswith("/static/") or self.path in ("/", "/index.html", "/favicon.ico"):
            return
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="MagnetScout - ricerca magnet in locale")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true",
                        help="non aprire automaticamente il browser")
    parser.add_argument("--dns", default="auto", choices=list(dnsclient.MODES),
                        help="resolver da usare: auto (DoH poi UDP), doh, udp, off "
                             "(default: auto = DNS non filtrato)")
    parser.add_argument("--dns-servers", default=",".join(dnsclient.DEFAULT_UDP_SERVERS),
                        help="server DNS pubblici per la modalita' UDP (default 1.1.1.1,8.8.8.8)")
    parser.add_argument("--downloads", default=None,
                        help="cartella dove salvare i file XDCC (default: ./downloads)")
    args = parser.parse_args()

    if args.downloads:
        try:
            xdccdl.manager.update_settings(download_dir=args.downloads)
        except OSError as exc:
            print(f"Cartella download non utilizzabile ({exc}): uso quella predefinita.", flush=True)

    dnsclient.configure(args.dns, [s for s in args.dns_servers.split(",") if s.strip()])
    if dnsclient.install():
        print(f"{dnsclient.resolver_label()} attivo.", flush=True)
    else:
        print("Resolver DNS non filtrato disattivato: uso il DNS di sistema.", flush=True)

    httpd = None
    for port in range(args.port, args.port + 5):
        try:
            httpd = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        print("Nessuna porta libera disponibile.")
        sys.exit(1)

    url = f"http://{args.host}:{httpd.server_address[1]}/"
    print(f"MagnetScout in ascolto su {url}  (Ctrl+C per fermare)", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")
        httpd.server_close()


if __name__ == "__main__":
    main()
