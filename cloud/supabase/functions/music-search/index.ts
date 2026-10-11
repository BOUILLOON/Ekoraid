// Déployer avec Supabase Edge Functions. Aucun SDK/CDN ni accès service_role.
import { parseOfficialSearch, parseYouTubeHTML, parseYouTubeVideoHTML } from "./parser.mjs";

const cors = { "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS" };

class SearchError extends Error {
  constructor(message: string, public status = 503) { super(message); }
}
function answer(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
}
async function boundedText(response: Response, maximum: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader(), chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    total += value.length;
    if (total > maximum) { await reader.cancel(); throw new SearchError("Réponse de recherche trop volumineuse."); }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let position = 0;
  for (const chunk of chunks) { all.set(chunk, position); position += chunk.length; }
  return new TextDecoder().decode(all);
}
async function remoteJSON(url: string): Promise<Record<string, any>> {
  const response = await fetch(url, {signal: AbortSignal.timeout(8000), redirect: "error"});
  if (!response.ok) throw new SearchError("Le fournisseur de recherche est indisponible ou son quota gratuit est atteint. Utilisez un lien ou un titre.");
  return JSON.parse(await boundedText(response, 1024 * 1024));
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") return new Response(null, {status: 204, headers: cors});
  if (request.method !== "POST") return answer({error: "Méthode non autorisée."}, 405);
  try {
    const raw = await boundedText(new Response(request.body), 4096);
    let body;
    try { body = JSON.parse(raw); } catch { throw new SearchError("Saisie invalide.", 422); }
    const action = body.action || "search";
    const query = typeof body.query === "string" ? body.query.trim() : "";
    if (!["search", "video"].includes(action) || typeof body.guest_token !== "string"
        || !/^[0-9a-f]{64}$/.test(body.guest_token))
      throw new SearchError("Saisie ou identité invalide.", 422);
    if (action === "search" && (query.length < 3 || query.length > 120 || /[\x00-\x1f\x7f]/.test(query)))
      throw new SearchError("Tapez entre 3 et 120 caractères pour chercher.", 422);
    if (action === "video" && (typeof body.video_id !== "string" || !/^[A-Za-z0-9_-]{11}$/.test(body.video_id)))
      throw new SearchError("Ce lien doit désigner un morceau YouTube précis.", 422);
    const supabase = Deno.env.get("SUPABASE_URL") || "";
    const anon = Deno.env.get("SUPABASE_ANON_KEY") || "";
    if (!/^https:\/\/[a-z0-9-]+\.supabase\.co\/?$/.test(supabase) || !anon)
      throw new SearchError("La recherche n’est pas configurée. L’organisateur doit déployer la fonction music-search.");
    const apiKey = Deno.env.get("YOUTUBE_API_KEY") || "";
    const rpcHeaders: Record<string, string> = {"Content-Type": "application/json", "apikey": anon};
    if (anon.startsWith("eyJ")) rpcHeaders.Authorization = `Bearer ${anon}`;
    const budgetResponse = await fetch(`${supabase.replace(/\/$/, "")}/rest/v1/rpc/playlist25_search_budget`, {
      method: "POST", headers: rpcHeaders, body: JSON.stringify({p_token: body.guest_token, p_official: action === "search" && Boolean(apiKey)}),
      signal: AbortSignal.timeout(5000), redirect: "error",
    });
    if (!budgetResponse.ok) throw new SearchError("Les limites de recherche ne sont pas configurées. L’organisateur doit exécuter le script SQL.");
    const budget = JSON.parse(await boundedText(budgetResponse, 8192));
    if (!budget.allowed) throw new SearchError(budget.error || "Recherche momentanément limitée.", budget.status || 429);
    if (action === "video") {
      const headers = {"Accept-Language": "fr-FR,fr;q=0.9", "User-Agent": "Playlist25/1.0"};
      try {
        const endpoint = `https://www.youtube.com/watch?${new URLSearchParams({v: body.video_id, hl: "fr"})}`;
        const response = await fetch(endpoint, {headers, signal: AbortSignal.timeout(8000), redirect: "error"});
        if (!response.ok) throw new SearchError("Métadonnées watch indisponibles.");
        return answer({item: parseYouTubeVideoHTML(await boundedText(response, 3 * 1024 * 1024), body.video_id),
          provider: "youtube-public-video"});
      } catch { /* Certains hébergements refusent watch mais autorisent la recherche publique. */ }
      const endpoint = `https://www.youtube.com/results?${new URLSearchParams({search_query: body.video_id, hl: "fr"})}`;
      const response = await fetch(endpoint, {headers, signal: AbortSignal.timeout(8000), redirect: "error"});
      if (!response.ok) throw new SearchError("YouTube n’a pas fourni les métadonnées de ce lien. La durée restera inconnue jusqu’à vérification.");
      const item = parseYouTubeHTML(await boundedText(response, 3 * 1024 * 1024), 5)
        .find((candidate: {video_id: string}) => candidate.video_id === body.video_id);
      if (!item) throw new SearchError("YouTube n’a pas retourné ce morceau exact. La durée reste inconnue jusqu’à vérification.");
      return answer({item, provider: "youtube-public-search-exact-video"});
    }
    let items;
    if (apiKey) {
      const params = new URLSearchParams({part: "snippet", type: "video", maxResults: "5", q: query, key: apiKey});
      const search = await remoteJSON(`https://www.googleapis.com/youtube/v3/search?${params}`);
      const ids = (search.items || []).map((item: any) => item.id?.videoId)
        .filter((id: unknown) => typeof id === "string" && /^[A-Za-z0-9_-]{11}$/.test(id)).join(",");
      let details = {};
      if (ids) {
        try {
          const parts = new URLSearchParams({part: "contentDetails", id: ids, key: apiKey});
          details = await remoteJSON(`https://www.googleapis.com/youtube/v3/videos?${parts}`);
        } catch { /* Durée absente : conserver les métadonnées de recherche réelles. */ }
      }
      items = parseOfficialSearch(search, details);
    } else {
      const endpoint = `https://www.youtube.com/results?${new URLSearchParams({search_query: query, hl: "fr"})}`;
      const response = await fetch(endpoint, { headers: {"Accept-Language": "fr-FR,fr;q=0.9", "User-Agent": "Playlist25/1.0"},
        signal: AbortSignal.timeout(8000), redirect: "error" });
      if (!response.ok) throw new SearchError("YouTube refuse la recherche externe. Utilisez un lien ou un titre ; l’organisateur peut activer l’API gratuite optionnelle.");
      items = parseYouTubeHTML(await boundedText(response, 3 * 1024 * 1024));
    }
    return answer({items, provider: apiKey ? "youtube-data-api" : "youtube-public-search"});
  } catch (error) {
    if (error instanceof SearchError) return answer({error: error.message}, error.status);
    // Ne jamais renvoyer les exceptions fetch : elles peuvent contenir la clé API.
    return answer({error: "La recherche externe a échoué. Collez un lien ou proposez un titre ; vos saisies sont conservées."}, 503);
  }
});
