import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {webcrypto} from "node:crypto";

const source = fs.readFileSync(new URL("../playlist25/static/cloud.js", import.meta.url), "utf8");
const publicKey = "sb_publishable_synthetic_test_key";
const token1 = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.fixture_signature";
const token2 = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZWZyZXNoZWQifQ.refreshed_signature";
const categoryId = "a".repeat(32), guestStore = new Map(), authStore = new Map(), calls = [];
const authKey = "playlist25-organizer-session-v1:https://testing-project.supabase.co";
let allowed = true, authStatus = 200, sessionUnauthorized = false, holdRefresh = null;
function storage(map) { return {getItem: key => map.get(key) || null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key)}; }
const context = {
  window: {PLAYLIST_CLOUD: {url: "https://testing-project.supabase.co", publicKey}},
  localStorage: storage(guestStore), sessionStorage: storage(authStore), URL, URLSearchParams,
  crypto: webcrypto, AbortController, setTimeout, clearTimeout, location: {href: "https://example.github.io/Ekoraid/"},
  atob: value => Buffer.from(value, "base64").toString("utf8"),
  fetch: async (destination, options) => {
    const url = new URL(destination), body = JSON.parse(options.body);
    assert.equal(url.origin, "https://testing-project.supabase.co");
    assert.equal(options.headers.apikey, publicKey);
    assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error");
    calls.push({url, options, body});
    let status = 200, data = {};
    if (url.pathname === "/auth/v1/token") {
      status = authStatus;
      if (url.searchParams.get("grant_type") === "refresh_token" && holdRefresh) await holdRefresh;
      data = status === 200 ? {access_token: url.searchParams.get("grant_type") === "refresh_token" ? token2 : token1,
        refresh_token: "opaque_fixture_refresh", expires_in: 3600} : {error_code: "email_not_confirmed", msg: "Email not confirmed"};
    } else if (url.pathname === "/auth/v1/signup") data = {user: {id: "fixture-account"}};
    else if (url.pathname === "/auth/v1/logout") status = 204;
    else if (url.pathname.endsWith("playlist25_organizer_session")) {
      if (sessionUnauthorized) { status = 401; sessionUnauthorized = false; }
      data = allowed ? {authenticated: true, organizer: {id: "fixture-account", email: "owner@example.invalid"}} : {authenticated: false, organizer: null};
    } else if (url.pathname.endsWith("playlist25_organizer_category")) data = {item: {id: categoryId, ...body.p_category}, categories: []};
    else if (url.pathname.endsWith("playlist25_proposal_categories") || url.pathname.endsWith("playlist25_organizer_proposal_categories")) data = {item: {id: body.p_proposal_id, category_ids: body.p_category_ids}};
    else if (url.pathname.endsWith("playlist25_categories_list")) data = {categories: [{id: categoryId, name: "Accueil", active: true, sort_order: 0}]};
    else if (url.pathname.endsWith("playlist25_category_suggestions")) data = {items: [{name: "Accueil", reason: "Suggestion privée"}]};
    else if (url.pathname.endsWith("playlist25_session")) data = {guest: null, settings: {}};
    else throw new Error(`Unexpected route: ${url.pathname}`);
    return {ok: status >= 200 && status < 300, status, json: async () => data};
  },
};
vm.runInNewContext(source, context);
const provider = context.window.PlaylistCloudProvider;
assert.equal((await provider.request("/api/organizer/session")).authenticated, false);
assert.equal(calls.length, 0, "No organizer session must require neither guest storage nor an Auth request");
const publicCategories = await provider.request("/api/categories");
assert.equal(publicCategories.categories[0].id, categoryId);
assert.equal(calls.at(-1).options.headers.Authorization, undefined);
const guestPick = await provider.request("/api/proposals/1/categories", {method: "PATCH", body: {category_ids: [categoryId]}});
assert.equal(guestPick.item.id, 1); assert.equal(calls.at(-1).url.pathname.endsWith("playlist25_proposal_categories"), true);
assert.match(calls.at(-1).body.p_token, /^[0-9a-f]{64}$/);
const guestToken = calls.at(-1).body.p_token;
await assert.rejects(provider.request("/api/categories", {method: "POST", body: {name: "No auth"}}), error => error.status === 401);
await assert.rejects(provider.request("/api/categories/suggestions"), error => error.status === 401);
const beforeInvalid = calls.length;
for (const category_ids of [[categoryId, categoryId], ["invalid"], [1], null])
  await assert.rejects(provider.request("/api/proposals/1/categories", {method: "PATCH", body: {category_ids}}));
await assert.rejects(provider.request("/api/proposals/9007199254740993/categories", {method: "PATCH", body: {category_ids: []}}));
await assert.rejects(provider.request("/api/categories/" + categoryId, {method: "DELETE"}));
assert.equal(calls.length, beforeInvalid);

const input = {email: " Owner@Example.Invalid ", password: "synthetic-only-password"};
const signup = await provider.request("/api/organizer/signup", {method: "POST", body: input});
assert.equal(signup.confirmation_required, true); assert.equal(authStore.size, 0);
assert.equal(calls.at(-1).url.pathname, "/auth/v1/signup");
assert.equal(calls.at(-1).body.email, "owner@example.invalid");
assert.equal(calls.at(-1).url.searchParams.get("redirect_to"), "https://example.github.io/Ekoraid/");
await assert.rejects(provider.request("/api/organizer/signup", {method: "POST", body: {...input, password: "short"}}));
authStatus = 400;
await assert.rejects(provider.request("/api/organizer/login", {method: "POST", body: input}), /Confirmez/);
assert.equal(authStore.size, 0);
authStatus = 200; allowed = false;
await assert.rejects(provider.request("/api/organizer/login", {method: "POST", body: input}), error => error.status === 403);
assert.equal(authStore.size, 0, "Valid Supabase password cannot grant an unapproved account organizer access");
allowed = true;
const loggedIn = await provider.request("/api/organizer/login", {method: "POST", body: input});
assert.equal(loggedIn.authenticated, true);
assert.equal(calls.at(-1).options.headers.Authorization, `Bearer ${token1}`);
assert.equal(authStore.size, 1); assert.equal(guestStore.size, 1);
assert.equal(guestStore.get("playlist25-cloud-guest-v1"), guestToken);
assert.equal(JSON.parse(authStore.get(authKey)).access_token, token1);
assert.ok(!authStore.get(authKey).includes(input.password));
assert.ok([...guestStore.values()].every(value => !value.includes(token1) && !value.includes("opaque_fixture_refresh")));
await provider.request("/api/categories", {method: "POST", body: {name: "Accueil", sort_order: 0}});
assert.equal(calls.at(-1).url.pathname.endsWith("playlist25_organizer_category"), true);
assert.equal(calls.at(-1).options.headers.Authorization, `Bearer ${token1}`);
await provider.request(`/api/categories/${categoryId}`, {method: "PATCH", body: {active: false}});
assert.equal(calls.at(-1).body.p_category.id, categoryId); assert.equal(calls.at(-1).body.p_category.active, false);
await provider.request("/api/categories/suggestions"); assert.equal(calls.at(-1).options.headers.Authorization, `Bearer ${token1}`);
await provider.request("/api/proposals/2/categories", {method: "PATCH", body: {category_ids: []}});
assert.equal(calls.at(-1).url.pathname.endsWith("playlist25_organizer_proposal_categories"), true);
assert.deepEqual(Object.keys(calls.at(-1).body).sort(), ["p_category_ids", "p_proposal_id", "p_token"]);
assert.equal(calls.at(-1).body.p_token, guestToken);
assert.ok(calls.filter(call => !call.url.pathname.startsWith("/auth/v1/")).every(call => !Object.hasOwn(call.body, "password")));

const stored = JSON.parse(authStore.get(authKey)); stored.expires_at = 0; authStore.set(authKey, JSON.stringify(stored));
const beforeRefresh = calls.length;
await Promise.all([provider.request("/api/organizer/session"), provider.request("/api/categories/suggestions")]);
assert.equal(calls.slice(beforeRefresh).filter(call => call.url.searchParams.get("grant_type") === "refresh_token").length, 1);
assert.equal(calls.at(-1).options.headers.Authorization, `Bearer ${token2}`);
sessionUnauthorized = true;
assert.equal((await provider.request("/api/organizer/session")).authenticated, true, "Expired JWT refreshes once on a server401");
const signout = await provider.request("/api/organizer/logout", {method: "POST", body: {}});
assert.equal(signout.authenticated, false); assert.equal(authStore.size, 0);
assert.equal(calls.at(-1).url.pathname, "/auth/v1/logout"); assert.equal(calls.at(-1).options.headers.Authorization, `Bearer ${token2}`);
assert.equal(guestStore.get("playlist25-cloud-guest-v1"), guestToken);

// A refresh completing after Logout may not restore organizer access.
await provider.request("/api/organizer/login", {method: "POST", body: input});
const expiring = JSON.parse(authStore.get(authKey)); expiring.expires_at = 0; authStore.set(authKey, JSON.stringify(expiring));
let release; holdRefresh = new Promise(resolve => {release = resolve;});
const pending = provider.request("/api/categories/suggestions");
await new Promise(resolve => setTimeout(resolve, 0));
await provider.request("/api/organizer/logout", {method: "POST", body: {}});
release(); await assert.rejects(pending, error => error.status === 401); holdRefresh = null;
assert.equal(authStore.size, 0);
assert.equal((await provider.request("/api/organizer/session")).authenticated, false);

// Email confirmation tokens are captured only in tab storage and removed from URL,
// including when that storage is blocked or the response is malformed.
for (const blocked of [false, true]) {
  const callbackStore = new Map(), history = [];
  const callbackContext = {...context, window: {PLAYLIST_CLOUD: context.window.PLAYLIST_CLOUD,
    history: {replaceState: (...args) => history.push(args)}},
    location: {href: `https://example.github.io/Ekoraid/#access_token=${token1}&refresh_token=callback_refresh&expires_in=3600&type=signup`},
    sessionStorage: blocked ? {getItem: () => null, setItem: () => {throw new Error("blocked");}, removeItem: () => {}} : storage(callbackStore)};
  vm.runInNewContext(source, callbackContext);
  assert.equal(history.at(-1)[2], "/Ekoraid/");
  assert.equal(callbackStore.size, blocked ? 0 : 1);
}
console.log("PASS: owner/category routing, private suggestions, confirmed/approved Auth checks, signup without auto-login, JWT only in sessionStorage, refresh/logout races, callback cleanup, no passwords in RPC or tokens in guest storage.");
