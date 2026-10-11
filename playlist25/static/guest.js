"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { session: null, items: [], mine: false, query: "", sort: "popular", identityResolve: null, searchSerial: 0, searchTimer: null, searchController: null, withdrawing: null, withdrawalPending: false };
  const cloud = () => window.PlaylistCloudProvider?.enabled?.() ? window.PlaylistCloudProvider : null;
  const el = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const errorText = (value) => {
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map((part) => part.msg || "Saisie invalide").join(". ");
    return value?.message || value?.error || "Cette action n’a pas abouti. Réessayez dans un instant.";
  };
  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = { "X-CSRF-Token": state.session?.csrf_token || "" };
    if (cloud()) return cloud().request(path, { method, body: options.body, headers });
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try { response = await fetch(path, { method, headers, signal: options.signal, credentials: "same-origin", body: options.body === undefined ? undefined : JSON.stringify(options.body) }); }
    catch (error) { if (error.name === "AbortError") throw error; throw new Error("Le site est momentanément inaccessible. Vos saisies restent ici ; réessayez quand la connexion revient."); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(errorText(data.detail || data)); error.status = response.status; error.detail = data.detail; throw error; }
    return data;
  }
  function message(target, text, type = "info") {
    target.replaceChildren();
    if (text) target.append(el("p", `feedback ${type}`, text));
  }
  function toast(text) { const node = el("div", "toast", text); $("#toasts").append(node); window.setTimeout(() => node.remove(), 4500); }
  function duration(seconds) { if (!Number.isFinite(Number(seconds)) || seconds == null) return "Durée non renseignée"; const n = Math.floor(Number(seconds)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`; }
  function nicknameInitial(nickname) { return Array.from(nickname || "?")[0].toUpperCase(); }
  function youtubeVideoId(item) {
    if (typeof item.video_id === "string" && /^[A-Za-z0-9_-]{11}$/.test(item.video_id)) return item.video_id;
    const canonical = /^https:\/\/www\.youtube\.com\/watch\?v=([A-Za-z0-9_-]{11})$/.exec(item.url || "");
    return canonical?.[1] || null;
  }
  function clearPreview() { $("#preview-player").replaceChildren(); }
  function openPreview(item) {
    const id = youtubeVideoId(item);
    if (!id) return;
    if (state.session?.settings?.offline_mode) { toast("L’écoute YouTube nécessite Internet et reste désactivée pendant la soirée hors ligne."); return; }
    clearPreview();
    $("#preview-heading").textContent = item.title || "Écouter le morceau";
    $("#preview-fallback").href = `https://www.youtube.com/watch?v=${id}`;
    const player = el("iframe");
    // No player exists or connects to YouTube before an explicit preview click.
    player.src = `https://www.youtube-nocookie.com/embed/${id}`;
    player.title = `Lecteur YouTube : ${item.title || "Morceau"}`;
    player.loading = "lazy";
    player.allow = "autoplay; encrypted-media; picture-in-picture";
    player.allowFullscreen = true;
    player.referrerPolicy = "strict-origin-when-cross-origin";
    $("#preview-player").append(player);
    if (!$("#preview-dialog").open) $("#preview-dialog").showModal();
  }
  function previewButton(item) {
    const id = youtubeVideoId(item);
    if (!id) return null;
    const button = el("button", "button subtle small preview-button", "Écouter");
    button.type = "button"; button.dataset.previewId = id;
    button.setAttribute("aria-label", `Écouter ${item.title || "ce morceau"}`);
    button.addEventListener("click", () => openPreview(item));
    if (state.session?.settings?.offline_mode) { button.disabled = true; button.textContent = "Écoute indisponible hors ligne"; button.title = "Un accès à Internet est nécessaire pour écouter une vidéo YouTube."; }
    return button;
  }
  function showIdentity() { $("#nickname-input").value = state.session?.guest?.nickname || ""; $("#identity-dialog").showModal(); window.setTimeout(() => $("#nickname-input").focus(), 20); }
  async function ensureIdentity() {
    if (state.session?.guest) return true;
    showIdentity();
    return new Promise((resolve) => { state.identityResolve = resolve; });
  }
  function syncSession() {
    const settings = state.session?.settings || {};
    const guest = state.session?.guest;
    const title = settings.event_name || "Les 25 ans de Fanny";
    if (["La playlist des 25 ans", "Les 25 ans de Fanny", "La playlist des 25 ans de Fanny"].includes(title)) $("#event-title").replaceChildren(document.createTextNode("Les 25 ans"), el("br"), el("span", "birthday-name", "de Fanny."));
    else $("#event-title").textContent = title;
    document.title = `${title === "La playlist des 25 ans" ? "Les 25 ans de Fanny" : title} · La playlist`;
    $("#guest-name").textContent = guest?.nickname || "Choisir mon pseudo";
    $("#guest-avatar").textContent = nicknameInitial(guest?.nickname);
    $("#quota-label").textContent = `${settings.max_proposals || 10} idées par invité`;
    $("#invitation-field").classList.toggle("hidden", !settings.invitation_required);
    $("#invitation-input").required = !!settings.invitation_required;
    let notice = "";
    if (settings.offline_mode) notice = "Mode soirée hors ligne : les idées restent ouvertes selon les réglages. La recherche YouTube et les téléchargements externes sont désactivés.";
    else if (settings.proposals_open === false) notice = "Les propositions sont fermées pour le moment. Vous pouvez toujours découvrir la playlist.";
    else if (cloud() && cloud().searchSupported === false) notice = "La playlist est accessible même quand le PC est éteint. Ajoutez un lien YouTube ou un titre ; les fichiers seront préparés avant la soirée.";
    $("#global-notice").textContent = notice;
    $("#global-notice").classList.toggle("hidden", !notice);
    $("#connection-label").replaceChildren(el("span"), document.createTextNode(settings.offline_mode ? " La soirée, même sans Internet" : " Ensemble, on prépare la soirée"));
    if (cloud()?.searchSupported === false) {
      $("#search-help").textContent = "La recherche YouTube n’est pas disponible avec l’hébergement gratuit. Collez un lien YouTube ou proposez un titre : aucune recherche fictive ne sera affichée.";
      setEntry("link");
    }
    $("#search-input").disabled = !!settings.offline_mode;
    $("#search-form button").disabled = $("#search-input").disabled;
    if (settings.offline_mode) { clearPreview(); if ($("#preview-dialog").open) $("#preview-dialog").close(); }
    document.querySelectorAll("[data-preview-id]").forEach((button) => { button.disabled = !!settings.offline_mode; button.textContent = settings.offline_mode ? "Écoute indisponible hors ligne" : "Écouter"; button.title = settings.offline_mode ? "Un accès à Internet est nécessaire pour écouter une vidéo YouTube." : "Ouvrir le lecteur YouTube de ce morceau."; });
  }
  function setEntry(name) {
    window.clearTimeout(state.searchTimer); state.searchController?.abort(); state.searchSerial++;
    $("#search-form button").disabled = !!state.session?.settings?.offline_mode;
    document.querySelectorAll("[data-entry]").forEach((tab) => { const active = tab.dataset.entry === name; tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1; });
    ["search", "link", "manual"].forEach((mode) => $(`#${mode}-form`).classList.toggle("hidden", name !== mode));
    message($("#entry-feedback"), "");
    $("#search-results").replaceChildren();
  }
  function youtubeFallback(query) {
    const actions = el("div", "button-row search-fallback");
    const link = el("a", "button subtle small", "Chercher sur YouTube →"); link.href = `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`; link.target = "_blank"; link.rel = "noopener noreferrer";
    const manual = el("button", "button subtle small", "Proposer ce titre sans lien"); manual.type = "button"; manual.addEventListener("click", () => { setEntry("manual"); $("#manual-title").value = query; $("#manual-title").focus(); });
    actions.append(link, manual); $("#entry-feedback").append(actions);
  }
  async function searchMusic(query, serial) {
    const target = $("#entry-feedback"), results = $("#search-results"), submit = $("#search-form button");
    if (query.length < 3) { message(target, "Écrivez au moins 3 caractères pour trouver des morceaux."); return; }
    if (cloud()?.searchSupported === false) { message(target, "La recherche intégrée n’est pas disponible. Ouvrez YouTube pour choisir votre morceau, puis collez son lien ici."); youtubeFallback(query); return; }
    state.searchController?.abort(); state.searchController = new AbortController(); submit.disabled = true;
    results.replaceChildren(); message(target, "On cherche les morceaux disponibles…");
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}`, { signal: state.searchController.signal });
      if (serial !== state.searchSerial) return;
      message(target, data.items?.length ? "Cliquez sur « Ajouter » à côté de votre morceau. Vérifiez la version : original, live ou remix." : "Aucun résultat disponible. Vous pouvez proposer le titre manuellement.");
      (data.items || []).forEach((item) => {
        const row = el("article", "search-result"); const image = el("img"); image.src = "./static/placeholder.svg"; image.alt = "";
        const description = el("div", "search-result-text"); description.append(el("h3", "", item.title), el("p", "muted", `${item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Artiste non renseigné")} · ${duration(item.duration)}`));
        const source = el("a", "result-source", "Voir sur YouTube →"); source.href = item.url; source.target = "_blank"; source.rel = "noopener noreferrer";
        // Source links come from the server’s canonical YouTube parser, never an arbitrary thumbnail URL.
        if (/^https:\/\/(www\.)?youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}$/.test(item.url || "")) description.append(source);
        const actions = el("div", "search-result-actions"), preview = previewButton(item);
        if (preview) actions.append(preview);
        const add = el("button", "button primary small", "Ajouter ce morceau ＋"); add.type = "button"; add.addEventListener("click", () => submitProposal({ kind: "youtube", url: item.url, title: item.title, artist: item.artist || "" }, add)); actions.append(add); row.append(image, description, actions); results.append(row);
      });
      if (!data.items?.length) youtubeFallback(query);
    } catch (error) {
      if (serial !== state.searchSerial || error.name === "AbortError") return;
      message(target, `${error.message} Vous pouvez toujours coller un lien YouTube ou proposer un titre.`, "error"); youtubeFallback(query);
    } finally { if (serial === state.searchSerial) submit.disabled = !!state.session?.settings?.offline_mode; }
  }
  async function submitProposal(body, button) {
    if (!await ensureIdentity()) return;
    button.disabled = true;
    message($("#entry-feedback"), "Ajout du morceau…");
    try {
      const data = await api("/api/proposals", { method: "POST", body });
      if (data.duplicate) {
        message($("#entry-feedback"), data.message || "Ce morceau est déjà dans la playlist. Donnez-lui un vote plutôt que de le proposer deux fois.", "info");
        const duplicate = typeof data.duplicate === "object" ? data.duplicate : data.item;
        if (duplicate?.id) {
          const vote = el("button", "button subtle small", "Voter pour ce morceau ♡"); vote.type = "button";
          vote.addEventListener("click", () => voteFor(duplicate, vote)); $("#entry-feedback").append(vote);
        }
      } else {
        const detail = Array.isArray(data.similar) && data.similar.length ? " Un titre proche existe déjà ; l’organisation vérifiera les versions." : "";
        message($("#entry-feedback"), (data.message || "Votre idée est dans la playlist. Merci !") + detail, "success");
        toast("Un bon son de plus dans la playlist !");
        if (body.kind === "manual") $("#manual-form").reset();
        if (body.kind === "youtube") $("#link-form").reset();
      }
      await loadProposals();
    } catch (error) { message($("#entry-feedback"), error.message, "error"); }
    finally { button.disabled = false; }
  }
  async function voteFor(item, button) {
    if (!await ensureIdentity()) return;
    button.disabled = true;
    try {
      const result = await api(`/api/proposals/${item.id}/vote`, { method: "POST", body: { active: !item.voted } });
      const found = state.items.find((track) => track.id === item.id);
      if (found) { found.votes = result.votes; found.voted = result.voted; }
      item.votes = result.votes; item.voted = result.voted;
      renderProposals();
      toast(result.voted ? "Votre vote est enregistré ♡" : "Votre vote a été retiré.");
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }
  function requestWithdrawal(item) {
    if (item.mine !== true) return;
    state.withdrawing = item;
    $("#withdraw-title").textContent = item.title || "Votre morceau";
    message($("#withdraw-feedback"), "");
    $("#withdraw-confirm").disabled = false;
    $("#withdraw-confirm").textContent = "Retirer ma proposition";
    $("#withdraw-dialog").showModal();
    // The safe choice gets initial focus; the removal always needs a second action.
    $("#withdraw-cancel").focus();
  }
  async function withdrawProposal() {
    if (!state.withdrawing || state.withdrawalPending) return;
    const item = state.withdrawing;
    state.withdrawalPending = true;
    $("#withdraw-confirm").disabled = $("#withdraw-cancel").disabled = true;
    $("#withdraw-dialog .modal-close").disabled = true;
    $("#withdraw-confirm").textContent = "Retrait en cours…";
    message($("#withdraw-feedback"), "Retrait de votre proposition…");
    try {
      await api(`/api/proposals/${item.id}/withdraw`, { method: "POST", body: {} });
      state.items = state.items.filter((track) => track.id !== item.id);
      renderProposals();
      $("#withdraw-dialog").close();
      toast("Votre proposition a été retirée de la playlist.");
      $(state.mine ? "#filter-mine" : "#filter-all").focus();
      try { await loadProposals(); }
      catch { toast("Le retrait est enregistré. Actualisez la playlist quand la connexion revient."); }
    } catch (error) { message($("#withdraw-feedback"), error.message, "error"); }
    finally {
      state.withdrawalPending = false;
      $("#withdraw-confirm").disabled = $("#withdraw-cancel").disabled = false;
      $("#withdraw-dialog .modal-close").disabled = false;
      $("#withdraw-confirm").textContent = "Retirer ma proposition";
      if (!$("#withdraw-dialog").open) state.withdrawing = null;
    }
  }
  function renderProposals() {
    const query = state.query.trim().toLocaleLowerCase("fr");
    const mine = state.items.filter((item) => item.mine === true);
    let items = (state.mine ? mine : state.items).filter((item) => [item.title, item.artist, item.nickname, item.channel].some((value) => (value || "").toLocaleLowerCase("fr").includes(query)));
    items = items.slice().sort(state.sort === "popular" ? (a, b) => Number(b.votes || 0) - Number(a.votes || 0) || Number(b.id) - Number(a.id) : (a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")) || Number(b.id) - Number(a.id));
    $("#stat-tracks").textContent = state.items.length;
    $("#stat-votes").textContent = state.items.reduce((total, item) => total + Number(item.votes || 0), 0);
    $("#stat-mine").textContent = mine.length;
    $("#count-all").textContent = state.items.length;
    $("#count-mine").textContent = mine.length;
    $("#filter-all").classList.toggle("active", !state.mine); $("#filter-mine").classList.toggle("active", state.mine);
    const list = $("#proposal-list"); list.replaceChildren();
    items.forEach((item, index) => {
      const card = el("article", "track-card");
      const visual = el("div", `track-art track-art-${index % 4}`); const image = el("img"); image.src = "./static/placeholder.svg"; image.alt = ""; image.loading = "lazy"; visual.append(image, el("span", "track-number", String(index + 1).padStart(2, "0")));
      const content = el("div", "track-content"); content.append(el("h3", "track-title", item.title || "Titre à retrouver"));
      content.append(el("p", "track-artist", item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Artiste à préciser")));
      const details = el("div", "track-details"); details.append(el("span", "", duration(item.duration)));
      if (!item.url) details.append(el("span", "mini-tag", "Une idée à retrouver"));
      else if (item.state === "approved") details.append(el("span", "mini-tag approved", "Validé"));
      content.append(details);
      const preview = previewButton(item);
      if (preview) {
        const sourceActions = el("div", "track-source-actions"), source = el("a", "result-source", "Voir sur YouTube →");
        source.href = `https://www.youtube.com/watch?v=${youtubeVideoId(item)}`; source.target = "_blank"; source.rel = "noopener noreferrer";
        sourceActions.append(preview, source); content.append(sourceActions);
      }
      const bottom = el("div", "track-bottom"); const author = el("span", "track-author"); author.append(el("span", "tiny-avatar", nicknameInitial(item.nickname)), document.createTextNode(item.nickname || "Organisation"));
      const button = el("button", `vote-button${item.voted ? " voted" : ""}`); button.type = "button"; button.setAttribute("aria-label", `${item.voted ? "Retirer mon vote pour" : "Voter pour"} ${item.title}`); button.setAttribute("aria-pressed", String(!!item.voted)); button.append(el("span", "heart", item.voted ? "♥" : "♡"), el("span", "", String(item.votes || 0))); button.disabled = state.session?.settings?.votes_open === false; button.addEventListener("click", () => voteFor(item, button)); bottom.append(author, button);
      card.append(visual, content, bottom);
      if (item.mine === true) {
        const ownActions = el("div", "track-own-actions");
        const withdraw = el("button", "withdraw-button", "Retirer ma proposition"); withdraw.type = "button"; withdraw.dataset.withdrawId = String(item.id); withdraw.setAttribute("aria-label", `Retirer ma proposition : ${item.title}`); withdraw.addEventListener("click", () => requestWithdrawal(item)); ownActions.append(withdraw); card.append(ownActions);
      }
      list.append(card);
    });
    $("#catalogue-empty").classList.toggle("hidden", items.length > 0);
    $("#catalogue-empty h3").textContent = state.items.length ? "Aucun morceau pour cette recherche." : "Le premier morceau, c’est peut-être le vôtre.";
    $("#catalogue-empty p").textContent = state.mine ? "Vos propositions apparaîtront ici après votre premier ajout." : query ? "Essayez un autre titre, un artiste ou un pseudo." : "Ajoutez une idée pour lancer la playlist.";
  }
  async function loadProposals() {
    const data = await api("/api/proposals?sort=popular");
    state.items = data.items || [];
    // Only the server’s boolean marker grants owner actions; nicknames are display names.
    renderProposals();
  }
  document.querySelectorAll("[data-entry]").forEach((button) => {
    button.addEventListener("click", () => setEntry(button.dataset.entry));
    button.addEventListener("keydown", (event) => {
      const tabs = Array.from(document.querySelectorAll("[data-entry]")), index = tabs.indexOf(button);
      const target = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
      if (target !== null) { event.preventDefault(); setEntry(tabs[target].dataset.entry); tabs[target].focus(); }
    });
  });
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.closeDialog}`).close()));
  $("#identity-dialog").addEventListener("close", () => { if (state.identityResolve) { state.identityResolve(!!state.session?.guest); state.identityResolve = null; } });
  $("#identity-button").addEventListener("click", showIdentity);
  $("#withdraw-confirm").addEventListener("click", withdrawProposal);
  $("#withdraw-cancel").addEventListener("click", () => { if (!state.withdrawalPending) $("#withdraw-dialog").close(); });
  $("#withdraw-dialog").addEventListener("cancel", (event) => { if (state.withdrawalPending) event.preventDefault(); });
  $("#withdraw-dialog").addEventListener("close", () => { if (!state.withdrawalPending) state.withdrawing = null; });
  $("#organisation-button").addEventListener("click", () => $("#organisation-dialog").showModal());
  $("#preview-dialog").addEventListener("close", clearPreview);
  $("#preview-dialog").addEventListener("cancel", clearPreview);
  $("#identity-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      const data = await api("/api/identity", { method: "POST", body: { nickname: $("#nickname-input").value.trim(), invitation_code: $("#invitation-input").value.trim() || undefined } });
      state.session.guest = data.guest || data; state.session = await api("/api/session"); syncSession();
      $("#identity-dialog").close(); toast(`Bienvenue ${state.session.guest.nickname} !`); await loadProposals();
    } catch (error) { message($("#identity-feedback"), error.message, "error"); }
    finally { button.disabled = false; }
  });
  $("#manual-form").addEventListener("submit", (event) => { event.preventDefault(); submitProposal({ kind: "manual", title: $("#manual-title").value.trim(), artist: $("#manual-artist").value.trim() }, event.submitter); });
  $("#link-form").addEventListener("submit", (event) => { event.preventDefault(); submitProposal({ kind: "youtube", url: $("#link-input").value.trim() }, event.submitter); });
  $("#search-form").addEventListener("submit", (event) => { event.preventDefault(); window.clearTimeout(state.searchTimer); searchMusic($("#search-input").value.trim(), ++state.searchSerial); });
  $("#search-input").addEventListener("input", (event) => {
    window.clearTimeout(state.searchTimer); state.searchController?.abort(); const serial = ++state.searchSerial, query = event.target.value.trim();
    $("#search-form button").disabled = !!state.session?.settings?.offline_mode;
    if (query.length < 3) { $("#search-results").replaceChildren(); message($("#entry-feedback"), ""); return; }
    state.searchTimer = window.setTimeout(() => searchMusic(query, serial), 800);
  });
  $("#filter-all").addEventListener("click", () => { state.mine = false; renderProposals(); });
  $("#filter-mine").addEventListener("click", async () => { if (!await ensureIdentity()) return; state.mine = true; renderProposals(); });
  $("#catalogue-query").addEventListener("input", (event) => { state.query = event.target.value; renderProposals(); });
  $("#catalogue-sort").addEventListener("change", (event) => { state.sort = event.target.value; renderProposals(); });
  $("#refresh-proposals").addEventListener("click", async (event) => { event.currentTarget.disabled = true; try { state.session = await api("/api/session"); syncSession(); await loadProposals(); toast("La playlist est à jour."); } catch (error) { toast(error.message); } finally { $("#refresh-proposals").disabled = false; } });
  $("#share-button").addEventListener("click", () => { $("#share-url").value = state.session?.settings?.public_url || window.location.href.split("#")[0]; $("#share-qr").classList.toggle("hidden", !!cloud() || !state.session?.settings?.public_url); if (!cloud() && state.session?.settings?.public_url) $("#share-qr").src = "/api/qr?scope=public"; $("#share-dialog").showModal(); });
  $("#copy-share").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("#share-url").value); message($("#share-feedback"), "Lien copié. À vous de le partager !", "success"); } catch { $("#share-url").select(); message($("#share-feedback"), "Sélectionnez et copiez le lien affiché avec votre navigateur."); } });
  async function boot() {
    try { state.session = await api("/api/session"); syncSession(); await loadProposals(); }
    catch (error) { $("#global-notice").textContent = error.message; $("#global-notice").classList.remove("hidden"); $("#catalogue-empty").classList.remove("hidden"); $("#catalogue-empty h3").textContent = "La playlist n’a pas pu être chargée."; $("#catalogue-empty p").textContent = "Utilisez Actualiser lorsque la connexion revient."; }
  }
  boot();
  window.setInterval(() => { if (!document.hidden && state.session) loadProposals().catch(() => {}); }, 30000);
})();
