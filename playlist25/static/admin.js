"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { session: null, settings: {}, view: "dashboard", proposals: [], selected: new Set(), library: [], exports: new Set(), analysis: null, positions: new Set(), jobs: [], paused: false, editing: null, rightsResolve: null, choiceResolve: null };
  const labels = { pending: "À vérifier", approved: "Validé", rejected: "Refusé", archived: "Archivé", queued: "En attente", downloading: "Téléchargement", converting: "Conversion", verifying: "Vérification", available: "Disponible", interrupted: "Interrompu", unavailable: "Indisponible", failed: "Erreur", error: "Erreur", stopped: "Arrêté" };
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  const button = (text, action, classes = "button subtle small") => { const node = el("button", classes, text); node.type = "button"; node.addEventListener("click", action); return node; };
  const feedback = (target, text, type = "info") => { target.replaceChildren(); if (text) target.append(el("p", `feedback ${type}`, text)); };
  const toast = (text) => { const node = el("div", "toast", text); $("#toasts").append(node); window.setTimeout(() => node.remove(), 5000); };
  const duration = (seconds) => { if (seconds == null || !Number.isFinite(Number(seconds))) return "—"; const n = Math.floor(Number(seconds)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`; };
  const totalDuration = (seconds) => { const n = Math.floor(Number(seconds || 0) / 60); return n >= 60 ? `${Math.floor(n / 60)} h ${String(n % 60).padStart(2, "0")}` : `${n} min`; };
  const number = (value) => Number(value || 0).toLocaleString("fr");
  const size = (bytes) => `${(Number(bytes || 0) / 1048576).toLocaleString("fr", { maximumFractionDigits: 1 })} Mo`;
  const status = (value) => el("span", `status-badge status-${value || "pending"}`, labels[value] || value || "À vérifier");
  const errorText = (value) => typeof value === "string" ? value : Array.isArray(value) ? value.map((item) => item.msg || "Saisie invalide").join(". ") : value?.message || value?.error || "Cette action n’a pas abouti. Vérifiez les diagnostics et réessayez.";
  async function api(path, options = {}) {
    const headers = { "X-CSRF-Token": state.session?.csrf_token || "" };
    const isFile = options.body instanceof FormData;
    if (options.body !== undefined && !isFile) headers["Content-Type"] = "application/json";
    let response;
    try { response = await fetch(path, { method: options.method || "GET", credentials: "same-origin", headers, body: options.body === undefined ? undefined : isFile ? options.body : JSON.stringify(options.body) }); }
    catch { throw new Error("L’application locale est inaccessible. Vérifiez que le lanceur est toujours ouvert, puis actualisez."); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(errorText(data.detail || data)); error.status = response.status; error.detail = data.detail;
      if (response.status === 401 && path !== "/api/login") showLogin();
      throw error;
    }
    return data;
  }
  async function work(trigger, action, target = $("#admin-feedback")) {
    if (trigger) trigger.disabled = true;
    feedback(target, "");
    try { return await action(); }
    catch (error) { feedback(target, error.message, "error"); return null; }
    finally { if (trigger) trigger.disabled = false; }
  }
  function showLogin() { $("#login-screen").classList.remove("hidden"); $("#admin-layout").classList.add("hidden"); state.session = null; }
  async function showStudio() {
    state.session = await api("/api/session"); state.settings = state.session.settings || {};
    $("#login-screen").classList.add("hidden"); $("#admin-layout").classList.remove("hidden"); syncSettings();
    const requested = window.location.hash.slice(1); await navigate(["dashboard", "proposals", "imports", "downloads", "library", "exports", "settings"].includes(requested) ? requested : "dashboard");
  }
  function syncSettings() {
    const settings = state.settings;
    $("#admin-event-name").textContent = settings.event_name || "La playlist des 25 ans";
    document.title = `Le studio · ${settings.event_name || "La playlist des 25 ans"}`;
    $("#admin-mode-label").textContent = settings.offline_mode ? "Soirée hors ligne" : "Préparation";
    $("#dashboard-proposals-open").checked = settings.proposals_open !== false;
    $("#dashboard-votes-open").checked = settings.votes_open !== false;
    $("#dashboard-offline").checked = !!settings.offline_mode;
    $("#dashboard-public-url").value = settings.public_url || "Lien public à configurer dans Réglages";
    $("#dashboard-local-url").value = settings.local_url || "http://127.0.0.1:8000";
    ["event_name", "public_url", "local_url", "max_proposals", "max_playlist_items", "output_format"].forEach((key) => { $(`#setting-${key.replaceAll("_", "-")}`).value = settings[key] ?? ""; });
    ["proposals_open", "votes_open", "offline_mode", "invitation_required"].forEach((key) => { $(`#setting-${key.replaceAll("_", "-")}`).checked = !!settings[key]; });
  }
  async function navigate(view) {
    state.view = view;
    window.history.replaceState(null, "", `#${view}`);
    document.querySelectorAll(".admin-view").forEach((section) => section.classList.toggle("hidden", section.id !== `view-${view}`));
    document.querySelectorAll("[data-view]").forEach((node) => { const active = node.dataset.view === view; node.classList.toggle("active", active); node.setAttribute("aria-current", active ? "page" : "false"); });
    feedback($("#admin-feedback"), "");
    await work(null, async () => {
      if (view === "dashboard") await loadDashboard();
      if (view === "proposals") await loadProposals();
      if (view === "imports") await loadPlaylists();
      if (view === "downloads") await loadJobs();
      if (view === "library" || view === "exports") await loadLibrary();
      if (view === "settings") { state.settings = await api("/api/settings"); syncSettings(); await Promise.all([loadDiagnostics(), loadCloudStatus()]); }
    });
  }
  async function loadDashboard() {
    const data = await api("/api/dashboard");
    const cards = [[data.proposals, "morceaux proposés", `${number(data.guests)} invités dans la bande`, "coral"], [data.approved, "morceaux validés", `${number(data.pending)} propositions à vérifier`, "indigo"], [data.available, "fichiers disponibles", `${number(data.jobs?.queued)} dans la file`, "lime"], [totalDuration(data.total_duration), "de musique validée", `${number(data.votes)} votes enregistrés`, "cream"]];
    $("#dashboard-stats").replaceChildren();
    cards.forEach(([count, title, detail, color]) => { const card = el("article", `stat-card ${color}`); card.append(el("span", "stat-card-label", title), el("strong", "", typeof count === "number" ? number(count) : count), el("small", "", detail)); $("#dashboard-stats").append(card); });
    $("#nav-proposal-count").textContent = number(data.proposals);
    $("#dashboard-summary").textContent = data.pending ? `${number(data.pending)} proposition${data.pending > 1 ? "s" : ""} attendent votre regard. La suite se prépare à votre rythme.` : "La collecte et les préparatifs, réunis au même endroit.";
  }
  function visibleProposals() { const q = $("#admin-proposal-query").value.trim().toLocaleLowerCase("fr"), filter = $("#admin-proposal-state").value; return state.proposals.filter((item) => (!filter || item.state === filter) && [item.title, item.artist, item.nickname].some((value) => (value || "").toLocaleLowerCase("fr").includes(q))).sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.id - b.id); }
  async function loadProposals() { state.proposals = (await api("/api/proposals")).items || []; const ids = new Set(state.proposals.map((item) => item.id)); state.selected.forEach((id) => { if (!ids.has(id)) state.selected.delete(id); }); $("#nav-proposal-count").textContent = state.proposals.length; renderProposals(); }
  function selectionCount() { $("#proposal-selection-count").textContent = `${state.selected.size} proposition${state.selected.size > 1 ? "s" : ""} sélectionnée${state.selected.size > 1 ? "s" : ""}`; $("#approve-selected").disabled = $("#download-selected").disabled = !state.selected.size; const visible = visibleProposals(); $("#select-all-proposals").checked = visible.length > 0 && visible.every((item) => state.selected.has(item.id)); $("#select-all-proposals").indeterminate = visible.some((item) => state.selected.has(item.id)) && !$("#select-all-proposals").checked; }
  function renderProposals() {
    const body = $("#admin-proposals"); body.replaceChildren();
    const items = visibleProposals();
    items.forEach((item, index) => {
      const row = el("tr"), checkCell = el("td", "checkbox-cell"), check = el("input"); check.type = "checkbox"; check.checked = state.selected.has(item.id); check.setAttribute("aria-label", `Sélectionner ${item.title}`); check.addEventListener("change", () => { check.checked ? state.selected.add(item.id) : state.selected.delete(item.id); selectionCount(); }); checkCell.append(check);
      const track = el("td", "table-track"); track.append(el("strong", "", item.title), el("small", "", `${item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Artiste à préciser")} · proposé par ${item.nickname || "Organisation"}`));
      const sourceBadge = el("small", "muted", item.url ? `Source ${item.source_id} · ${labels[item.status] || item.status}` : "Sans source : associez un lien YouTube"); track.append(sourceBadge);
      const vote = el("td", "vote-cell", `♡ ${number(item.votes)}`), time = el("td", "nowrap", duration(item.duration)), badge = el("td"); badge.append(status(item.state));
      const actions = el("td", "table-actions"); actions.append(button("Modifier", () => edit(item, "proposal")));
      if (item.state !== "approved") actions.append(button("Valider", (event) => work(event.currentTarget, async () => { await api(`/api/proposals/${item.id}`, { method: "PATCH", body: { state: "approved" } }); await loadProposals(); toast("Proposition validée."); })));
      const up = button("↑", (event) => reorder(item, items[index - 1], event.currentTarget), "icon-button"), down = button("↓", (event) => reorder(item, items[index + 1], event.currentTarget), "icon-button"); up.disabled = index === 0; down.disabled = index === items.length - 1; up.setAttribute("aria-label", `Remonter ${item.title}`); down.setAttribute("aria-label", `Descendre ${item.title}`); actions.append(up, down); row.append(checkCell, track, vote, time, badge, actions); body.append(row);
    });
    if (!items.length) { const row = el("tr"), cell = el("td", "table-empty", "Aucune proposition pour le moment ou pour ce filtre."); cell.colSpan = 6; row.append(cell); body.append(row); }
    selectionCount();
  }
  async function reorder(item, other, trigger) {
    if (!other) return;
    await work(trigger, async () => {
      const ordered = state.proposals.slice().sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.id - b.id);
      const index = ordered.findIndex((p) => p.id === item.id), otherIndex = ordered.findIndex((p) => p.id === other.id);
      [ordered[index], ordered[otherIndex]] = [ordered[otherIndex], ordered[index]];
      // Assign stable positions, including equal initial sort_order values.
      for (let i = 0; i < ordered.length; i++) if (ordered[i].sort_order !== i) await api(`/api/proposals/${ordered[i].id}`, { method: "PATCH", body: { sort_order: i } });
      await loadProposals();
    });
  }
  function edit(item, kind) {
    state.editing = { item, kind };
    $("#edit-title").value = item.title || ""; $("#edit-artist").value = item.artist || ""; $("#edit-url").value = item.url || ""; $("#edit-state").value = item.state || "pending";
    $("#edit-heading").textContent = kind === "library" ? "Renommer le morceau" : "Modifier la proposition";
    $("#edit-url-field").classList.toggle("hidden", kind === "library"); $("#edit-state-field").classList.toggle("hidden", kind === "library"); feedback($("#edit-feedback"), ""); $("#edit-dialog").showModal();
  }
  function confirmRights(text) { $("#rights-summary").textContent = text; $("#rights-confirm").checked = false; $("#rights-start").disabled = true; $("#rights-dialog").showModal(); return new Promise((resolve) => { state.rightsResolve = resolve; }); }
  function chooseScope() { $("#choice-dialog").showModal(); return new Promise((resolve) => { state.choiceResolve = resolve; }); }
  function queuedText(result) { const count = Array.isArray(result.queued) ? result.queued.length : Number(result.queued || 0), skipped = Array.isArray(result.skipped) ? result.skipped.length : Number(result.skipped || 0); return result.message || `${count} morceau${count > 1 ? "x" : ""} ajouté${count > 1 ? "s" : ""} à la file${skipped ? ` ; ${skipped} déjà disponible(s) ou déjà en cours` : ""}.`; }
  async function loadPlaylists() {
    const data = await api("/api/playlists"), target = $("#saved-playlists"); target.replaceChildren();
    (data.items || []).forEach((item) => { const row = el("article", "list-row"), content = el("div", "list-row-content"); content.append(el("h3", "", item.title || "Playlist importée")); const entries = item.entries || []; content.append(el("p", "muted", `${entries.length} entrées · ordre d’origine conservé · ${item.analysis_complete ? "analyse complète" : "analyse incomplète"}`)); (item.warnings || []).forEach((warning) => content.append(el("small", "warning-text", warning))); row.append(content); const selected = entries.filter((entry) => entry.selected && entry.source_id && entry.available); if (selected.length) row.append(button("Télécharger la sélection ↓", async (event) => { if (!await confirmRights(`${selected.length} entrée(s) sélectionnée(s) de cette playlist.`)) return; await work(event.currentTarget, async () => { const result = await api("/api/downloads", { method: "POST", body: { source_ids: Array.from(new Set(selected.map((entry) => entry.source_id))), rights_confirmed: true } }); toast(queuedText(result)); }); })); target.append(row); });
    if (!data.items?.length) target.append(el("p", "empty-inline", "Vos playlists importées apparaîtront ici."));
  }
  async function analyzePlaylist(mode = null) {
    feedback($("#import-feedback"), "Analyse en cours : récupération des titres et des éléments indisponibles…");
    try {
      state.analysis = await api("/api/playlists/analyze", { method: "POST", body: { url: $("#playlist-url").value.trim(), mode } }); renderAnalysis();
    } catch (error) {
      if (error.detail?.need_choice) { const choice = await chooseScope(); if (choice) return analyzePlaylist(choice); feedback($("#import-feedback"), "Analyse annulée. Choisissez un morceau ou une playlist pour continuer."); }
      else throw error;
    }
  }
  function renderAnalysis() {
    const analysis = state.analysis, entries = analysis.entries || []; state.positions = new Set(entries.filter((entry) => entry.available).map((entry) => entry.position));
    $("#analysis-panel").classList.remove("hidden"); $("#analysis-title").textContent = analysis.title || "Vos morceaux"; $("#analysis-completeness").textContent = analysis.complete ? "Analyse complète" : "Analyse incomplète — vérifiez le rapport";
    $("#analysis-summary").textContent = `${entries.length} entrées analysées · ${entries.filter((entry) => !entry.available).length} indisponibles · ${entries.filter((entry) => entry.already_available).length} fichiers déjà présents. L’ordre d’origine est conservé.`;
    const warnings = $("#analysis-warnings"); warnings.replaceChildren(); (analysis.warnings || []).forEach((warning) => warnings.append(el("p", "feedback warning", warning))); if (!analysis.complete) warnings.append(el("p", "feedback warning", "Cette liste ne doit pas être considérée comme la playlist complète. Les limites ou interruptions signalées doivent être vérifiées."));
    const body = $("#analysis-entries"); body.replaceChildren(); entries.forEach((item) => {
      const row = el("tr"), checkCell = el("td", "checkbox-cell"), check = el("input"); check.type = "checkbox"; check.checked = !!item.available; check.disabled = !item.available; check.setAttribute("aria-label", `Sélectionner la position ${item.position} : ${item.title}`); check.addEventListener("change", () => { check.checked ? state.positions.add(item.position) : state.positions.delete(item.position); analysisSelection(); }); checkCell.append(check);
      const track = el("td", "table-track"); track.append(el("strong", "", item.title || "Élément non résolu")); track.append(el("small", "", item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Métadonnées limitées")));
      const availability = el("td"); availability.append(status(item.already_available ? "available" : item.available ? "pending" : "unavailable")); if (item.reason) availability.append(el("small", "warning-text", item.reason)); row.append(checkCell, el("td", "muted", item.position), track, el("td", "nowrap", duration(item.duration)), availability); body.append(row);
    });
    analysisSelection(); feedback($("#import-feedback"), analysis.complete ? "Analyse terminée. Vérifiez et choisissez votre sélection." : "Analyse terminée avec des limites. Vérifiez les avertissements avant d’importer.", analysis.complete ? "success" : "warning");
  }
  function analysisSelection() { $("#analysis-selection-count").textContent = `${state.positions.size} morceau${state.positions.size > 1 ? "x" : ""} sélectionné${state.positions.size > 1 ? "s" : ""}`; $("#import-download").disabled = $("#import-only").disabled = !state.positions.size; }
  async function importAnalysis(download, trigger) {
    if (!state.analysis || !state.positions.size) return;
    if (download && !await confirmRights(`${state.positions.size} entrée(s) de « ${state.analysis.title || "la sélection"} ». Les fichiers déjà présents seront réutilisés.`)) return;
    await work(trigger, async () => { const result = await api("/api/playlists/import", { method: "POST", body: { analysis: { token: state.analysis.token }, positions: Array.from(state.positions), download, rights_confirmed: download } }); toast(download ? `Playlist enregistrée. ${queuedText(result.jobs || result)}` : "Playlist enregistrée sans téléchargement."); feedback($("#import-feedback"), "Playlist enregistrée. Les éléments indisponibles et l’ordre d’origine sont conservés.", "success"); await loadPlaylists(); }, $("#import-feedback"));
  }
  async function loadJobs() { const data = await api("/api/jobs"); state.jobs = data.items || []; state.paused = !!data.paused; $("#queue-toggle").textContent = data.paused ? "Reprendre la file →" : "Suspendre après le morceau en cours"; $("#queue-notice").textContent = data.offline ? "Mode soirée hors ligne : les traitements externes attendent la désactivation de ce mode." : data.paused ? "File suspendue après le traitement en cours. Les tâches restent enregistrées." : "La file traite un morceau à la fois. Suspendre laisse le morceau en cours terminer son traitement."; renderJobs(); }
  function renderJobs() {
    const target = $("#jobs-list"); target.replaceChildren(); state.jobs.forEach((job) => {
      const row = el("article", "job-row"), head = el("div", "job-head"), copy = el("div"); copy.append(el("h3", "", job.title || `Source ${job.source_id}`), el("small", "muted", `Source ${job.source_id} · ${job.attempts || 0} tentative(s)`)); head.append(copy, status(job.status)); row.append(head);
      const progress = el("progress"); progress.max = 100; progress.value = Math.min(100, Math.max(0, Number(job.progress || 0))); progress.setAttribute("aria-label", `Progression de ${job.title || job.source_id}`); row.append(progress);
      if (job.error) row.append(el("p", "feedback error", job.error));
      if (job.events?.length) { const details = el("details"), summary = el("summary", "", "Voir l’historique"); details.append(summary); job.events.forEach((event) => details.append(el("p", "job-event", `${labels[event.status] || event.status} · ${event.message || ""}`))); row.append(details); }
      if (["failed", "error", "unavailable", "interrupted"].includes(job.status)) row.append(button("Réessayer ce morceau ↻", async (event) => { if (!await confirmRights(`Relancer uniquement « ${job.title || `Source ${job.source_id}`} ».`)) return; await work(event.currentTarget, async () => { toast(queuedText(await api("/api/downloads/retry", { method: "POST", body: { rights_confirmed: true, source_ids: [job.source_id] } }))); await loadJobs(); }); })); target.append(row);
    });
    if (!state.jobs.length) target.append(el("p", "empty-inline", "La file est vide. Choisissez des propositions ou importez une playlist pour préparer les fichiers."));
    $("#retry-failed").disabled = !state.jobs.some((job) => ["failed", "error", "unavailable", "interrupted"].includes(job.status));
  }
  async function loadLibrary() {
    state.library = (await api("/api/library")).items || [];
    const available = new Set(state.library.map((item) => item.id)); state.exports.forEach((id) => { if (!available.has(id)) state.exports.delete(id); });
    if (!state.exportsInitialized) { state.library.forEach((item) => state.exports.add(item.id)); state.exportsInitialized = true; }
    renderLibrary(); renderExportSelection();
  }
  function renderLibrary() {
    const q = $("#library-query").value.trim().toLocaleLowerCase("fr"), target = $("#library-list"); target.replaceChildren(); $("#library-count").textContent = `${state.library.length} fichier(s) disponible(s)`;
    state.library.filter((item) => [item.title, item.artist].some((v) => (v || "").toLocaleLowerCase("fr").includes(q))).forEach((item) => { const row = el("article", "library-row"), image = el("img"); image.src = "./static/placeholder.svg"; image.alt = ""; const content = el("div", "list-row-content"); content.append(el("h3", "", item.title), el("p", "muted", `${item.artist || "Artiste à préciser"} · ${duration(item.duration)} · source ${item.id}`)); const audio = el("audio"); audio.controls = true; audio.preload = "none"; audio.src = `/api/library/${item.id}/audio`; audio.setAttribute("aria-label", `Écouter ${item.title}`); row.append(image, content, audio, button("Renommer", () => edit(item, "library"))); target.append(row); });
    if (!target.children.length) target.append(el("p", "empty-inline", state.library.length ? "Aucun fichier pour cette recherche." : "Vos fichiers disponibles apparaîtront ici après téléchargement ou import."));
  }
  function renderExportSelection() {
    const target = $("#export-selection"); target.replaceChildren(); state.library.forEach((item) => { const label = el("label", "export-check"), check = el("input"); check.type = "checkbox"; check.checked = state.exports.has(item.id); check.addEventListener("change", () => { check.checked ? state.exports.add(item.id) : state.exports.delete(item.id); $("#prepare-export").disabled = !state.exports.size; }); const copy = el("span"); copy.append(el("strong", "", item.title), el("small", "muted", `${item.artist || "Artiste à préciser"} · ${duration(item.duration)}`)); label.append(check, copy); target.append(label); });
    if (!state.library.length) target.append(el("p", "empty-inline", "Préparez d’abord les fichiers dans Téléchargements ou importez votre audio.")); $("#prepare-export").disabled = !state.exports.size;
  }
  function diagnosticRows(target, data) {
    target.replaceChildren(); const names = { python: "Python", yt_dlp: "yt-dlp", yt_dlp_ejs: "Composants YouTube JavaScript", ffmpeg: "FFmpeg", ffprobe: "ffprobe", javascript: "Moteur JavaScript", writeable: "Droits d’écriture", writable: "Droits d’écriture", disk: "Espace disque", queue: "File de travail", offline: "Mode hors ligne", configured: "Connexion configurée", url: "Projet Supabase", last_sync: "Dernière récupération", status: "État", guest: "Service invités", admin: "Administration", cloud: "Site public" };
    Object.entries(data).forEach(([key, value]) => { const row = el("div", "diagnostic-row"); row.append(el("strong", "", names[key] || key.replaceAll("_", " "))); let text; if (value && typeof value === "object") text = Object.entries(value).filter(([k]) => !["secret", "password", "token", "service_key", "anon_key", "publicKey"].includes(k)).map(([k, v]) => `${k.replaceAll("_", " ")} : ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · "); else text = value === true ? "Oui" : value === false ? "Non" : value == null ? "Non renseigné" : String(value); row.append(el("span", "", text)); target.append(row); });
  }
  async function loadDiagnostics() { diagnosticRows($("#diagnostics-list"), await api("/api/diagnostics")); }
  async function loadCloudStatus() { try { diagnosticRows($("#cloud-status"), await api("/api/cloud/status")); } catch (error) { feedback($("#cloud-status"), error.status === 404 ? "La synchronisation cloud n’est pas disponible dans cette version du serveur." : error.message, "warning"); } }

  document.querySelectorAll("[data-view]").forEach((node) => node.addEventListener("click", () => navigate(node.dataset.view)));
  document.querySelectorAll("[data-go]").forEach((node) => node.addEventListener("click", () => navigate(node.dataset.go)));
  document.querySelectorAll("[data-close-dialog]").forEach((node) => node.addEventListener("click", () => $(`#${node.dataset.closeDialog}`).close()));
  $("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); await work(event.submitter, async () => { if (!state.session) state.session = await api("/api/session"); const result = await api("/api/login", { method: "POST", body: { password: $("#admin-password").value } }); state.session = { ...state.session, ...result }; $("#admin-password").value = ""; await showStudio(); }, $("#login-feedback")); });
  $("#logout-button").addEventListener("click", (event) => work(event.currentTarget, async () => { await api("/api/logout", { method: "POST" }); showLogin(); state.session = await api("/api/session"); }));
  $("#admin-refresh").addEventListener("click", (event) => work(event.currentTarget, async () => { state.settings = await api("/api/settings"); syncSettings(); await navigate(state.view); toast("Le studio est à jour."); }));
  [["#dashboard-proposals-open", "proposals_open"], ["#dashboard-votes-open", "votes_open"], ["#dashboard-offline", "offline_mode"]].forEach(([selector, key]) => $(selector).addEventListener("change", async (event) => { const result = await work(event.currentTarget, () => api("/api/settings", { method: "PATCH", body: { [key]: event.target.checked } })); if (result) { state.settings = result; toast("Réglage enregistré."); } syncSettings(); }));
  $("#copy-public-url").addEventListener("click", (event) => work(event.currentTarget, async () => { if (!state.settings.public_url) throw new Error("Renseignez le lien public dans Réglages après la publication de GitHub Pages."); try { await navigator.clipboard.writeText(state.settings.public_url); toast("Lien public copié."); } catch { $("#dashboard-public-url").select(); toast("Copiez le lien sélectionné avec votre navigateur."); } }));
  $("#show-local-qr").addEventListener("click", () => { $("#admin-local-qr").src = "/api/qr?scope=local"; $("#qr-local-url").value = state.settings.local_url || "http://127.0.0.1:8000"; $("#qr-dialog").showModal(); });
  $("#admin-proposal-query").addEventListener("input", renderProposals); $("#admin-proposal-state").addEventListener("change", renderProposals);
  $("#select-all-proposals").addEventListener("change", (event) => { visibleProposals().forEach((item) => event.target.checked ? state.selected.add(item.id) : state.selected.delete(item.id)); renderProposals(); });
  $("#select-approved").addEventListener("click", () => { state.selected = new Set(state.proposals.filter((item) => item.state === "approved").map((item) => item.id)); renderProposals(); });
  $("#approve-selected").addEventListener("click", (event) => work(event.currentTarget, async () => { for (const id of state.selected) await api(`/api/proposals/${id}`, { method: "PATCH", body: { state: "approved" } }); await loadProposals(); toast("Sélection validée."); }));
  $("#download-selected").addEventListener("click", async (event) => { const trigger = event.currentTarget, items = state.proposals.filter((item) => state.selected.has(item.id)), sources = Array.from(new Set(items.filter((item) => item.url).map((item) => item.source_id))); if (!sources.length) { feedback($("#admin-feedback"), "Associez d’abord un lien YouTube aux propositions sélectionnées avec Modifier.", "warning"); return; } if (!await confirmRights(`${sources.length} source(s) précise(s) sélectionnée(s). ${items.filter((item) => !item.url).length ? "Les demandes sans lien seront ignorées et restent à retrouver." : "Les fichiers déjà présents seront réutilisés."}`)) return; await work(trigger, async () => { toast(queuedText(await api("/api/downloads", { method: "POST", body: { source_ids: sources, rights_confirmed: true } }))); await navigate("downloads"); }); });
  $("#edit-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, async () => { const { item, kind } = state.editing, body = { title: $("#edit-title").value.trim(), artist: $("#edit-artist").value.trim() }; if (kind === "proposal") { body.state = $("#edit-state").value; const url = $("#edit-url").value.trim(); if (url && url !== item.url) body.url = url; } const result = await api(`/api/${kind === "library" ? "library" : "proposals"}/${item.id}`, { method: "PATCH", body }); $("#edit-dialog").close(); toast("Morceau mis à jour."); if (result.warnings?.length) feedback($("#admin-feedback"), result.warnings.join(" "), "warning"); await (kind === "library" ? loadLibrary() : loadProposals()); }, $("#edit-feedback")); });
  $("#open-merge").addEventListener("click", () => { const items = state.proposals.filter((item) => item.state !== "archived"); ["#merge-keep", "#merge-other"].forEach((selector) => { const select = $(selector); select.replaceChildren(); items.forEach((item) => { const option = el("option", "", `#${item.id} · ${item.title} · ${item.nickname || "Organisation"}`); option.value = item.id; select.append(option); }); }); if (items.length > 1) $("#merge-other").selectedIndex = 1; $("#merge-confirm").checked = false; feedback($("#merge-feedback"), ""); $("#merge-dialog").showModal(); });
  $("#merge-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, async () => { await api("/api/proposals/merge", { method: "POST", body: { keep_id: Number($("#merge-keep").value), merge_id: Number($("#merge-other").value), confirmed: $("#merge-confirm").checked } }); $("#merge-dialog").close(); await loadProposals(); toast("Propositions réunies, votes conservés."); }, $("#merge-feedback")); });
  $("#rights-confirm").addEventListener("change", (event) => { $("#rights-start").disabled = !event.target.checked; });
  $("#rights-start").addEventListener("click", () => { if (state.rightsResolve && $("#rights-confirm").checked) { const resolve = state.rightsResolve; state.rightsResolve = null; resolve(true); $("#rights-dialog").close(); } });
  $("#rights-dialog").addEventListener("close", () => { state.rightsResolve?.(false); state.rightsResolve = null; });
  [["#choice-video", "video"], ["#choice-playlist", "playlist"]].forEach(([selector, mode]) => $(selector).addEventListener("click", () => { const resolve = state.choiceResolve; state.choiceResolve = null; resolve?.(mode); $("#choice-dialog").close(); })); $("#choice-dialog").addEventListener("close", () => { state.choiceResolve?.(null); state.choiceResolve = null; });
  document.querySelectorAll("[data-import-mode]").forEach((node) => node.addEventListener("click", () => { document.querySelectorAll("[data-import-mode]").forEach((tab) => tab.classList.toggle("active", tab === node)); $("#playlist-analyze-form").classList.toggle("hidden", node.dataset.importMode !== "playlist"); $("#links-analyze-form").classList.toggle("hidden", node.dataset.importMode !== "links"); }));
  $("#playlist-analyze-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, () => analyzePlaylist(), $("#import-feedback")); });
  $("#links-analyze-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, async () => { if (!$("#links-text").value.trim()) throw new Error("Collez au moins un lien YouTube ou chargez un fichier TXT."); feedback($("#import-feedback"), "Analyse des liens en cours…"); state.analysis = await api("/api/links/analyze", { method: "POST", body: { text: $("#links-text").value } }); renderAnalysis(); }, $("#import-feedback")); });
  $("#links-txt").addEventListener("change", (event) => work(null, async () => { const file = event.target.files[0]; if (!file) return; if (file.size > 1000000) throw new Error("Le fichier TXT doit faire moins de 1 Mo."); $("#links-text").value = await file.text(); }, $("#import-feedback")));
  $("#analysis-select-all").addEventListener("click", () => { state.positions = new Set((state.analysis?.entries || []).filter((item) => item.available).map((item) => item.position)); $("#analysis-entries").querySelectorAll("input:not(:disabled)").forEach((check) => { check.checked = true; }); analysisSelection(); });
  $("#import-only").addEventListener("click", (event) => importAnalysis(false, event.currentTarget)); $("#import-download").addEventListener("click", (event) => importAnalysis(true, event.currentTarget));
  $("#queue-toggle").addEventListener("click", (event) => work(event.currentTarget, async () => { const result = await api("/api/queue", { method: "POST", body: { paused: !state.paused } }); toast(result.message); await loadJobs(); }));
  $("#retry-failed").addEventListener("click", async (event) => { const trigger = event.currentTarget; if (!await confirmRights("Relancer uniquement les téléchargements en échec. Les fichiers disponibles ne seront pas téléchargés à nouveau.")) return; await work(trigger, async () => { toast(queuedText(await api("/api/downloads/retry", { method: "POST", body: { rights_confirmed: true } }))); await loadJobs(); }); });
  $("#library-query").addEventListener("input", renderLibrary);
  $("#open-audio-import").addEventListener("click", () => { $("#audio-import-form").reset(); feedback($("#audio-import-feedback"), ""); $("#audio-import-dialog").showModal(); });
  $("#audio-import-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, async () => { const data = new FormData(); data.append("file", $("#audio-file").files[0]); data.append("title", $("#audio-title").value.trim()); data.append("artist", $("#audio-artist").value.trim()); feedback($("#audio-import-feedback"), "Copie et vérification du fichier…"); const result = await api("/api/library/import", { method: "POST", body: data }); if (result.item?.id) state.exports.add(result.item.id); $("#audio-import-dialog").close(); await loadLibrary(); toast("Votre fichier audio est disponible."); }, $("#audio-import-feedback")); });
  $("#export-select-all").addEventListener("click", () => { state.library.forEach((item) => state.exports.add(item.id)); renderExportSelection(); }); $("#export-select-none").addEventListener("click", () => { state.exports.clear(); renderExportSelection(); });
  $("#prepare-export").addEventListener("click", (event) => work(event.currentTarget, async () => { feedback($("#export-result"), "Préparation et contrôle du pack…"); const result = await api("/api/exports", { method: "POST", body: { source_ids: Array.from(state.exports), make_zip: $("#export-make-zip").checked } }), target = $("#export-result"); target.replaceChildren(); target.append(el("p", "feedback success", `${result.count} fichier(s) préparé(s) · ${size(result.size)}`)); if (result.directory) target.append(el("p", "export-path", `Dossier sur cette machine : ${result.directory}`)); (result.warnings || []).forEach((warning) => target.append(el("p", "feedback warning", warning))); if (result.download_url && result.download_url.startsWith("/api/exports/")) { const link = el("a", "button primary full-width", "Récupérer le ZIP sur mon ordinateur ↓"); link.href = result.download_url; link.download = ""; target.append(link); } target.append(el("p", "field-help", "Le dossier est créé sur la machine de préparation. Le ZIP est récupéré sur votre ordinateur par votre navigateur. Gardez audio/ et les playlists ensemble après extraction.")); }, $("#export-result")));
  $("#settings-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, async () => { const body = {}; ["event_name", "public_url", "local_url", "output_format"].forEach((key) => { body[key] = $(`#setting-${key.replaceAll("_", "-")}`).value.trim(); }); ["max_proposals", "max_playlist_items"].forEach((key) => { body[key] = Number($(`#setting-${key.replaceAll("_", "-")}`).value); }); ["proposals_open", "votes_open", "offline_mode", "invitation_required"].forEach((key) => { body[key] = $(`#setting-${key.replaceAll("_", "-")}`).checked; }); state.settings = await api("/api/settings", { method: "PATCH", body }); syncSettings(); toast("Réglages enregistrés."); }); });
  $("#invitation-form").addEventListener("submit", (event) => { event.preventDefault(); work(event.submitter, async () => { const data = await api("/api/invitations", { method: "POST", body: { count: Number($("#invitation-count").value) } }); $("#invitation-codes").value = (data.codes || []).join("\n"); $("#invitation-codes").classList.remove("hidden"); toast("Codes créés. Copiez-les avant de quitter cet écran."); }); });
  $("#refresh-diagnostics").addEventListener("click", (event) => work(event.currentTarget, loadDiagnostics));
  $("#cloud-sync").addEventListener("click", (event) => work(event.currentTarget, async () => { feedback($("#cloud-feedback"), "Récupération des propositions et des votes du site public…"); const data = await api("/api/cloud/sync", { method: "POST", body: {} }); feedback($("#cloud-feedback"), data.message || `Récupération terminée : ${data.proposals ?? data.imported ?? 0} proposition(s), ${data.votes ?? 0} vote(s).`, "success"); await loadCloudStatus(); }, $("#cloud-feedback")));
  $("#cloud-publish").addEventListener("click", (event) => work(event.currentTarget, async () => { feedback($("#cloud-feedback"), "Publication des réglages et de la modération sur le site public…"); const data = await api("/api/cloud/publish", { method: "POST", body: {} }); feedback($("#cloud-feedback"), data.message || "Réglages et modération publiés sur le site public.", "success"); await loadCloudStatus(); }, $("#cloud-feedback")));
  async function boot() { try { state.session = await api("/api/session"); if (state.session.authenticated) await showStudio(); else showLogin(); } catch (error) { feedback($("#login-feedback"), error.message, "error"); } }
  boot();
  window.setInterval(() => { if (!document.hidden && state.session?.authenticated && state.view === "downloads") loadJobs().catch((error) => feedback($("#admin-feedback"), error.message, "error")); }, 5000);
})();
