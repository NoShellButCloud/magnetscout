/* =========================================================================
   MagnetScout — portale: sezioni Torrent / XDCC / Download.
   Usa le utilita' globali di app.js ($, esc, toast, copyText, SIZE_LADDER...).
   ========================================================================= */
"use strict";

(function () {
  const VIEWS = ["torrent", "xdcc", "download"];

  const XDCC_SORTS = [
    { code: "relevance", label: "Rilevanza" },
    { code: "size-desc", label: "Dimensione ↓" },
    { code: "size-asc", label: "Dimensione ↑" },
    { code: "episode", label: "Episodio" },
    { code: "name", label: "Nome (A→Z)" },
    { code: "gets", label: "Più scaricati" },
  ];

  const ACTIONS = {
    pause: { icon: "⏸️", title: "Metti in pausa (riprende da dove era arrivato)" },
    resume: { icon: "▶️", title: "Riprendi il download" },
    retry: { icon: "🔁", title: "Riprova" },
    open: { icon: "📂", title: "Apri la cartella del file" },
    remove: { icon: "🗑️", title: "Togli dalla lista" },
    delete: { icon: "🧨", title: "Elimina il file scaricato dal disco" },
  };

  let currentView = "torrent";
  let meta = null;
  let xdccResults = [];
  let xdccQuery = "";
  let xdccStats = [];
  let dlSnapshot = null;
  let pollTimer = null;
  let pollInterval = 1400;
  let previousDone = null;
  let lastActiveJobs = 0;
  const dlNodes = new Map();          // id -> nodi della scheda
  const queueKeys = new Set();         // "bot|pack" gia' in coda

  const el = (id) => document.getElementById(id);

  /* ==================================================================== */
  /*  Sezioni (tab)                                                       */
  /* ==================================================================== */
  function setView(name, remember = true) {
    if (!VIEWS.includes(name)) name = "torrent";
    currentView = name;
    for (const view of VIEWS) {
      el("view-" + view).hidden = view !== name;
      el("tab-" + view).setAttribute("aria-selected", String(view === name));
    }
    if (remember) localStorage.setItem("ms_view", name);
    if (name === "xdcc") {
      const box = el("xdcc-q");
      if (box) box.focus();
      if (!xdccResults.length) {
        const last = localStorage.getItem("ms_xdcc_q") || "";
        if (last.trim().length >= 2 && !box.dataset.searched) xdccSearch(last);
      }
    }
    if (name === "download") refreshDownloads();
    updatePolling();
  }

  function updatePolling() {
    const queued = Number((dlSnapshot && dlSnapshot.queued) || 0);
    const needed = currentView === "download" || lastActiveJobs > 0 || queued > 0;
    if (needed && !pollTimer) pollTimer = setInterval(refreshDownloads, pollInterval);
    if (!needed && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  /* Con trasferimenti attivi si aggiorna spesso, altrimenti si rallenta. */
  function setPollInterval(value) {
    if (pollInterval === value) return;
    pollInterval = value;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = setInterval(refreshDownloads, pollInterval);
    }
  }

  /* ==================================================================== */
  /*  Ricerca XDCC                                                         */
  /* ==================================================================== */
  function xdccLadder() {
    return Array.isArray(SIZE_LADDER) && SIZE_LADDER.length ? SIZE_LADDER : [0];
  }

  function xdccSizeValue() {
    const input = el("xdcc-size");
    return gbAtIndex(input.value);
  }

  function updateXdccSizeOut() {
    const input = el("xdcc-size");
    const gb = xdccSizeValue();
    const max = Number(input.max) || 1;
    el("xdcc-size-out").textContent = gb ? `≤ ${fmtGb(gb)} GB` : "nessun limite";
    input.style.setProperty("--fill", `${(Number(input.value) / max) * 100}%`);
  }

  function configureXdccSize() {
    const input = el("xdcc-size");
    const ladder = xdccLadder();
    input.min = "0";
    input.max = String(ladder.length - 1);
    input.step = "1";
    const saved = Number(localStorage.getItem("ms_xdcc_size") || 0);
    input.value = String(saved ? indexOfGb(saved) : ladder.length - 1);
    input.addEventListener("input", () => {
      localStorage.setItem("ms_xdcc_size", String(xdccSizeValue()));
      updateXdccSizeOut();
      renderXdccTable();
    });
    updateXdccSizeOut();
  }

  function setXdccStatus(text) {
    el("xdcc-status-text").textContent = text;
  }

  function renderXdccChips() {
    const box = el("xdcc-chips");
    const chips = xdccStats.map((stat) => {
      const tone = stat.ok ? "ok" : "err";
      const value = stat.ok ? stat.count : "✕";
      const title = stat.ok ? `${stat.count} risultati in ${stat.ms} ms` : (stat.error || "errore");
      return `<span class="chip ${tone}" title="${esc(title)}">${esc(stat.label)} ${value}</span>`;
    });
    box.innerHTML = chips.join("");
  }

  function xdccFiltered() {
    const source = el("xdcc-source").value;
    const limit = xdccSizeValue();
    let list = xdccResults.filter((item) => {
      if (source && item.source !== source) return false;
      if (limit) {
        const size = Number(item.size_bytes) || 0;
        if (!size || size > limit * 1024 * 1024 * 1024) return false;
      }
      return true;
    });
    const sort = el("xdcc-sort").value;
    const bySize = (item) => Number(item.size_bytes) || 0;
    if (sort === "size-desc") list = [...list].sort((a, b) => bySize(b) - bySize(a));
    else if (sort === "size-asc") list = [...list].sort((a, b) => bySize(a) - bySize(b));
    else if (sort === "name") list = [...list].sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === "gets") list = [...list].sort((a, b) => (Number(b.gets) || 0) - (Number(a.gets) || 0));
    else if (sort === "episode") {
      list = [...list].sort((a, b) => (a.episode ?? 1e9) - (b.episode ?? 1e9)
        || a.name.localeCompare(b.name));
    }
    return list;
  }

  function resClass(resolution) {
    if (resolution === "2160p") return "b-r2160";
    if (resolution === "1080p") return "b-r1080";
    if (resolution === "720p") return "b-r720";
    if (resolution === "3d") return "b-3d";
    return "b-rlow";
  }

  function xdccCommand(item) {
    return item.command || (item.bot ? `${item.bot} xdcc send #${item.pack}` : "");
  }

  function refreshQueueKeys() {
    queueKeys.clear();
    for (const job of (dlSnapshot && dlSnapshot.jobs) || []) {
      if (job.state === "cancelled" || job.state === "error") continue;
      queueKeys.add(`${job.bot.toLowerCase()}|${job.pack}`);
    }
  }

  function renderXdccTable() {
    const list = xdccFiltered();
    const box = el("xdcc-results");
    if (!xdccResults.length) {
      el("xdcc-toolbar").hidden = true;
      box.innerHTML = `<div class="empty">
        <div class="empty-icon">📦</div>
        <div>Cerca un pacchetto XDCC per iniziare.</div>
        <div class="examples">
          <span class="muted">Prova:</span>
          <button type="button" class="chip-btn" data-xdcc-example="frieren">📦 frieren</button>
          <button type="button" class="chip-btn" data-xdcc-example="one piece">🏴‍☠️ one piece</button>
          <button type="button" class="chip-btn" data-xdcc-example="bocchi">🎸 bocchi</button>
          <button type="button" class="chip-btn" data-xdcc-example="gundam">🤖 gundam</button>
        </div>
      </div>`;
      return;
    }
    if (!list.length) {
      box.innerHTML = `<div class="empty">
        <div class="empty-icon">🕳️</div>
        <div>Nessun pacchetto con questi filtri.<br>Prova con un altro nome, un'altra fonte o senza limite di dimensione.</div>
        <div class="examples">
          <button type="button" class="chip-btn" data-xdcc-action="reset">♻️ Azzera i filtri</button>
        </div>
      </div>`;
    } else {
      const rows = list.map((item, index) => {
        const queued = queueKeys.has(`${(item.bot || "").toLowerCase()}|${item.pack}`);
        const srcClass = item.source === "nibl" ? "xsrc-nibl" : "xsrc-xdcc-eu";
        const badges = [
          `<span class="badge ${srcClass}" title="${esc(item.source_label)}">${esc(item.source_label)}</span>`,
          item.network ? `🌐 ${esc(item.network)}` : "",
          item.bot ? `· 🤖 <span class="xbot">${esc(item.bot)}</span>` : "",
          `<code>${esc(xdccCommand(item))}</code>`,
          item.gets ? `· ⬇︎ ${item.gets}×` : "",
        ].filter(Boolean).join(" ");
        const quality = [
          item.resolution ? `<span class="badge ${resClass(item.resolution)}">${esc(item.resolution)}</span>` : "",
          item.quality_label ? `<span class="badge b-q">${esc(item.quality_label)}</span>` : "",
        ].filter(Boolean).join(" ") || `<span class="muted">—</span>`;
        return `<tr class="row" data-i="${index}">
          <td class="xname"><span class="t" title="${esc(item.name)}">${highlightTitle(item.name, xdccQuery)}</span>
            <div class="xmeta">${badges}</div></td>
          <td class="num">${esc(item.size_human || "—")}</td>
          <td>${quality}</td>
          <td class="num">${item.episode ?? ""}</td>
          <td class="actions">
            <button class="act" data-act="download" data-i="${index}" title="${queued ? "Già in coda" : "Scarica con XDCC"}"${queued ? " disabled" : ""}>${queued ? "✓" : "⬇️"}</button>
            <button class="act" data-act="copy" data-i="${index}" title="Copia il comando IRC">📋</button>
          </td>
        </tr>`;
      }).join("");
      box.innerHTML = `<table class="grid xdcc-grid">
        <thead><tr>
          <th>Pacchetto</th><th class="num">Dimensione</th><th>Qualità</th><th class="num">Ep.</th><th class="actions">Azioni</th>
        </tr></thead>
        <tbody>${rows}</tbody></table>`;
    }
    const shown = list.length;
    const total = xdccResults.length;
    el("xdcc-toolbar").hidden = !total;
    el("xdcc-count").textContent = shown === total
      ? `${total} pacchetti`
      : `${shown} di ${total} pacchetti`;
  }

  async function xdccSearch(query) {
    const box = el("xdcc-q");
    const text = String(query ?? box.value).trim();
    if (text.length < 2) {
      toast("Inserisci almeno 2 caratteri");
      return;
    }
    xdccQuery = text;
    box.value = text;
    box.dataset.searched = "1";
    el("xdcc-clear").hidden = !text;
    localStorage.setItem("ms_xdcc_q", text);
    localStorage.setItem("ms_xdcc_episode", el("xdcc-episode").value);
    const button = el("xdcc-go");
    button.disabled = true;
    setXdccStatus("Cerco tra i bot dei motori XDCC…");
    try {
      const params = new URLSearchParams({ q: text, limit: "250" });
      const episode = el("xdcc-episode").value;
      if (episode) params.set("episode", episode);
      const response = await fetch("/api/xdcc/search?" + params.toString());
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || "errore del server");
      xdccResults = data.results || [];
      xdccStats = data.sources || [];
      renderXdccChips();
      renderXdccTable();
      setXdccStatus(xdccResults.length
        ? `${xdccResults.length} pacchetti trovati in ${fmtMs(data.took_ms)}`
        : "Nessun pacchetto trovato: prova un altro nome.");
    } catch (error) {
      xdccResults = [];
      xdccStats = [];
      renderXdccChips();
      renderXdccTable();
      setXdccStatus(`Ricerca non riuscita: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  }

  function resetXdccFilters() {
    el("xdcc-episode").value = "";
    el("xdcc-source").value = "";
    el("xdcc-sort").value = "relevance";
    el("xdcc-size").value = String(xdccLadder().length - 1);
    localStorage.setItem("ms_xdcc_size", "0");
    localStorage.setItem("ms_xdcc_episode", "");
    updateXdccSizeOut();
    renderXdccTable();
  }

  async function addDownload(item) {
    const key = `${(item.bot || "").toLowerCase()}|${item.pack}`;
    if (queueKeys.has(key)) {
      toast("Pacchetto già in coda");
      return false;
    }
    try {
      const response = await fetch("/api/xdcc/downloads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          server: item.server,
          port: item.port || 6697,
          tls: true,
          channel: item.channel,
          bot: item.bot,
          pack: item.pack,
          command: xdccCommand(item),
          name: item.name,
          size_bytes: item.size_bytes,
          network: item.network || item.source_label,
        }),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || "errore del server");
      applyDownloadSnapshot(data);
      toast(data.duplicate ? "Pacchetto già in coda" : `In coda: ${item.name}`);
      return !data.duplicate;
    } catch (error) {
      toast(`Non aggiunto: ${error.message}`);
      return false;
    }
  }

  /* ==================================================================== */
  /*  Download                                                            */
  /* ==================================================================== */
  function applyDownloadSnapshot(data) {
    if (!data || !data.jobs) return;
    dlSnapshot = data;
    lastActiveJobs = Number(data.active) || 0;
    refreshQueueKeys();
    renderDownloadSettings(data.settings);
    renderSummary(data);
    renderDownloadList(data);
    const badge = el("dl-badge");
    const pending = lastActiveJobs + (Number(data.queued) || 0);
    badge.hidden = pending === 0;
    badge.textContent = String(pending);
    const done = (data.counts && data.counts.done) || 0;
    if (previousDone !== null && done > previousDone) {
      toast("✅ Download completato");
    }
    previousDone = done;
    setPollInterval(lastActiveJobs > 0 ? 1200 : 4000);
    updatePolling();
    if (currentView === "xdcc") renderXdccTable();
  }

  async function refreshDownloads() {
    try {
      const response = await fetch("/api/xdcc/downloads");
      if (!response.ok) return;
      applyDownloadSnapshot(await response.json());
    } catch {
      /* server non raggiungibile: si riprova al prossimo giro */
    }
  }

  async function downloadAction(body) {
    try {
      const response = await fetch("/api/xdcc/downloads/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || "errore del server");
      if (body.action === "open" && data.folder) toast(`Cartella aperta: ${data.folder}`);
      if (body.action === "clear") toast(`${data.removed || 0} voci rimosse`);
      applyDownloadSnapshot(data);
    } catch (error) {
      toast(`Operazione non riuscita: ${error.message}`);
    }
  }

  function renderDownloadSettings(settings) {
    if (!settings) return;
    const dir = el("dl-dir");
    if (document.activeElement !== dir) dir.value = settings.download_dir || "";
    const parallel = el("dl-parallel");
    if (settings.max_parallel && parallel.value !== String(settings.max_parallel)) {
      parallel.value = String(settings.max_parallel);
    }
  }

  function renderSummary(data) {
    const parts = [];
    if (data.active) parts.push(`<b>${data.active}</b> in corso`);
    if (data.queued) parts.push(`<b>${data.queued}</b> in coda`);
    if (data.done) parts.push(`<b>${data.done}</b> completati`);
    if (data.total_speed) parts.push(`<b>${data.total_speed_human}</b>`);
    el("dl-summary").innerHTML = parts.length ? parts.join(" · ") : "Nessun download in corso.";
  }

  function actionsFor(job) {
    const actions = [];
    if (job.active || job.state === "queued") actions.push("pause");
    if (job.state === "paused") actions.push("resume");
    if (job.state === "error" || job.state === "cancelled") actions.push("retry");
    if (job.state === "done") actions.push("open");
    actions.push("remove");
    if (job.state === "done") actions.push("delete");
    return actions;
  }

  function createDownloadCard(job) {
    const root = document.createElement("div");
    root.className = "dl-item";
    root.dataset.id = job.id;
    root.innerHTML = `<div class="dl-top"><span class="dl-name"></span><span class="dl-state"></span></div>
      <div class="dl-bar"><span></span></div>
      <div class="dl-info"></div>
      <div class="dl-note"></div>
      <div class="dl-actions"></div>`;
    return {
      root,
      name: root.querySelector(".dl-name"),
      state: root.querySelector(".dl-state"),
      bar: root.querySelector(".dl-bar"),
      fill: root.querySelector(".dl-bar span"),
      info: root.querySelector(".dl-info"),
      note: root.querySelector(".dl-note"),
      actions: root.querySelector(".dl-actions"),
      actionsKey: "",
    };
  }

  function updateDownloadCard(node, job) {
    node.name.textContent = job.name;
    node.name.title = `${job.name}\n${job.bot} · ${job.server}${job.channel ? " · " + job.channel : ""}`;
    node.state.textContent = job.state_label;
    node.state.className = "dl-state st-" + job.state;
    const known = Number(job.size_bytes) > 0;
    node.bar.classList.toggle("indeterminate", !known && job.active);
    node.fill.style.width = `${known ? job.percent : 100}%`;
    const bits = [`<span>${esc(job.received_human)} / ${esc(job.size_human)}</span>`];
    if (job.active) {
      bits.push(`<span><b>${esc(job.speed_human)}</b></span>`);
      if (job.eta_human && job.eta_human !== "—") bits.push(`<span>mancano <b>${esc(job.eta_human)}</b></span>`);
    }
    bits.push(`<span>${esc(job.network || job.server)} · 🤖 ${esc(job.bot)} #${esc(job.pack)}</span>`);
    node.info.innerHTML = bits.join("");
    node.note.textContent = job.error || job.note || "";
    node.note.className = "dl-note" + (job.error ? " err" : "");
    node.root.classList.toggle("done", job.state === "done");
    node.root.classList.toggle("error", job.state === "error");
    const actions = actionsFor(job);
    const key = actions.join(",");
    if (key !== node.actionsKey) {
      node.actionsKey = key;
      node.actions.innerHTML = actions.map((action) => {
        const info = ACTIONS[action];
        const del = action === "delete" ? ' data-delete="1"' : "";
        const api = action === "delete" ? "remove" : action;
        return `<button class="act" type="button" data-act="${api}"${del} title="${esc(info.title)}">${info.icon}</button>`;
      }).join("");
    }
  }

  function renderDownloadList(data) {
    const box = el("dl-list");
    if (!data.jobs.length) {
      dlNodes.clear();
      box.innerHTML = `<div class="empty">
        <div class="empty-icon">⬇️</div>
        <div>Nessun download.<br>Avvia un pacchetto dalla sezione <strong>📦 XDCC</strong>.</div>
      </div>`;
      return;
    }
    let container = box.querySelector(".dl-list");
    if (!container) {
      box.innerHTML = `<div class="dl-list"></div>`;
      container = box.querySelector(".dl-list");
    }
    const ids = new Set(data.jobs.map((job) => job.id));
    for (const [id, node] of dlNodes) {
      if (!ids.has(id)) {
        node.root.remove();
        dlNodes.delete(id);
      }
    }
    const scroll = box.scrollTop;
    data.jobs.forEach((job, index) => {
      let node = dlNodes.get(job.id);
      if (!node) {
        node = createDownloadCard(job);
        dlNodes.set(job.id, node);
        container.insertBefore(node.root, container.children[index] || null);
      }
      updateDownloadCard(node, job);
    });
    box.scrollTop = scroll;
  }

  async function saveDownloadSettings() {
    try {
      const response = await fetch("/api/xdcc/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          download_dir: el("dl-dir").value.trim(),
          max_parallel: Number(el("dl-parallel").value) || 2,
        }),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || "errore del server");
      applyDownloadSnapshot(data);
      toast("Impostazioni dei download salvate");
    } catch (error) {
      toast(`Impostazioni non salvate: ${error.message}`);
    }
  }

  /* ==================================================================== */
  /*  Collegamenti agli elementi                                          */
  /* ==================================================================== */
  function wireTabs() {
    for (const view of VIEWS) {
      el("tab-" + view).addEventListener("click", () => setView(view));
    }
  }

  function wireXdcc() {
    const box = el("xdcc-q");
    el("xdcc-form").addEventListener("submit", (event) => {
      event.preventDefault();
      xdccSearch();
    });
    box.addEventListener("input", () => {
      el("xdcc-clear").hidden = !box.value;
    });
    el("xdcc-clear").addEventListener("click", () => {
      box.value = "";
      box.dataset.searched = "";
      el("xdcc-clear").hidden = true;
      box.focus();
    });
    el("xdcc-reset").addEventListener("click", resetXdccFilters);
    el("xdcc-source").addEventListener("change", () => {
      localStorage.setItem("ms_xdcc_source", el("xdcc-source").value);
      renderXdccTable();
    });
    el("xdcc-sort").addEventListener("change", () => {
      localStorage.setItem("ms_xdcc_sort", el("xdcc-sort").value);
      renderXdccTable();
    });
    let episodeTimer = null;
    el("xdcc-episode").addEventListener("input", () => {
      clearTimeout(episodeTimer);
      episodeTimer = setTimeout(() => {
        localStorage.setItem("ms_xdcc_episode", el("xdcc-episode").value);
        if (xdccQuery.length >= 2) xdccSearch(xdccQuery);
      }, 500);
    });
    el("xdcc-results").addEventListener("click", async (event) => {
      const example = event.target.closest("[data-xdcc-example]");
      if (example) {
        xdccSearch(example.dataset.xdccExample);
        return;
      }
      const reset = event.target.closest("[data-xdcc-action='reset']");
      if (reset) {
        resetXdccFilters();
        return;
      }
      const button = event.target.closest("button[data-act]");
      if (!button || button.disabled) return;
      const list = xdccFiltered();
      const item = list[Number(button.dataset.i)];
      if (!item) return;
      if (button.dataset.act === "download") {
        button.disabled = true;
        const added = await addDownload(item);
        if (!added) button.disabled = false;
      } else if (button.dataset.act === "copy") {
        copyText(`/msg ${xdccCommand(item)}`, "Comando IRC copiato");
      }
    });
    el("xdcc-copy").addEventListener("click", () => {
      const list = xdccFiltered();
      if (!list.length) {
        toast("Nessun comando da copiare");
        return;
      }
      copyText(list.map((item) => `/msg ${xdccCommand(item)}`).join("\n"),
        `${list.length} comandi copiati`);
    });
    el("xdcc-download-all").addEventListener("click", async () => {
      const list = xdccFiltered();
      if (!list.length) return;
      if (list.length > 30 && !confirm(`Aggiungere ${list.length} pacchetti alla coda di download?`)) return;
      let added = 0;
      for (const item of list.slice(0, 30)) {
        if (await addDownload(item)) added += 1;
      }
      toast(`${added} pacchetti aggiunti alla coda`);
      setView("download");
    });
  }

  function wireDownloads() {
    el("dl-list").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-act]");
      if (!button) return;
      const item = button.closest(".dl-item");
      if (!item) return;
      const body = { id: item.dataset.id, action: button.dataset.act };
      if (button.dataset.delete === "1") {
        if (!confirm("Eliminare il file scaricato dal disco?")) return;
        body.delete_file = true;
      }
      downloadAction(body);
    });
    el("dl-dir-save").addEventListener("click", saveDownloadSettings);
    el("dl-dir").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        saveDownloadSettings();
      }
    });
    el("dl-parallel").addEventListener("change", saveDownloadSettings);
    el("dl-open").addEventListener("click", () => downloadAction({ action: "open" }));
    el("dl-clear").addEventListener("click", () => {
      if (!confirm("Togliere dall'elenco tutti i download completati, annullati o con errore?")) return;
      downloadAction({ action: "clear" });
    });
  }

  function wireShortcuts() {
    document.addEventListener("keydown", (event) => {
      const active = document.activeElement || {};
      const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName || "");
      if (typing) return;
      if (event.key === "/" || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k")) {
        if (currentView === "xdcc") {
          event.preventDefault();
          el("xdcc-q").focus();
        }
      }
    });
  }

  /* ==================================================================== */
  /*  Avvio                                                               */
  /* ==================================================================== */
  async function initPortal() {
    wireTabs();
    try {
      meta = await fetch("/api/meta").then((response) => response.json());
    } catch {
      meta = null;
    }
    if (meta) {
      if (meta.size_filter && Array.isArray(meta.size_filter.ladder) && meta.size_filter.ladder.length) {
        SIZE_LADDER = meta.size_filter.ladder;
      }
      const source = el("xdcc-source");
      source.innerHTML = "";
      source.append(new Option("📡 Tutte le fonti", ""));
      for (const item of (meta.xdcc && meta.xdcc.search_sources) || []) {
        source.append(new Option(item.label, item.id));
      }
      const sort = el("xdcc-sort");
      sort.innerHTML = "";
      for (const item of XDCC_SORTS) sort.append(new Option(item.label, item.code));
      const parallel = el("dl-parallel");
      parallel.innerHTML = "";
      const maxParallel = (meta.xdcc && Number(meta.xdcc.max_parallel)) || 3;
      for (let n = 1; n <= maxParallel; n += 1) {
        parallel.append(new Option(String(n), String(n)));
      }
    } else {
      const parallel = el("dl-parallel");
      parallel.innerHTML = "";
      for (let n = 1; n <= 3; n += 1) parallel.append(new Option(String(n), String(n)));
    }
    const box = el("xdcc-q");
    box.value = localStorage.getItem("ms_xdcc_q") || "";
    el("xdcc-clear").hidden = !box.value;
    el("xdcc-episode").value = localStorage.getItem("ms_xdcc_episode") || "";
    el("xdcc-source").value = localStorage.getItem("ms_xdcc_source") || "";
    el("xdcc-sort").value = localStorage.getItem("ms_xdcc_sort") || "relevance";
    configureXdccSize();
    wireXdcc();
    wireDownloads();
    wireShortcuts();
    await refreshDownloads();
    previousDone = dlSnapshot && dlSnapshot.counts ? dlSnapshot.counts.done || 0 : null;
    setView(localStorage.getItem("ms_view") || "torrent", false);
  }

  initPortal();
})();
