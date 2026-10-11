import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import {webcrypto} from "node:crypto";

const source = fs.readFileSync(new URL("../playlist25/static/cloud.js", import.meta.url), "utf8");
const storage = new Map(), calls = [];
let searchStatus = 200;
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
    if (url.endsWith("playlist25_session")) data = {guest: null, settings: {}};
    if (url.endsWith("playlist25_propose")) data = {item: {id: 1, video_id: body.p_video_id}};
    if (url.endsWith("playlist25_withdraw")) data = {id: body.p_proposal_id, withdrawn: true, state: "archived"};
    if (url.endsWith("/music-search")) {status = searchStatus; data = status === 200 ? {items: [{video_id: "abcDEF_1234", title: "Fixture"}]} : {error: "YouTube refuse la recherche. Utilisez un lien."};}
    return {ok: status === 200, status, json: async () => data};
  },
};
vm.runInNewContext(source, context);
const provider = context.window.PlaylistCloudProvider;
assert.equal(provider.enabled(), true);
assert.equal(provider.searchSupported, true);
const session = await provider.request("/api/session");
assert.equal(session.settings.public_url, "https://example.github.io/playlist25/");
const token = calls[0].body.p_token;
assert.match(token, /^[0-9a-f]{64}$/);
await provider.request("/api/identity", {method: "POST", body: {nickname: "Camille"}});
assert.equal(calls[1].body.p_token, token);
for (const url of ["https://youtu.be/abcDEF_1234?t=3", "https://www.youtube.com/watch?v=abcDEF_1234&list=PL-demo", "https://m.youtube.com/watch?v=abcDEF_1234"])
  assert.equal((await provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url, title: "Titre choisi"}})).item.video_id, "abcDEF_1234");
assert.equal(calls.at(-1).body.p_title, "Titre choisi");
for (const url of ["http://127.0.0.1/private", "https://youtube.com.evil.example/watch?v=abcDEF_1234", "https://evil@youtube.com/watch?v=abcDEF_1234", "file:///etc/passwd", "https://youtube.com/playlist?list=PL-demo", "https://youtube.com/watch?v=abcDEF_1234&v=otherABC123"])
  await assert.rejects(provider.request("/api/proposals", {method: "POST", body: {kind: "youtube", url}}));
await assert.rejects(provider.request("/api/downloads", {method: "POST", body: {source_ids: [1]}}));
const withdrawn = await provider.request("/api/proposals/1/withdraw", {method: "POST", body: {nickname: "Camille"}});
assert.equal(withdrawn.id, 1);
assert.equal(withdrawn.withdrawn, true);
assert.equal(calls.at(-1).body.p_token, token);
assert.deepEqual(Object.keys(calls.at(-1).body).sort(), ["p_proposal_id", "p_token"]);
await assert.rejects(provider.request("/api/proposals/9007199254740993/withdraw", {method: "POST"}));
await assert.rejects(provider.request("/api/proposals/1/withdraw", {method: "DELETE"}));
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
console.log("PASS: browser token persistence, URL normalization/rejections, guest-only routing/withdrawal token transport, real search transport/fallback, secret-key rejection.");
