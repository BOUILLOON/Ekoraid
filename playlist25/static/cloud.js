"use strict";

// Aucun SDK distant, cookie d'administration ou clé privilégiée dans cette page.
(() => {
  const TOKEN_NAME = "playlist25-cloud-guest-v1";
  const config = () => window.PLAYLIST_CLOUD || {};
  const fail = (message, status = 400, detail = null) => {
    const error = new Error(message);
    error.status = status;
    if (detail) error.detail = detail;
    throw error;
  };
  function publicConfig() {
    const cfg = config();
    let url;
    try { url = new URL(cfg.url); } catch { fail("L’adresse Supabase du site n’est pas configurée.", 503); }
    if (url.protocol !== "https:" || !/^[a-z0-9-]+\.supabase\.co$/.test(url.hostname)
        || url.username || url.password || url.port || !["", "/"].includes(url.pathname)
        || url.search || url.hash) fail("Configuration Supabase invalide.", 503);
    if (typeof cfg.publicKey !== "string" || !cfg.publicKey) fail("La clé publique Supabase manque.", 503);
    if (cfg.publicKey.startsWith("sb_publishable_")) return { url: url.origin, key: cfg.publicKey, legacy: false };
    // Refuser explicitement une clé service_role/secret copiée par erreur.
    try {
      const encoded = cfg.publicKey.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      const claims = JSON.parse(atob(encoded));
      if (claims.role === "anon") return { url: url.origin, key: cfg.publicKey, legacy: true };
    } catch { /* message unique, aucune clé recopiée dans une erreur */ }
    fail("Utilisez seulement la clé publishable ou anon. Une clé secrète ne doit jamais être publiée.", 503);
  }
  function guestToken() {
    let token;
    try {
      token = localStorage.getItem(TOKEN_NAME);
      if (!/^[0-9a-f]{64}$/.test(token || "")) {
        const bytes = new Uint8Array(32);
        crypto.getRandomValues(bytes);
        token = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
        localStorage.setItem(TOKEN_NAME, token);
      }
    } catch { fail("Autorisez le stockage de ce site dans votre navigateur pour garder votre pseudo et vos votes.", 503); }
    return token;
  }
  function youtubeSource(value, mode = null) {
    let url;
    if (typeof value !== "string" || value.trim().length > 2048 || /[\x00-\x1f\x7f\\]/.test(value))
      fail("Collez un lien YouTube valide.", 422);
    try { url = new URL(value); } catch { fail("Collez un lien YouTube complet.", 422); }
    if (mode && !["video", "playlist"].includes(mode)) fail("Choisissez ce morceau ou toute la playlist.", 422);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port)
      fail("Seuls les liens YouTube publics ordinaires sont acceptés.", 422);
    const host = url.hostname.toLowerCase();
    if (!["youtu.be", "www.youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(host))
      fail("Seuls les liens YouTube publics ordinaires sont acceptés.", 422);
    let id;
    if (url.searchParams.getAll("v").length > 1 || url.searchParams.getAll("list").length > 1)
      fail("Le lien contient plusieurs identifiants incompatibles.", 422);
    const playlistId = url.searchParams.get("list");
    if (url.searchParams.has("list") && (!/^[A-Za-z0-9_-]{10,150}$/.test(playlistId || "")
        || /^(?:RD|UL|UU)/.test(playlistId) || ["WL", "LL"].includes(playlistId)))
      fail("Utilisez une playlist YouTube ordinaire accessible.", 422);
    if (["youtu.be", "www.youtu.be"].includes(host)) {
      const match = /^\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname);
      id = match?.[1];
      if (!id) fail("Le chemin du lien youtu.be est invalide.", 422);
    } else if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(host)) {
      if (["/watch", "/watch/"].includes(url.pathname)) id = url.searchParams.get("v");
      else if (["/playlist", "/playlist/"].includes(url.pathname)) {
        if (url.searchParams.has("v")) fail("Cette adresse de playlist est invalide.", 422);
      } else {
        id = /^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
        if (!id) fail("Les chaînes et pages YouTube générales ne sont pas acceptées.", 422);
      }
    }
    if (id && !/^[A-Za-z0-9_-]{11}$/.test(id) || !id && !playlistId)
      fail("Ce lien doit désigner un morceau ou une playlist YouTube.", 422);
    if (mode === "video" && !id || mode === "playlist" && !playlistId)
      fail("Le lien ne correspond pas au choix morceau ou playlist.", 422);
    if (id && playlistId && !mode)
      fail("Ce lien contient un morceau et une playlist. Choisissez ce morceau seulement ou toute la playlist.", 409, {need_choice: true});
    return {videoId: id, playlistId, kind: mode === "playlist" || !id ? "playlist" : "youtube"};
  }
  async function rpc(name, payload) {
    const cfg = publicConfig();
    const headers = { "Content-Type": "application/json", apikey: cfg.key };
    if (cfg.legacy) headers.Authorization = `Bearer ${cfg.key}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let response;
    let data;
    try {
      response = await fetch(`${cfg.url}/rest/v1/rpc/${name}`, {
        method: "POST", headers, body: JSON.stringify(payload),
        credentials: "omit", redirect: "error", signal: controller.signal,
      });
      data = await response.json();
    } catch {
      fail("La collecte est momentanément inaccessible. Vérifiez Internet puis réessayez ; vos saisies sont conservées.", 503);
    } finally { clearTimeout(timeout); }
    if (!response.ok) {
      if (response.status === 401 || response.status === 403)
        fail("L’accès Supabase est refusé. L’organisateur doit vérifier la clé publique et le script SQL.", response.status);
      fail("La collecte n’a pas répondu. L’organisateur doit vérifier le projet Supabase et le script SQL.", response.status);
    }
    if (data?.error) fail(data.error, data.status || 400, data.need_choice ? {need_choice: true} : null);
    return data;
  }
  async function edge(payload) {
    const cfg = publicConfig();
    const headers = {"Content-Type": "application/json", apikey: cfg.key};
    if (cfg.legacy) headers.Authorization = `Bearer ${cfg.key}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), payload.action === "video" ? 25000 : 20000);
    let response, data;
    try {
      response = await fetch(`${cfg.url}/functions/v1/music-search`, {method: "POST", headers,
        body: JSON.stringify(payload), credentials: "omit", redirect: "error", signal: controller.signal});
      data = await response.json();
    } catch { fail("YouTube n’a pas répondu. Votre proposition peut être enregistrée avec une durée inconnue.", 503); }
    finally { clearTimeout(timeout); }
    if (!response.ok) {
      if (response.status === 404) fail("La fonction YouTube n’est pas encore déployée. Collez un lien ou proposez un titre manuellement.", 503);
      fail(typeof data?.error === "string" ? data.error : "YouTube est indisponible. Collez un lien ou proposez un titre manuellement.", response.status);
    }
    return data;
  }
  const videoCache = new Map();
  function rememberVideo(item) {
    if (typeof item?.video_id !== "string" || !/^[A-Za-z0-9_-]{11}$/.test(item.video_id)
        || typeof item.title !== "string" || !item.title.trim()) return null;
    const duration = item.duration;
    const metadata = {...item,
      title: item.title.replace(/[\x00-\x1f\x7f]/g, " ").trim().slice(0, 300),
      channel: typeof item.channel === "string" ? item.channel.replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 150) : "",
      duration: typeof duration === "number" && Number.isFinite(duration) && duration > 0 && duration < 1e8 ? duration : null};
    if (!metadata.title) return null;
    const previous = videoCache.get(item.video_id);
    if (metadata.duration === null && previous && Date.now() - previous.created < 300000)
      metadata.duration = previous.item.duration;
    if (!videoCache.has(item.video_id) && videoCache.size >= 200) videoCache.delete(videoCache.keys().next().value);
    videoCache.set(item.video_id, {item: metadata, created: Date.now()});
    return metadata;
  }
  async function videoMetadata(id, token) {
    const cached = videoCache.get(id);
    if (cached && Date.now() - cached.created < 300000) return cached.item;
    const result = await edge({action: "video", video_id: id, guest_token: token});
    if (result?.item?.video_id !== id)
      fail("YouTube n’a pas fourni de métadonnées valides pour ce morceau.", 503);
    const metadata = rememberVideo(result.item);
    if (!metadata) fail("YouTube n’a pas fourni de métadonnées valides pour ce morceau.", 503);
    return metadata;
  }
  async function request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const body = options.body || {};
    const url = new URL(path, "https://playlist.invalid");
    if (url.origin !== "https://playlist.invalid") fail("Route non autorisée.", 404);
    const route = url.pathname;
    const token = guestToken();
    if (method === "GET" && route === "/api/search") {
      const query = (url.searchParams.get("q") || "").trim();
      if (query.length < 3 || query.length > 120) fail("Tapez entre 3 et 120 caractères pour chercher.", 422);
      const result = await edge({query, guest_token: token});
      if (Array.isArray(result?.items)) result.items.forEach(rememberVideo);
      return result;
    }
    if (method === "GET" && route === "/api/metadata") {
      const source = youtubeSource(url.searchParams.get("url"), url.searchParams.get("mode"));
      if (source.kind !== "youtube") fail("La durée d’une playlist sera calculée après son analyse sur le PC.", 422);
      return {item: await videoMetadata(source.videoId, token)};
    }
    if (method === "GET" && route === "/api/session") {
      const session = await rpc("playlist25_session", { p_token: token });
      session.settings.public_url = location.href.split("#")[0].split("?")[0];
      return session;
    }
    if (method === "POST" && route === "/api/identity")
      return rpc("playlist25_identity", { p_token: token, p_nickname: body.nickname,
        p_invitation_code: body.invitation_code || null });
    if (method === "POST" && route === "/api/profile") {
      if (typeof body.avatar !== "string" || body.avatar.length > 200000)
        fail("Photo de profil invalide.", 422);
      return rpc("playlist25_profile", { p_token: token, p_avatar: body.avatar });
    }
    if (method === "GET" && route === "/api/avatars") {
      const rawIds = url.searchParams.get("ids") || "";
      const ids = rawIds ? rawIds.split(",") : [];
      if (ids.length > 60 || ids.some(id => !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)))
        fail("Choisissez jusqu’à 60 profils valides.", 422);
      if (!ids.length) return {items: []};
      return rpc("playlist25_avatars", { p_ids: [...new Set(ids.map(id => id.toLowerCase()))] });
    }
    if (method === "GET" && route === "/api/proposals")
      return rpc("playlist25_list", { p_token: token, p_query: url.searchParams.get("q") || "",
        p_sort: url.searchParams.get("sort") || "popular", p_mine: url.searchParams.get("mine") === "true" });
    if (method === "POST" && route === "/api/proposals") {
      if (!["manual", "youtube", "playlist"].includes(body.kind)) fail("Type de proposition invalide.", 422);
      if (body.kind === "manual" && body.url) fail("Utilisez l’onglet lien pour une source YouTube.", 422);
      const source = body.kind === "manual" ? null : youtubeSource(body.url, body.kind === "playlist" ? "playlist" : body.mode);
      let metadata = null;
      if (source?.kind === "youtube") {
        try { metadata = await videoMetadata(source.videoId, token); }
        catch { /* Le lien reste accepté avec une durée inconnue. */ }
      }
      const duration = metadata?.duration;
      return rpc("playlist25_propose", { p_token: token, p_kind: source?.kind || body.kind,
        p_title: body.title || metadata?.title || null, p_artist: body.artist || "",
        p_video_id: source?.kind === "youtube" ? source.videoId : null,
        p_playlist_id: source?.kind === "playlist" ? source.playlistId : null,
        p_duration: typeof duration === "number" && Number.isFinite(duration) && duration > 0 && duration < 1e8 ? duration : null,
        p_channel: typeof metadata?.channel === "string" ? metadata.channel.slice(0, 150) : "" });
    }
    const withdrawal = /^\/api\/proposals\/([1-9][0-9]*)\/withdraw$/.exec(route);
    if (method === "POST" && withdrawal) {
      const id = Number(withdrawal[1]);
      if (!Number.isSafeInteger(id)) fail("Proposition invalide.", 422);
      return rpc("playlist25_withdraw", {p_token: token, p_proposal_id: id});
    }
    const vote = /^\/api\/proposals\/([1-9][0-9]*)\/vote$/.exec(route);
    if (method === "POST" && vote) {
      if (typeof body.active !== "boolean") fail("Vote invalide.", 422);
      const id = Number(vote[1]);
      if (!Number.isSafeInteger(id)) fail("Proposition invalide.", 422);
      return rpc("playlist25_vote", { p_token: token, p_proposal_id: id, p_active: body.active });
    }
    fail("Cette action est réservée à l’administration sur le PC.", 404);
  }
  window.PlaylistCloudProvider = Object.freeze({
    enabled: () => Boolean(config().url || config().publicKey),
    searchSupported: true,
    request,
  });
})();
