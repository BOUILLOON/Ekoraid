// Parsers purs : résultats réels seulement. Aucune chanson de démonstration.
export function textOf(value) {
  if (typeof value?.simpleText === "string") return value.simpleText;
  if (Array.isArray(value?.runs)) return value.runs.map((part) => typeof part.text === "string" ? part.text : "").join("");
  return "";
}

export function parseDuration(value) {
  if (typeof value !== "string") return null;
  const clock = /^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})$/.exec(value);
  if (clock && Number(clock[2]) < 60 && Number(clock[3]) < 60)
    return Number(clock[1] || 0) * 3600 + Number(clock[2]) * 60 + Number(clock[3]);
  const iso = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(value);
  if (iso && iso.slice(1).some(Boolean)) return Number(iso[1] || 0) * 3600 + Number(iso[2] || 0) * 60 + Number(iso[3] || 0);
  return null;
}

function safeThumbnail(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && ["i.ytimg.com", "img.youtube.com"].includes(url.hostname)
        && !url.username && !url.password && !url.port) return url.href;
  } catch { /* métadonnée absente */ }
  return "";
}

function initialObject(html, name) {
  if (typeof html !== "string" || html.length > 4 * 1024 * 1024) throw new Error("Réponse de recherche trop volumineuse.");
  const markers = new RegExp(`(?:var\\s+${name}\\s*=|window\\s*\\[\\s*["']${name}["']\\s*\\]\\s*=|(?:^|[;\\s])${name}\\s*=)\\s*`, "g");
  let match;
  while ((match = markers.exec(html))) {
    const start = markers.lastIndex;
    if (html[start] !== "{") continue;
    let depth = 0, quoted = false, escaped = false;
    for (let index = start; index < html.length; index++) {
      const char = html[index];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === "{") depth++;
      else if (char === "}" && --depth === 0) {
        try { return JSON.parse(html.slice(start, index + 1)); } catch { break; }
      }
    }
  }
  throw new Error("YouTube n’a pas fourni de résultats lisibles. Utilisez un lien ou une proposition manuelle.");
}

export function initialData(html) { return initialObject(html, "ytInitialData"); }

export function parseYouTubeVideoHTML(html, expectedId) {
  if (!/^[A-Za-z0-9_-]{11}$/.test(expectedId || "")) throw new Error("Identifiant de vidéo invalide.");
  const data = initialObject(html, "ytInitialPlayerResponse");
  const video = data.videoDetails;
  if (data.playabilityStatus?.status !== "OK" || video?.videoId !== expectedId
      || video.isLive || data.playabilityStatus?.liveStreamability
      || typeof video.title !== "string" || !video.title.trim())
    throw new Error("YouTube n’a pas fourni de métadonnées accessibles pour ce morceau.");
  const duration = /^[0-9]{1,8}$/.test(String(video.lengthSeconds || "")) ? Number(video.lengthSeconds) : null;
  const thumbnails = video.thumbnail?.thumbnails || [];
  return {video_id: expectedId, url: `https://www.youtube.com/watch?v=${expectedId}`,
    title: video.title.replace(/[\x00-\x1f\x7f]/g, " ").trim().slice(0, 300), artist: "",
    channel: String(video.author || "").replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 150),
    duration: duration !== null && duration > 0 && duration < 1e8 ? duration : null,
    thumbnail: safeThumbnail(thumbnails[thumbnails.length - 1]?.url), status: "pending"};
}

export function parseYouTubeHTML(html, limit = 5) {
  const data = initialData(html);
  const items = [], seen = new Set();
  let inspected = 0;
  function visit(node, depth) {
    if (!node || typeof node !== "object" || depth > 60 || inspected++ > 50000 || items.length >= limit) return;
    if (node.adSlotRenderer || node.promotedVideoRenderer || node.advertisementRenderer) return;
    const video = node.videoRenderer;
    if (video && /^[A-Za-z0-9_-]{11}$/.test(video.videoId || "") && !seen.has(video.videoId)) {
      const title = textOf(video.title).trim();
      const live = video.upcomingEventData || (video.badges || []).some((badge) =>
        badge.metadataBadgeRenderer?.style === "BADGE_STYLE_TYPE_LIVE_NOW");
      if (title && !live) {
        seen.add(video.videoId);
        const thumbnails = video.thumbnail?.thumbnails || [];
        items.push({video_id: video.videoId, url: `https://www.youtube.com/watch?v=${video.videoId}`,
          title: title.slice(0, 300), artist: "",
          channel: textOf(video.ownerText || video.longBylineText || video.shortBylineText).slice(0, 150),
          duration: parseDuration(textOf(video.lengthText)),
          thumbnail: safeThumbnail(thumbnails[thumbnails.length - 1]?.url),
          status: "pending"});
      }
    }
    for (const child of Object.values(node)) {
      if (Array.isArray(child)) child.forEach((part) => visit(part, depth + 1));
      else visit(child, depth + 1);
    }
  }
  visit(data, 0);
  return items;
}

export function parseOfficialSearch(search, details = {}) {
  const metadata = new Map((details.items || []).map((item) => [item.id, item]));
  const seen = new Set();
  return (search.items || []).filter((item) => {
    const id = item.id?.videoId;
    if (!/^[A-Za-z0-9_-]{11}$/.test(id || "") || seen.has(id)
        || ["live", "upcoming"].includes(item.snippet?.liveBroadcastContent) || !item.snippet?.title) return false;
    seen.add(id);
    return true;
  }).slice(0, 5).map((item) => {
    const id = item.id.videoId, snippet = item.snippet;
    return {video_id: id, url: `https://www.youtube.com/watch?v=${id}`,
      title: String(snippet.title).slice(0, 300), artist: "", channel: String(snippet.channelTitle || "").slice(0, 150),
      duration: parseDuration(metadata.get(id)?.contentDetails?.duration),
      thumbnail: safeThumbnail(snippet.thumbnails?.medium?.url || snippet.thumbnails?.default?.url), status: "pending"};
  });
}
