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
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PLAYLIST_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const artifactDir = process.env.PLAYLIST_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-browser-'));
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-browser-data-'));
const password = randomBytes(24).toString('base64url');
const report = { youtubeMetadata: 'simulated', audioFixture: 'locally generated one-second silent WAV',
  workerStarted: false, checks: [], pageErrors: [], consoleErrors: [], requestFailures: [], screenshots: [], externalRequests: [] };
const children = [];
let browser;

const fixture = `
import sys, uvicorn
from playlist25 import providers
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
  page.on('request', (request) => { const url = new URL(request.url()); if (!['127.0.0.1', 'localhost'].includes(url.hostname) && url.protocol !== 'data:') report.externalRequests.push(request.url()); });
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

async function main() {
  fs.mkdirSync(artifactDir, { recursive: true });
  const guestPort = await availablePort(), adminPort = await availablePort();
  const guestUrl = `http://127.0.0.1:${guestPort}`, adminUrl = `http://127.0.0.1:${adminPort}`;
  const env = { ...process.env, DATA_DIR: dataDir, ADMIN_PASSWORD: password,
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
  const guest = await desktop.newPage(); inspect(guest, 'invités bureau');
  await guest.goto(guestUrl); await guest.locator('#global-notice').filter({ hasText: 'hors ligne' }).waitFor();
  assert.equal(await guest.locator('#search-input').isDisabled(), true);
  await chooseNickname(guest, 'Camille');
  await manualProposal(guest, 'Une idée accentuée été', 'Artiste test');
  checked('Pseudo persistant et proposition manuelle hors ligne');
  await guest.locator('#tab-link').click();
  await guest.locator('#link-input').fill('https://youtu.be/abcdefghijk');
  await guest.locator('#link-form button[type="submit"]').click();
  await guest.locator('.track-title').filter({ hasText: 'abcdefghijk' }).waitFor();
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

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mobile = await mobileContext.newPage(); inspect(mobile, 'invités mobile');
  await mobile.goto(guestUrl); await mobile.locator('#stat-tracks').filter({ hasText: '2' }).waitFor();
  await chooseNickname(mobile, 'Baptiste');
  await manualProposal(mobile, 'Deuxième navigateur', 'Autre artiste');
  await mobile.locator('#filter-mine').click(); assert.equal(await mobile.locator('.track-card').count(), 1);
  checked('Deux navigateurs sur la même IP gardent des identités distinctes');
  await noOverflow(mobile, 'Invités mobile 390 px'); await screenshot(mobile, 'guest-mobile');

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
  await guest.locator('#search-form button[type="submit"]').click();
  await guest.locator('.search-result').waitFor();
  await guest.locator('.search-result button').click();
  await guest.locator('#entry-feedback').filter({ hasText: 'déjà' }).waitFor();
  checked('Recherche simulée et même source proposée sous un autre lien sans doublon');
  await navigate(admin, 'proposals');
  await admin.locator('#admin-proposals tr').filter({ hasText: 'Deuxième navigateur' }).waitFor();
  await admin.locator('#select-all-proposals').check();
  await admin.locator('#approve-selected').click();
  await admin.waitForFunction(() => [...document.querySelectorAll('#admin-proposals .status-badge')].length === 3 && [...document.querySelectorAll('#admin-proposals .status-badge')].every(n => n.textContent === 'Validé'));
  checked('Validation de toutes les propositions sélectionnées');
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

  await navigate(admin, 'exports');
  await admin.locator('#export-select-all').click(); await admin.locator('#prepare-export').click();
  const zipLink = admin.locator('#export-result a'); await zipLink.waitFor();
  const downloadPromise = admin.waitForEvent('download'); await zipLink.click();
  const download = await downloadPromise; const zipPath = path.join(artifactDir, 'pack-test.zip'); await download.saveAs(zipPath);
  assert.ok(fs.statSync(zipPath).size > 0);
  execFileSync(python, ['-c', `import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n text=z.read('Anniversaire25/01-selection.m3u8').decode('utf-8')\n assert 'audio/source' in text and 'Audio été renommé' in text\n assert not any(n.startswith('/') or '..' in n.split('/') for n in z.namelist())`, zipPath], { cwd: root, env });
  checked('Pack ZIP récupéré par le navigateur avec audio et M3U8 relatif UTF-8');
  await screenshot(admin, 'admin-export');
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
  checked('Aucune erreur JavaScript/console/réseau et aucune ressource externe');
}

(async () => {
  try { await main(); report.success = true; }
  catch (error) { report.success = false; report.error = error.stack || String(error); process.exitCode = 1; process.stderr.write(`${report.error}\n`); }
  finally {
    if (browser) await browser.close().catch(() => {});
    for (const child of children) { if (child.exitCode === null) child.kill('SIGTERM'); }
    await Promise.all(children.map(child => child.exitCode !== null ? Promise.resolve() : new Promise(resolve => { child.once('exit', resolve); const timer = setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); resolve(); }, 5000); timer.unref(); })));
    fs.writeFileSync(path.join(artifactDir, 'report.json'), JSON.stringify(report, null, 2));
    fs.rmSync(dataDir, { recursive: true, force: true });
    process.stdout.write(`Rapport navigateur : ${path.join(artifactDir, 'report.json')}\n`);
  }
})();
