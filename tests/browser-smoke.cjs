/** Browser integration check; mocked YouTube metadata, local generated silent audio.
 * Run from the checkout: node tests/browser-smoke.cjs
 * Requires Playwright and Chromium (CHROMIUM_PATH may select an installed binary).
 * Uses a disposable database, loopback ports and test credentials; no worker runs.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PLAYLIST_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const artifactDir = process.env.PLAYLIST_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-browser-'));
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-browser-data-'));
const password = randomBytes(24).toString('base64url');
const simulatedLanHost = '192.168.50.10';
const report = { youtubeMetadata: 'simulated', audioFixture: 'locally generated one-second silent WAV',
  youtubePlayer: 'minimal locally fulfilled iframe; no YouTube connection or music playback',
  transferNetwork: 'simulated private address routed to loopback with exact Host; real Wi-Fi/phone not tested',
  workerStarted: false, checks: [], pageErrors: [], consoleErrors: [], requestFailures: [], screenshots: [],
  externalRequests: [], mockedEmbedRequests: [], simulatedLanRequests: [] };
const children = [];
const proxies = [];
let browser;

const fixture = `
import sys, uvicorn
from playlist25 import providers, transfers
from playlist25.app import create_app
from playlist25.config import load_settings
cfg=load_settings()
def entry(video_id,position=1):
 return dict(position=position,video_id=video_id,url='https://www.youtube.com/watch?v='+video_id,title='Lien YouTube de test',artist='',channel='Fournisseur simulé',duration=1,thumbnail='',available=True,reason='',already_available=False)
def analyze(url,cfg,mode=None):
 parsed=providers.parse_youtube_url(url,mode)
 if parsed['kind']=='choice':
  raise providers.SourceError('Choisissez une vidéo ou une playlist.',code='need_choice',need_choice=True,status=409)
 entries=[entry(parsed['video_id'])] if parsed['kind']=='video' else [entry('abcdefghijk'),entry('abcdefghijk',2),dict(position=3,video_id=None,url=None,title='Vidéo privée (test)',artist='',channel='',duration=None,thumbnail='',available=False,reason='Indisponible (simulation)',already_available=False)]
 return dict(kind=parsed['kind'],title='Playlist de test' if parsed['kind']=='playlist' else 'Lien YouTube de test',source_url=parsed['url'],complete=True,warnings=[],entries=entries)
providers.analyze=analyze
providers.search=lambda query,cfg: [entry('abcdefghijk')]
providers.analyze_links=lambda text,cfg: dict(kind='links',title='Liens de test',source_url='',complete=True,warnings=[],entries=[dict(entry(providers.parse_youtube_url(line,'video')['video_id']),position=i) for i,line in enumerate(text.splitlines(),1) if line.strip()])
transfers.transfer_addresses=lambda cfg: [dict(host='192.168.50.10',label='Wi-Fi simulé — 192.168.50.10')]
surface=sys.argv[1]
uvicorn.run(create_app(surface,cfg),host='127.0.0.1',port=cfg.guest_port if surface=='guest' else cfg.admin_port,access_log=False,log_level='warning')
`;

async function availablePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function checked(label) { report.checks.push(label); process.stdout.write(`✓ ${label}\n`); }
async function waitHealth(url, child) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Fixture server exited (${child.exitCode}): ${child.fixtureLogs || ''}`);
    try { if ((await fetch(`${url}/health`)).ok) return; } catch {}
    await pause(100);
  }
  throw new Error(`Fixture server did not start: ${child.fixtureLogs || ''}`);
}
function inspect(page, label) {
  page.on('pageerror', (error) => report.pageErrors.push(`${label}: ${error.message}`));
  page.on('console', (message) => { if (message.type() === 'error') report.consoleErrors.push(`${label}: ${message.text()}`); });
  page.on('requestfailed', (request) => report.requestFailures.push(`${label}: ${request.url()} ${request.failure()?.errorText}`));
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost', simulatedLanHost].includes(url.hostname) && url.protocol !== 'data:' && !isMockedEmbed(url)) report.externalRequests.push(request.url());
  });
}
function isMockedEmbed(url) {
  return url.protocol === 'https:' && url.hostname === 'www.youtube-nocookie.com' && !url.port &&
    url.pathname === '/embed/abcdefghijk' && !url.search && !url.username && !url.password;
}
async function interceptNetwork(context, guestUrl, guestPort) {
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (isMockedEmbed(url)) {
      await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Lecteur simulé</title><body><p>Lecteur YouTube simulé pour le test, sans audio.</p><button type="button">Lecture de test</button></body></html>' });
      report.mockedEmbedRequests.push({ videoId: 'abcdefghijk', fulfilledLocally: true });
      return;
    }
    if (url.hostname === simulatedLanHost && url.protocol === 'http:' && url.port === String(guestPort)) {
      // Exercise the real guest transfer guard with its exact private Host while
      // connecting only to our loopback fixture; this is not a physical LAN test.
      const response = await route.fetch({ url: `${guestUrl}${url.pathname}${url.search}`, maxRedirects: 0,
        headers: { ...request.headers(), host: `${simulatedLanHost}:${guestPort}` } });
      await route.fulfill({ response });
      report.simulatedLanRequests.push({ kind: url.pathname.endsWith('/download') ? 'ZIP download' : url.pathname.startsWith('/transfer/') ? 'transfer page' : 'local asset', forwardedToLoopback: true,
        status: response.status(), contentDisposition: response.headers()['content-disposition'] || '' });
      return;
    }
    if (['127.0.0.1', 'localhost'].includes(url.hostname) || url.protocol === 'data:') return route.continue();
    // Unexpected external destinations never get a real network connection.
    await route.abort('blockedbyclient');
  });
}
async function simulatedLanProxy(guestUrl, guestPort) {
  // Chromium downloads bypass Playwright Fetch interception. Route those only
  // through a loopback proxy too, preserving the exact Host required by the API.
  const proxy = http.createServer(async (request, response) => {
    try {
      const target = new URL(request.url);
      assert.equal(request.method, 'GET');
      assert.equal(target.origin, `http://${simulatedLanHost}:${guestPort}`);
      assert.match(target.pathname, /^\/(?:transfer\/[A-Za-z0-9_-]{43}(?:\/download)?|static\/(?:styles\.css|placeholder\.svg))$/);
      const forwarded = await new Promise((resolve, reject) => {
        // Node's fetch does not preserve a supplied Host; http.request does.
        const upstream = http.request(new URL(target.pathname, guestUrl), { method: 'GET', headers: { Host: `${simulatedLanHost}:${guestPort}` } }, reply => {
          const chunks = [];
          reply.on('data', chunk => chunks.push(chunk));
          reply.once('error', reject);
          reply.once('end', () => resolve({ status: reply.statusCode, headers: reply.headers, body: Buffer.concat(chunks) }));
        });
        upstream.once('error', reject); upstream.setTimeout(5000, () => upstream.destroy(new Error('Loopback fixture timed out'))); upstream.end();
      });
      const headers = { ...forwarded.headers };
      delete headers['transfer-encoding'];
      response.writeHead(forwarded.status, headers); response.end(forwarded.body);
      report.simulatedLanRequests.push({ kind: target.pathname.endsWith('/download') ? 'ZIP download' : 'transfer page',
        forwardedToLoopback: true, loopbackDownloadProxy: true, status: forwarded.status,
        contentDisposition: forwarded.headers['content-disposition'] || '' });
    } catch {
      response.writeHead(403, { 'Content-Type': 'text/plain' }); response.end('Destination hors du test refusée.');
    }
  });
  proxy.on('connect', (_request, socket) => socket.destroy());
  await new Promise((resolve, reject) => { proxy.once('error', reject); proxy.listen(0, '127.0.0.1', resolve); });
  proxies.push(proxy);
  return `http://127.0.0.1:${proxy.address().port}`;
}
async function screenshot(page, name) {
  const target = path.join(artifactDir, `${name}.png`);
  // Capture a settled page; fast automated actions can otherwise leave several
  // transient confirmations on top of the content being inspected.
  await page.waitForFunction(() => document.querySelectorAll('.toast').length === 0);
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); });
  await page.screenshot({ path: target, fullPage: true });
  report.screenshots.push(target);
}
async function noOverflow(page, label) {
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  assert.ok(sizes.document <= sizes.viewport + 1 && sizes.body <= sizes.viewport + 1, `${label} overflows horizontally: ${JSON.stringify(sizes)}`);
  checked(`${label} sans débordement horizontal`);
}
async function navigate(page, view) {
  await page.locator(`[data-view="${view}"]`).click();
  await page.locator(`#view-${view}`).waitFor({ state: 'visible' });
}
async function chooseNickname(page, nickname) {
  await page.locator('#identity-button').click();
  await page.locator('#nickname-input').fill(nickname);
  await page.locator('#identity-form button[type="submit"]').click();
  await page.locator('#identity-dialog').waitFor({ state: 'hidden' });
  await page.locator('#guest-name').filter({ hasText: nickname }).waitFor();
}
async function manualProposal(page, title, artist) {
  await page.locator('#tab-manual').click();
  await page.locator('#manual-title').fill(title);
  await page.locator('#manual-artist').fill(artist);
  await page.locator('#manual-form button[type="submit"]').click();
  await page.locator('.track-title').filter({ hasText: title }).waitFor();
}
async function preview(page, trigger) {
  const previousRequests = report.mockedEmbedRequests.length;
  await trigger.click();
  await page.locator('#preview-dialog').waitFor({ state: 'visible' });
  assert.equal(await page.locator('iframe').count(), 1);
  const source = new URL(await page.locator('#preview-player iframe').getAttribute('src'));
  assert.ok(isMockedEmbed(source));
  assert.notEqual(source.searchParams.get('autoplay'), '1');
  assert.equal(await page.locator('#preview-fallback').getAttribute('href'), 'https://www.youtube.com/watch?v=abcdefghijk');
  await page.frameLocator('#preview-player iframe').getByRole('button', { name: 'Lecture de test' }).waitFor();
  assert.equal(report.mockedEmbedRequests.length, previousRequests + 1);
}
async function previewClosed(page) {
  await page.locator('#preview-dialog').waitFor({ state: 'hidden' });
  await page.waitForFunction(() => document.querySelectorAll('iframe').length === 0);
}

async function main() {
  fs.mkdirSync(artifactDir, { recursive: true });
  const guestPort = await availablePort(), adminPort = await availablePort();
  const guestUrl = `http://127.0.0.1:${guestPort}`, adminUrl = `http://127.0.0.1:${adminPort}`;
  const env = { ...process.env, DATA_DIR: dataDir, ADMIN_PASSWORD: password,
    EVENT_NAME: 'Les 25 ans de Fanny',
    SESSION_SECRET: randomBytes(48).toString('base64url'), GUEST_HOST: '127.0.0.1', ADMIN_HOST: '127.0.0.1',
    GUEST_PORT: String(guestPort), ADMIN_PORT: String(adminPort), COOKIE_SECURE: 'false', LOCAL_URL: guestUrl,
    PUBLIC_URL: '', SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '', SUPABASE_ANON_KEY: '', MIN_FREE_MB: '0' };
  execFileSync(python, ['-c', `
from playlist25.config import load_settings
from playlist25 import db
from pathlib import Path
import wave
cfg=load_settings();db.init_db(cfg)
with db.connect(cfg) as conn:
 db.set_setting(conn,'offline_mode',True)
 conn.commit()
with wave.open(str(Path(cfg.data_dir)/'Silence autorisé.wav'),'wb') as wav:
 wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\\x00\\x00'*8000)
`], { cwd: root, env });
  for (const surface of ['guest', 'admin']) {
    const child = spawn(python, ['-c', fixture, surface], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
    child.fixtureLogs = '';
    for (const stream of [child.stdout, child.stderr]) stream.on('data', (chunk) => { child.fixtureLogs = (child.fixtureLogs + chunk.toString()).slice(-5000); });
    children.push(child);
  }
  await Promise.all([waitHealth(guestUrl, children[0]), waitHealth(adminUrl, children[1])]);
  const executablePath = process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
  browser = await chromium.launch({ headless: true, executablePath, args: process.platform === 'linux' ? ['--no-sandbox'] : [] });
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  await interceptNetwork(desktop, guestUrl, guestPort);
  const guest = await desktop.newPage(); inspect(guest, 'invités bureau');
  await guest.goto(guestUrl); await guest.locator('#global-notice').filter({ hasText: 'hors ligne' }).waitFor();
  assert.equal(await guest.locator('#search-input').isDisabled(), true);
  assert.match(await guest.title(), /Fanny/);
  assert.match(await guest.locator('#event-title').innerText(), /Les 25 ans\s+de Fanny\./);
  assert.match(await guest.locator('.birthday-invite').innerText(), /17 octobre/);
  assert.match(await guest.locator('.birthday-invite').innerText(), /19 h/);
  assert.match(await guest.locator('.birthday-invite').innerText(), /23 rue du Stade/);
  assert.match(await guest.locator('.birthday-invite').innerText(), /25660 Fontain/);
  assert.equal(await guest.locator('iframe').count(), 0);
  assert.equal(report.mockedEmbedRequests.length, 0);
  checked('Invitation de Fanny, date et adresse affichées sans lecteur ni connexion YouTube');
  await chooseNickname(guest, 'Camille');
  await manualProposal(guest, 'Une idée accentuée été', 'Artiste test');
  checked('Pseudo persistant et proposition manuelle hors ligne');
  await guest.locator('#tab-link').click();
  await guest.locator('#link-input').fill('https://youtu.be/abcdefghijk');
  await guest.locator('#link-form button[type="submit"]').click();
  await guest.locator('.track-title').filter({ hasText: 'abcdefghijk' }).waitFor();
  assert.equal(await guest.locator('[data-preview-id="abcdefghijk"]').isDisabled(), true);
  checked('Lien YouTube canonique enregistré hors ligne');
  const ownTrack = guest.locator('.track-card').filter({ hasText: 'Une idée accentuée été' });
  await ownTrack.locator('.vote-button').click();
  await guest.waitForFunction(() => [...document.querySelectorAll('.track-card')].find(n => n.textContent.includes('Une idée accentuée été'))?.querySelector('.vote-button')?.getAttribute('aria-pressed') === 'true');
  await guest.locator('.track-card').filter({ hasText: 'Une idée accentuée été' }).locator('.vote-button').click();
  await guest.waitForFunction(() => [...document.querySelectorAll('.track-card')].find(n => n.textContent.includes('Une idée accentuée été'))?.querySelector('.vote-button')?.getAttribute('aria-pressed') === 'false');
  checked('Vote ajouté puis retiré dans le navigateur');
  await guest.locator('#filter-mine').click();
  assert.equal(await guest.locator('.track-card').count(), 2);
  await guest.locator('#catalogue-query').fill('accentuée');
  assert.equal(await guest.locator('.track-card').count(), 1);
  await guest.locator('#catalogue-query').fill('');
  await guest.reload(); await guest.locator('#guest-name').filter({ hasText: 'Camille' }).waitFor();
  checked('Mes propositions, recherche locale et conservation de session');
  await noOverflow(guest, 'Invités bureau'); await screenshot(guest, 'guest-desktop');

  await manualProposal(guest, 'Une proposition ajoutée par erreur', 'Artiste test');
  const withdrawnTrack = guest.locator('.track-card').filter({ hasText: 'Une proposition ajoutée par erreur' });
  assert.equal(await guest.locator('#stat-tracks').innerText(), '3');
  assert.equal(await guest.locator('#stat-mine').innerText(), '3');
  await withdrawnTrack.locator('[data-withdraw-id]').click();
  await guest.locator('#withdraw-dialog').waitFor({ state: 'visible' });
  assert.match(await guest.locator('#withdraw-explanation').innerText(), /ATTENTION/);
  assert.equal(await guest.locator('#withdraw-title').innerText(), 'Une proposition ajoutée par erreur');
  assert.equal(await guest.evaluate(() => document.activeElement.id), 'withdraw-cancel');
  await screenshot(guest, 'guest-desktop-withdraw-dialog');
  await guest.locator('#withdraw-cancel').click();
  await guest.locator('#withdraw-dialog').waitFor({ state: 'hidden' });
  assert.equal(await withdrawnTrack.count(), 1);
  assert.equal(await guest.locator('#stat-tracks').innerText(), '3');
  checked('Annuler un retrait conserve le morceau et les compteurs');
  await withdrawnTrack.locator('[data-withdraw-id]').click();
  const withdrawalResponse = guest.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/withdraw'));
  await guest.locator('#withdraw-confirm').click();
  assert.equal((await withdrawalResponse).status(), 200);
  await guest.locator('#withdraw-dialog').waitFor({ state: 'hidden' });
  await guest.waitForFunction(() => document.querySelector('#stat-tracks').textContent === '2' && document.querySelector('#stat-mine').textContent === '2');
  assert.equal(await withdrawnTrack.count(), 0);
  await guest.reload(); await guest.locator('#stat-tracks').filter({ hasText: '2' }).waitFor();
  assert.equal(await withdrawnTrack.count(), 0);
  assert.equal(await guest.locator('[data-withdraw-id]').count(), 2);
  checked('Retrait confirmé enregistré, listes et compteurs corrigés après rechargement');

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await interceptNetwork(mobileContext, guestUrl, guestPort);
  const mobile = await mobileContext.newPage(); inspect(mobile, 'invités mobile');
  await mobile.goto(guestUrl); await mobile.locator('#stat-tracks').filter({ hasText: '2' }).waitFor();
  await chooseNickname(mobile, 'Camille');
  assert.equal(await mobile.locator('[data-withdraw-id]').count(), 0);
  checked('Deux invités avec le même pseudo ne reçoivent aucun bouton de retrait pour les morceaux de l’autre');
  await manualProposal(mobile, 'Deuxième navigateur', 'Autre artiste');
  assert.equal(await mobile.locator('[data-withdraw-id]').count(), 1);
  assert.equal(await mobile.locator('.track-card').filter({ hasText: 'Une idée accentuée été' }).locator('[data-withdraw-id]').count(), 0);
  await mobile.locator('#filter-mine').click(); assert.equal(await mobile.locator('.track-card').count(), 1);
  checked('Deux navigateurs sur la même IP gardent des identités distinctes');
  await noOverflow(mobile, 'Invités mobile 390 px'); await screenshot(mobile, 'guest-mobile');
  await mobile.locator('[data-withdraw-id]').click();
  await mobile.locator('#withdraw-dialog').waitFor({ state: 'visible' });
  await noOverflow(mobile, 'Confirmation de retrait mobile 390 px');
  await screenshot(mobile, 'guest-mobile-withdraw-dialog');
  await mobile.locator('#withdraw-cancel').click();
  await mobile.locator('#withdraw-dialog').waitFor({ state: 'hidden' });

  const admin = await desktop.newPage(); inspect(admin, 'administration');
  await admin.goto(adminUrl); await admin.locator('#admin-password').fill(password);
  await admin.locator('#login-form button[type="submit"]').click();
  await admin.locator('#admin-layout').waitFor({ state: 'visible' });
  await admin.locator('#dashboard-stats .stat-card').first().waitFor();
  checked('Connexion administrateur et tableau de bord');
  await noOverflow(admin, 'Administration bureau'); await screenshot(admin, 'admin-dashboard');
  await admin.locator('#show-local-qr').click();
  await admin.waitForFunction(() => document.querySelector('#admin-local-qr').complete && document.querySelector('#admin-local-qr').naturalWidth > 0);
  await admin.locator('[data-close-dialog="qr-dialog"]').click();
  checked('QR code local généré dans l’administration privée');
  await admin.locator('#dashboard-offline').uncheck();
  await admin.waitForFunction(() => document.querySelector('#admin-mode-label').textContent === 'Préparation');
  await guest.locator('#refresh-proposals').click();
  await guest.locator('#search-input').waitFor({ state: 'attached' });
  await guest.waitForFunction(() => !document.querySelector('#search-input').disabled);
  await guest.locator('#tab-search').click();
  await guest.locator('#search-input').fill('Dalida');
  await guest.locator('.search-result').waitFor();
  assert.equal(await guest.locator('.search-result').count(), 1);
  checked('Recherche pendant la saisie avec délai, sans soumettre le formulaire');
  assert.equal(await guest.locator('iframe').count(), 0);
  assert.equal(report.mockedEmbedRequests.length, 0);
  checked('La recherche et le catalogue ne chargent aucun lecteur avant un clic explicite');
  await preview(guest, guest.locator('.search-result [data-preview-id="abcdefghijk"]'));
  await noOverflow(guest, 'Lecteur de prévisualisation bureau');
  await screenshot(guest, 'guest-desktop-preview');
  await guest.locator('[data-close-dialog="preview-dialog"]').click();
  await previewClosed(guest);
  checked('Écoute à la demande : un iframe simulé sans autoplay, lien canonique et destruction à la fermeture');
  await preview(guest, guest.locator('.search-result [data-preview-id="abcdefghijk"]'));
  await guest.locator('#preview-fallback').focus();
  await guest.keyboard.press('Escape');
  await previewClosed(guest);
  checked('Annuler le lecteur avec Échap supprime également l’iframe');
  await mobile.locator('#refresh-proposals').click();
  await mobile.locator('#filter-all').click();
  await mobile.locator('[data-preview-id="abcdefghijk"]:not(:disabled)').waitFor();
  await preview(mobile, mobile.locator('.track-card [data-preview-id="abcdefghijk"]'));
  await noOverflow(mobile, 'Lecteur de prévisualisation mobile 390 px');
  await screenshot(mobile, 'guest-mobile-preview');
  await mobile.locator('[data-close-dialog="preview-dialog"]').click();
  await previewClosed(mobile);
  checked('Lecteur mobile ouvert uniquement à la demande et fermé sans connexion musicale réelle');
  await guest.locator('#search-form button[type="submit"]').click();
  await guest.locator('.search-result').waitFor();
  await guest.locator('.search-result button').filter({ hasText: 'Ajouter ce morceau' }).click();
  await guest.locator('#entry-feedback').filter({ hasText: 'déjà' }).waitFor();
  checked('Recherche simulée et même source proposée sous un autre lien sans doublon');
  await navigate(admin, 'proposals');
  await admin.locator('#admin-proposals tr').filter({ hasText: 'Deuxième navigateur' }).waitFor();
  await admin.locator('#select-all-proposals').check();
  assert.match(await admin.locator('#proposal-source-summary').innerText(), /1 source\(s\).*2 demande\(s\)/);
  const bulkCalls = [];
  const recordBulk = request => { if (request.method() === 'PATCH' && new URL(request.url()).pathname === '/api/proposals') bulkCalls.push(request.postDataJSON()); };
  admin.on('request', recordBulk);
  const bulkResponse = admin.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === '/api/proposals');
  await admin.locator('#approve-selected').click();
  assert.equal((await bulkResponse).status(), 200);
  admin.off('request', recordBulk);
  assert.equal(bulkCalls.length, 1);
  assert.equal(bulkCalls[0].proposal_ids.length, 3);
  await admin.waitForFunction(() => [...document.querySelectorAll('#admin-proposals .status-badge')].filter(n => n.textContent === 'Validé').length === 3);
  const archivedRow = admin.locator('#admin-proposals tr').filter({ hasText: 'Une proposition ajoutée par erreur' });
  assert.equal(await archivedRow.locator('input[type="checkbox"]').isDisabled(), true);
  assert.match(await archivedRow.innerText(), /Retirée par l’invité/);
  assert.equal(await archivedRow.getByRole('button', { name: 'Valider', exact: true }).count(), 0);
  checked('Validation de toutes les propositions sélectionnées');
  checked('Validation groupée par un seul appel et retrait invité conservé sans possibilité de validation');
  await admin.locator('#download-selected').click();
  await admin.locator('#rights-dialog').waitFor({ state: 'visible' });
  assert.equal(await admin.locator('#rights-start').isDisabled(), true);
  await admin.locator('#rights-confirm').check(); await admin.locator('#rights-start').click();
  await admin.locator('#view-downloads').waitFor({ state: 'visible' });
  await admin.locator('#jobs-list').filter({ hasText: 'En attente' }).waitFor();
  checked('Confirmation des autorisations et file persistante sans worker');
  await admin.locator('#queue-toggle').click();
  await admin.locator('#queue-toggle').filter({ hasText: 'Reprendre' }).waitFor();
  checked('Suspension de la file après le morceau en cours');

  await navigate(admin, 'imports');
  await admin.locator('#playlist-url').fill('https://www.youtube.com/playlist?list=PLabcdefghijk');
  await admin.locator('#playlist-analyze-form button[type="submit"]').click();
  await admin.locator('#analysis-panel').waitFor({ state: 'visible' });
  assert.equal(await admin.locator('#analysis-entries tr').count(), 3);
  assert.equal(await admin.locator('#analysis-entries input:disabled').count(), 1);
  await admin.locator('#analysis-select-all').click();
  await admin.locator('#import-only').click();
  await admin.locator('#saved-playlists').filter({ hasText: 'Playlist de test' }).waitFor();
  checked('Import de playlist simulée avec doublon et élément indisponible, sans téléchargement');

  await navigate(admin, 'library'); await admin.locator('#open-audio-import').click();
  await admin.locator('#audio-file').setInputFiles(path.join(dataDir, 'Silence autorisé.wav'));
  await admin.locator('#audio-title').fill('Audio autorisé pour test');
  await admin.locator('#audio-artist').fill('Test local');
  await admin.locator('#audio-import-form button[type="submit"]').click();
  await admin.locator('#audio-import-dialog').waitFor({ state: 'hidden' });
  await admin.locator('.library-row').filter({ hasText: 'Audio autorisé pour test' }).waitFor();
  await admin.locator('.library-row button').filter({ hasText: 'Renommer' }).click();
  await admin.locator('#edit-title').fill('Audio été renommé');
  await admin.locator('#edit-form button[type="submit"]').click();
  await admin.locator('#edit-dialog').waitFor({ state: 'hidden' });
  await admin.locator('.library-row').filter({ hasText: 'Audio été renommé' }).waitFor();
  checked('Import audio local généré, contrôle ffprobe et renommage accentué');

  await navigate(admin, 'downloads');
  await admin.locator('#queue-ready-count').filter({ hasText: '1 fichier prêt' }).waitFor();
  assert.equal(await admin.locator('#queue-ready-export').isEnabled(), true);
  assert.equal(await admin.locator('#jobs-list').filter({ hasText: 'En attente' }).count(), 1);
  await admin.locator('#queue-ready-export').click();
  await admin.locator('#view-exports').waitFor({ state: 'visible' });
  await admin.locator('#export-selection-count').filter({ hasText: '1 fichier prêt sélectionné' }).waitFor();
  assert.equal(await admin.locator('#export-selection input[type="checkbox"]').count(), 1);
  assert.equal(await admin.locator('#export-selection input:checked').count(), 1);
  assert.equal(await admin.locator('#prepare-export').innerText(), 'Créer le ZIP de 1 fichier ↓');
  assert.equal(await admin.locator('#prepare-export').isEnabled(), true);
  checked('La file et le ZIP comptent uniquement le fichier réellement prêt, même avec une source en attente');
  await navigate(admin, 'exports');
  await admin.locator('#export-select-all').click(); await admin.locator('#prepare-export').click();
  const zipLink = admin.locator('#export-result a[href^="/api/exports/"]'); await zipLink.waitFor();
  const downloadPromise = admin.waitForEvent('download'); await zipLink.click();
  const download = await downloadPromise; const zipPath = path.join(artifactDir, 'pack-test.zip'); await download.saveAs(zipPath);
  assert.ok(fs.statSync(zipPath).size > 0);
  execFileSync(python, ['-c', `import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n text=z.read('Anniversaire25/01-selection.m3u8').decode('utf-8')\n assert 'audio/source' in text and 'Audio été renommé' in text\n assert not any(n.startswith('/') or '..' in n.split('/') for n in z.namelist())`, zipPath], { cwd: root, env });
  checked('Pack ZIP récupéré par le navigateur avec audio et M3U8 relatif UTF-8');
  await screenshot(admin, 'admin-export');

  const addressesResponse = admin.waitForResponse(response => response.url().endsWith('/api/transfers/addresses'));
  await admin.locator('#share-export').click();
  assert.equal((await addressesResponse).status(), 200);
  await admin.locator('#transfer-dialog').waitFor({ state: 'visible' });
  assert.equal(await admin.locator('#transfer-create').isDisabled(), true);
  assert.equal(await admin.locator('#transfer-host option').count(), 2);
  await admin.locator('#transfer-host').selectOption(simulatedLanHost);
  const transferResponse = admin.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/transfers');
  const beforeGrant = Date.now();
  await admin.locator('#transfer-create').click();
  const grantResponse = await transferResponse;
  assert.equal(grantResponse.status(), 200);
  const grant = await grantResponse.json(), transferUrl = new URL(grant.url);
  assert.equal(transferUrl.protocol, 'http:');
  assert.equal(transferUrl.hostname, simulatedLanHost);
  assert.equal(transferUrl.port, String(guestPort));
  assert.match(transferUrl.pathname, /^\/transfer\/[A-Za-z0-9_-]{43}$/);
  assert.match(grant.id, /^[a-f0-9]{32}$/);
  assert.match(grant.filename, /^Anniversaire25-[a-f0-9]{8}\.zip$/);
  assert.equal(grant.size, fs.statSync(zipPath).size);
  const grantLifetime = Date.parse(grant.expires_at) - beforeGrant;
  assert.ok(grantLifetime >= 599000 && grantLifetime <= 602000, 'Grant lifetime is ten minutes');
  await admin.locator('#transfer-result').waitFor({ state: 'visible' });
  await admin.waitForFunction(() => document.querySelector('#transfer-qr').complete && document.querySelector('#transfer-qr').naturalWidth > 0);
  assert.match(await admin.locator('#transfer-qr').getAttribute('src'), /^data:image\/png;base64,/);
  assert.equal(await admin.locator('#transfer-url').inputValue(), grant.url);
  assert.match(await admin.locator('#transfer-expiry').innerText(), /10 minutes maximum/);
  assert.ok((await admin.locator('#transfer-expiry').innerText()).includes(grant.filename));
  await noOverflow(admin, 'QR de partage bureau');
  await screenshot(admin, 'admin-desktop-transfer-qr');
  await admin.setViewportSize({ width: 390, height: 844 });
  await noOverflow(admin, 'QR de partage mobile 390 px');
  await screenshot(admin, 'admin-mobile-transfer-qr');
  await admin.setViewportSize({ width: 1440, height: 1000 });
  checked('Partage Wi-Fi créé via l’API réelle : QR lisible, nom du ZIP et expiration de dix minutes');

  const lanProxyUrl = await simulatedLanProxy(guestUrl, guestPort);
  const lanContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, acceptDownloads: true,
    proxy: { server: lanProxyUrl, bypass: '127.0.0.1,localhost' } });
  await interceptNetwork(lanContext, guestUrl, guestPort);
  const lan = await lanContext.newPage(); inspect(lan, 'transfert mobile simulé');
  const transferPageResponse = await lan.goto(grant.url);
  assert.equal(transferPageResponse.status(), 200);
  await lan.locator('.transfer-download').waitFor();
  assert.equal(await lan.locator('.transfer-download').getAttribute('href'), `${transferUrl.pathname}/download`);
  assert.ok((await lan.locator('.transfer-file').innerText()).includes(grant.filename));
  await noOverflow(lan, 'Page de transfert mobile 390 px');
  await screenshot(lan, 'transfer-mobile');
  const lanDownloadPromise = lan.waitForEvent('download');
  await lan.locator('.transfer-download').click();
  const lanDownload = await lanDownloadPromise;
  const lanZipPath = path.join(artifactDir, 'pack-transfer-test.zip');
  await lanDownload.saveAs(lanZipPath);
  assert.equal(lanDownload.suggestedFilename(), grant.filename);
  assert.deepEqual(fs.readFileSync(lanZipPath), fs.readFileSync(zipPath));
  checked('ZIP identique téléchargé sur un second navigateur mobile via une adresse LAN simulée');
  const revokeResponse = admin.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/transfers/revoke'));
  await admin.locator('#transfer-revoke').click();
  const revocation = await revokeResponse;
  assert.equal(revocation.status(), 200);
  assert.equal((await revocation.json()).revoked, true);
  await admin.locator('#transfer-dialog').waitFor({ state: 'hidden' });
  assert.equal(await admin.locator('#transfer-qr').getAttribute('src'), null);
  assert.equal(await admin.locator('#transfer-url').inputValue(), '');
  const revokedPage = await lanContext.request.get(`${guestUrl}${transferUrl.pathname}`, { headers: { Host: `${simulatedLanHost}:${guestPort}` } });
  const revokedZip = await lanContext.request.get(`${guestUrl}${transferUrl.pathname}/download`, { headers: { Host: `${simulatedLanHost}:${guestPort}` } });
  assert.equal(revokedPage.status(), 404);
  assert.equal(revokedZip.status(), 404);
  checked('Fermer le partage révoque l’accès à la page et au ZIP et efface le QR et le lien');
  await lanContext.close();

  await navigate(admin, 'settings');
  await admin.locator('#diagnostics-list .diagnostic-row').first().waitFor();
  await admin.locator('#setting-event-name').fill('La fête de test');
  await admin.locator('#settings-form button[type="submit"]').click();
  await admin.locator('#admin-event-name').filter({ hasText: 'La fête de test' }).waitFor();
  await admin.locator('#invitation-count').fill('2'); await admin.locator('#invitation-form button').click();
  await admin.locator('#invitation-codes').waitFor({ state: 'visible' });
  assert.equal((await admin.locator('#invitation-codes').inputValue()).trim().split('\n').length, 2);
  checked('Réglages, diagnostics locaux et génération de codes d’invitation');
  await admin.setViewportSize({ width: 390, height: 844 });
  await noOverflow(admin, 'Administration mobile 390 px'); await screenshot(admin, 'admin-mobile-settings');
  await navigate(admin, 'proposals'); await noOverflow(admin, 'Tableau de modération mobile 390 px');
  await screenshot(admin, 'admin-mobile-proposals');

  // Direct HTTP checks do not deliberately inject failed fetches into the browser.
  assert.equal((await fetch(`${guestUrl}/api/library`)).status, 404);
  assert.equal((await fetch(`${guestUrl}/api/downloads`, { method: 'POST' })).status, 404);
  assert.equal((await fetch(`${adminUrl}/api/library`)).status, 401);
  assert.equal((await fetch(`${adminUrl}/api/proposals`, { method: 'DELETE' })).status, 405);
  checked('Surface publique sans fichiers/admin et administration exigeant une session');
  assert.deepEqual(report.pageErrors, [], 'Unexpected JavaScript exceptions');
  assert.deepEqual(report.consoleErrors, [], 'Unexpected browser console errors');
  assert.deepEqual(report.requestFailures, [], 'Unexpected failed browser requests');
  assert.deepEqual(report.externalRequests, [], 'Interface requested an external asset or service');
  assert.equal(report.mockedEmbedRequests.length, 3);
  assert.ok(report.mockedEmbedRequests.every(request => request.fulfilledLocally));
  assert.ok(report.simulatedLanRequests.some(request => request.kind === 'ZIP download' && request.forwardedToLoopback));
  checked('Aucune erreur JavaScript/console/réseau ni connexion externe réelle ; seuls les lecteurs simulés demandés sont chargés');
}

(async () => {
  try { await main(); report.success = true; }
  catch (error) { report.success = false; report.error = error.stack || String(error); process.exitCode = 1; process.stderr.write(`${report.error}\n`); }
  finally {
    if (browser) await browser.close().catch(() => {});
    await Promise.all(proxies.map(proxy => new Promise(resolve => { proxy.close(resolve); proxy.closeAllConnections(); })));
    for (const child of children) { if (child.exitCode === null) child.kill('SIGTERM'); }
    await Promise.all(children.map(child => child.exitCode !== null ? Promise.resolve() : new Promise(resolve => { child.once('exit', resolve); const timer = setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); resolve(); }, 5000); timer.unref(); })));
    fs.writeFileSync(path.join(artifactDir, 'report.json'), JSON.stringify(report, null, 2));
    fs.rmSync(dataDir, { recursive: true, force: true });
    process.stdout.write(`Rapport navigateur : ${path.join(artifactDir, 'report.json')}\n`);
  }
})();
