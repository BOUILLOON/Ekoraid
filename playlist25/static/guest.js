"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { session: null, items: [], mine: false, query: "", sort: "popular", page: 1, identityResolve: null, searchSerial: 0, searchTimer: null, searchController: null, searchItems: [], catalogueSerial: 0, linkCheckTimer: null, lastCheckedLink: "", proposalPending: false, manualMatchResolve: null, withdrawing: null, withdrawalPending: false, profileSerial: 0, profileAvatar: "", profileDirty: false, profileProcessing: false, profilePending: false, proposalScopeResolve: null };
  const PAGE_SIZE = 48;
  const sourceMatches = new Map(), manualMatches = new Map();
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
  function duration(seconds) { if (!Number.isFinite(Number(seconds)) || seconds == null || Number(seconds) <= 0) return "Durée à compléter"; const n = Math.max(0, Math.floor(Number(seconds))), hours = Math.floor(n / 3600), minutes = Math.floor(n % 3600 / 60), remainder = String(n % 60).padStart(2, "0"); return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${remainder}` : `${minutes}:${remainder}`; }
  function playlistDuration() {
    const sources = new Map(), playlists = new Set();
    state.items.forEach((item) => { const key = String(item.source_id ?? `proposal-${item.id}`), known = item.duration !== null && item.duration !== undefined && item.duration !== "" && Number.isFinite(Number(item.duration)) && Number(item.duration) > 0; if (!sources.has(key) || known) sources.set(key, known ? Number(item.duration) : null); if (item.source_kind === "playlist") playlists.add(key); });
    const values = Array.from(sources.values()), seconds = Math.floor(values.reduce((total, value) => total + (value || 0), 0)), hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), unknown = values.filter((value) => value === null).length;
    const unknownPlaylists = Array.from(playlists).filter((key) => sources.get(key) === null).length;
    $("#stat-duration").textContent = values.length && values.length === unknown ? "À compléter" : hours ? `${hours.toLocaleString("fr")} h ${String(minutes).padStart(2, "0")} min` : `${minutes} min ${String(seconds % 60).padStart(2, "0")} s`;
    $("#stat-duration-note").textContent = unknown ? `${unknown.toLocaleString("fr")} à compléter${unknownPlaylists ? `, dont ${unknownPlaylists} playlist${unknownPlaylists > 1 ? "s" : ""} à analyser` : ""}` : "Tous les morceaux ont une durée connue";
  }
  function nicknameInitial(nickname) { return Array.from(nickname || "?")[0].toUpperCase(); }
  const AVATAR_SIZE = 128, MAX_PHOTO_BYTES = 10 * 1024 * 1024, MAX_PHOTO_PIXELS = 20000000, AVATAR_CACHE_LIMIT = 200;
  const avatarCache = new Map(), avatarPending = new Set();
  const avatarKey = (id, version) => `${id}:${version}`;
  const validAvatarId = (id) => typeof id === "string" && id.length <= 120 && /^(?:cloud:[a-z0-9-]+:)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
  const validAvatarVersion = (version) => typeof version === "string" && /^[0-9a-f]{32}$/i.test(version);
  const validAvatarData = (value) => typeof value === "string" && value.length <= 200000 && /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value);
  function cachedAvatar(id, version) {
    const key = avatarKey(id, version), cached = avatarCache.get(key);
    if (cached !== undefined) { avatarCache.delete(key); avatarCache.set(key, cached); }
    return cached;
  }
  function cacheAvatar(id, version, data) {
    const key = avatarKey(id, version); avatarCache.delete(key); avatarCache.set(key, data);
    while (avatarCache.size > AVATAR_CACHE_LIMIT) avatarCache.delete(avatarCache.keys().next().value);
  }
  function paintAvatar(node, nickname, data) {
    node.replaceChildren();
    if (validAvatarData(data)) { const image = el("img"); image.src = data; image.alt = ""; image.width = AVATAR_SIZE; image.height = AVATAR_SIZE; node.append(image); }
    else node.textContent = nicknameInitial(nickname);
  }
  function setAvatar(node, person) {
    node.dataset.avatarNickname = person?.nickname || "";
    const id = person?.avatar_id, version = person?.avatar_version;
    if (validAvatarId(id) && validAvatarVersion(version)) { node.dataset.avatarId = id; node.dataset.avatarVersion = version; paintAvatar(node, person?.nickname, cachedAvatar(id, version)); }
    else { delete node.dataset.avatarId; delete node.dataset.avatarVersion; paintAvatar(node, person?.nickname, ""); }
  }
  function paintLoadedAvatars(id, version, data) {
    document.querySelectorAll("[data-avatar-id][data-avatar-version]").forEach((node) => { if (node.dataset.avatarId === id && node.dataset.avatarVersion === version) paintAvatar(node, node.dataset.avatarNickname, data); });
    if (state.session?.guest?.avatar_id === id && state.session.guest.avatar_version === version && $("#profile-dialog").open && !state.profileDirty && !state.profileProcessing) { state.profileAvatar = data; renderProfilePreview(); }
  }
  async function fetchAvatars(people) {
    const wanted = new Map();
    people.forEach((person) => {
      const id = person?.avatar_id, version = person?.avatar_version;
      if (!validAvatarId(id) || !validAvatarVersion(version)) return;
      const key = avatarKey(id, version), cached = cachedAvatar(id, version);
      if (cached !== undefined) { paintLoadedAvatars(id, version, cached); return; }
      if (!avatarPending.has(key)) wanted.set(id, version);
    });
    const ids = Array.from(wanted.keys());
    for (let first = 0; first < ids.length; first += PAGE_SIZE) {
      const batch = ids.slice(first, first + PAGE_SIZE); batch.forEach((id) => avatarPending.add(avatarKey(id, wanted.get(id))));
      try {
        const response = await api(`/api/avatars?ids=${encodeURIComponent(batch.join(","))}`);
        const returned = new Map((response.items || []).map((item) => [item.id, item]));
        batch.forEach((id) => {
          const version = wanted.get(id), item = returned.get(id), data = item?.version === version && validAvatarData(item.avatar_data) ? item.avatar_data : "";
          cacheAvatar(id, version, data); paintLoadedAvatars(id, version, data);
        });
      } catch { /* Initials remain usable if a photo cannot be loaded. */ }
      finally { batch.forEach((id) => avatarPending.delete(avatarKey(id, wanted.get(id)))); }
    }
  }
  function profileButtons() {
    const busy = state.profileProcessing || state.profilePending;
    $("#profile-photo-input").disabled = $("#profile-remove-photo").disabled = busy;
    $("#profile-remove-photo").disabled = busy || !(state.profileAvatar || state.session?.guest?.avatar_version);
    $("#profile-save").disabled = busy || !state.profileDirty;
    $("#profile-cancel").disabled = $("#profile-dialog .modal-close").disabled = !!state.profilePending;
    $("#profile-save").textContent = state.profilePending ? "Enregistrement…" : "Enregistrer ma photo";
  }
  function renderProfilePreview() {
    paintAvatar($("#profile-preview"), state.session?.guest?.nickname, state.profileAvatar);
    $("#profile-photo-status").textContent = state.profileAvatar ? "Aperçu de votre photo, recadrée en carré." : "Votre initiale s’affichera à la place de la photo.";
    profileButtons();
  }
  async function showProfile() {
    if (!state.session?.guest) { showIdentity(); return; }
    const guest = state.session.guest, serial = ++state.profileSerial;
    state.profileDirty = state.profileProcessing = state.profilePending = false;
    state.profileAvatar = cachedAvatar(guest.avatar_id, guest.avatar_version) || "";
    $("#profile-nickname").textContent = guest.nickname; $("#profile-photo-input").value = ""; message($("#profile-feedback"), ""); renderProfilePreview(); $("#profile-dialog").showModal();
    await fetchAvatars([guest]);
    if (serial === state.profileSerial && $("#profile-dialog").open && !state.profileDirty) { state.profileAvatar = cachedAvatar(guest.avatar_id, guest.avatar_version) || ""; renderProfilePreview(); }
  }
  function imageDimensions(buffer) {
    const bytes = new Uint8Array(buffer), view = new DataView(buffer), text = (start, length) => String.fromCharCode(...bytes.subarray(start, start + length));
    if (bytes.length >= 24 && bytes[0] === 137 && text(1, 3) === "PNG" && bytes[4] === 13 && bytes[5] === 10 && bytes[6] === 26 && bytes[7] === 10 && text(12, 4) === "IHDR") return { width: view.getUint32(16), height: view.getUint32(20), type: "image/png" };
    if (bytes.length >= 4 && bytes[0] === 255 && bytes[1] === 216) {
      let offset = 2;
      while (offset + 4 <= bytes.length) {
        if (bytes[offset++] !== 255) break;
        while (bytes[offset] === 255) offset++;
        const marker = bytes[offset++];
        if (marker === 217 || marker === 218) break;
        if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
        if (offset + 2 > bytes.length) break;
        const length = view.getUint16(offset);
        if (length < 2 || offset + length > bytes.length) break;
        if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker) && length >= 8) return { width: view.getUint16(offset + 5), height: view.getUint16(offset + 3), type: "image/jpeg" };
        offset += length;
      }
    }
    if (bytes.length >= 30 && text(0, 4) === "RIFF" && text(8, 4) === "WEBP") {
      let offset = 12;
      while (offset + 8 <= bytes.length) {
        const chunk = text(offset, 4), length = view.getUint32(offset + 4, true), start = offset + 8;
        if (start + length > bytes.length) break;
        if (chunk === "VP8X" && length >= 10) {
          if (bytes[start] & 2) throw new Error("Choisissez une photo fixe, plutôt qu’une image animée.");
          return { width: 1 + bytes[start + 4] + (bytes[start + 5] << 8) + (bytes[start + 6] << 16), height: 1 + bytes[start + 7] + (bytes[start + 8] << 8) + (bytes[start + 9] << 16), type: "image/webp" };
        }
        if (chunk === "VP8L" && length >= 5 && bytes[start] === 47) { const bits = view.getUint32(start + 1, true); return { width: (bits & 16383) + 1, height: ((bits >>> 14) & 16383) + 1, type: "image/webp" }; }
        if (chunk === "VP8 " && length >= 10 && bytes[start + 3] === 157 && bytes[start + 4] === 1 && bytes[start + 5] === 42) return { width: view.getUint16(start + 6, true) & 16383, height: view.getUint16(start + 8, true) & 16383, type: "image/webp" };
        offset = start + length + (length % 2);
      }
    }
    throw new Error("Cette image n’est pas reconnue. Choisissez une photo PNG, JPEG ou WebP.");
  }
  async function prepareProfilePhoto(file) {
    if (file.size > MAX_PHOTO_BYTES) throw new Error("Cette photo dépasse 10 Mo. Choisissez une image plus légère.");
    if (!file.size) throw new Error("Ce fichier est vide. Choisissez une autre photo.");
    const buffer = await file.arrayBuffer(), dimensions = imageDimensions(buffer);
    if (!dimensions.width || !dimensions.height || dimensions.width > 10000 || dimensions.height > 10000 || dimensions.width * dimensions.height > MAX_PHOTO_PIXELS) throw new Error("Cette photo est trop grande. Choisissez une image de moins de 20 millions de pixels, avec des côtés de 10 000 pixels maximum.");
    const blob = new Blob([buffer], { type: dimensions.type }); let image, objectUrl;
    try {
      if (window.createImageBitmap) image = await createImageBitmap(blob, { imageOrientation: "from-image" });
      else { objectUrl = URL.createObjectURL(blob); image = new Image(); image.src = objectUrl; await image.decode(); }
      const width = image.width || image.naturalWidth, height = image.height || image.naturalHeight, side = Math.min(width, height);
      if (!side || width * height > MAX_PHOTO_PIXELS) throw new Error("Cette photo est trop grande pour être préparée dans votre navigateur.");
      const canvas = document.createElement("canvas"); canvas.width = canvas.height = AVATAR_SIZE;
      const context = canvas.getContext("2d"); if (!context) throw new Error("Votre navigateur ne peut pas préparer cette photo. Essayez une autre image ou un autre navigateur.");
      context.imageSmoothingQuality = "high"; context.drawImage(image, (width - side) / 2, (height - side) / 2, side, side, 0, 0, AVATAR_SIZE, AVATAR_SIZE);
      const data = canvas.toDataURL("image/png"); if (!validAvatarData(data)) throw new Error("Cette photo n’a pas pu être préparée. Choisissez une autre image.");
      return data;
    } catch (error) { if (error instanceof Error && error.message.startsWith("Cette photo")) throw error; throw new Error("Cette image ne peut pas être lue. Choisissez une autre photo PNG, JPEG ou WebP."); }
    finally { image?.close?.(); if (objectUrl) URL.revokeObjectURL(objectUrl); }
  }
  async function chooseProfilePhoto(file) {
    if (!file || state.profilePending) return;
    const serial = ++state.profileSerial; state.profileProcessing = true; profileButtons(); message($("#profile-feedback"), "Préparation de la photo…");
    try {
      const data = await prepareProfilePhoto(file);
      if (serial !== state.profileSerial || !$("#profile-dialog").open) return;
      state.profileAvatar = data; state.profileDirty = true; renderProfilePreview(); message($("#profile-feedback"), "Votre photo est prête. Enregistrez-la pour la partager avec les invités.", "success");
    } catch (error) { if (serial === state.profileSerial) message($("#profile-feedback"), error.message, "error"); }
    finally { if (serial === state.profileSerial) { state.profileProcessing = false; profileButtons(); } $("#profile-photo-input").value = ""; }
  }
  async function saveProfile(event) {
    event.preventDefault(); if (!state.profileDirty || state.profilePending || state.profileProcessing) return;
    state.profilePending = true; profileButtons(); message($("#profile-feedback"), "Enregistrement de votre photo…");
    try {
      const result = await api("/api/profile", { method: "POST", body: { avatar: state.profileAvatar || "" } }), guest = result.guest;
      if (!guest) throw new Error("Le profil n’a pas pu être actualisé. Réessayez.");
      const oldId = state.session.guest.avatar_id; Array.from(avatarCache.keys()).filter((key) => key.startsWith(`${oldId}:`)).forEach((key) => avatarCache.delete(key));
      state.session.guest = guest; if (validAvatarVersion(guest.avatar_version)) cacheAvatar(guest.avatar_id, guest.avatar_version, state.profileAvatar);
      state.items.forEach((item) => { if (item.avatar_id === guest.avatar_id) item.avatar_version = guest.avatar_version; });
      syncSession(); renderProposals(); $("#profile-dialog").close(); toast(state.profileAvatar ? "Votre photo de profil est enregistrée." : "Votre photo a été retirée. Vos propositions restent dans la playlist.");
    } catch (error) { message($("#profile-feedback"), error.message, "error"); }
    finally { state.profilePending = false; profileButtons(); }
  }
  function youtubeVideoId(item) {
    if (item.source_kind === "playlist" || item.kind === "playlist") return null;
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
  function youtubeSource(value, mode) {
    if (typeof value !== "string" || value.length > 2048 || /[\x00-\x1f\x7f\\]/.test(value)) return null;
    try {
      const url = new URL(value.trim()), host = url.hostname;
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port || !["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"].includes(host)) return null;
      if (["v", "list"].some((key) => url.searchParams.getAll(key).length > 1)) return null;
      let video = null; const playlist = url.searchParams.get("list");
      if (["youtu.be", "www.youtu.be"].includes(host)) video = /^\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
      else if (["/watch", "/watch/"].includes(url.pathname)) video = url.searchParams.get("v");
      else if (["/playlist", "/playlist/"].includes(url.pathname)) { if (url.searchParams.has("v")) return null; }
      else video = /^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
      if (video !== null && !/^[A-Za-z0-9_-]{11}$/.test(video || "")) return null;
      if (playlist !== null && (!/^[A-Za-z0-9_-]{10,150}$/.test(playlist) || /^(RD|UL|UU)/.test(playlist))) return null;
      if (!video && !playlist || mode === "video" && !video || mode === "playlist" && !playlist) return null;
      const kind = mode || (video && playlist ? "choice" : video ? "video" : "playlist");
      return { kind, video, playlist, key: kind === "video" ? `video:${video}` : kind === "playlist" ? `playlist:${playlist}` : null };
    } catch { return null; }
  }
  const normalText = (value) => (value || "").toLocaleLowerCase("fr").normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^\p{L}\p{N}_]+/gu, " ").trim();
  const manualKey = (title, artist) => JSON.stringify([normalText(title), normalText(artist)]);
  const exactText = (value) => (value || "").trim().toLocaleLowerCase("fr").replace(/\s+/g, " ");
  const exactManualMatch = (item, title, artist) => !item.url && !item.video_id && exactText(item.title) === exactText(title) && exactText(item.artist) === exactText(artist);
  function rebuildMatches() {
    sourceMatches.clear(); manualMatches.clear();
    state.items.forEach((item) => {
      const video = youtubeVideoId(item), source = video ? { key: `video:${video}` } : youtubeSource(item.url);
      if (source?.key) {
        const previous = sourceMatches.get(source.key);
        if (!previous || item.voted && !previous.voted || !!item.voted === !!previous.voted && Number(item.votes || 0) > Number(previous.votes || 0)) sourceMatches.set(source.key, item);
      }
      if (item.source_kind !== "playlist" && normalText(item.title)) { const key = manualKey(item.title, item.artist), matches = manualMatches.get(key) || []; matches.push(item); manualMatches.set(key, matches); }
    });
  }
  function existingSource(body) { const source = youtubeSource(body.url, body.mode); return source?.key ? sourceMatches.get(source.key) : null; }
  function existingLabel(item) {
    if (item.status === "available") return "Déjà dans la playlist · fichier prêt";
    if (["queued", "downloading", "converting", "verifying"].includes(item.status)) return "Déjà dans la playlist · en préparation";
    return "Déjà dans la playlist";
  }
  function filteredProposals() {
    const query = state.query.trim().toLocaleLowerCase("fr");
    return state.items.filter((item) => (!state.mine || item.mine === true) && [item.title, item.artist, item.nickname, item.channel].some((value) => (value || "").toLocaleLowerCase("fr").includes(query)))
      .sort(state.sort === "popular" ? (a, b) => Number(b.votes || 0) - Number(a.votes || 0) || Number(b.id) - Number(a.id) : (a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")) || Number(b.id) - Number(a.id));
  }
  function locateProposal(item) {
    if ($("#manual-match-dialog").open) $("#manual-match-dialog").close();
    state.mine = false; state.query = ""; $("#catalogue-query").value = "";
    const index = filteredProposals().findIndex((candidate) => candidate.id === item.id);
    if (index < 0) { toast("Cette proposition n’est plus dans la playlist. Actualisez pour vérifier."); return; }
    state.page = Math.floor(index / PAGE_SIZE) + 1; renderProposals();
    const card = Array.from(document.querySelectorAll("[data-proposal-id]")).find((node) => node.dataset.proposalId === String(item.id));
    if (card) { card.classList.add("track-highlight"); card.focus({ preventScroll: true }); card.scrollIntoView({ block: "center", behavior: "smooth" }); window.setTimeout(() => card.classList.remove("track-highlight"), 4500); }
  }
  function existingActions(item) {
    const actions = el("div", "button-row existing-actions"), locate = el("button", "button subtle small", "Retrouver ce morceau →");
    locate.type = "button"; locate.dataset.locateId = String(item.id); locate.addEventListener("click", () => locateProposal(item));
    const vote = el("button", "button primary small", item.voted ? "Vote enregistré ♥" : "Voter pour ce morceau ♡");
    vote.type = "button"; vote.dataset.existingVoteId = String(item.id); vote.disabled = !!item.voted || state.session?.settings?.votes_open === false;
    if (state.session?.settings?.votes_open === false) vote.textContent = "Votes fermés";
    vote.addEventListener("click", () => voteFor(item, vote)); actions.append(locate, vote); return actions;
  }
  function showExisting(target, item, text) {
    message(target, text || `${existingLabel(item)}. Inutile de l’ajouter deux fois : retrouvez-le ou votez pour lui.`);
    target.append(el("p", "existing-title", `${item.title || "Morceau proposé"}${item.nickname ? ` · proposé par ${item.nickname}` : ""}`), existingActions(item));
  }
  function refreshEntryMatches() {
    const link = youtubeSource($("#link-input").value), existing = link?.key ? sourceMatches.get(link.key) : null;
    $("#link-form button[type=submit]").disabled = state.proposalPending || !!existing;
    if (existing) showExisting($("#link-existing-feedback"), existing);
    else if (link?.kind === "choice" && (sourceMatches.has(`video:${link.video}`) || sourceMatches.has(`playlist:${link.playlist}`))) message($("#link-existing-feedback"), "Ce morceau ou cette playlist existe déjà. Choisissez ce que vous souhaitez proposer : nous vérifierons votre choix.");
    else message($("#link-existing-feedback"), "");
    const matches = manualMatches.get(manualKey($("#manual-title").value, $("#manual-artist").value)) || [], exactManual = matches.find((item) => exactManualMatch(item, $("#manual-title").value, $("#manual-artist").value));
    $("#manual-form button[type=submit]").disabled = state.proposalPending || !!exactManual;
    if (exactManual) showExisting($("#manual-existing-feedback"), exactManual, "Cette idée avec le même titre et le même artiste est déjà proposée. Retrouvez-la ou votez pour elle.");
    else if (matches.length) showExisting($("#manual-existing-feedback"), matches[0], "Un morceau avec ce titre et cet artiste existe déjà. Vérifiez sa version avant d’ajouter votre idée.");
    else message($("#manual-existing-feedback"), "");
  }
  function renderSearchResults() {
    const results = $("#search-results"); results.replaceChildren();
    state.searchItems.forEach((item) => {
      const row = el("article", "search-result"), image = el("img"); image.src = "./static/placeholder.svg"; image.alt = "";
      const description = el("div", "search-result-text"); description.append(el("h3", "", item.title), el("p", "muted", `${item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Artiste non renseigné")} · ${duration(item.duration)}`));
      const source = el("a", "result-source", "Voir sur YouTube →"); source.href = item.url; source.target = "_blank"; source.rel = "noopener noreferrer";
      if (/^https:\/\/(www\.)?youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}$/.test(item.url || "")) description.append(source);
      const actions = el("div", "search-result-actions"), preview = previewButton(item), existing = existingSource(item);
      if (preview) actions.append(preview);
      if (existing) { description.append(el("span", "mini-tag existing-tag", existingLabel(existing))); actions.append(existingActions(existing)); }
      else { const add = el("button", "button primary small", "Ajouter ce morceau ＋"); add.type = "button"; add.disabled = state.proposalPending; add.addEventListener("click", () => submitProposal({ kind: "youtube", url: item.url, title: item.title, artist: item.artist || "" }, add)); actions.append(add); }
      row.append(image, description, actions); results.append(row);
    });
  }
  function chooseManualVersion(matches) {
    const list = $("#manual-match-list"); list.replaceChildren();
    matches.slice(0, 3).forEach((item) => { const row = el("div", "manual-match-row"); row.append(el("strong", "", item.title), el("p", "muted", item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Artiste à préciser")), existingActions(item)); list.append(row); });
    if (matches.length > 3) list.append(el("p", "muted", `${matches.length - 3} autre(s) proposition(s) avec ce titre et cet artiste dans la playlist.`));
    $("#manual-match-dialog").showModal(); $("#manual-match-cancel").focus();
    return new Promise((resolve) => { state.manualMatchResolve = resolve; });
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
    setAvatar($("#guest-avatar"), guest); fetchAvatars(guest ? [guest] : []);
    const maxProposals = Number(settings.max_proposals ?? 0);
    $("#quota-label").textContent = maxProposals > 0 ? `${maxProposals} idées par invité` : "Autant d’idées que vous voulez";
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
    state.searchItems = []; $("#search-results").replaceChildren(); refreshEntryMatches();
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
    state.searchItems = []; results.replaceChildren(); message(target, "On cherche les morceaux disponibles…");
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}`, { signal: state.searchController.signal });
      if (serial !== state.searchSerial) return;
      message(target, data.items?.length ? "Choisissez votre version : original, live ou remix. Si elle est déjà proposée, retrouvez-la ou votez pour elle." : "Aucun résultat disponible. Vous pouvez proposer le titre manuellement.");
      state.searchItems = data.items || []; renderSearchResults();
      if (!data.items?.length) youtubeFallback(query);
    } catch (error) {
      if (serial !== state.searchSerial || error.name === "AbortError") return;
      message(target, `${error.message} Vous pouvez toujours coller un lien YouTube ou proposer un titre.`, "error"); youtubeFallback(query);
    } finally { if (serial === state.searchSerial) submit.disabled = !!state.session?.settings?.offline_mode; }
  }
  function chooseProposalScope() { $("#proposal-scope-dialog").showModal(); return new Promise((resolve) => { state.proposalScopeResolve = resolve; }); }
  function ambiguousYoutubeLink(value) { return youtubeSource(value)?.kind === "choice"; }
  async function submitProposal(body, button) {
    if (state.proposalPending) return;
    if (body.kind === "youtube" && !body.mode && ambiguousYoutubeLink(body.url)) { const mode = await chooseProposalScope(); if (!mode) { message($("#entry-feedback"), "Ajout annulé. Vous pouvez choisir un autre lien."); return; } body = { ...body, mode }; }
    state.proposalPending = true; button.disabled = true; refreshEntryMatches();
    message($("#entry-feedback"), "Vérification de la playlist…");
    try {
      // A fresh snapshot catches ideas added by another guest since the last refresh.
      // The server still decides duplicates atomically if an addition races this check.
      try { await loadProposals(); } catch { /* Submission retains the normal server validation when a refresh fails. */ }
      const matches = body.kind === "manual" ? manualMatches.get(manualKey(body.title, body.artist)) || [] : [];
      const existing = body.kind === "manual" ? matches.find((item) => exactManualMatch(item, body.title, body.artist)) : existingSource(body);
      if (existing) { showExisting($("#entry-feedback"), existing); return; }
      if (matches.length && !await chooseManualVersion(matches)) { message($("#entry-feedback"), "Ajout annulé. Le morceau existant reste dans la playlist."); return; }
      if (!await ensureIdentity()) return;
      message($("#entry-feedback"), "Ajout du morceau…");
      const data = await api("/api/proposals", { method: "POST", body });
      if (data.duplicate) {
        const duplicate = typeof data.duplicate === "object" ? data.duplicate : data.item;
        if (duplicate?.id) showExisting($("#entry-feedback"), duplicate, data.message);
        else message($("#entry-feedback"), data.message || "Ce morceau est déjà proposé. Retrouvez-le dans la playlist pour voter.");
      } else {
        const detail = Array.isArray(data.similar) && data.similar.length ? " Un titre proche existe déjà ; l’organisation vérifiera les versions." : "";
        message($("#entry-feedback"), (data.message || "Votre idée est dans la playlist. Merci !") + detail, "success");
        toast("Un bon son de plus dans la playlist !");
        if (body.kind === "manual") $("#manual-form").reset();
        if (body.kind === "youtube") $("#link-form").reset();
      }
      await loadProposals();
    } catch (error) {
      if (error.detail?.need_choice && !body.mode) { const mode = await chooseProposalScope(); if (mode) { state.proposalPending = false; await submitProposal({ ...body, mode }, button); } else message($("#entry-feedback"), "Ajout annulé. Vous pouvez choisir un autre lien."); }
      else message($("#entry-feedback"), error.message, "error");
    }
    finally { state.proposalPending = false; button.disabled = false; refreshEntryMatches(); renderSearchResults(); }
  }
  async function voteFor(item, button) {
    if (!await ensureIdentity()) return;
    button.disabled = true;
    try {
      const result = await api(`/api/proposals/${item.id}/vote`, { method: "POST", body: { active: !item.voted } });
      const found = state.items.find((track) => track.id === item.id);
      if (found) { found.votes = result.votes; found.voted = result.voted; }
      item.votes = result.votes; item.voted = result.voted;
      renderProposals(); refreshEntryMatches(); renderSearchResults();
      toast(result.voted ? "Votre vote est enregistré ♡" : "Votre vote a été retiré.");
    } catch (error) { toast(error.message); }
    finally {
      button.disabled = button.dataset.existingVoteId ? !!item.voted || state.session?.settings?.votes_open === false : false;
      if (button.dataset.existingVoteId) button.textContent = state.session?.settings?.votes_open === false ? "Votes fermés" : item.voted ? "Vote enregistré ♥" : "Voter pour ce morceau ♡";
    }
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
    const items = filteredProposals();
    const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
    state.page = Math.min(pages, Math.max(1, state.page));
    const first = (state.page - 1) * PAGE_SIZE;
    $("#proposal-pagination").classList.toggle("hidden", items.length <= PAGE_SIZE);
    $("#proposal-page-label").textContent = `Page ${state.page} sur ${pages}`;
    $("#proposal-range-label").textContent = `${first + 1}–${Math.min(first + PAGE_SIZE, items.length)} sur ${items.length} morceaux`;
    $("#proposal-prev").disabled = state.page === 1;
    $("#proposal-next").disabled = state.page === pages;
    playlistDuration();
    $("#stat-tracks").textContent = state.items.length;
    $("#stat-votes").textContent = state.items.reduce((total, item) => total + Number(item.votes || 0), 0);
    $("#stat-mine").textContent = mine.length;
    $("#count-all").textContent = state.items.length;
    $("#count-mine").textContent = mine.length;
    $("#filter-all").classList.toggle("active", !state.mine); $("#filter-mine").classList.toggle("active", state.mine);
    const list = $("#proposal-list"); list.replaceChildren();
    items.slice(first, first + PAGE_SIZE).forEach((item, localIndex) => {
      const index = first + localIndex;
      const card = el("article", "track-card"); card.dataset.proposalId = String(item.id); card.tabIndex = -1;
      const visual = el("div", `track-art track-art-${index % 4}`); const image = el("img"); image.src = "./static/placeholder.svg"; image.alt = ""; image.loading = "lazy"; visual.append(image, el("span", "track-number", String(index + 1).padStart(2, "0")));
      const content = el("div", "track-content"); content.append(el("h3", "track-title", item.title || "Titre à retrouver"));
      content.append(el("p", "track-artist", item.artist || (item.channel ? `Chaîne : ${item.channel}` : "Artiste à préciser")));
      const details = el("div", "track-details"); details.append(el("span", "", duration(item.duration)));
      if (item.source_kind === "playlist") details.append(el("span", "mini-tag playlist-tag", "Playlist"));
      else if (!item.url) details.append(el("span", "mini-tag", "Une idée à retrouver"));
      else if (item.state === "approved") details.append(el("span", "mini-tag approved", "Validé"));
      content.append(details);
      if (item.source_kind === "playlist" && /^https:\/\/www\.youtube\.com\/playlist\?list=[A-Za-z0-9_-]{2,200}$/.test(item.url || "")) { const source = el("a", "result-source", "Voir la playlist sur YouTube →"); source.href = item.url; source.target = "_blank"; source.rel = "noopener noreferrer"; const actions = el("div", "track-source-actions"); actions.append(source); content.append(actions); }
      const preview = previewButton(item);
      if (preview) {
        const sourceActions = el("div", "track-source-actions"), source = el("a", "result-source", "Voir sur YouTube →");
        source.href = `https://www.youtube.com/watch?v=${youtubeVideoId(item)}`; source.target = "_blank"; source.rel = "noopener noreferrer";
        sourceActions.append(preview, source); content.append(sourceActions);
      }
      const bottom = el("div", "track-bottom"); const author = el("span", "track-author"), avatar = el("span", "tiny-avatar"); setAvatar(avatar, item); author.append(avatar, document.createTextNode(item.nickname || "Organisation"));
      const button = el("button", `vote-button${item.voted ? " voted" : ""}`); button.type = "button"; button.setAttribute("aria-label", `${item.voted ? "Retirer mon vote pour" : "Voter pour"} ${item.title}`); button.setAttribute("aria-pressed", String(!!item.voted)); button.append(el("span", "heart", item.voted ? "♥" : "♡"), el("span", "", String(item.votes || 0))); button.disabled = state.session?.settings?.votes_open === false; button.addEventListener("click", () => voteFor(item, button)); bottom.append(author, button);
      card.append(visual, content, bottom);
      if (item.mine === true) {
        const ownActions = el("div", "track-own-actions");
        const withdraw = el("button", "withdraw-button", "Retirer ma proposition"); withdraw.type = "button"; withdraw.dataset.withdrawId = String(item.id); withdraw.setAttribute("aria-label", `Retirer ma proposition : ${item.title}`); withdraw.addEventListener("click", () => requestWithdrawal(item)); ownActions.append(withdraw); card.append(ownActions);
      }
      list.append(card);
    });
    fetchAvatars(items.slice(first, first + PAGE_SIZE));
    $("#catalogue-empty").classList.toggle("hidden", items.length > 0);
    $("#catalogue-empty h3").textContent = state.items.length ? "Aucun morceau pour cette recherche." : "Le premier morceau, c’est peut-être le vôtre.";
    $("#catalogue-empty p").textContent = state.mine ? "Vos propositions apparaîtront ici après votre premier ajout." : query ? "Essayez un autre titre, un artiste ou un pseudo." : "Ajoutez une idée pour lancer la playlist.";
    rebuildMatches(); refreshEntryMatches(); renderSearchResults();
  }
  async function loadProposals() {
    const serial = ++state.catalogueSerial;
    const data = await api("/api/proposals?sort=popular");
    if (serial !== state.catalogueSerial) return;
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
  $("#identity-button").addEventListener("click", showProfile);
  $("#profile-photo-input").addEventListener("change", (event) => chooseProfilePhoto(event.target.files[0]));
  $("#profile-remove-photo").addEventListener("click", () => { if (state.profilePending || state.profileProcessing) return; state.profileAvatar = ""; state.profileDirty = true; renderProfilePreview(); message($("#profile-feedback"), "Enregistrez pour retirer votre photo. Vos propositions et vos votes restent conservés."); });
  $("#profile-form").addEventListener("submit", saveProfile);
  $("#profile-cancel").addEventListener("click", () => { if (!state.profilePending) $("#profile-dialog").close(); });
  $("#profile-dialog").addEventListener("cancel", (event) => { if (state.profilePending) event.preventDefault(); });
  $("#profile-dialog").addEventListener("close", () => { state.profileSerial++; state.profileProcessing = false; state.profileDirty = false; $("#profile-photo-input").value = ""; });
  $("#withdraw-confirm").addEventListener("click", withdrawProposal);
  $("#withdraw-cancel").addEventListener("click", () => { if (!state.withdrawalPending) $("#withdraw-dialog").close(); });
  $("#withdraw-dialog").addEventListener("cancel", (event) => { if (state.withdrawalPending) event.preventDefault(); });
  $("#withdraw-dialog").addEventListener("close", () => { if (!state.withdrawalPending) state.withdrawing = null; });
  $("#organisation-button").addEventListener("click", () => $("#organisation-dialog").showModal());
  [["#proposal-choice-video", "video"], ["#proposal-choice-playlist", "playlist"]].forEach(([selector, mode]) => $(selector).addEventListener("click", () => { const resolve = state.proposalScopeResolve; state.proposalScopeResolve = null; resolve?.(mode); $("#proposal-scope-dialog").close(); }));
  $("#proposal-scope-dialog").addEventListener("close", () => { state.proposalScopeResolve?.(null); state.proposalScopeResolve = null; });
  $("#manual-match-confirm").addEventListener("click", () => { const resolve = state.manualMatchResolve; state.manualMatchResolve = null; resolve?.(true); $("#manual-match-dialog").close(); });
  $("#manual-match-cancel").addEventListener("click", () => $("#manual-match-dialog").close());
  $("#manual-match-dialog").addEventListener("close", () => { state.manualMatchResolve?.(false); state.manualMatchResolve = null; });
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
  $("#link-input").addEventListener("input", () => {
    window.clearTimeout(state.linkCheckTimer); refreshEntryMatches();
    const value = $("#link-input").value.trim();
    if (value === state.lastCheckedLink || !youtubeSource(value)) return;
    state.linkCheckTimer = window.setTimeout(async () => { state.lastCheckedLink = value; try { await loadProposals(); } catch { /* The existing snapshot and server duplicate protection remain available. */ } }, 550);
  });
  ["#manual-title", "#manual-artist"].forEach((selector) => $(selector).addEventListener("input", refreshEntryMatches));
  $("#search-form").addEventListener("submit", (event) => { event.preventDefault(); window.clearTimeout(state.searchTimer); searchMusic($("#search-input").value.trim(), ++state.searchSerial); });
  $("#search-input").addEventListener("input", (event) => {
    window.clearTimeout(state.searchTimer); state.searchController?.abort(); const serial = ++state.searchSerial, query = event.target.value.trim();
    state.searchItems = []; $("#search-results").replaceChildren();
    $("#search-form button").disabled = !!state.session?.settings?.offline_mode;
    if (query.length < 3) { $("#search-results").replaceChildren(); message($("#entry-feedback"), ""); return; }
    state.searchTimer = window.setTimeout(() => searchMusic(query, serial), 800);
  });
  $("#filter-all").addEventListener("click", () => { state.mine = false; state.page = 1; renderProposals(); });
  $("#filter-mine").addEventListener("click", async () => { if (!await ensureIdentity()) return; state.mine = true; state.page = 1; renderProposals(); });
  $("#catalogue-query").addEventListener("input", (event) => { state.query = event.target.value; state.page = 1; renderProposals(); });
  $("#catalogue-sort").addEventListener("change", (event) => { state.sort = event.target.value; state.page = 1; renderProposals(); });
  [["#proposal-prev", -1], ["#proposal-next", 1]].forEach(([selector, direction]) => $(selector).addEventListener("click", () => { state.page += direction; renderProposals(); $("#propositions").scrollIntoView({ block: "start" }); }));
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
