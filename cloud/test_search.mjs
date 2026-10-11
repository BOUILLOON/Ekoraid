import assert from "node:assert/strict";
import { initialData, parseYouTubeHTML, parseOfficialSearch, parseDuration } from "./supabase/functions/music-search/parser.mjs";

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
console.log("PASS: real-format YouTube parser, duplicates/live exclusions, absent metadata, strict thumbnails, duration/API parsing, honest blocked-search errors.");
