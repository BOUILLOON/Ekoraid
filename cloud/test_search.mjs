import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {stripTypeScriptTypes} from "node:module";
import { initialData, parseYouTubeHTML, parseYouTubeSearchPage, parseYouTubeVideoHTML, parseOfficialSearch, parseDuration, pageSearchItems, searchWindow } from "./supabase/functions/music-search/parser.mjs";

// Fixtures synthétiques dans la structure réelle YouTube, jamais données runtime.
const renderer = (id, title = "Titre de test") => ({videoRenderer: {
  videoId: id, title: {runs: [{text: title}]}, ownerText: {runs: [{text: "Chaîne de test"}]},
  lengthText: {simpleText: "3:42"}, thumbnail: {thumbnails: [{url: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`}]},
}});
const valid = renderer("abcDEF_1234", 'Accents éà et } " guillemets');
const live = renderer("liveABC1234");
live.videoRenderer.badges = [{metadataBadgeRenderer: {style: "BADGE_STYLE_TYPE_LIVE_NOW"}}];
const upcoming = renderer("nextABC1234");
upcoming.videoRenderer.upcomingEventData = {startTime: "99999999"};
const noDuration = renderer("nullABC1234");
delete noDuration.videoRenderer.lengthText;
noDuration.videoRenderer.thumbnail.thumbnails[0].url = "http://127.0.0.1/private";
const data = {contents: {twoColumnSearchResultsRenderer: {primaryContents: {sectionListRenderer: {contents: [
  {itemSectionRenderer: {contents: [valid, valid, live, upcoming, noDuration, {playlistRenderer: {playlistId: "PL-demo"}}]}}
]}}}}};
const html = '<html><script>var ytInitialData = ' + JSON.stringify(data) + '; unrelated = {};</script></html>';
assert.deepEqual(initialData(html), data);
const items = parseYouTubeHTML(html);
assert.equal(items.length, 2);
assert.equal(items[0].title, 'Accents éà et } " guillemets');
assert.equal(items[0].channel, "Chaîne de test");
assert.equal(items[0].artist, "");
assert.equal(items[0].duration, 222);
assert.equal(items[1].duration, null);
assert.equal(items[1].thumbnail, "");
assert.throws(() => parseYouTubeHTML("<html>Consentement requis</html>"));
assert.deepEqual(parseYouTubeHTML('<script>window["ytInitialData"] = {"contents":{}};</script>'), []);
assert.equal(parseDuration("1:02:03"), 3723);
assert.equal(parseDuration("PT1H2M3S"), 3723);
assert.equal(parseDuration("3:99"), null);
const official = parseOfficialSearch({items: [{id: {videoId: "abcDEF_1234"}, snippet: {title: "Titre réel fourni", channelTitle: "Chaîne", liveBroadcastContent: "none"}},
  {id: {videoId: "liveABC1234"}, snippet: {title: "Live", liveBroadcastContent: "live"}}]},
  {items: [{id: "abcDEF_1234", contentDetails: {duration: "PT4M"}}]});
assert.equal(official.length, 1);
assert.equal(official[0].duration, 240);
assert.equal(official[0].artist, "");
const player = {playabilityStatus: {status: "OK"}, videoDetails: {videoId: "abcDEF_1234", title: 'Métadonnée } " reçue',
  author: "Chaîne YouTube", lengthSeconds: "222", thumbnail: {thumbnails: [{url: "https://i.ytimg.com/vi/abcDEF_1234/hqdefault.jpg"}]}}};
const videoHTML = response => `<script>var ytInitialPlayerResponse = ${JSON.stringify(response)};</script>`;
const video = parseYouTubeVideoHTML(videoHTML(player), "abcDEF_1234");
assert.equal(video.video_id, "abcDEF_1234");
assert.equal(video.duration, 222);
assert.equal(video.title, 'Métadonnée } " reçue');
assert.equal(video.channel, "Chaîne YouTube");
assert.equal(video.artist, "");
assert.throws(() => parseYouTubeVideoHTML(videoHTML(player), "foreign1234"));
assert.throws(() => parseYouTubeVideoHTML(videoHTML({...player, playabilityStatus: {status: "LOGIN_REQUIRED"}}), "abcDEF_1234"));
assert.throws(() => parseYouTubeVideoHTML("<html>Consent required</html>", "abcDEF_1234"));
assert.throws(() => parseYouTubeVideoHTML(videoHTML({...player, videoDetails: {...player.videoDetails, isLive: true}}), "abcDEF_1234"));
assert.equal(parseYouTubeVideoHTML(videoHTML({...player, videoDetails: {...player.videoDetails, lengthSeconds: "invalid"}}), "abcDEF_1234").duration, null);
const catalogue = Array.from({length: 13}, (_, index) => renderer(String(index).padStart(11, "0"), `Version ${index}`));
const pageHTML = `<script>var ytInitialData = ${JSON.stringify({contents: [...catalogue, catalogue[0], live, upcoming]})};</script>`;
const first = parseYouTubeSearchPage(pageHTML);
const second = parseYouTubeSearchPage(pageHTML, first.next_offset);
const last = parseYouTubeSearchPage(pageHTML, second.next_offset);
assert.equal(first.items.length, 5);
assert.equal(first.has_more, true);
assert.equal(first.next_offset, 5);
assert.equal(second.items.length, 5);
assert.equal(second.next_offset, 10);
assert.equal(last.items.length, 3);
assert.equal(last.has_more, false);
assert.equal(last.next_offset, null);
assert.equal(new Set([...first.items, ...second.items, ...last.items].map(item => item.video_id)).size, 13);
assert.deepEqual(parseYouTubeSearchPage(pageHTML, 20), {items: [], has_more: false, next_offset: null});
assert.deepEqual(pageSearchItems(items), {items, has_more: false, next_offset: null});
for (const [offset, limit] of [[-1, 5], [100, 5], [true, 5], [1.5, 5], ["5", 5], [0, 0], [0, 11], [0, true]])
  assert.throws(() => searchWindow(offset, limit), /Page de recherche invalide/);
const officialWindow = parseOfficialSearch({nextPageToken: "actual-token-with-no-followed-page", items: catalogue.map(item => ({id: {videoId: item.videoRenderer.videoId}, snippet: {title: "Version réelle"}}))}, {}, 50);
assert.equal(officialWindow.length, 13);
assert.equal(pageSearchItems(officialWindow, 10).has_more, false);

// Execute the production Edge handler using Node's TypeScript transformer and
// mocked Deno/fetch APIs. Every network request is intercepted; no cloud data
// or provider quota is touched by these tests.
let handler;
const edgeEnv = new Map([["SUPABASE_URL", "https://fixture.supabase.co"], ["SUPABASE_ANON_KEY", "public-fixture-key"]]);
const actualFetch = globalThis.fetch, actualDeno = globalThis.Deno;
const networkCalls = [];
globalThis.Deno = {env: {get: name => edgeEnv.get(name)}, serve: callback => { handler = callback; }};
globalThis.fetch = async (url, options = {}) => {
  networkCalls.push({url: String(url), options});
  if (String(url).endsWith("/rest/v1/rpc/playlist25_search_budget"))
    return Response.json({allowed: true});
  if (String(url).startsWith("https://www.youtube.com/results?")) return new Response(pageHTML);
  if (String(url).startsWith("https://www.googleapis.com/youtube/v3/search?"))
    return Response.json({nextPageToken: "unused-real-token", items: catalogue.map(item => ({id: {videoId: item.videoRenderer.videoId}, snippet: {title: "Version API"}}))});
  if (String(url).startsWith("https://www.googleapis.com/youtube/v3/videos?")) return Response.json({items: []});
  throw new Error("Unexpected network request in isolated Edge test.");
};
try {
  const sourcePath = new URL("./supabase/functions/music-search/index.ts", import.meta.url);
  const parserURL = new URL("./supabase/functions/music-search/parser.mjs", import.meta.url).href;
  const source = (await readFile(sourcePath, "utf8")).replace('"./parser.mjs"', JSON.stringify(parserURL));
  const javascript = stripTypeScriptTypes(source, {mode: "transform"});
  await import(`data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`);
  const requestSearch = async extra => handler(new Request("https://fixture.supabase.co/functions/v1/music-search", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({query: "Artiste", guest_token: "a".repeat(64), ...extra}),
  }));
  for (const extra of [{offset: -1}, {offset: 100}, {offset: "5"}, {limit: 11}, {limit: true}]) {
    assert.equal((await requestSearch(extra)).status, 422);
  }
  assert.equal(networkCalls.length, 0);
  const edgeFirst = await (await requestSearch({})).json();
  assert.equal(edgeFirst.items.length, 5);
  assert.equal(edgeFirst.next_offset, 5);
  assert.equal(edgeFirst.provider, "youtube-public-search");
  const edgeMore = await (await requestSearch({offset: edgeFirst.next_offset})).json();
  assert.equal(edgeMore.items.length, 5);
  assert.equal(edgeMore.next_offset, 10);
  const edgeLast = await (await requestSearch({offset: edgeMore.next_offset})).json();
  assert.equal(edgeLast.items.length, 3);
  assert.equal(edgeLast.has_more, false);
  assert.equal(edgeLast.next_offset, null);
  edgeEnv.set("YOUTUBE_API_KEY", "private-api-fixture-never-a-real-key");
  const edgeOfficial = await (await requestSearch({offset: 10})).json();
  assert.equal(edgeOfficial.items.length, 3);
  assert.equal(edgeOfficial.provider, "youtube-data-api");
  assert.equal(edgeOfficial.has_more, false);
  assert.equal(edgeOfficial.next_offset, null);
  const apiCall = networkCalls.find(call => call.url.startsWith("https://www.googleapis.com/youtube/v3/search?"));
  assert.equal(new URL(apiCall.url).searchParams.get("maxResults"), "50");
  assert(!JSON.stringify(edgeOfficial).includes("private-api-fixture"));
} finally {
  globalThis.fetch = actualFetch;
  if (actualDeno === undefined) delete globalThis.Deno;
  else globalThis.Deno = actualDeno;
}
console.log("PASS: real search/player metadata, finite pagination and production Edge handler with isolated network mocks, duplicates/live exclusions, invalid-window rejection before budget, secret-key exclusion and honest provider errors.");
