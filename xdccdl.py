# -*- coding: utf-8 -*-
"""Motore di scaricamento XDCC: client IRC minimale + ricezione DCC, con coda.

Il flusso di un pacchetto XDCC:

1. connessione al server IRC della rete (TLS se disponibile), registrazione;
2. ingresso nel canale indicato (alcune reti lo pretendono);
3. invio di ``/msg <bot> xdcc send #<numero>``;
4. il bot risponde con un'offerta ``DCC SEND nome ip porta dimensione``;
5. connessione diretta al bot e ricezione del file, confermando i byte
   ricevuti (ACK): il file viene scritto come ``.part`` e rinominato alla fine.

Solo libreria standard. Ogni download ha la sua connessione IRC (semplice e
robusto); la coda limita quanti download procedono insieme.
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import unicodedata

from paths import DATA_DIR, data_path

STATE_FILE = data_path("xdcc_downloads.json")

DEFAULT_DIR = os.path.join(DATA_DIR, "downloads")
DEFAULT_NICK = "MagnetScout"
MAX_PARALLEL = 3

IRC_REGISTER_TIMEOUT = 45
IRC_NICK_RETRY = 3
DCC_OFFER_TIMEOUT = 300      # i bot possono metterti in coda prima di inviare
DCC_CONNECT_TIMEOUT = 30
DCC_IDLE_TIMEOUT = 120       # nessun dato per questo tempo -> errore
RESUME_TIMEOUT = 20
SOCKET_TICK = 2              # timeout dei socket: controlla annullamento/ripresa

DCC_SEND_RE = re.compile(
    r"DCC SEND (?:\"|\')?(?P<name>.+?)(?:\"|\')? (?P<ip>[\d.]+|\d+) "
    r"(?P<port>\d+)(?: (?P<size>\d+))?")
DCC_ACCEPT_RE = re.compile(
    r"DCC ACCEPT (?:\"|\')?(?P<name>.+?)(?:\"|\')? (?P<port>\d+) (?P<offset>\d+)")

STATE_LABELS = {
    "queued": "In coda",
    "connecting": "Connessione",
    "requesting": "Richiesta inviata",
    "waiting": "In attesa del bot",
    "downloading": "Scaricamento",
    "paused": "In pausa",
    "done": "Completato",
    "error": "Errore",
    "cancelled": "Annullato",
}
ACTIVE_STATES = ("connecting", "requesting", "waiting", "downloading")


# ---------------------------------------------------------------------------
# Utilita'
# ---------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Nome file sicuro: niente percorsi, caratteri di controllo o nomi riservati."""
    text = str(name or "").replace("\\", "/").split("/")[-1]
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", text).strip(" .")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = "xdcc_download"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if text.split(".")[0].upper() in reserved:
        text = "_" + text
    return text[:180]


def unique_path(directory: str, filename: str) -> str:
    """Percorso libero: se il file esiste aggiunge ' (2)', ' (3)'..."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def human_size(num) -> str:
    if num is None:
        return "—"
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if num >= div:
            value = num / div
            return f"{value:.2f} {unit}" if value < 100 else f"{value:.0f} {unit}"
    return f"{int(num)} B"


def human_speed(num) -> str:
    return human_size(num) + "/s" if num else "—"


def human_eta(seconds):
    if seconds is None or seconds < 0 or seconds > 86400 * 7:
        return "—"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def ip_to_text(value: str) -> str:
    """DCC puo' mandare l'indirizzo come intero a 32 bit."""
    text = str(value or "")
    if re.fullmatch(r"\d+", text):
        number = int(text)
        if number > 65535:
            return socket.inet_ntoa(struct.pack(">I", number & 0xFFFFFFFF))
    return text


# ---------------------------------------------------------------------------
# Client IRC minimale
# ---------------------------------------------------------------------------
class IrcClient:
    """Connessione IRC essenziale: registrazione, PING/PONG, lettura a righe."""

    def __init__(self, server: str, port: int = 6697, tls: bool = True, nick: str = DEFAULT_NICK):
        self.server = server
        self.port = int(port)
        self.tls = tls
        self.nick = nick
        self.sock = None
        self.buffer = b""
        self.connected = False

    def connect(self) -> str:
        raw = socket.create_connection((self.server, self.port), timeout=20)
        if self.tls:
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=self.server)
        raw.settimeout(SOCKET_TICK)
        self.sock = raw
        nick = self.nick
        self.send(f"NICK {nick}")
        self.send(f"USER {nick} 0 * :MagnetScout XDCC")
        deadline = time.time() + IRC_REGISTER_TIMEOUT
        attempts = 0
        while time.time() < deadline:
            for line in self.lines(remaining=2):
                code = line.split(" ")[1] if " " in line else ""
                if code == "433":       # nick in uso
                    attempts += 1
                    if attempts > IRC_NICK_RETRY:
                        raise RuntimeError("nick gia' in uso sulla rete")
                    nick = f"{self.nick}{attempts}"
                    self.send(f"NICK {nick}")
                elif code == "001":
                    self.nick = nick
                    self.connected = True
                    return nick
                elif code in ("464", "465"):
                    raise RuntimeError("autenticazione richiesta dal server IRC")
        raise RuntimeError("registrazione IRC non completata")

    def send(self, line: str):
        if not self.sock:
            raise RuntimeError("connessione IRC chiusa")
        self.sock.sendall((line + "\r\n").encode("utf-8", "replace"))

    def lines(self, remaining=None):
        """Genera le righe ricevute; 'remaining' e' il tempo massimo di attesa."""
        deadline = None if remaining is None else time.time() + remaining
        while True:
            if deadline is not None and time.time() >= deadline:
                return
            try:
                chunk = self.sock.recv(8192)
            except socket.timeout:
                if deadline is None:
                    continue
                return
            except OSError:
                return
            if not chunk:
                self.connected = False
                return
            self.buffer += chunk
            while b"\r\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\r\n", 1)
                line = raw.decode("utf-8", "replace")
                if line.startswith("PING"):
                    try:
                        self.send("PONG " + line[5:])
                    except OSError:
                        return
                yield line
            if deadline is None:
                continue

    def close(self):
        if not self.sock:
            return
        try:
            self.send("QUIT :MagnetScout")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None
        self.connected = False


# ---------------------------------------------------------------------------
# Job di download
# ---------------------------------------------------------------------------
class Download:
    """Un pacchetto XDCC in coda o in corso."""

    def __init__(self, job_id, *, server, port=6697, tls=True, channel="", bot="",
                 pack="", command="", name="", size_bytes=None, network=""):
        self.id = job_id
        self.server = server
        self.port = int(port or 6697)
        self.tls = bool(tls)
        self.channel = channel or ""
        self.bot = bot
        self.pack = str(pack or "")
        self.command = command or ""
        self.name = name or f"pack-{pack}"
        self.size_bytes = size_bytes or None
        self.network = network or ""
        self.state = "queued"
        self.received = 0
        self.path = ""
        self.error = ""
        self.note = ""
        self.speed = 0.0
        self.created = int(time.time())
        self.updated = self.created
        self.started = None
        self.finished = None
        self._cancel = False
        self._pause = False
        self._running = False
        self._requeue = False
        self._irc = None
        self._data = None

    # -- serializzazione ---------------------------------------------------
    def disk(self) -> dict:
        return {"id": self.id, "server": self.server, "port": self.port, "tls": self.tls,
                "channel": self.channel, "bot": self.bot, "pack": self.pack,
                "command": self.command, "name": self.name, "size_bytes": self.size_bytes,
                "network": self.network, "state": self.state, "received": self.received,
                "path": self.path, "error": self.error, "created": self.created,
                "updated": self.updated, "started": self.started, "finished": self.finished}

    def view(self) -> dict:
        percent = 0.0
        if self.size_bytes:
            percent = min(100.0, self.received / self.size_bytes * 100)
        elif self.state == "done":
            percent = 100.0
        eta = None
        if self.speed and self.size_bytes and self.state == "downloading":
            eta = max(0.0, (self.size_bytes - self.received) / self.speed)
        return {
            "id": self.id, "network": self.network, "server": self.server,
            "channel": self.channel, "bot": self.bot, "pack": self.pack,
            "name": self.name, "state": self.state,
            "state_label": STATE_LABELS.get(self.state, self.state),
            "active": self.state in ACTIVE_STATES,
            "size_bytes": self.size_bytes, "size_human": human_size(self.size_bytes),
            "received": self.received, "received_human": human_size(self.received),
            "percent": round(percent, 1), "speed": round(self.speed),
            "speed_human": human_speed(self.speed), "eta": eta,
            "eta_human": human_eta(eta), "error": self.error, "note": self.note,
            "created": self.created, "updated": self.updated,
            "started": self.started, "finished": self.finished, "path": self.path,
        }

    @property
    def part_path(self) -> str:
        return self.path + ".part" if self.path and not self.path.endswith(".part") else self.path


# ---------------------------------------------------------------------------
# Gestore della coda
# ---------------------------------------------------------------------------
class DownloadManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs: list[Download] = []
        self.settings = {"download_dir": DEFAULT_DIR, "max_parallel": 2,
                         "nick": DEFAULT_NICK}
        self._dirty = False
        self._stop = threading.Event()
        self._load()
        threading.Thread(target=self._dispatcher, name="xdcc-dispatch", daemon=True).start()
        threading.Thread(target=self._saver, name="xdcc-save", daemon=True).start()

    # -- persistenza -------------------------------------------------------
    def _load(self):
        if not os.path.isfile(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        settings = data.get("settings") if isinstance(data, dict) else None
        if isinstance(settings, dict):
            if settings.get("download_dir"):
                self.settings["download_dir"] = str(settings["download_dir"])
            if settings.get("max_parallel"):
                self.settings["max_parallel"] = max(1, min(int(settings["max_parallel"]), MAX_PARALLEL))
            if settings.get("nick"):
                self.settings["nick"] = str(settings["nick"])
        for item in (data.get("jobs") or []) if isinstance(data, dict) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            job = Download(item["id"], server=item.get("server") or "",
                           port=item.get("port") or 6697, tls=item.get("tls", True),
                           channel=item.get("channel") or "", bot=item.get("bot") or "",
                           pack=item.get("pack") or "", command=item.get("command") or "",
                           name=item.get("name") or "", size_bytes=item.get("size_bytes"),
                           network=item.get("network") or "")
            job.received = int(item.get("received") or 0)
            job.path = item.get("path") or ""
            job.error = item.get("error") or ""
            job.state = item.get("state") or "queued"
            job.created = int(item.get("created") or job.created)
            job.updated = int(item.get("updated") or job.updated)
            job.started = item.get("started")
            job.finished = item.get("finished")
            # i job interrotti da un riavvio tornano in coda se erano attivi
            if job.state in ACTIVE_STATES:
                job.state = "queued"
                job.note = "riavviato dopo un riavvio del server"
            if job.state in ("queued", "connecting", "requesting", "waiting", "downloading"):
                job._cancel = False
            self.jobs.append(job)

    def _save(self):
        data = {"settings": self.settings, "jobs": [job.disk() for job in self.jobs[-200:]]}
        tmp = STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass

    def _saver(self):
        while not self._stop.is_set():
            time.sleep(2)
            with self.lock:
                dirty = self._dirty
                self._dirty = False
            if dirty:
                self._save()

    def _touch(self):
        self._dirty = True

    # -- impostazioni ------------------------------------------------------
    def update_settings(self, download_dir=None, max_parallel=None, nick=None) -> dict:
        with self.lock:
            if download_dir is not None:
                path = os.path.abspath(os.path.expanduser(str(download_dir).strip() or DEFAULT_DIR))
                os.makedirs(path, exist_ok=True)
                self.settings["download_dir"] = path
            if max_parallel is not None:
                self.settings["max_parallel"] = max(1, min(int(max_parallel), MAX_PARALLEL))
            if nick is not None:
                cleaned = re.sub(r"[^\w\[\]\\`^{}|-]", "", str(nick).strip())[:24]
                if cleaned:
                    self.settings["nick"] = cleaned
            self._touch()
        return dict(self.settings)

    # -- coda --------------------------------------------------------------
    def add(self, **fields) -> dict:
        server = str(fields.get("server") or "").strip()
        bot = str(fields.get("bot") or "").strip()
        pack = str(fields.get("pack") or "").strip()
        if not server or not bot or not pack:
            raise ValueError("server, bot e numero di pacchetto sono obbligatori")
        command = str(fields.get("command") or "").strip()
        if command.lower().startswith(bot.lower()):
            command = command[len(bot):].strip()
        if not command:
            command = f"xdcc send #{pack}"
        with self.lock:
            # gia' in coda o scaricato? segnala e non duplicare
            for job in self.jobs:
                if (job.bot.lower() == bot.lower() and job.pack == pack
                        and job.state not in ("cancelled", "error")):
                    return {"job": job.view(), "duplicate": True}
            job = Download(
                f"dl{int(time.time() * 1000)}{len(self.jobs) % 97}",
                server=server, port=fields.get("port") or 6697,
                tls=fields.get("tls", True), channel=str(fields.get("channel") or ""),
                bot=bot, pack=pack, command=command,
                name=str(fields.get("name") or ""),
                size_bytes=fields.get("size_bytes"),
                network=str(fields.get("network") or ""))
            self.jobs.insert(0, job)
            self._touch()
            return {"job": job.view(), "duplicate": False}

    def cancel(self, job_id: str, pause=False) -> bool:
        with self.lock:
            job = self._find(job_id)
            if not job:
                return False
            job._cancel = True
            job._requeue = False
            job._pause = bool(pause)
            job.note = "in pausa" if pause else "annullato"
            if not job._running:
                job.state = "paused" if pause else "cancelled"
                job.speed = 0.0
                job.updated = int(time.time())
            self._touch()
            return True

    def retry(self, job_id: str) -> dict | None:
        with self.lock:
            job = self._find(job_id)
            if not job:
                return None
            job.error = ""
            job.speed = 0.0
            if job._running:
                # il download in corso si ferma e torna in coda appena esce
                job._requeue = True
                job._cancel = True
            else:
                job._cancel = False
                job._pause = False
                job.state = "queued"
                job.note = ""
            job.updated = int(time.time())
            self._touch()
            return job.view()

    def remove(self, job_id: str, delete_file=False) -> bool:
        with self.lock:
            job = self._find(job_id)
            if not job:
                return False
            job._cancel = True
            self.jobs.remove(job)
            self._touch()
        if delete_file and job.path:
            for path in (job.path, job.path + ".part"):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass
        return True

    def clear_finished(self) -> int:
        with self.lock:
            finished = [job for job in self.jobs
                        if job.state in ("done", "error", "cancelled")]
            self.jobs = [job for job in self.jobs if job not in finished]
            self._touch()
            return len(finished)

    def open_folder(self, job_id=None) -> str:
        with self.lock:
            job = self._find(job_id) if job_id else None
        folder = os.path.dirname(job.path) if job and job.path else self.settings["download_dir"]
        folder = os.path.abspath(folder)
        os.makedirs(folder, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as exc:
            raise RuntimeError(f"impossibile aprire la cartella: {exc}") from exc
        return folder

    def _find(self, job_id):
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def snapshot(self) -> dict:
        with self.lock:
            jobs = [job.view() for job in self.jobs]
            active = [job for job in self.jobs if job.state == "downloading"]
            total_speed = sum(job.speed for job in active)
            counts = {}
            for job in self.jobs:
                counts[job.state] = counts.get(job.state, 0) + 1
            return {
                "settings": dict(self.settings),
                "jobs": jobs,
                "counts": counts,
                "active": len([job for job in self.jobs if job.state in ACTIVE_STATES]),
                "queued": counts.get("queued", 0),
                "done": counts.get("done", 0),
                "total_speed": round(total_speed),
                "total_speed_human": human_speed(total_speed),
            }

    # -- dispatcher --------------------------------------------------------
    def _dispatcher(self):
        while not self._stop.is_set():
            time.sleep(0.6)
            with self.lock:
                running = len([job for job in self.jobs if job.state in ACTIVE_STATES])
                limit = max(1, int(self.settings.get("max_parallel") or 1))
                pending = [job for job in self.jobs
                           if job.state == "queued" and not job._cancel and not job._running]
                to_start = pending[:max(0, limit - running)]
            for job in to_start:
                threading.Thread(target=self._run, args=(job,),
                                 name=f"xdcc-{job.id}", daemon=True).start()

    # -- esecuzione --------------------------------------------------------
    def _run(self, job: Download):
        with self.lock:
            job._running = True
            job.state = "connecting"
            job.started = int(time.time())
            job.updated = job.started
            self._touch()
        irc = None
        try:
            nick = self.settings.get("nick") or DEFAULT_NICK
            irc = self._connect_irc(job, nick)
            job._irc = irc
            with self.lock:
                job.updated = int(time.time())
                self._touch()
            if job.channel:
                irc.send(f"JOIN {job.channel}")
                # qualche secondo per essere accettati nel canale
                deadline = time.time() + 4
                for line in irc.lines(remaining=max(0.5, deadline - time.time())):
                    _ = line
            message = job.command or f"xdcc send #{job.pack}"
            irc.send(f"PRIVMSG {job.bot} :{message}")
            with self.lock:
                job.state = "waiting"
                job.note = "richiesta inviata, attendo il bot"
                job.updated = int(time.time())
                self._touch()
            offer = self._wait_for_offer(job, irc)
            if not offer:
                raise RuntimeError("il bot non ha inviato il file entro il tempo massimo")
            self._receive(job, offer)
        except _Cancelled:
            with self.lock:
                job.state = "paused" if job._pause else "cancelled"
                job.speed = 0.0
                job.updated = int(time.time())
                self._touch()
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                job.state = "error"
                job.error = str(exc)[:300]
                job.speed = 0.0
                job.updated = int(time.time())
                self._touch()
        finally:
            if job._irc:
                job._irc.close()
                job._irc = None
            if job._data:
                try:
                    job._data.close()
                except OSError:
                    pass
                job._data = None
            with self.lock:
                job._running = False
                if job._requeue:
                    job._requeue = False
                    job._cancel = False
                    job._pause = False
                    job.state = "queued"
                    job.note = "in ripresa"
                    job.updated = int(time.time())
                self._touch()

    def _connect_irc(self, job: Download, nick: str) -> IrcClient:
        """Prova TLS sul 6697, poi in chiaro sul 6667 (o sulla porta indicata)."""
        attempts = []
        if job.port == 6697:
            attempts = [(6697, True), (6667, False)]
        else:
            attempts = [(job.port, job.tls), (6667, False)]
        last = None
        for port, tls in attempts:
            if job._cancel:
                raise _Cancelled()
            client = IrcClient(job.server, port, tls, nick)
            try:
                client.connect()
                if port != job.port or tls != job.tls:
                    with self.lock:
                        job.port, job.tls = port, tls
                        self._touch()
                return client
            except Exception as exc:  # noqa: BLE001
                last = exc
                client.close()
        raise RuntimeError(f"connessione IRC a {job.server} non riuscita: {last}")

    def _wait_for_offer(self, job: Download, irc: IrcClient):
        deadline = time.time() + DCC_OFFER_TIMEOUT
        pattern = re.compile(
            r"^:(?P<sender>[^!\s]+)!\S+ (?:PRIVMSG|NOTICE) \S+ :(?P<text>.*)$")
        while time.time() < deadline:
            if job._cancel:
                raise _Cancelled()
            for line in irc.lines(remaining=min(SOCKET_TICK, max(0.5, deadline - time.time()))):
                if job._cancel:
                    raise _Cancelled()
                if "DCC SEND" in line:
                    match = DCC_SEND_RE.search(line)
                    if match:
                        return match.groupdict()
                    continue
                match = pattern.match(line)
                if not match:
                    continue
                text = match.group("text").replace("\x01", "").strip()
                sender = match.group("sender")
                if sender.lower() == job.bot.lower() and text:
                    with self.lock:
                        job.note = text[:160]
                        job.updated = int(time.time())
                        self._touch()
                    lowered = text.lower()
                    if "no such pack" in lowered or "not found" in lowered:
                        raise RuntimeError(f"il bot non ha il pacchetto: {text[:120]}")
        return None

    def _receive(self, job: Download, offer: dict):
        size = int(offer.get("size") or 0) or job.size_bytes or 0
        host = ip_to_text(offer["ip"])
        port = int(offer["port"])
        name = sanitize_filename(offer.get("name") or job.name)
        directory = self.settings["download_dir"]
        os.makedirs(directory, exist_ok=True)
        final_path = unique_path(directory, name)
        with self.lock:
            # due download diversi con lo stesso nome file non devono scriversi addosso
            busy = any(other is not job and other.state == "downloading"
                       and other.path == final_path for other in self.jobs)
        if busy:
            base, ext = os.path.splitext(name)
            final_path = unique_path(directory, f"{base} [{job.id[-4:]}]{ext}")
        part_path = final_path + ".part"
        with self.lock:
            job.name = name
            job.path = final_path
            job.size_bytes = size or job.size_bytes
            job.state = "downloading"
            job.note = f"Connessione al bot {host}:{port}"
            job.updated = int(time.time())
            self._touch()

        offset = 0
        if os.path.isfile(part_path) and size:
            offset = os.path.getsize(part_path)
            if offset >= size:
                offset = 0
                try:
                    os.remove(part_path)
                except OSError:
                    pass
        if offset and not self._request_resume(job, offer, port, offset):
            offset = 0
        mode = "ab" if offset else "wb"
        received = offset
        with self.lock:
            job.received = received

        data = socket.create_connection((host, port), timeout=DCC_CONNECT_TIMEOUT)
        data.settimeout(SOCKET_TICK)
        job._data = data
        started = time.time()
        window_start = started
        window_bytes = received
        last_data = started
        try:
            with open(part_path, mode) as handle:
                while True:
                    if job._cancel:
                        raise _Cancelled()
                    try:
                        chunk = data.recv(262144)
                    except socket.timeout:
                        if time.time() - last_data > DCC_IDLE_TIMEOUT:
                            raise RuntimeError(
                                "nessun dato ricevuto dal bot (timeout)")
                        continue
                    except OSError as exc:
                        raise RuntimeError(f"connessione dati interrotta: {exc}") from exc
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    last_data = time.time()
                    try:
                        data.sendall(struct.pack(">I", received & 0xFFFFFFFF))
                    except OSError:
                        pass
                    now = time.time()
                    if now - window_start >= 1.0:
                        instant = (received - window_bytes) / (now - window_start)
                        with self.lock:
                            job.speed = (job.speed * 0.4) + (instant * 0.6) if job.speed else instant
                            job.received = received
                            job.updated = int(now)
                            self._touch()
                        window_start, window_bytes = now, received
            if size and received < size:
                with self.lock:
                    job.received = received
                    job.speed = 0.0
                    job.error = f"trasferimento incompleto ({human_size(received)} di {human_size(size)})"
                    job.state = "error"
                    job.updated = int(time.time())
                    self._touch()
                return
            os.replace(part_path, final_path)
            elapsed = max(0.1, time.time() - started)
            with self.lock:
                job.received = received
                job.path = final_path
                job.state = "done"
                job.error = ""
                job.speed = 0.0
                job.finished = int(time.time())
                job.updated = job.finished
                job.note = (f"completato in {human_eta(elapsed)} "
                            f"({human_size(received / elapsed)}/s di media)")
                self._touch()
        finally:
            try:
                data.close()
            except OSError:
                pass
            job._data = None

    def _request_resume(self, job: Download, offer: dict, port: int, offset: int) -> bool:
        """Chiede al bot di riprendere da 'offset' (DCC RESUME/ACCEPT)."""
        if not job._irc:
            return False
        name = offer.get("name") or ""
        try:
            job._irc.send(f"PRIVMSG {job.bot} :\x01DCC RESUME {name} {port} {offset}\x01")
        except OSError:
            return False
        deadline = time.time() + RESUME_TIMEOUT
        while time.time() < deadline:
            if job._cancel:
                raise _Cancelled()
            for line in job._irc.lines(remaining=min(SOCKET_TICK, max(0.5, deadline - time.time()))):
                if "DCC ACCEPT" in line:
                    match = DCC_ACCEPT_RE.search(line)
                    if match and int(match.group("offset")) == offset:
                        with self.lock:
                            job.note = f"Ripresa da {human_size(offset)}"
                            self._touch()
                        return True
                if "DCC SEND" in line:
                    # il bot ha ignorato la ripresa: si riparte da zero
                    return False
        return False


class _Cancelled(Exception):
    """Annullamento richiesto dall'utente."""


manager = DownloadManager()
