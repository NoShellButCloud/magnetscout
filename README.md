# MagnetScout

Il tuo **portale locale per il download** (gira sul tuo PC, niente servizi
esterni): un'unica interfaccia con tre sezioni.

- **🧲 Torrent** — cerca magnet di film e serie TV su più indici pubblici, con
  filtri per **lingua**, **tipo**, **genere**, **risoluzione**, qualità e
  dimensione; motore per **fonti personalizzate** e **crawler** che scopre nuove
  fonti da una parola chiave.
- **📦 XDCC** — cerca pacchetti sui motori XDCC (bot su IRC) e avviali con un
  clic: il trasferimento avviene in background.
- **⬇️ Download** — la coda dei file XDCC: avanzamento, velocità, tempo
  rimanente, pausa/ripresa, cartella di destinazione.

## Requisiti

- Python 3.9+ (nessuna dipendenza: solo libreria standard)

## Avvio

```powershell
python server.py
```

Si apre il browser su `http://127.0.0.1:8765/` (l'host è limitato a localhost).

Opzioni utili:

```powershell
python server.py --port 9000      # porta diversa (prova anche le successive se occupata)
python server.py --no-browser     # non aprire il browser
python server.py --dns auto       # resolver non filtrato (default)
python server.py --dns doh        # solo DNS-over-HTTPS (Cloudflare/Google)
python server.py --dns udp        # solo DNS classico verso 1.1.1.1 / 8.8.8.8
python server.py --dns off        # usa il DNS del provider
python server.py --dns-servers 9.9.9.9,1.1.1.1
python server.py --downloads "D:\Download"   # cartella dei file XDCC
```

## DNS non filtrato (attivo di default)

Molti provider filtrano o avvelenano le risposte DNS verso gli indici torrent:
il sito esiste, ma "non risolve". All'avvio MagnetScout installa un resolver
proprio e lo usa per tutte le richieste HTTP:

1. **DNS over HTTPS** su Cloudflare (1.1.1.1) e poi Google (8.8.8.8) — cifrato,
   non manomettibile dai DNS del provider;
2. **DNS classico UDP** verso `1.1.1.1` / `8.8.8.8` (personalizzabile con
   `--dns-servers`);
3. resolver di sistema, come ultima risorsa.

Le risposte sono messe in cache (10 minuti, 30 secondi per i fallimenti) e le
connessioni usano gli indirizzi risolti **mantenendo la verifica del certificato
TLS** sul nome del sito (SNI e hostname check invariati): il filtro DNS viene
aggirato, la sicurezza della connessione no.

Diagnostica: `GET /api/dns?host=nyaa.si` (aggiungi `&fresh=1` per ignorare la
cache) mostra indirizzi, resolver che ha risposto e contatori. Il resolver
attivo è indicato anche nel piè di pagina dell'interfaccia.

## Fonti

| Fonte | Note |
|---|---|
| **The Pirate Bay** (apibay.org) | lingua/qualità/risoluzione dedotte dal nome del file |
| **YTS** | film in 720p/1080p/4K con genere e lingua nei metadati; usa mirror alternativi automaticamente |
| **Internet Archive** | film di **pubblico dominio** / licenze aperte; il magnet viene generato al volo dal file `.torrent` dell'item (pulsante *Genera magnet*) |
| **Knaben** | aggregatore di più indici (The Pirate Bay, 1337x…): si interroga via API JSON e si tengono solo le categorie film/serie |
| **Torrents-CSV** | database pubblico di metadati; nessuna categoria film/serie e nessun genere |
| **Nyaa** | anime e live action via feed RSS; non distingue film e serie |
| **RuTor** (rutor.info) | indice russo generalista (film, serie, libri, musica) con magnet, dimensione, data e seed nella pagina dei risultati |
| **TorrentKitty** | indice generalista (DHT/aggregatore) con magnet, dimensione e data; i titoli sono spesso "sporchi" (prefissi di chi li ha pubblicati) |
| **TNT Village DB** | archivio storico del tracker italiano TNT Village: titoli spesso in italiano; il magnet è ricostruito dall'info hash del file `.torrent` e **i seed non sono indicati** (molti rilasci sono vecchi) |
| **AnimeTosho** | anime e live action con magnet e dimensione; distingue film e serie dal nome |
| **Tokyo Toshokan** | anime via feed RSS (magnet nel corpo del feed, sezione per adulti esclusa) |
| **SubsPlease** | episodi anime sub (480p/720p/1080p) via API JSON, con magnet e risoluzione |
| **Academic Torrents** | corsi e materiali accademici di pubblico dominio/aperto; **disattivata di default**: la prima ricerca scarica il catalogo XML (~3 MB, poi valido un giorno) e cerca in locale |

Le fonti sono selezionabili dall'interfaccia. Una fonte non raggiungibile non
blocca le altre: viene mostrato un chip di errore con il dettaglio.

Gli indici generalisti (TorrentKitty, RuTor) vengono scremati dai contenuti per
adulti; per le fonti senza categorie (TorrentKitty, RuTor, TNT Village,
Academic Torrents) il *tipo* Film/Serie viene dedotto dal nome del file.

Quando una fonte aggiunge torrent, i duplicati vengono uniti per info hash:
lo stesso torrent trovato da più fonti compare una volta sola.

Per ogni riga il pulsante **ℹ** mostra una riga di verifica con il nome reale
del torrent letto dal magnet (`dn=`), la fonte, la qualità e la dimensione:
serve a controllare che il magnet corrisponda davvero alla riga scelta.

## XDCC: ricerca e download

**XDCC** è un modo di scaricare file da **IRC**: un *bot* tiene dei pacchetti
numerati e li invia su richiesta con `/msg <bot> xdcc send #<numero>`.
MagnetScout fa tutto da sé: cerca i pacchetti, si connette alla rete giusta,
chiede il file e lo salva sul disco.

### Ricerca

| Motore | Cosa indicizza |
|---|---|
| **xdcc.eu** | indice multi-rete (Abjects, Rizon, Abandoned-IRC…): per ogni pacchetto fornisce rete, **server**, canale, bot, slot, dimensione e il **comando esatto** |
| **NIBL** (api.nibl.co.uk) | indice specializzato in **anime/manga** sui bot di Rizon, via API JSON (con numero di episodio e data) |

I due motori vengono interrogati in parallelo e i risultati **alternati** uno per
uno, così nessuna fonte satura l'elenco; i doppioni (stesso bot, stesso
pacchetto) vengono uniti. Filtri disponibili: **episodio** (numero esatto),
**fonte**, **dimensione massima** (stesso cursore a scaglioni dei torrent) e
**ordinamento** (rilevanza, dimensione, episodio, nome, più scaricati).

### Download

Avviando un pacchetto, MagnetScout:

1. si connette al server IRC della rete (**TLS** sul 6697, altrimenti 6667);
2. entra nel canale indicato, se serve (alcune reti lo pretendono);
3. invia `/msg <bot> xdcc send #<numero>`;
4. attende l'offerta **DCC SEND** del bot (se il bot ha le code piene, il
   messaggio di attesa viene mostrato nella scheda);
5. scarica il file in diretta dal bot, confermando i byte ricevuti (ACK).

Dettagli che contano nell'uso reale:

- il file viene scritto come **`.part`** e rinominato solo a trasferimento
  completo; se il file esiste già viene salvato come `nome (2).ext`;
- **pausa e ripresa**: mettendo in pausa il `.part` resta su disco e la ripresa
  chiede al bot il **DCC RESUME** (riparte dal punto in cui era arrivato); se il
  bot non lo supporta, il file ricomincia da capo;
- i **download simultanei** sono limitati (1-3, impostabile) e ogni download ha
  la sua connessione IRC;
- la coda e lo stato sono salvati in `xdcc_downloads.json`: chiudendo e
  riaprendo il server i download interrotti tornano in coda;
- nomi di file pericolosi (percorsi, caratteri riservati di Windows) vengono
  normalizzati prima di scrivere.

La cartella di destinazione predefinita è `downloads/` accanto al progetto e si
cambia dall'interfaccia (o con `--downloads`). Endpoint: `GET
/api/xdcc/search`, `GET /api/xdcc/downloads`, `POST /api/xdcc/downloads`, `POST
/api/xdcc/downloads/action` (pausa, riprendi, riprova, rimuovi, apri cartella,
pulisci), `POST /api/xdcc/settings`.

## Fonti personalizzate

Dal pannello *Aggiungi fonti e scopri nuove fonti* puoi aggiungere le tue fonti;
restano salvate in `custom_sources.json` nella cartella del progetto. Tipi
disponibili:

| Tipo | Cosa fa | Impostazioni |
|---|---|---|
| `rss` | legge un feed RSS/Atom (come nyaa) | URL con `{query}` |
| `json` | interroga un'API JSON | URL con `{query}`, `results_path`, `fields` |
| `html` | estrae i link `magnet:` da una pagina di risultati | URL con `{query}` |
| `link` | non espone risultati: mostra un collegamento alla ricerca sul sito | URL con `{query}` |

`{query}` viene sostituito con il testo cercato (codificato per l'URL).

Per il tipo `json` le **impostazioni avanzate** (JSON) permettono di indicare
dove sono i risultati e come si chiamano i campi, per esempio:

```json
{
  "results_path": "data.items",
  "fields": {
    "title": "name",
    "magnet": "magnet",
    "size": "size_bytes",
    "seeds": "seeders",
    "leechers": "leechers",
    "date": "created_unix",
    "url": "details",
    "torrent_url": "download"
  }
}
```

Se non indichi `fields`, i nomi più comuni (`name`, `title`, `info_hash`,
`size_bytes`, `seeders`, `magnet`, …) vengono riconosciuti da soli; se manca il
magnet ma c'è un `torrent_url` che punta a un file `.torrent`, il magnet viene
generato su richiesta con il pulsante *Genera magnet* (endpoint
`/api/magnet?url=…`, solo http/https).

Le fonti personalizzate senza categoria (film/serie) non vengono filtrate per
*tipo*: si adattano al filtro richiesto. Endpoint: `GET/POST/DELETE
/api/custom-sources`.

## Scoperta di nuove fonti (crawler)

Nel pannello *Aggiungi fonti*: scrivi una parola chiave (es. `ilcorsaronero`)
o direttamente un dominio e premi **Analizza**. Il crawler:

1. cerca i domini citati sul web con **DuckDuckGo** (HTML) e, come tentativo
   best-effort, con l'API JSON di **Reddit** (spesso blocca le richieste
   automatiche: in quel caso compare come "non disponibile");
2. scarta motori di ricerca, social, blog e i domini già presenti;
3. prova ogni dominio: feed RSS con risultati veri, pagina di ricerca con
   almeno 3 link `magnet:`, oppure solo una ricerca da aprire;
4. propone le fonti utilizzabili ordinate per qualità della scoperta, pronte da
   aggiungere con un clic (endpoint `GET /api/discover?q=…`).

## Preferenze salvate (filtri e fonti)

Dal pannello *💾 Preferenze: salva filtri e fonti*:

- **Profili con nome** — salvi la combinazione corrente di **filtri + fonti
  selezionate** con un nome (es. *Film in italiano*, *Anime sub*, *Pubblico
  dominio*) e la richiami con un clic. Puoi **eliminare** un profilo, **esportare**
  tutti i profili in un file JSON e **importarli** su un'altra macchina.
- **Salvataggio automatico** — con *Ricorda automaticamente filtri e fonti*
  attivo, ogni modifica di filtri o fonti viene scritta su disco (con un piccolo
  ritardo) e ripristinata all'avvio, anche su un browser diverso o dopo aver
  svuotato i dati del browser.

Le preferenze stanno in `preferences.json` nella cartella del progetto, accanto
a `custom_sources.json`: niente cloud, niente account. La memoria locale del
browser resta come appoggio immediato, mentre il file è la copia affidabile.

Endpoint: `GET /api/preferences`, `POST /api/preferences` (salva un profilo),
`POST /api/preferences/last` (ultimo stato), `POST /api/preferences/import`,
`DELETE /api/preferences?name=…`.

## Filtri

- **Lingua** — la lingua si ricava dai **metadati della fonte** e dal **nome del
  file** (`ITA`, `ENG`, `MULTI`, `sub-ita`, `Italiano`…). Il filtro è
  *restrittivo*: la lingua scelta deve comparire **davvero**, quindi con
  *Italiano* non passano i generici `MULTI` di cui non si conoscono le lingue,
  ma passano i titoli `MULTI` in cui l'italiano è riconosciuto. Scegliendo
  `MULTI` si vedono i titoli dichiarati multilingua (o con almeno due lingue
  riconosciute).
- **Tipo** — Film / Serie TV (cambia categorie e collection interrogate).
- **Genere** — disponibile per le fonti che lo forniscono nei metadati (YTS,
  Internet Archive); il filtro esclude le fonti senza generi (avviso in UI).
- **Risoluzione** — 480p/576p, 720p, 1080p, 4K, 3D. Gli item senza qualità
  dichiarata (es. Internet Archive) sono esclusi quando il filtro è attivo.
- **Qualità** — Remux, BluRay, WEB, HDTV, DVD, CAM/TS.
- **Max dimensione** — cursore a **scaglioni**: da `0,1 GB` a `1 GB` a passi di
  `0,1 GB`, poi da `1,5 GB` a `10 GB` a passi di `0,5 GB`, e in fondo
  `♾️ nessun limite` (posizione tutta a sinistra). Il valore iniziale è `≤ 5 GB`;
  `♻️ Azzera filtri` porta a *nessun limite*. Gli item **senza dimensione nota**
  sono esclusi quando il limite è attivo. La soglia è confrontata con la stessa
  dimensione mostrata in tabella (1 GB = 1024³ byte).
- **Corrispondenza** — confronto esatto **ignorando i separatori** (`. _ - '`
  spazi, parentesi, accenti…):
  - *Parole esatte*: ogni parola cercata deve comparire come parola intera, quindi
    `the matrix` trova `The.Matrix.Reloaded.2003` ma non `TheMatrix` o `matrixx`.
  - *Titolo identico*: il titolo deve combaciare in pieno, quindi
    `night of the living dead` trova `Night of the Living Dead` ma non
    `Night of the Living Dead Trailer`.

Ordinamento per seed (default), rilevanza, data o dimensione; clic sulle
intestazioni per riordinare (le azioni restano legate alla riga giusta). Azioni
per riga: copia magnet, apri nel client torrent, dettagli (magnet completo,
pagina fonte, IMDb). Puoi anche copiare tutti i magnet o esportarli in
`magnet-scout.txt`.

## Interfaccia

Le tre sezioni si aprono dalle linguette in alto (**🧲 Torrent**, **📦 XDCC**,
**⬇️ Download**); la sezione attiva viene ricordata, e sulla linguetta
*Download* compare un contatore con i trasferimenti in corso o in coda.

Torrent:

- **Bandierine** nelle lingue (menu e risultati), icone nei filtri e nelle
  etichette (tipo, genere, risoluzione, qualità, dimensione, corrispondenza,
  ordinamento) e nelle azioni.
- **Filtri, fonti e ultima ricerca ricordati**: al ricaricamento della pagina
  l'ultima ricerca riparte da sola; con il pannello *Preferenze* puoi salvare
  profili con nome e ripristinare tutto anche su un altro browser (vedi
  *Preferenze salvate*); `♻️ Azzera filtri` rimette tutto a zero (dimensione
  inclusa: nessun limite).
- **Intestazione fissa** della tabella mentre si scorre l'elenco; l'elenco scorre
  sia in verticale (max ~74% dell'altezza) sia in orizzontale.
- **Clic su una riga** per aprire/chiudere i dettagli; le parole cercate sono
  evidenziate nei titoli.
- **Scorciatoie**: `/` o `Ctrl+K` mettono a fuoco la ricerca, `Esc` chiude i
  dettagli aperti (o svuota il campo se stai scrivendo).
- **Fonti**: pulsanti `tutte` / `nessuna` per selezionarle in blocco.
- Conteggio e tempo di risposta in alto, con i risultati per fonte e gli avvisi
  (⚠) quando una fonte risponde ma con limitazioni.

XDCC e Download:

- nella tabella XDCC ogni riga mostra il **comando IRC** pronto e i pulsanti
  **⬇️** (scarica) e **📋** (copia il comando); la toolbar in alto permette di
  **copiare tutti i comandi** o di **aggiungere alla coda tutti i risultati
  mostrati** (utile per una serie intera);
- un pacchetto già in coda resta marcato **✓** per non essere aggiunto due volte;
- nella sezione *Download* ogni trasferimento ha barra di avanzamento, byte,
  **velocità**, **tempo rimanente**, rete/bot/pacchetto e i comandi
  ⏸️ pausa, ▶️ riprendi, 🔁 riprova, 📂 apri cartella, 🗑️ togli dalla lista,
  🧨 elimina il file;
- i filtri XDCC (ricerca, episodio, fonte, ordinamento, dimensione) e l'ultima
  ricerca vengono ricordati come quelli dei torrent.

## Struttura

```
server.py         server HTTP + API JSON (/api/meta, /api/search, /api/magnet,
                  /api/dns, /api/discover, /api/custom-sources, /api/preferences,
                  /api/xdcc/...)
dnsclient.py      resolver non filtrato (DoH/UDP) e connessioni HTTP/HTTPS
sourcebase.py     helper HTTP, filtri, filtro centrale, ordinamento, parser
                  generici (feed RSS, link magnet)
sources_builtin.py adattatori delle fonti incluse
source_custom.py  fonti personalizzate (rss/json/html/link) + custom_sources.json
sourcecrawl.py    scoperta di nuove fonti (DuckDuckGo/Reddit + prove sui domini)
sources.py        registro delle fonti + orchestrazione della ricerca
preferences.py    profili e ultimo stato (filtri + fonti) in preferences.json
xdcc.py           ricerca XDCC (xdcc.eu, NIBL) e risultati normalizzati
xdccdl.py         client IRC + ricezione DCC, coda dei download
                  (xdcc_downloads.json, file in downloads/)
textutil.py       estrazione da titoli (risoluzione, qualità, lingua, dimensioni,
                  confronto esatto ignorando i separatori)
torrent.py        parser bencode minimale + costruzione link magnet
taxonomy.py       nomi lingue, etichette qualità, generi canonici
static/           interfaccia web (HTML/CSS/JS vanilla, nessun build step):
                  app.js = sezione Torrent, portal.js = sezioni e XDCC/Download
```

## Aggiungere una fonte al codice

1. In `sources_builtin.py` scrivi una sottoclasse di `Source` che implementa
   `search(filters)` e ritorna `(lista_di_risultati, nota)`; usa `make_result()`
   per i campi normalizzati.
2. Aggiungila a `BUILTIN_SOURCES`.

Il server la espone automaticamente in `/api/meta`, checkboxes incluse. Per
fonti "una tantum" conviene invece il pannello delle fonti personalizzate.

## Note

- Tutto gira su `127.0.0.1`: l'interfaccia non è raggiungibile dalla rete.
- È presente una piccola cache in memoria (3 minuti) per le ricerche ripetute.
- Gli indici interrogati sono pubblici; scarica solo contenuti che hai il
  diritto di scaricare. Internet Archive offre migliaia di film di pubblico
  dominio: ottimo punto di partenza.
- I pacchetti XDCC sono offerti da bot di terzi su IRC: la velocità dipende dal
  bot, dalle sue code e dalla tua connessione. Scaricando più pacchetti insieme
  la banda si divide; i pacchetti grandi (decine di GB) possono durare ore.
