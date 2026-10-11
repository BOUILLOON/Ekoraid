import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import {webcrypto} from "node:crypto";

const source = fs.readFileSync(new URL("../playlist25/static/cloud.js", import.meta.url), "utf8");
const storage = new Map(), calls = [];
let searchStatus = 200, videoStatus = 200;
const context = {
  window: {PLAYLIST_CLOUD: {url: "https://testing-project.supabase.co", publicKey: "sb_publishable_synthetic_test_public_key"}},
  URL, crypto: webcrypto, AbortController, setTimeout, clearTimeout,
  location: {href: "https://example.github.io/playlist25/"},
  atob: (value) => Buffer.from(value, "base64").toString("utf8"),
  localStorage: {getItem: (name) => storage.get(name) || null, setItem: (name, value) => storage.set(name, value)},
  fetch: async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({url, options, body});
    let data = {items: []}, status = 200;
    if (url.endsWith("playlist25_session")) data = {guest: null, settings: {max_proposals: 0}};
    if (url.endsWith("playlist25_propose")) data = {item: {id: 1, video_id: body.p_video_id}};
    if (url.endsWith("playlist25_withdraw")) data = {id: body.p_proposal_id, withdrawn: true, state: "archived"};
    if (url.endsWith("playlist25_profile")) data = {guest: {id: "11111111-1111-4111-8111-111111111111", avatar_version: body.p_avatar ? "a".repeat(32) : ""}};
    if (url.endsWith("playlist25_avatars")) data = {items: body.p_ids.map(id => ({id, version: "a".repeat(32), avatar_data: "data:image/png;base64,fixture"}))};
    if (url.endsWith("/music-search")) {
      status = body.action === "video" ? videoStatus : searchStatus;
      data = status !== 200 ? {error: "YouTube refuse la recherche. Utilisez un lien."} : body.action === "video"
        ? {item: {video_id: body.video_id, title: "Fixture vidéo", duration: 222, channel: "Chaîne vidéo"}}
        : {items: [{video_id: "abcDEF_1234", title: "Fixture"}]};
    }
    return {ok: status === 200, status, json: async () => data};
  },
};
vm.runInNewContext(source, context);
const provider = context.window.PlaylistCloudProvider;
assert.equal(provider.enabled(), true);
assert.equal(provider.searchSupported, true);
const session = await provider.request("/api/session");
assert.equal(session.settings.public_url, "https://example.github.io/playlist25/");
assert.equal(session.settings.max_proposals, 0);
const token = calls[0].body.p_token;
assert.match(token, /^[0-9a-f]{64}$/);
await provider.request("/api/identity", {method: "POST", body: {nickname: "Camille"}});
assert.equal(calls[1].body.p_token, token);
for (const url of ["https://youtu.be/abcDEF_1234?t=3", "https://www.youtube.com/watch?v=abcDEF_1234&list=PL1234567890abc", "https://m.youtube.com/watch?v=abcDEF_1234"])
  assert.equal((await provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url, mode: "video", title: "Titre choisi"}})).item.video_id, "abcDEF_1234");
assert.equal(calls.at(-1).body.p_title, "Titre choisi");
assert.equal(calls.at(-1).body.p_duration, 222);
assert.equal(calls.at(-1).body.p_channel, "Chaîne vidéo");
const both = "https://youtube.com/watch?v=abcDEF_1234&list=PL1234567890abc";
await assert.rejects(provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url: both}}), error => error.status === 409 && error.detail.need_choice === true);
const beforePlaylist = calls.length;
await provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url: both, mode: "playlist"}});
assert.equal(calls.at(-1).body.p_kind, "playlist");
assert.equal(calls.at(-1).body.p_playlist_id, "PL1234567890abc");
assert.equal(calls.at(-1).body.p_video_id, null);
assert.equal(calls.at(-1).body.p_duration, null);
assert.equal(calls.length, beforePlaylist + 1);
for (const url of ["http://127.0.0.1/private", "https://youtube.com.evil.example/watch?v=abcDEF_1234", "https://evil@youtube.com/watch?v=abcDEF_1234", "file:///etc/passwd", "https://youtube.com/playlist?list=PL-demo", "https://youtube.com/watch?v=abcDEF_1234&v=otherABC123", "https://example.com/playlist?list=PL1234567890abc", "https://youtube.com/channel/example?list=PL1234567890abc"])
  await assert.rejects(provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url}}));
const known = await provider.request("/api/metadata?url=" + encodeURIComponent("https://youtu.be/abcDEF_1234"));
assert.equal(known.item.duration, 222);
videoStatus = 503;
await provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url: "https://youtu.be/unknown1234"}});
assert.equal(calls.at(-1).body.p_video_id, "unknown1234");
assert.equal(calls.at(-1).body.p_duration, null);
assert.equal(calls.at(-1).body.p_title, null);
await assert.rejects(provider.request("/api/metadata?url=" + encodeURIComponent("https://youtu.be/unknown1234")), /Utilisez un lien/);
videoStatus = 200;
// Zero means unlimited. More than ten ideas must reach the server rather than
// being stopped by a stale browser-side default quota.
const beforeUnlimited = calls.length;
for (let i = 0; i < 12; i++) await provider.request("/api/proposals", {method: "POST", body: {kind: "manual", title: `Idée sans quota ${i}`}});
assert.equal(calls.slice(beforeUnlimited).filter((call) => call.url.endsWith("playlist25_propose")).length, 12);
await assert.rejects(provider.request("/api/downloads", {method: "POST", body: {source_ids: [1]}}));
const withdrawn = await provider.request("/api/proposals/1/withdraw", {method: "POST", body: {nickname: "Camille"}});
assert.equal(withdrawn.id, 1);
assert.equal(withdrawn.withdrawn, true);
assert.equal(calls.at(-1).body.p_token, token);
assert.deepEqual(Object.keys(calls.at(-1).body).sort(), ["p_proposal_id", "p_token"]);
await assert.rejects(provider.request("/api/proposals/9007199254740993/withdraw", {method: "POST"}));
await assert.rejects(provider.request("/api/proposals/1/withdraw", {method: "DELETE"}));
const avatarId = "11111111-1111-4111-8111-111111111111";
const profile = await provider.request("/api/profile", {method: "POST", body: {avatar: "data:image/png;base64,fixture", id: "foreign"}});
assert.equal(profile.guest.id, avatarId);
assert.equal(calls.at(-1).body.p_token, token);
assert.deepEqual(Object.keys(calls.at(-1).body).sort(), ["p_avatar", "p_token"]);
assert.equal(calls.at(-1).body.p_avatar, "data:image/png;base64,fixture");
assert.equal((await provider.request("/api/profile", {method: "POST", body: {avatar: ""}})).guest.avatar_version, "");
const avatars = await provider.request(`/api/avatars?ids=${avatarId},${avatarId}`);
assert.equal(avatars.items.length, 1);
assert.deepEqual(calls.at(-1).body.p_ids, [avatarId]);
assert.deepEqual(Object.keys(calls.at(-1).body), ["p_ids"]);
const beforeInvalidAvatars = calls.length;
assert.equal((await provider.request("/api/avatars")).items.length, 0);
for (const path of ["/api/avatars?ids=foreign", `/api/avatars?ids=${avatarId},`, `/api/avatars?ids=${Array(61).fill(avatarId).join(",")}`])
  await assert.rejects(provider.request(path));
await assert.rejects(provider.request("/api/profile", {method: "POST", body: {avatar: "x".repeat(200001)}}));
await assert.rejects(provider.request("/api/profile", {method: "POST", body: {avatar_data: ""}}));
assert.equal(calls.length, beforeInvalidAvatars);
const search = await provider.request("/api/search?q=Dalida");
assert.equal(search.items.length, 1);
assert.equal(calls.at(-1).body.guest_token, token);
assert.equal(calls.at(-1).options.credentials, "omit");
searchStatus = 503;
await assert.rejects(provider.request("/api/search?q=Dalida"), /Utilisez un lien/);
context.window.PLAYLIST_CLOUD = {url: "https://testing-project.supabase.co", publicKey: "sb_secret_must_never_leave_the_PC"};
const before = calls.length;
await assert.rejects(provider.request("/api/session"), /clé secrète/);
assert.equal(calls.length, before);
context.window.PLAYLIST_CLOUD = {};
assert.equal(provider.enabled(), false);
console.log("PASS: browser token persistence, zero unlimited proposal quota, URL normalization/rejections, guest-only routing/withdrawal token transport, profile ownership/clear, bounded avatar batches, real search transport/fallback, secret-key rejection.");
