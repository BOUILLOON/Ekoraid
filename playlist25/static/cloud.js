"use strict";

// Aucun SDK distant, cookie d'administration ou clé privilégiée dans cette page.
(() => {
  const TOKEN_NAME = "playlist25-cloud-guest-v1";
  const config = () => window.PLAYLIST_CLOUD || {};
  const fail = (message, status = 400) => {
    const error = new Error(message);
    error.status = status;
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
  function youtubeId(value) {
    let url;
    try { url = new URL(value); } catch { fail("Collez un lien YouTube complet.", 422); }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port)
      fail("Seuls les liens YouTube publics ordinaires sont acceptés.", 422);
    const host = url.hostname.toLowerCase();
    let id;
    if (["youtu.be", "www.youtu.be"].includes(host)) {
      const match = /^\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname);
      id = match?.[1];
    } else if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(host)) {
      if (url.pathname === "/watch" && url.searchParams.getAll("v").length === 1) id = url.searchParams.get("v");
      else id = /^\/(?:shorts|embed)\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
    }
    if (!id || !/^[A-Za-z0-9_-]{11}$/.test(id))
      fail("Ce lien doit désigner un morceau YouTube. Les playlists complètes sont importées par l’organisateur.", 422);
    return id;
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
    if (data?.error) fail(data.error, data.status || 400);
    return data;
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
      const cfg = publicConfig();
      const headers = {"Content-Type": "application/json", apikey: cfg.key};
      if (cfg.legacy) headers.Authorization = `Bearer ${cfg.key}`;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 20000);
      let response, data;
      try {
        response = await fetch(`${cfg.url}/functions/v1/music-search`, {method: "POST", headers,
          body: JSON.stringify({query, guest_token: token}), credentials: "omit", redirect: "error", signal: controller.signal});
        data = await response.json();
      } catch { fail("La recherche externe n’a pas répondu. Collez un lien YouTube ou proposez le titre manuellement.", 503); }
      finally { clearTimeout(timeout); }
      if (!response.ok) {
        if (response.status === 404) fail("La recherche n’est pas encore déployée. Collez un lien ou proposez un titre manuellement.", 503);
        fail(typeof data?.error === "string" ? data.error : "La recherche est indisponible. Collez un lien ou proposez un titre manuellement.", response.status);
      }
      return data;
    }
    if (method === "GET" && route === "/api/session") {
      const session = await rpc("playlist25_session", { p_token: token });
      session.settings.public_url = location.href.split("#")[0].split("?")[0];
      return session;
    }
    if (method === "POST" && route === "/api/identity")
      return rpc("playlist25_identity", { p_token: token, p_nickname: body.nickname,
        p_invitation_code: body.invitation_code || null });
    if (method === "GET" && route === "/api/proposals")
      return rpc("playlist25_list", { p_token: token, p_query: url.searchParams.get("q") || "",
        p_sort: url.searchParams.get("sort") || "popular", p_mine: url.searchParams.get("mine") === "true" });
    if (method === "POST" && route === "/api/proposals") {
      if (!["manual", "youtube"].includes(body.kind)) fail("Type de proposition invalide.", 422);
      if (body.kind === "manual" && body.url) fail("Utilisez l’onglet lien pour une source YouTube.", 422);
      return rpc("playlist25_propose", { p_token: token, p_kind: body.kind,
        p_title: body.title || null, p_artist: body.artist || "",
        p_video_id: body.kind === "youtube" ? youtubeId(body.url) : null });
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
