// Tests Edge locaux : requêtes simulées, aucune musique téléchargée.
let handler: (request: Request) => Promise<Response>;
const originalServe = Deno.serve;
Deno.serve = ((callback: (request: Request) => Promise<Response>) => {
  handler = callback;
  return {};
}) as unknown as typeof Deno.serve;
await import("./index.ts");
Deno.serve = originalServe;

function check(value: unknown, message: string) { if (!value) throw new Error(message); }
function searchRequest(query = "Dalida", guest = "a".repeat(64)) {
  return new Request("https://testing-project.supabase.co/functions/v1/music-search", {
    method: "POST", body: JSON.stringify({query, guest_token: guest}) });
}
function videoRequest(video_id = "abcDEF_1234") {
  return new Request("https://testing-project.supabase.co/functions/v1/music-search", {
    method: "POST", body: JSON.stringify({action: "video", video_id, guest_token: "a".repeat(64)}) });
}
const fixtureHTML = '<script>var ytInitialData = {"contents":{"sectionListRenderer":{"contents":[{"itemSectionRenderer":{"contents":[{"videoRenderer":{"videoId":"abcDEF_1234","title":{"runs":[{"text":"Fixture de résultat réellement reçue"}]},"ownerText":{"runs":[{"text":"Une chaîne"}]},"lengthText":{"simpleText":"3:42"}}}]}}]}}};</script>';

Deno.test("Edge bounded routes, server budgets, actual metadata, optional free API and honest failure", async (test) => {
  const originalFetch = globalThis.fetch;
  Deno.env.set("SUPABASE_URL", "https://testing-project.supabase.co");
  Deno.env.set("SUPABASE_ANON_KEY", "sb_publishable_synthetic_test_only");
  Deno.env.delete("YOUTUBE_API_KEY");
  try {
    await test.step("real-shaped public results without paid key", async () => {
      const destinations: string[] = [];
      globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input); destinations.push(url);
        if (url.includes("playlist25_search_budget")) {
          const body = JSON.parse(String(init?.body));
          check(body.p_token === "a".repeat(64) && body.p_official === false, "Server rate budget missing");
          return new Response(JSON.stringify({allowed: true}));
        }
        return new Response(fixtureHTML);
      };
      const response = await handler(searchRequest());
      const result = await response.json();
      check(response.status === 200 && result.items.length === 1, "Real metadata was not parsed");
      check(result.items[0].duration === 222 && result.items[0].channel === "Une chaîne" && result.items[0].artist === "", "Channel must not masquerade as artist");
      check(destinations.every((url) => url.startsWith("https://testing-project.supabase.co/") || url.startsWith("https://www.youtube.com/results?")), "Unexpected remote destination");
    });
    await test.step("quota denial prevents provider request", async () => {
      let requests = 0;
      globalThis.fetch = async () => { requests++; return new Response(JSON.stringify({error: "Limite", status: 429})); };
      const response = await handler(searchRequest());
      check(response.status === 429 && requests === 1, "Provider called after quota denial");
    });
    await test.step("canonical watch metadata with server budget and exact duration", async () => {
      const destinations: string[] = [];
      globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input); destinations.push(url);
        if (url.includes("playlist25_search_budget")) {
          check(JSON.parse(String(init?.body)).p_official === false, "Public watch lookup must not consume optional API quota");
          return new Response(JSON.stringify({allowed: true}));
        }
        return new Response('<script>var ytInitialPlayerResponse = {"playabilityStatus":{"status":"OK"},"videoDetails":{"videoId":"abcDEF_1234","title":"Métadonnée réellement retournée","author":"Chaîne","lengthSeconds":"321"}};</script>');
      };
      const response = await handler(videoRequest());
      const result = await response.json();
      check(response.status === 200 && result.item.duration === 321 && result.item.video_id === "abcDEF_1234", "Actual video metadata absent");
      check(destinations[1] === "https://www.youtube.com/watch?v=abcDEF_1234&hl=fr", "Noncanonical destination fetched");
    });
    await test.step("blocked watch falls back to public search by exact requested video ID", async () => {
      const destinations: string[] = [];
      globalThis.fetch = async (input: RequestInfo | URL) => {
        const url = String(input); destinations.push(url);
        if (url.includes("playlist25_search_budget")) return new Response(JSON.stringify({allowed: true}));
        if (url.startsWith("https://www.youtube.com/watch?")) return new Response("Blocked", {status: 403});
        return new Response(fixtureHTML);
      };
      const response = await handler(videoRequest());
      const result = await response.json();
      check(response.status === 200 && result.item.video_id === "abcDEF_1234" && result.item.duration === 222, "Exact search metadata not retained");
      check(result.provider === "youtube-public-search-exact-video", "Fallback provider missing");
      check(destinations[2] === "https://www.youtube.com/results?search_query=abcDEF_1234&hl=fr", "Fallback searched an untrusted destination or query");
      check(destinations.filter(url => url.includes("playlist25_search_budget")).length === 1, "Fallback consumed budget twice");
    });
    await test.step("malformed watch falls back but approximate search results never match", async () => {
      let exact = true;
      globalThis.fetch = async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("playlist25_search_budget")) return new Response(JSON.stringify({allowed: true}));
        if (url.startsWith("https://www.youtube.com/watch?")) return new Response("Consent required");
        return new Response(exact ? fixtureHTML : fixtureHTML.replaceAll("abcDEF_1234", "other__1234"));
      };
      const found = await handler(videoRequest());
      check(found.status === 200 && (await found.json()).item.duration === 222, "Malformed watch did not fall back to exact search");
      exact = false;
      const response = await handler(videoRequest());
      const result = await response.json();
      check(response.status === 503 && !result.item && typeof result.error === "string", "Approximate result invented metadata for another ID");
    });
    await test.step("video metadata forbids URLs and quota failures before provider access", async () => {
      let requests = 0;
      globalThis.fetch = async () => { requests++; return new Response(JSON.stringify({allowed: false, error: "Budget", status: 429})); };
      check((await handler(videoRequest("https://127.0.0.1/private"))).status === 422 && requests === 0, "Arbitrary URL reached network");
      check((await handler(videoRequest())).status === 429 && requests === 1, "Video fetched after denied budget");
      globalThis.fetch = async (input) => String(input).includes("playlist25_search_budget")
        ? new Response(JSON.stringify({allowed: true})) : new Response("Forbidden", {status: 403});
      const denied = await handler(videoRequest());
      const result = await denied.json();
      check(denied.status === 503 && !result.item, "Unavailable video invented metadata");
    });
    await test.step("invalid identity and oversized query do not reach network", async () => {
      let requests = 0;
      globalThis.fetch = async () => { requests++; throw new Error("Must not reach network"); };
      check((await handler(searchRequest("x".repeat(121)))).status === 422, "Unbounded query accepted");
      check((await handler(searchRequest("Dalida", "nickname"))).status === 422, "Nickname used as identity");
      check(requests === 0, "Invalid input reached network");
      const huge = new Request("https://testing.invalid", {method: "POST", body: "x".repeat(5000)});
      check((await handler(huge)).status >= 400, "Unbounded body accepted");
    });
    await test.step("blocked provider yields useful empty-free failure", async () => {
      globalThis.fetch = async (input) => String(input).includes("playlist25_search_budget")
        ? new Response(JSON.stringify({allowed: true})) : new Response("Accès refusé", {status: 403});
      const response = await handler(searchRequest());
      const result = await response.json();
      check(response.status === 503 && typeof result.error === "string" && !result.items, "Blocked search invented choices");
    });
    await test.step("optional official key remains server-only", async () => {
      Deno.env.set("YOUTUBE_API_KEY", "synthetic-test-secret-do-not-echo");
      globalThis.fetch = async (input, init) => {
        const url = String(input);
        if (url.includes("playlist25_search_budget")) {
          check(JSON.parse(String(init?.body)).p_official === true, "Daily free API budget not enforced");
          return new Response(JSON.stringify({allowed: true}));
        }
        if (url.includes("/search?")) return new Response(JSON.stringify({items: [{id: {videoId: "abcDEF_1234"}, snippet: {title: "Fixture officielle", channelTitle: "Chaîne", liveBroadcastContent: "none"}}]}));
        return new Response(JSON.stringify({items: [{id: "abcDEF_1234", contentDetails: {duration: "PT5M"}}]}));
      };
      const response = await handler(searchRequest());
      const result = await response.json();
      check(result.items[0].duration === 300 && result.provider === "youtube-data-api", "Official duration absent");
      check(!JSON.stringify(result).includes("synthetic-test-secret"), "API key exposed");
      globalThis.fetch = async () => { throw new Error("fetch URL contains synthetic-test-secret-do-not-echo"); };
      const failed = await handler(searchRequest());
      check(failed.status === 503 && !(await failed.text()).includes("synthetic-test-secret"), "Exception exposed secret");
    });
  } finally {
    globalThis.fetch = originalFetch;
    Deno.env.delete("YOUTUBE_API_KEY");
    Deno.env.delete("SUPABASE_URL");
    Deno.env.delete("SUPABASE_ANON_KEY");
  }
});
