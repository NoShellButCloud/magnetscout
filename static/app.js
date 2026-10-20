"use strict";

/* MagnetScout - logica dell'interfaccia. */

const $ = (sel) => document.querySelector(sel);

const state = {
  results: [],
  byUid: new Map(),
  sortKey: null,
  sortDir: 1,
};

const RES_ORDER = { "360p": 0, "480p": 1, "576p": 1, "720p": 2, "1080p": 3, "2160p": 4 };
const HEAD_LABELS = {
  title: "Titolo", source: "Fonte", lang: "Lingua", resolution: "Risoluzione",
  quality: "Qualità", size: "Dimensione", seeds: "⬆️ S", leechers: "⬇️ L", date: "Data",
};
const CMP = {
  title: (r) => (r.title || "").toLowerCase(),
  source: (r) => r.source_label || "",
  lang: (r) => r.lang_label || "",
  resolution: (r) => RES_ORDER[r.resolution] ?? -1,
  quality: (r) => r.quality_label || "",
  size: (r) => r.size_bytes || 0,
  seeds: (r) => r.seeds ?? -1,
  leechers: (r) => r.leechers ?? -1,
  date: (r) => r.date_ts || 0,
};

/* ------------------------------------------------- icone e bandierine */
const LANG_FLAGS = {
  it: "🇮🇹", en: "🇬🇧", multi: "🌐", fr: "🇫🇷", es: "🇪🇸", de: "🇩🇪", pt: "🇵🇹",
  ru: "🇷🇺", ja: "🇯🇵", ko: "🇰🇷", zh: "🇨🇳", ar: "🇸🇦", tr: "🇹🇷", pl: "🇵🇱",
  nl: "🇳🇱", sv: "🇸🇪", no: "🇳🇴", da: "🇩🇰", fi: "🇫🇮", el: "🇬🇷", he: "🇮🇱",
  hu: "🇭🇺", cs: "🇨🇿", ro: "🇷🇴", uk: "🇺🇦", hi: "🇮🇳",
};
const TYPE_ICONS = { "": "🌍", movie: "🎬", tv: "📺" };
const GENRE_ICONS = {
  azione: "💥", avventura: "🧭", animazione: "🎨", commedia: "😂", crime: "🕵️",
  documentario: "🎥", dramma: "🎭", famiglia: "👨‍👩‍👧", fantasy: "🐉",
  fantascienza: "🚀", guerra: "⚔️", horror: "👻", mistero: "🔎", musical: "🎵",
  romantico: "❤️", storico: "🏛️", thriller: "🔪", western: "🤠",
};
const RES_ICONS = { "": "🎯", "480p": "📼", "720p": "📺", "1080p": "🖥️", "2160p": "🌈", "3d": "🕶️" };
const QUALITY_ICONS = { "": "✨", remux: "💎", bluray: "📀", web: "☁️", hdtv: "📡", dvd: "💿", cam: "📹" };
const MATCH_ICONS = { "": "✅", words: "🔤", title: "🎯" };
const SORT_ICONS = { seeds: "🔥", relevance: "🎯", date: "🆕", size: "💾" };
const KIND_ICONS = { rss: "📡", json: "🧩", html: "🕸️", link: "🔗" };
const HEAD_TITLES = {
  title: "Titolo del torrent", source: "Fonte", lang: "Lingua rilevata",
  resolution: "Risoluzione", quality: "Qualità", size: "Dimensione dichiarata dalla fonte",
  seeds: "Seed (fonti attive)", leechers: "Leecher", date: "Data di pubblicazione",
};
const FILTER_IDS = ["mtype", "lang", "genre", "res", "quality", "size", "match", "sort"];

function iconFor(id, item) {
  const code = item.code ?? item.slug ?? "";
  if (id === "lang") return LANG_FLAGS[code] || (code ? "🏳️" : "🌍");
  if (id === "mtype") return TYPE_ICONS[code] || "🎬";
  if (id === "genre") return GENRE_ICONS[code] || "🎭";
  if (id === "res") return RES_ICONS[code] || "🎯";
  if (id === "quality") return QUALITY_ICONS[code] || "✨";
  if (id === "size") return code ? "📦" : "♾️";
  if (id === "match") return MATCH_ICONS[code] || "✅";
  if (id === "sort") return SORT_ICONS[code] || "🔥";
  if (id === "cf-kind") return KIND_ICONS[code] || "🧩";
  return "";
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtMs(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2).replace(".", ",")} s` : `${ms} ms`;
}

function fmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("it-IT", { year: "2-digit", month: "2-digit", day: "2-digit" });
}

function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { box.hidden = true; }, 2200);
}

async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast(message || "Magnet copiato ✓");
}

/* Ricava il nome del torrent (dn) da un link magnet: serve a verificare che il
   magnet copiato corrisponda davvero alla riga che si sta guardando. */
function magnetName(magnet) {
  const match = /[?&]dn=([^&]+)/.exec(magnet || "");
  if (!match) return "";
  try {
    return decodeURIComponent(match[1].replace(/\+/g, " "));
  } catch {
    return match[1];
  }
}

/* ---------------------------------------------------------------- avvio */
async function init() {
  const meta = await fetch("/api/meta").then((r) => r.json());
  fillSelect("mtype", meta.types, "Tutti");
  fillSelect("lang", meta.languages, "Tutte");
  fillSelect("genre", meta.genres, "Tutti");
  fillSelect("res", meta.resolutions, "Tutte");
  fillSelect("quality", meta.qualities, "Tutte");
  configureSizeFilter(meta.size_filter);
  fillSelect("match", meta.match_modes, null);
  fillSelect("sort", meta.sorts, null, "seeds");
  renderSources(meta.sources);
  renderCustomSources(meta.custom_sources, meta.custom_kinds);
  if (meta.dns) $("#dns-info").textContent = meta.dns.label;
  restoreFilters();

  $("#sources").addEventListener("change", persistSources);
  $("#sources-all").addEventListener("click", () => setAllSources(true));
  $("#sources-none").addEventListener("click", () => setAllSources(false));
  for (const id of FILTER_IDS) document.getElementById(id).addEventListener("change", saveFilters);

  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    search();
  });
  $("#q").addEventListener("input", updateClearButton);
  $("#clear-q").addEventListener("click", () => {
    $("#q").value = "";
    $("#q").focus();
    updateClearButton();
  });
  $("#reset").addEventListener("click", resetFilters);
  $("#discover-form").addEventListener("submit", onDiscoverSubmit);
  $("#discover-results").addEventListener("click", onDiscoverClick);
  $("#custom-form").addEventListener("submit", onCustomFormSubmit);
  $("#custom-list").addEventListener("click", onCustomListClick);
  $("#copy-all").addEventListener("click", copyAll);
  $("#export").addEventListener("click", exportTxt);
  $("#results").addEventListener("click", onResultsClick);
  addKeyboardShortcuts();
  updateClearButton();

  await initPreferences();

  const lastQuery = ($("#q").value || localStorage.getItem("ms_query") || "").trim();
  if (lastQuery.length >= 2) {
    $("#q").value = lastQuery;
    updateClearButton();
    search();
  }
}

function fillSelect(id, items, anyLabel, defaultValue) {
  const select = document.getElementById(id);
  const previous = select.value;
  select.innerHTML = "";
  if (anyLabel !== null) select.append(new Option(`${iconFor(id, { code: "" })}  ${anyLabel}`, ""));
  for (const item of items) {
    select.append(new Option(`${iconFor(id, item)}  ${item.label}`, item.code ?? item.slug));
  }
  if (defaultValue !== undefined) select.value = defaultValue;
  else if (previous) select.value = previous;
}

function rememberedSources() {
  try { return JSON.parse(localStorage.getItem("ms_sources") || "null"); } catch { return null; }
}

function rememberedKnown() {
  try { return JSON.parse(localStorage.getItem("ms_sources_known") || "null"); } catch { return null; }
}

/* Una fonte e' spuntata se: l'utente l'aveva scelta, oppure e' stata aggiunta
   dopo il suo ultimo salvataggio (in quel caso vale il default della fonte).
   Cosi' le fonti nuove arrivano attive senza riattivare quelle tolte a mano. */
function sourceChecked(source, saved, known) {
  if (!saved) return !!source.default;
  if (saved.includes(source.id)) return true;
  const seen = Array.isArray(known) ? known : [];
  if (!seen.includes(source.id)) return !!source.default;
  return false;
}

function rememberKnownSources() {
  const ids = [...document.querySelectorAll("#sources input")].map((box) => box.value);
  localStorage.setItem("ms_sources_known", JSON.stringify(ids));
}

function renderSources(list) {
  const saved = rememberedSources();
  const known = rememberedKnown();
  const box = $("#sources");
  box.innerHTML = "";
  for (const source of list) {
    const label = document.createElement("label");
    label.className = "src";
    label.title = source.description;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = source.id;
    checkbox.checked = sourceChecked(source, saved, known);
    label.append(checkbox, document.createTextNode(" " + source.label));
    box.append(label);
  }
  persistSources();
}

function selectedSources() {
  return [...document.querySelectorAll("#sources input:checked")].map((c) => c.value);
}

/* ------------------------------------- filtri ricordati, scorciatoie, reset */
function updateClearButton() {
  $("#clear-q").hidden = !$("#q").value;
}

/* Cursore "Max dimensione": scala a scaglioni (0,1 GB fino a 1 GB, poi 0,5 GB
   fino a 10 GB, quindi "nessun limite"). Il valore inviato e' in GB, 0 = nessun limite. */
let SIZE_LADDER = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.5, 2, 2.5, 3, 3.5, 4,
  4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10, 0];

function configureSizeFilter(config) {
  const input = $("#size");
  if (config) {
    if (Array.isArray(config.ladder) && config.ladder.length) SIZE_LADDER = config.ladder;
    input.min = "0";
    input.max = String(SIZE_LADDER.length - 1);
    input.step = "1";
    input.value = String(indexOfGb(config.default));
  }
  input.addEventListener("input", updateSizeOut);
  updateSizeOut();
}

function gbAtIndex(index) {
  const i = Math.max(0, Math.min(Number(index) || 0, SIZE_LADDER.length - 1));
  return Number(SIZE_LADDER[i]) || 0;
}

function indexOfGb(gb) {
  const target = Number(gb) || 0;
  let best = 0;
  let bestDelta = Infinity;
  SIZE_LADDER.forEach((value, index) => {
    const delta = Math.abs((Number(value) || 0) - target);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = index;
    }
  });
  return best;
}

function sizeValue() {
  return gbAtIndex($("#size").value);
}

function fmtGb(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(".", ",");
}

function setSizeValue(gb) {
  $("#size").value = String(indexOfGb(gb));
  updateSizeOut();
}

function updateSizeOut() {
  const input = $("#size");
  const gb = sizeValue();
  const max = Number(input.max) || 1;
  $("#size-out").textContent = gb ? `≤ ${fmtGb(gb)} GB` : "♾️ nessun limite";
  input.style.setProperty("--fill", `${(Number(input.value) / max) * 100}%`);
}

function currentFilters() {
  const values = {};
  for (const id of FILTER_IDS) {
    values[id] = id === "size" ? String(sizeValue()) : document.getElementById(id).value;
  }
  values.q = $("#q").value;
  return values;
}

function saveFilters() {
  const values = currentFilters();
  localStorage.setItem("ms_filters", JSON.stringify(values));
  localStorage.setItem("ms_query", $("#q").value);
  syncPreferences();
}

function persistSources() {
  localStorage.setItem("ms_sources", JSON.stringify(selectedSources()));
  rememberKnownSources();
  syncPreferences();
}

function restoreFilters() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("ms_filters") || "null"); } catch { saved = null; }
  if (saved) {
    for (const id of FILTER_IDS) {
      const value = saved[id];
      if (value === undefined || value === null) continue;
      if (id === "size") { setSizeValue(value); continue; }
      document.getElementById(id).value = value;
    }
    if (saved.q) $("#q").value = saved.q;
  }
}

function resetFilters() {
  for (const id of FILTER_IDS) {
    if (id === "size") continue;
    document.getElementById(id).value = "";
  }
  setSizeValue(0);
  $("#sort").value = "seeds";
  saveFilters();
  toast("Filtri azzerati ♻️");
  if (state.results.length) search();
}

function setAllSources(checked) {
  for (const box of document.querySelectorAll("#sources input")) box.checked = checked;
  persistSources();
}

/* ------------------------------------------------------ preferenze salvate */
const PREF_AUTO_KEY = "ms_prefs_auto";
let prefState = { profiles: [], last: null };
let prefReady = false;
let prefTimer = null;

function preferencesAuto() {
  return $("#pref-auto").checked;
}

function setPrefStatus(message, tone) {
  const box = $("#pref-status");
  if (!box) return;
  box.textContent = message || "—";
  box.className = "muted" + (tone ? " " + tone : "");
}

/* Salva su disco (lato server) l'ultimo stato usato: filtri + fonti. */
function syncPreferences(immediate) {
  if (!prefReady || !preferencesAuto()) return;
  clearTimeout(prefTimer);
  const send = () => {
    if (!preferencesAuto()) return;
    fetch("/api/preferences/last", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filters: currentFilters(), sources: selectedSources() }),
    }).catch(() => {});
  };
  if (immediate) send();
  else prefTimer = setTimeout(send, 700);
}

/* Applica filtri + fonti all'interfaccia (profili e ripristino da disco). */
function applyState(filters, sources) {
  if (filters && typeof filters === "object") {
    for (const id of FILTER_IDS) {
      const value = filters[id];
      if (value === undefined || value === null) continue;
      if (id === "size") { setSizeValue(value); continue; }
      const field = document.getElementById(id);
      if (field) field.value = String(value);
    }
    if (filters.q !== undefined) {
      $("#q").value = filters.q;
      updateClearButton();
    }
  }
  if (Array.isArray(sources) && sources.length) {
    const boxes = [...document.querySelectorAll("#sources input")];
    const known = new Set(boxes.map((box) => box.value));
    const wanted = new Set(sources.filter((id) => known.has(id)));
    if (wanted.size) {
      for (const box of boxes) box.checked = wanted.has(box.value);
    }
  }
  localStorage.setItem("ms_filters", JSON.stringify(currentFilters()));
  localStorage.setItem("ms_query", $("#q").value);
  localStorage.setItem("ms_sources", JSON.stringify(selectedSources()));
}

function fillProfileList(profiles) {
  const select = $("#pref-list");
  const previous = select.value;
  select.innerHTML = "";
  if (!profiles.length) {
    select.append(new Option("(nessun profilo salvato)", ""));
    select.disabled = true;
    $("#pref-load").disabled = true;
    $("#pref-delete").disabled = true;
    return;
  }
  select.disabled = false;
  $("#pref-load").disabled = false;
  $("#pref-delete").disabled = false;
  for (const profile of profiles) {
    const when = profile.updated ? new Date(profile.updated * 1000).toLocaleDateString("it-IT") : "";
    select.append(new Option(when ? `${profile.name} · ${when}` : profile.name, profile.name));
  }
  if (previous && profiles.some((profile) => profile.name === previous)) select.value = previous;
}

async function refreshPreferences() {
  try {
    const data = await fetch("/api/preferences").then((r) => r.json());
    prefState = { profiles: data.profiles || [], last: data.last || null };
    fillProfileList(prefState.profiles);
  } catch (err) {
    setPrefStatus("Impossibile leggere le preferenze: " + err.message, "err");
  }
}

function saveProfile() {
  const name = $("#pref-name").value.trim();
  if (!name) {
    setPrefStatus("Scrivi un nome per il profilo.", "err");
    $("#pref-name").focus();
    return;
  }
  fetch("/api/preferences", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, filters: currentFilters(), sources: selectedSources() }),
  })
    .then(async (response) => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "salvataggio non riuscito");
      prefState.profiles = data.profiles || [];
      fillProfileList(prefState.profiles);
      $("#pref-list").value = name;
      $("#pref-name").value = "";
      setPrefStatus(`Profilo «${name}» salvato ✓`, "ok");
      toast(`Profilo «${name}» salvato ✓`);
    })
    .catch((err) => setPrefStatus(err.message, "err"));
}

function loadProfile() {
  const name = $("#pref-list").value;
  const profile = prefState.profiles.find((item) => item.name === name);
  if (!profile) {
    setPrefStatus("Scegli un profilo da caricare.", "err");
    return;
  }
  applyState(profile.filters, profile.sources);
  setPrefStatus(`Profilo «${name}» caricato ✓`, "ok");
  toast(`Profilo «${name}» caricato ✓`);
  if ($("#q").value.trim().length >= 2) search();
}

function deleteProfile() {
  const name = $("#pref-list").value;
  if (!name) return;
  if (!window.confirm(`Eliminare il profilo «${name}»?`)) return;
  fetch("/api/preferences?name=" + encodeURIComponent(name), { method: "DELETE" })
    .then(async (response) => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "eliminazione non riuscita");
      prefState.profiles = data.profiles || [];
      fillProfileList(prefState.profiles);
      setPrefStatus(`Profilo «${name}» eliminato.`);
    })
    .catch((err) => setPrefStatus(err.message, "err"));
}

function exportPreferences() {
  fetch("/api/preferences")
    .then((response) => response.json())
    .then((data) => {
      const payload = { app: "MagnetScout", exported: new Date().toISOString(),
                        profiles: data.profiles || [] };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "magnetscout-preferenze.json";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setPrefStatus(`Esportati ${payload.profiles.length} profili ✓`, "ok");
    })
    .catch((err) => setPrefStatus("Esportazione non riuscita: " + err.message, "err"));
}

function importPreferences(file) {
  const reader = new FileReader();
  reader.onload = () => {
    let payload;
    try { payload = JSON.parse(reader.result); }
    catch { setPrefStatus("Il file non è un JSON valido.", "err"); return; }
    fetch("/api/preferences/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "importazione non riuscita");
        prefState.profiles = data.profiles || [];
        fillProfileList(prefState.profiles);
        setPrefStatus(`Importati ${data.imported} profili ✓`, "ok");
        toast(`Importati ${data.imported} profili ✓`);
      })
      .catch((err) => setPrefStatus(err.message, "err"));
  };
  reader.onerror = () => setPrefStatus("Lettura del file non riuscita.", "err");
  reader.readAsText(file);
}

async function initPreferences() {
  $("#pref-auto").checked = localStorage.getItem(PREF_AUTO_KEY) !== "off";
  $("#pref-load").addEventListener("click", loadProfile);
  $("#pref-save").addEventListener("click", saveProfile);
  $("#pref-delete").addEventListener("click", deleteProfile);
  $("#pref-export").addEventListener("click", exportPreferences);
  $("#pref-import").addEventListener("click", () => $("#pref-file").click());
  $("#pref-file").addEventListener("change", () => {
    const file = $("#pref-file").files[0];
    if (file) importPreferences(file);
    $("#pref-file").value = "";
  });
  $("#pref-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveProfile();
    }
  });
  $("#pref-auto").addEventListener("change", () => {
    const auto = $("#pref-auto").checked;
    localStorage.setItem(PREF_AUTO_KEY, auto ? "on" : "off");
    if (auto) {
      syncPreferences(true);
      setPrefStatus("Salvataggio automatico attivo ✓", "ok");
    } else {
      setPrefStatus("Salvataggio automatico disattivato (resta solo la memoria locale).");
    }
  });
  await refreshPreferences();
  const fresh = !localStorage.getItem("ms_filters");
  if (fresh && $("#pref-auto").checked && prefState.last) {
    applyState(prefState.last.filters, prefState.last.sources);
    setPrefStatus("Filtri e fonti ripristinati dall'ultimo stato salvato su disco ✓", "ok");
  }
  prefReady = true;
  if (fresh && $("#pref-auto").checked) syncPreferences(true);
}

function addKeyboardShortcuts() {
  document.addEventListener("keydown", (event) => {
    const active = document.activeElement || {};
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName || "");
    if (event.key === "/" && !typing) {
      event.preventDefault();
      $("#q").focus();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#q").focus();
      return;
    }
    if (event.key === "Escape") {
      const open = [...document.querySelectorAll("tr.detail:not([hidden])")];
      if (open.length) {
        open.forEach((row) => { row.hidden = true; });
        return;
      }
      if (typing) {
        $("#q").value = "";
        updateClearButton();
      }
    }
  });
}

/* ------------------------------------------------------- titoli e stati */
function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Evidenzia le parole cercate nel titolo (gia' passato da esc()). */
function highlightTitle(title, query) {
  const escaped = esc(title);
  const words = (query || "").split(/\s+/).map((w) => w.trim())
    .filter((w) => w.length >= 2).map(escapeRegExp);
  if (!words.length) return escaped;
  try {
    return escaped.replace(new RegExp("(" + words.join("|") + ")", "gi"), "<mark>$1</mark>");
  } catch {
    return escaped;
  }
}

function emptyState() {
  return `<div class="empty">
      <div class="empty-icon">🧭</div>
      <div>Cerca un titolo per iniziare.</div>
      <div class="examples">
        <span class="muted">Prova:</span>
        <button type="button" class="chip-btn" data-example="matrix">🎬 matrix</button>
        <button type="button" class="chip-btn" data-example="il padrino">🍝 il padrino</button>
        <button type="button" class="chip-btn" data-example="night of the living dead">🧟 night of the living dead</button>
        <button type="button" class="chip-btn" data-example="breaking bad">📺 breaking bad</button>
      </div>
    </div>`;
}

function noResultsState() {
  return `<div class="empty">
      <div class="empty-icon">🕳️</div>
      <div>Nessun risultato: prova con meno filtri, un'altra fonte o un titolo diverso.</div>
      <div class="examples">
        <button type="button" class="chip-btn" data-action="reset">♻️ Azzera i filtri</button>
      </div>
    </div>`;
}

/* ------------------------------------------------- fonti personalizzate */
function selectSource(id, selected) {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem("ms_sources") || "[]") || []; } catch { saved = []; }
  saved = saved.filter((value) => value !== id);
  if (selected) saved.push(id);
  localStorage.setItem("ms_sources", JSON.stringify(saved));
  rememberKnownSources();
  syncPreferences();
}

function renderCustomSources(defs, kinds) {
  if (kinds && kinds.length) fillSelect("cf-kind", kinds, null, $("#cf-kind").value || kinds[0].code);
  const box = $("#custom-list");
  if (!box) return;
  if (!defs || !defs.length) {
    box.innerHTML = `<div class="muted">Nessuna fonte personalizzata: aggiungine una dal modulo qui sopra
      oppure usa la scoperta automatica.</div>`;
    return;
  }
  box.innerHTML = defs.map((definition) => {
    const kindLabel = (kinds || []).find((k) => k.code === definition.kind);
    return `<div class="custom-item">
      <div class="custom-head">
        <strong>${esc(definition.label)}</strong>
        <span class="badge b-q">${KIND_ICONS[definition.kind] || "🧩"} ${esc(kindLabel ? kindLabel.label : definition.kind)}</span>
        <button class="ghost small" data-remove="${esc(definition.id)}">Rimuovi</button>
      </div>
      ${definition.search_url ? `<code class="custom-url">${esc(definition.search_url)}</code>` : ""}
      ${definition.description ? `<div class="muted">${esc(definition.description)}</div>` : ""}
    </div>`;
  }).join("");
}

async function applyMetaResponse(data) {
  if (data.meta) {
    renderSources(data.meta.sources);
    renderCustomSources(data.meta.custom_sources, data.meta.custom_kinds);
  }
}

async function saveCustomSource(definition, message) {
  const response = await fetch("/api/custom-sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(definition),
  }).then((r) => r.json());
  if (response.error) { toast(response.error); return false; }
  if (response.saved) selectSource(response.saved.id, true);
  await applyMetaResponse(response);
  toast(message || "Fonte salvata ✓");
  return true;
}

async function onCustomFormSubmit(event) {
  event.preventDefault();
  const label = $("#cf-label").value.trim();
  if (!label) { toast("Serve un nome per la fonte"); return; }
  const definition = {
    label,
    kind: $("#cf-kind").value,
    search_url: $("#cf-url").value.trim(),
    description: $("#cf-description").value.trim(),
  };
  const extra = $("#cf-extra").value.trim();
  if (extra) {
    try {
      Object.assign(definition, JSON.parse(extra));
    } catch {
      toast("Impostazioni avanzate: JSON non valido");
      return;
    }
  }
  if (await saveCustomSource(definition, "Fonte aggiunta ✓")) $("#custom-form").reset();
}

async function onCustomListClick(event) {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  const id = button.dataset.remove;
  if (!window.confirm(`Rimuovere la fonte "${id}"?`)) return;
  const data = await fetch("/api/custom-sources?id=" + encodeURIComponent(id), { method: "DELETE" })
    .then((r) => r.json());
  if (data.error) { toast(data.error); return; }
  selectSource(id, false);
  await applyMetaResponse(data);
  toast("Fonte rimossa");
}

/* ------------------------------------------------------------ scoperta */
async function onDiscoverSubmit(event) {
  event.preventDefault();
  const keyword = $("#discover-q").value.trim();
  if (keyword.length < 3) { toast("Inserisci almeno 3 caratteri"); return; }
  const button = $("#discover-form button");
  button.disabled = true;
  $("#discover-status").textContent = "Analizzo il web e provo i siti trovati… (puo' richiedere mezzo minuto)";
  $("#discover-results").innerHTML = "";
  try {
    const data = await fetch("/api/discover?q=" + encodeURIComponent(keyword)).then((r) => r.json());
    if (data.error) { $("#discover-status").textContent = data.error; return; }
    renderDiscover(data);
  } catch (err) {
    $("#discover-status").textContent = "Errore: " + err.message;
  } finally {
    button.disabled = false;
  }
}

function renderDiscover(data) {
  const providers = (data.providers || []).map((p) => (p.ok
    ? `<span class="chip ok">${esc(p.label)}: ${p.found} link</span>`
    : `<span class="chip warn" title="${esc(p.error || "")}">${esc(p.label)}: non disponibile</span>`)).join(" ");
  $("#discover-status").innerHTML =
    `${providers} <span class="muted">· domini provati: ${data.domains_checked ?? 0}</span>`;

  const box = $("#discover-results");
  const found = data.candidates || [];
  box.dataset.candidates = JSON.stringify(found.map((c) => c.definition));
  if (!found.length) {
    const reasons = (data.discarded || []).map((d) => `${esc(d.domain)}: ${esc(d.reason)}`).join("<br>");
    box.innerHTML = `<div class="muted">Nessuna fonte utilizzabile trovata.${reasons ? "<br>" + reasons : ""}</div>`;
    return;
  }
  box.innerHTML = found.map((c, index) => `<div class="proposal">
      <div class="custom-head">
        <strong>${esc(c.definition.label)}</strong>
        <span class="badge b-q">${esc(c.kind_label)}</span>
        <span class="muted">${esc(c.domain)}</span>
        <button class="ghost small" data-add="${index}">Aggiungi come fonte</button>
        <a class="ghost small" href="${esc(c.home_url)}" target="_blank" rel="noopener">Visita il sito</a>
      </div>
      ${c.sample ? `<div class="muted">Esempio: ${esc(c.sample)}</div>` : ""}
      ${c.magnets ? `<div class="muted">magnet trovati nello stesso momento: ${c.magnets}</div>` : ""}
      ${c.note ? `<div class="muted">${esc(c.note)}</div>` : ""}
      <code class="custom-url">${esc(c.definition.search_url)}</code>
    </div>`).join("");
}

async function onDiscoverClick(event) {
  const button = event.target.closest("[data-add]");
  if (!button) return;
  const candidates = JSON.parse($("#discover-results").dataset.candidates || "[]");
  const definition = candidates[Number(button.dataset.add)];
  if (!definition) return;
  button.disabled = true;
  const added = await saveCustomSource(definition, `Fonte "${definition.label}" aggiunta ✓`);
  button.textContent = added ? "Aggiunta ✓" : "Riprova";
  if (!added) button.disabled = false;
}

/* ---------------------------------------------------------------- ricerca */
async function search() {
  const query = $("#q").value.trim();
  if (query.length < 2) { toast("Inserisci almeno 2 caratteri"); return; }
  const sources = selectedSources();
  if (!sources.length) { toast("Seleziona almeno una fonte"); return; }
  saveFilters();

  const params = new URLSearchParams({
    q: query,
    mtype: $("#mtype").value,
    lang: $("#lang").value,
    genre: $("#genre").value,
    res: $("#res").value,
    quality: $("#quality").value,
    size: String(sizeValue()),
    match: $("#match").value,
    sort: $("#sort").value,
    sources: sources.join(","),
  });

  const button = $("#go");
  button.disabled = true;
  button.textContent = "Cerco…";
  setStatus("Ricerca in corso…");

  try {
    const data = await fetch("/api/search?" + params).then((r) => r.json());
    if (data.error) { setStatus(data.error); toast(data.error); return; }
    state.results = data.results.map((result, index) => ({ ...result, uid: index }));
    state.byUid = new Map(state.results.map((result) => [result.uid, result]));
    state.sortKey = null;
    render(data);
  } catch (err) {
    setStatus("Errore di rete: " + err.message);
  } finally {
    button.disabled = false;
    button.textContent = "Cerca";
  }
}

function setStatus(text) {
  $("#status-text").textContent = text;
}

function render(data) {
  $("#toolbar").hidden = data.total === 0;
  $("#count").textContent = `${data.total} risultati · ⏱️ ${fmtMs(data.took_ms)}`;

  $("#source-chips").innerHTML = data.sources.map((s) => {
    if (!s.ok) {
      return `<span class="chip err" title="${esc(s.error || "")}">${esc(s.label)}: errore</span>`;
    }
    const tip = s.note ? s.note : `${s.ms} ms`;
    const cls = s.note ? "chip warn" : "chip ok";
    return `<span class="${cls}" title="${esc(tip)}">${esc(s.label)}: ${s.count}${s.note ? " ⚠" : ""}</span>`;
  }).join("");

  const hints = [];
  if ($("#res").value) hints.push("Filtro risoluzione: gli item senza qualità dichiarata (Internet Archive) sono esclusi");
  if ($("#genre").value) hints.push("Filtro genere: solo le fonti con generi nei metadati (YTS, Internet Archive)");
  if ($("#lang").value) hints.push($("#lang").value === "multi"
    ? "Lingua MULTI: solo i titoli dichiarati multilingua o con almeno due lingue riconosciute"
    : "Lingua: solo i titoli in cui quella lingua è dichiarata (un “MULTI” generico non basta)");
  const sizeLimit = sizeValue();
  if (sizeLimit) hints.push(`Max ${fmtGb(sizeLimit)} GB: esclusi gli item senza dimensione nota`);
  if ($("#match").value) hints.push($("#match").value === "title"
    ? "Titolo identico: il titolo deve combaciare in pieno (separatori ignorati)"
    : "Parole esatte: le parole cercate devono comparire intere (separatori ignorati)");
  $("#filter-hint").textContent = hints.join(" · ");

  setStatus(`${data.total} risultati da ${data.sources.filter((s) => s.ok).length} fonti`);
  renderRows();
}

/* ---------------------------------------------------------------- tabella */
function sortedRows() {
  const rows = state.results.slice();
  if (state.sortKey) {
    const cmp = CMP[state.sortKey];
    rows.sort((a, b) => {
      const va = cmp(a), vb = cmp(b);
      if (va < vb) return -1 * state.sortDir;
      if (va > vb) return 1 * state.sortDir;
      return 0;
    });
  }
  return rows;
}

function langBadge(r) {
  if (!r.lang_label) return `<span class="badge b-none" title="Lingua non riconosciuta">❔ n/d</span>`;
  const cls = { it: "b-it", multi: "b-multi", en: "b-en" }[r.lang] || "b-lang";
  const flag = LANG_FLAGS[r.lang] || "🏳️";
  return `<span class="badge ${cls}" title="${esc(r.lang_label)}">${flag} ${esc(r.lang_label)}</span>`;
}

function resBadge(r) {
  if (!r.resolution && !r.is3d) return "—";
  let out = "";
  if (r.resolution) {
    const cls = { "2160p": "b-r2160", "1080p": "b-r1080", "720p": "b-r720" }[r.resolution] || "b-rlow";
    out += `<span class="badge ${cls}">${esc(r.resolution)}</span>`;
  }
  if (r.is3d) out += `<span class="badge b-3d">3D</span>`;
  return out;
}

function seedClass(seeds) {
  if (seeds == null) return "";
  if (seeds >= 20) return "good";
  if (seeds >= 5) return "mid";
  if (seeds > 0) return "low";
  return "dead";
}

function rowActions(result) {
  const uid = result.uid;
  if (result.magnet) {
    return `<button class="act" data-act="copy" data-uid="${uid}" title="Copia il magnet negli appunti">📋</button>` +
           `<a class="act" href="${esc(result.magnet)}" title="Apri nel client torrent">🧲</a>` +
           `<button class="act" data-act="toggle" data-uid="${uid}" title="Mostra i dettagli">ℹ️</button>`;
  }
  if (result.needs_magnet) {
    return `<button class="act" data-act="gen" data-uid="${uid}" title="Scarica il .torrent e genera il magnet">🧲 Genera magnet</button>` +
           `<button class="act" data-act="toggle" data-uid="${uid}" title="Mostra i dettagli">ℹ️</button>`;
  }
  if (result.url) {
    return `<a class="act" href="${esc(result.url)}" target="_blank" rel="noopener" title="Apri la ricerca sul sito">🔗</a>` +
           `<button class="act" data-act="toggle" data-uid="${uid}" title="Mostra i dettagli">ℹ️</button>`;
  }
  return "—";
}

function renderRows() {
  const rows = sortedRows();
  const box = $("#results");
  if (!rows.length) {
    box.innerHTML = noResultsState();
    return;
  }
  const head = ["title", "source", "lang", "resolution", "quality", "size", "seeds", "leechers", "date"];
  let html = "<table class='grid'><thead><tr>";
  for (const key of head) {
    const sorted = state.sortKey === key;
    const arrow = sorted ? (state.sortDir > 0 ? " ▲" : " ▼") : "";
    const tip = `${esc(HEAD_TITLES[key] || key)} — clicca per ordinare`;
    const aria = sorted ? (state.sortDir > 0 ? ' aria-sort="ascending"' : ' aria-sort="descending"') : "";
    html += `<th data-key="${key}"${aria} title="${tip}">${HEAD_LABELS[key]}${arrow}</th>`;
  }
  html += `<th class="actions" title="Azioni rapide">Azioni</th></tr></thead><tbody>`;

  const query = $("#q").value;
  rows.forEach((r) => {
    html += `<tr class="row" data-uid="${r.uid}">` +
      `<td class="title"><span class="t" title="${esc(r.title)}">${highlightTitle(r.title, query)}</span></td>` +
      `<td><span class="badge b-src-${esc(r.source)}">${esc(r.source_label)}</span></td>` +
      `<td>${langBadge(r)}</td>` +
      `<td>${resBadge(r)}</td>` +
      `<td>${r.quality_label ? `<span class="badge b-q" title="${esc(r.quality_label)}">${QUALITY_ICONS[r.quality] || "✨"} ${esc(r.quality_label)}</span>` : "—"}</td>` +
      `<td class="num">${esc(r.size_human)}</td>` +
      `<td class="num ${seedClass(r.seeds)}">${r.seeds ?? "—"}</td>` +
      `<td class="num low">${r.leechers ?? "—"}</td>` +
      `<td class="num">${fmtDate(r.date_ts)}</td>` +
      `<td class="actions">${rowActions(r)}</td></tr>` +
      `<tr class="detail" id="row-${r.uid}" hidden><td colspan="10"><div class="magnet-box" data-magnet="${r.uid}"></div></td></tr>`;
  });
  html += "</tbody></table>";
  box.innerHTML = html;

  box.querySelectorAll("th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.sortKey === key) state.sortDir *= -1;
      else { state.sortKey = key; state.sortDir = 1; }
      renderRows();
    });
  });
}

/* ---------------------------------------------------------------- azioni */
async function onResultsClick(event) {
  const example = event.target.closest("[data-example]");
  if (example) {
    $("#q").value = example.dataset.example;
    updateClearButton();
    search();
    return;
  }
  if (event.target.closest('[data-action="reset"]')) {
    resetFilters();
    return;
  }

  const button = event.target.closest("[data-act]");
  if (!button) {
    // Clic sulla riga (fuori dalle azioni) = apri/chiudi i dettagli.
    const row = event.target.closest("tr.row");
    if (row && !event.target.closest("td.actions") && !event.target.closest("a")) {
      const uid = Number(row.dataset.uid);
      const detail = document.getElementById("row-" + uid);
      if (detail) {
        detail.hidden = !detail.hidden;
        if (!detail.hidden) fillMagnetBox(uid);
      }
    }
    return;
  }
  const uid = Number(button.dataset.uid);
  const result = state.byUid.get(uid);
  if (!result) return;
  const act = button.dataset.act;

  if (act === "copy") {
    const name = magnetName(result.magnet);
    copyText(result.magnet, name ? `Copiato: ${name}` : "Magnet copiato ✓");
    const originale = button.textContent;
    button.textContent = "✓";
    button.disabled = true;
    window.setTimeout(() => {
      button.textContent = originale;
      button.disabled = false;
    }, 1400);
  } else if (act === "toggle") {
    const row = document.getElementById("row-" + uid);
    row.hidden = !row.hidden;
    if (!row.hidden) fillMagnetBox(uid);
  } else if (act === "gen") {
    button.disabled = true;
    button.textContent = "…";
    try {
      const query = result.torrent_url
        ? "url=" + encodeURIComponent(result.torrent_url)
        : "id=" + encodeURIComponent(result.id.split(":", 2)[1]);
      const data = await fetch("/api/magnet?" + query).then((r) => r.json());
      if (data.error) {
        toast(data.error);
        button.disabled = false;
        button.textContent = "Riprova";
        return;
      }
      result.magnet = data.magnet;
      toast("Magnet generato ✓");
      renderRows();
    } catch (err) {
      toast("Errore: " + err.message);
      button.disabled = false;
      button.textContent = "Riprova";
    }
  }
}

function fillMagnetBox(uid) {
  const result = state.byUid.get(uid);
  const box = document.querySelector(`.magnet-box[data-magnet="${uid}"]`);
  if (!box || !result) return;
  if (!result.magnet) {
    box.innerHTML = `<span class="muted">Magnet non ancora generato.</span>`;
    return;
  }
  const name = magnetName(result.magnet) || result.title;
  box.innerHTML = `<div class="verify">Verifica prima di aprire → torrent: <strong>${esc(name)}</strong>
      · fonte: ${esc(result.source_label)} · qualità: ${esc(result.resolution || "n/d")}
      · dimensione: <strong>${esc(result.size_human)}</strong></div>
    <code>${esc(result.magnet)}</code>
    <div class="magnet-actions">
      <button class="ghost small" data-act="copy" data-uid="${uid}">Copia</button>
      <a class="ghost small" href="${esc(result.magnet)}">Apri nel client</a>
      ${result.url ? `<a class="ghost small" target="_blank" rel="noopener" href="${esc(result.url)}">Pagina fonte</a>` : ""}
      ${result.imdb ? `<a class="ghost small" target="_blank" rel="noopener" href="https://www.imdb.com/title/${esc(result.imdb)}/">IMDb</a>` : ""}
    </div>`;
}

function availableMagnets() {
  return sortedRows().filter((r) => r.magnet).map((r) => r.magnet);
}

function copyAll() {
  const magnets = availableMagnets();
  if (!magnets.length) { toast("Nessun magnet disponibile: usa «Genera magnet» sulle righe che lo richiedono"); return; }
  copyText(magnets.join("\n"));
}

function exportTxt() {
  const magnets = availableMagnets();
  if (!magnets.length) { toast("Nessun magnet disponibile"); return; }
  const blob = new Blob([magnets.join("\n")], { type: "text/plain" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "magnet-scout.txt";
  link.click();
  URL.revokeObjectURL(link.href);
}

init().catch((err) => {
  $("#status-text").textContent = "Errore di avvio: " + err.message;
});
