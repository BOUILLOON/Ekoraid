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
